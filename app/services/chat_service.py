"""会话服务：编排 Agent 管线并持久化对话。

职责：
- 维护会话生命周期（创建 / 列表 / 详情 / 删除）；
- 调用 Agent 五阶段管线生成回复；
- 将用户消息与助手回复落库，供后续上下文回溯；
- 基于多租户 RBAC 做归属校验（普通用户仅可见自己的会话，系统管理员可见同租户全部）。
"""

import logging
from collections.abc import AsyncIterator

from sqlmodel import Session, select

from app.agents.pipeline import AgentEvent, AgentPipeline, AgentState
from app.agents.tools.base import ToolRegistry
from app.agents.tools.builtin import default_tools
from app.core.config import settings
from app.llm.base import ChatMessage, ChatRole, LLMOptions
from app.llm.factory import get_llm_provider
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.rag.service import RAGService

logger = logging.getLogger(__name__)

# 送入管线的历史轮次上限，避免上下文过长。
_HISTORY_LIMIT = 20


class ChatService:
    """对话编排与持久化服务。"""

    def __init__(self, llm_provider=None) -> None:
        # 允许注入自定义 LLM（测试 / 运行时覆盖）；为空时按需取全局提供商。
        self._llm = llm_provider

    @property
    def llm(self):
        return self._llm or get_llm_provider()

    @property
    def _options(self) -> LLMOptions:
        return LLMOptions(
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
            timeout=settings.LLM_TIMEOUT,
        )

    # ── 会话生命周期 ──────────────────────────────
    def list_conversations(
        self, session: Session, user: User, *, limit: int = 50, offset: int = 0
    ) -> list[Conversation]:
        """列出当前用户可见的会话（系统管理员可见同租户全部）。"""
        stmt = select(Conversation).where(Conversation.tenant_id == user.tenant_id)
        if user.role_enum.value != "system_admin":
            stmt = stmt.where(Conversation.user_id == user.id)
        stmt = stmt.order_by(Conversation.updated_at.desc()).limit(limit).offset(offset)
        return list(session.exec(stmt).all())

    def get_conversation(
        self, session: Session, user: User, conversation_id: str
    ) -> Conversation | None:
        """按 ID 获取会话，无权限时返回 None。"""
        conv = session.get(Conversation, conversation_id)
        if conv is None or not self._can_access(conv, user):
            return None
        return conv

    def delete_conversation(
        self, session: Session, user: User, conversation_id: str
    ) -> bool:
        """删除会话（级联删除消息）。无权限或不存在返回 False。"""
        conv = self.get_conversation(session, user, conversation_id)
        if conv is None:
            return False
        session.delete(conv)
        session.commit()
        return True

    def _can_access(self, conv: Conversation, user: User) -> bool:
        if user.role_enum.value == "system_admin":
            return conv.tenant_id == user.tenant_id
        return conv.user_id == user.id and conv.tenant_id == user.tenant_id

    # ── 对话执行 ──────────────────────────────────
    def _history_messages(self, conv: Conversation) -> list[ChatMessage]:
        recent = conv.messages[-_HISTORY_LIMIT:]
        return [
            ChatMessage(role=ChatRole(m.role), content=m.content) for m in recent
        ]

    def _build_retriever(self, session: Session, user: User):
        """按配置构建检索钩子（未开启 RAG 时返回 None）。"""
        if not settings.RAG_ENABLED:
            return None
        rag = RAGService(session, user.tenant_id)
        return rag.make_retriever()

    def _build_tools(self) -> ToolRegistry:
        """构建工具注册表（内置工具集，可在运行时扩展）。"""
        return ToolRegistry(default_tools())

    async def chat(
        self, session: Session, user: User, message: str, conversation_id: str | None = None
    ) -> tuple[Conversation, str]:
        """执行一次对话（非流式），返回会话与最终回复。"""
        conv = self._resolve_conversation(session, user, conversation_id, message)
         # 1. 取历史对话（最近 20 轮）
        history = self._history_messages(conv)
        state = AgentState(user_input=message, history=history)
        # 3. 创建管线，注入 LLM + 检索器
        pipeline = AgentPipeline(
            # 2. 构建检索器（通过 RAGService）
            self.llm,
            options=self._options,
            retriever=self._build_retriever(session, user),
            tools=self._build_tools(),
        )
        # 4. 执行
        pipeline.max_tool_rounds = settings.AGENT_MAX_TOOL_ROUNDS
        result = await pipeline.run(state)
        # 5. 持久化用户消息和助手回复
        self._persist_user(session, conv, message)
        self._persist_assistant(session, conv, result.answer, self._model_name())
        session.refresh(conv)
        return conv, result.answer

    async def chat_stream(
        self, session: Session, user: User, message: str, conversation_id: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        """流式执行对话，逐个产出管线事件（阶段 / token / 结束）。"""
        conv = self._resolve_conversation(session, user, conversation_id, message)
        history = self._history_messages(conv)
        state = AgentState(user_input=message, history=history)

        pipeline = AgentPipeline(
            self.llm,
            options=self._options,
            retriever=self._build_retriever(session, user),
            tools=self._build_tools(),
        )
        pipeline.max_tool_rounds = settings.AGENT_MAX_TOOL_ROUNDS
        collected: list[str] = []
        async for event in pipeline.run_stream(state):
            if event.type == "token":
                collected.append(event.data)
            yield event

        answer = "".join(collected) or state.answer
        self._persist_user(session, conv, message)
        self._persist_assistant(session, conv, answer, self._model_name())
        session.refresh(conv)

    # ── 内部辅助 ──────────────────────────────────
    def _resolve_conversation(
        self, session: Session, user: User, conversation_id: str | None, message: str
    ) -> Conversation:
        if conversation_id:
            conv = session.get(Conversation, conversation_id)
            if conv is None or not self._can_access(conv, user):
                raise ValueError("会话不存在或无权访问")
            return conv
        title = (message or "新会话")[:40]
        conv = Conversation(tenant_id=user.tenant_id, user_id=user.id, title=title)
        session.add(conv)
        session.commit()
        session.refresh(conv)
        return conv

    def _persist_user(self, session: Session, conv: Conversation, message: str) -> None:
        session.add(
            Message(conversation_id=conv.id, role="user", content=message)
        )
        session.commit()

    def _persist_assistant(
        self, session: Session, conv: Conversation, content: str, model: str | None
    ) -> None:
        session.add(
            Message(
                conversation_id=conv.id, role="assistant", content=content, model=model
            )
        )
        session.commit()

    def _model_name(self) -> str | None:
        return getattr(self.llm, "model", None)
