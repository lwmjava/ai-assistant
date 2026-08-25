"""会话服务：编排 Agent 管线并持久化对话。

职责：
- 维护会话生命周期（创建 / 列表 / 详情 / 删除）；
- 调用 Agent 五阶段管线生成回复；
- 将用户消息与助手回复落库，供后续上下文回溯；
- 基于多租户 RBAC 做归属校验（普通用户仅可见自己的会话，系统管理员可见同租户全部）。
"""

import asyncio
import logging
from collections.abc import AsyncIterator

from sqlmodel import Session, select

from app.agents.pipeline import AgentEvent, AgentPipeline, AgentState
from app.agents.skills.base import SkillContext
from app.agents.tools.base import Tool, ToolRegistry
from app.agents.tools.builtin import default_tools
from app.core.config import settings
from app.llm.base import ChatMessage, ChatRole, LLMOptions
from app.llm.factory import get_llm_provider
from app.memory.base import ConversationMemory
from app.memory.manager import MemoryManager
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
        """从会话中提取历史消息（最近 N 轮）。

        注意：此方法仅做简单窗口裁剪；记忆压缩由 MemoryManager 负责。
        """
        recent = conv.messages[-_HISTORY_LIMIT:]
        return [
            ChatMessage(role=ChatRole(m.role), content=m.content) for m in recent
        ]

    async def _build_memory(
        self, conv: Conversation
    ) -> ConversationMemory:
        """构建对话记忆：窗口裁剪 + 必要时压缩。

        SKELETON：当前使用 MemoryManager 做内存级管理；
        内核打磨阶段补充数据库持久化与跨会话记忆。
        """
        if not settings.MEMORY_ENABLED:
            return ConversationMemory(
                recent_messages=self._history_messages(conv),
                total_messages=len(conv.messages),
            )
        try:
            all_messages = [
                ChatMessage(role=ChatRole(m.role), content=m.content)
                for m in conv.messages
            ]
            mgr = MemoryManager(self.llm)
            return await mgr.manage(all_messages)
        except Exception:  # noqa: BLE001 — 记忆管理失败不应阻塞对话
            logger.exception("记忆管理失败，回退到简单窗口")
            return ConversationMemory(
                recent_messages=self._history_messages(conv),
                total_messages=len(conv.messages),
            )

    def _build_retriever(self, session: Session, user: User):
        """按配置构建检索钩子（未开启 RAG 时返回 None）。"""
        if not settings.RAG_ENABLED:
            return None
        rag = RAGService(session, user.tenant_id)
        return rag.make_retriever()

    async def _build_tools(self) -> ToolRegistry:
        """构建工具注册表：内置工具 +（启用时）MCP 服务器工具。

        MCP 工具通过进程级单例管理器连接，连接失败仅告警并回退到内置工具，
        不阻断对话。MCP 未启用时完全不触碰 MCP 模块。
        """
        tools: list[Tool] = list(default_tools())
        if settings.MCP_ENABLED:
            try:
                from app.mcp.manager import get_mcp_manager

                mgr = await get_mcp_manager()
                if mgr is not None:
                    mcp_tools = await mgr.collect_tools()
                    tools.extend(mcp_tools)
                    logger.info("已向 Agent 注入 %d 个 MCP 工具", len(mcp_tools))
            except Exception:  # noqa: BLE001 — MCP 异常不应影响基础对话能力
                logger.exception("加载 MCP 工具失败，仅使用内置工具")
        return ToolRegistry(tools)

    def _build_pipeline(self, retriever, tools, skill_ctx: SkillContext | None = None):
        """按配置构造编排器：默认自研管线，可切换 LangGraph Supervisor 子编排。

        当配置 ``AGENT_ORCHESTRATION=langgraph`` 但 ``langgraph`` 未安装时，
        记录告警并以自研管线兜底，避免直接中断服务。

        Args:
            retriever: 检索器钩子（可选）。
            tools: 工具注册表。
            skill_ctx: 技能激活上下文（可选），用于提示词注入。
        """
        if settings.AGENT_ORCHESTRATION != "langgraph":
            pipeline = AgentPipeline(
                self.llm, options=self._options, retriever=retriever, tools=tools
            )
            if skill_ctx and skill_ctx.prompt_injection:
                pipeline.skill_prompt_injection = skill_ctx.prompt_injection
            return pipeline
        try:
            from app.agents.supervisor import SupervisorGraph

            return SupervisorGraph(
                self.llm, options=self._options, retriever=retriever, tools=tools
            )
        except ImportError as exc:
            logger.warning(
                "AGENT_ORCHESTRATION=langgraph 不可用，回退自研管线：%s", exc
            )
            pipeline = AgentPipeline(
                self.llm, options=self._options, retriever=retriever, tools=tools
            )
            if skill_ctx and skill_ctx.prompt_injection:
                pipeline.skill_prompt_injection = skill_ctx.prompt_injection
            return pipeline

    async def chat(
        self, session: Session, user: User, message: str, conversation_id: str | None = None
    ) -> tuple[Conversation, str]:
        """执行一次对话（非流式），返回会话与最终回复。"""
        # 0. 安全治理：输入过滤 + 注入检测
        sec_ctx = self._apply_input_security(message, user)
        if sec_ctx and sec_ctx.blocked and settings.SECURITY_BLOCK_ON_INJECTION:
            raise ValueError("输入被安全策略拒绝")
        conv = self._resolve_conversation(session, user, conversation_id, message)
        # 1. 构建对话记忆（窗口裁剪 + 必要时压缩）
        memory = await self._build_memory(conv)
        state = AgentState(user_input=message, history=memory.recent_messages)
        # 2. 注入记忆上下文到管线
        if memory.memory_context:
            state.context = memory.memory_context
        # 3. 构建检索器（通过 RAGService）/ 工具箱
        retriever = self._build_retriever(session, user)
        tools = await self._build_tools()
        # 4. 技能匹配与激活
        skill_ctx = self._match_skills(message)
        # 5. 按配置构造编排器
        pipeline = self._build_pipeline(retriever, tools, skill_ctx)
        if isinstance(pipeline, AgentPipeline):
            pipeline.max_tool_rounds = settings.AGENT_MAX_TOOL_ROUNDS
        # 6. 执行
        result = await pipeline.run(state)
        # 7. 安全治理：输出过滤
        self._apply_output_security(result.answer, sec_ctx)
        # 8. 持久化用户消息和助手回复
        self._persist_user(session, conv, message)
        self._persist_assistant(session, conv, result.answer, self._model_name())
        session.refresh(conv)
        # 9. 异步反思（不阻塞对话响应）
        self._maybe_reflect(conv, result)
        return conv, result.answer

    async def chat_stream(
        self, session: Session, user: User, message: str, conversation_id: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        """流式执行对话，逐个产出管线事件（阶段 / token / 结束）。"""
        # 0. 安全治理：输入过滤 + 注入检测
        sec_ctx = self._apply_input_security(message, user)
        if sec_ctx and sec_ctx.blocked and settings.SECURITY_BLOCK_ON_INJECTION:
            yield AgentEvent("error", "输入被安全策略拒绝")
            return
        conv = self._resolve_conversation(session, user, conversation_id, message)
        # 构建对话记忆（窗口裁剪 + 必要时压缩）
        memory = await self._build_memory(conv)
        state = AgentState(user_input=message, history=memory.recent_messages)
        # 注入记忆上下文
        if memory.memory_context:
            state.context = memory.memory_context

        # 技能匹配与激活
        skill_ctx = self._match_skills(message)

        pipeline = self._build_pipeline(
            self._build_retriever(session, user), await self._build_tools(), skill_ctx
        )
        if isinstance(pipeline, AgentPipeline):
            pipeline.max_tool_rounds = settings.AGENT_MAX_TOOL_ROUNDS
        collected: list[str] = []
        async for event in pipeline.run_stream(state):
            if event.type == "token":
                collected.append(event.data)
            yield event

        answer = "".join(collected) or state.answer
        # 安全治理：输出过滤
        self._apply_output_security(answer, sec_ctx)
        self._persist_user(session, conv, message)
        self._persist_assistant(session, conv, answer, self._model_name())
        session.refresh(conv)
        # 异步反思（不阻塞流式响应）
        self._maybe_reflect(conv, state)

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

    # ── 技能系统 ──────────────────────────────────

    @staticmethod
    def _match_skills(message: str) -> SkillContext | None:
        """匹配用户输入到技能，返回激活上下文。

        SKELETON：当前仅做关键词匹配；内核打磨阶段补充 LLM 语义匹配。
        """
        if not settings.SKILL_ENABLED:
            return None
        try:
            from app.agents.skills import get_skill_manager

            mgr = get_skill_manager()
            matches = mgr.match(message)
            if not matches:
                return None
            return mgr.activate(matches)
        except Exception:  # noqa: BLE001 — 技能匹配失败不应影响对话
            logger.exception("技能匹配失败")
            return None

    # ── 进化系统（Reflect 反思）───────────────────

    def _maybe_reflect(self, conv: Conversation, state: AgentState) -> None:
        """对话结束后触发异步反思。

        仅在 EVOLUTION_ENABLED + EVOLUTION_REFLECT_ENABLED 时触发；
        反思失败不影响对话响应，仅记录日志。

        SKELETON：当前仅做 LLM 反思并记录日志；
        内核打磨阶段补充：改进点持久化、Skill 自动更新、Action Item 调度。
        """
        if not settings.EVOLUTION_ENABLED or not settings.EVOLUTION_REFLECT_ENABLED:
            return
        try:
            conversation_text = self._build_reflect_conversation_text(conv)
            if not conversation_text.strip():
                return

            async def _do_reflect():
                try:
                    from app.evolution.reflector import Reflector

                    reflector = Reflector(self.llm)
                    result = await reflector.reflect(
                        conversation_text=conversation_text,
                        conversation_id=conv.id,
                        quality_score=state.quality_score,
                        revision_count=state.revision,
                    )
                    if result.error:
                        logger.warning("反思异常: %s", result.error)
                    elif result.has_improvements:
                        logger.info(
                            "反思发现 %d 个改进点（严重: %d）: %s",
                            len(result.improvements),
                            result.critical_count,
                            result.summary,
                        )
                    if result.has_action_items:
                        logger.info(
                            "反思提取 %d 个待办事项: %s",
                            len(result.action_items),
                            [item.description[:50] for item in result.action_items],
                        )
                except Exception:  # noqa: BLE001 — 反思失败不应影响对话
                    logger.exception("异步反思执行失败")

            if settings.EVOLUTION_REFLECT_ASYNC:
                # 异步执行：fire-and-forget，不阻塞对话响应
                asyncio.create_task(_do_reflect())
            else:
                # 同步执行（调试用）
                asyncio.get_event_loop().run_until_complete(_do_reflect())

        except Exception:  # noqa: BLE001
            logger.exception("触发反思失败")

    @staticmethod
    def _build_reflect_conversation_text(conv: Conversation) -> str:
        """将对话消息序列化为反思器可读的文本。"""
        role_map = {"user": "用户", "assistant": "助手", "system": "系统"}
        lines: list[str] = []
        for m in sorted(conv.messages, key=lambda x: x.created_at):
            role_label = role_map.get(m.role, m.role)
            lines.append(f"{role_label}：{m.content}")
        return "\n".join(lines)

    # ── 安全治理 ──────────────────────────────────

    @staticmethod
    def _apply_input_security(message: str, user: User) -> "SecurityContext | None":
        """对用户输入执行安全过滤。

        包含：输入过滤（PII 检测 + 敏感词）+ Prompt 注入检测。
        失败时仅记录日志，不阻断对话。

        SKELETON：当前仅做模式匹配告警；
        内核打磨阶段补充：可配置阻断策略、LLM 语义审查。
        """
        if not settings.SECURITY_ENABLED:
            return None
        try:
            from app.security import (
                InputFilter,
                PromptInjectionDetector,
                SecurityContext,
            )

            ctx = SecurityContext()

            # 输入过滤
            if settings.SECURITY_INPUT_FILTER:
                input_filter = InputFilter()
                result = input_filter.filter(message, ctx)
                if result.flagged:
                    logger.warning(
                        "输入安全告警: user=%s, reasons=%s",
                        user.id,
                        result.reasons,
                    )

            # 注入检测
            if settings.SECURITY_INJECTION_DETECTION:
                detector = PromptInjectionDetector(
                    threshold=settings.SECURITY_INJECTION_THRESHOLD
                )
                inj_result = detector.detect(message, ctx)
                if inj_result.detected:
                    logger.warning(
                        "注入检测告警: user=%s, confidence=%.2f, matches=%s",
                        user.id,
                        inj_result.confidence,
                        inj_result.matches,
                    )

            return ctx

        except Exception:  # noqa: BLE001 — 安全过滤失败不应影响对话
            logger.exception("输入安全过滤失败")
            return None

    @staticmethod
    def _apply_output_security(answer: str, ctx: "SecurityContext | None") -> None:
        """对模型输出执行安全过滤。

        包含：输出过滤（有害内容检测）。
        失败时仅记录日志，不阻断对话。
        """
        if not settings.SECURITY_ENABLED or not settings.SECURITY_OUTPUT_FILTER:
            return
        try:
            from app.security import OutputFilter

            output_filter = OutputFilter()
            result = output_filter.filter(answer, ctx)
            if result.flagged:
                logger.warning(
                    "输出安全告警: reasons=%s",
                    result.reasons,
                )
        except Exception:  # noqa: BLE001
            logger.exception("输出安全过滤失败")
