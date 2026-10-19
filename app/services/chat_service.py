"""会话服务：编排 Agent 管线并持久化对话。

职责：
- 维护会话生命周期（创建 / 列表 / 详情 / 删除）；
- 调用 Agent 五阶段管线生成回复；
- 将用户消息与助手回复落库，供后续上下文回溯；
- 基于多租户 RBAC 做归属校验（普通用户仅可见自己的会话，系统管理员可见同租户全部）。
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.engine import Connection, Engine
from sqlmodel import Session, col, select

from app.agents.fast_path import iter_fast_path
from app.agents.pipeline import AgentEvent, AgentPipeline, AgentState, protocol_event, safe_error_metadata
from app.agents.route import ChatRoute, RouteKind, route_message
from app.agents.skills.base import SkillBindingRejected, SkillContext, SkillRuntime
from app.agents.tools.base import Tool, ToolRegistry
from app.agents.tools.builtin import default_tools
from app.agents.tools.executor import ToolExecutor
from app.core.config import settings
from app.llm.base import ChatMessage, ChatRole, LLMOptions
from app.llm.budget import BUDGET_EXCEEDED_MESSAGE, ContextBudgetError
from app.llm.factory import get_llm_provider
from app.llm.vision import validate_image_inputs
from app.memory.base import ConversationMemory
from app.memory.manager import MemoryManager
from app.models.conversation import Conversation, Message
from app.models.user import Tenant, User
from app.rag.access import read_scope_for
from app.rag.service import RAGService, sources_from_hits
from app.services.agent_execution_guard import (
    ResumeRejectedError,
    claim_execution,
    guarded_chat,
    guarded_stream,
)
from app.services.cache_scope import cached_chat_scope, cached_stream_scope
from app.services.chat_attachments import (
    AttachmentInput,
    AttachmentValidationError,
    PreparedAttachment,
    image_urls,
    model_user_text,
    prepare_attachments,
    restore_attachments,
    serialize_attachments,
)
from app.services.chat_retrieval import ChatRetriever
from app.services.chat_skills import (
    make_skill_runtime,
    precheck_resume_skill_binding,
    restore_skill_context,
    validate_resume_skill_binding,
)
from app.services.combined_tools import combined_events
from app.services.quota import accept_user_message

if TYPE_CHECKING:
    from app.debug.trace import AgentTrace
    from app.security.types import SecurityContext, SecurityRejectedError


def _skill_name_list(ctx: SkillContext | None) -> list[str]:
    """把激活上下文里的技能名收成列表。未命中时为空。"""
    if ctx is None or not ctx.skill_name:
        return []
    return [part.strip() for part in ctx.skill_name.split(",") if part.strip()]


def _skill_names_payload(names: list[str] | None) -> str | None:
    """未选用时不写 JSON，读路径把空值当成空列表。"""
    if not names:
        return None
    return json.dumps(names, ensure_ascii=False)


logger = logging.getLogger(__name__)

# 送入管线的历史轮次上限，避免上下文过长。
_HISTORY_LIMIT = 20
HISTORY_REDACTED = "（历史内容已省略）"
# 预算超限时给用户的固定提示。异常原文（计数数字、模型名、原因码）不外传。
BUDGET_EXCEEDED_REPLY = BUDGET_EXCEEDED_MESSAGE


class ModelUnavailableError(RuntimeError):
    """Generation failed after the failed assistant response was durably saved."""

    def __init__(self) -> None:
        super().__init__(str(safe_error_metadata({"code": "model_unavailable"})["message"]))


class RegenerationFailedError(RuntimeError):
    """重新生成未产出完整回复，保留旧助手消息。"""


def sanitize_history_text(text: str) -> str:
    """脱敏历史正文。不做限流或注入检测，失败时不回退原文。"""
    try:
        from app.security.input_filter import InputFilter
        from app.security.log_sanitizer import LogSanitizer

        filtered = InputFilter().filter(text or "").sanitized_text
        return LogSanitizer().sanitize(filtered)
    except Exception:  # noqa: BLE001 — 脱敏失败必须隐藏原文
        return HISTORY_REDACTED


class ChatService:
    """对话编排与持久化服务。"""

    def __init__(self, llm_provider=None) -> None:
        # 允许注入自定义 LLM（测试 / 运行时覆盖）；为空时按需取全局提供商。
        self._llm = llm_provider

    @property
    def llm(self):
        return self._llm or get_llm_provider()

    def _intent_llm(self):
        """构造时传入的客户端盖住意图链，避免评测脚本再去读环境变量。"""
        if self._llm is not None:
            return self._llm
        return get_llm_provider("intent")

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
        stmt = stmt.order_by(col(Conversation.updated_at).desc()).limit(limit).offset(offset)
        return list(session.exec(stmt).all())

    def get_conversation(self, session: Session, user: User, conversation_id: str) -> Conversation | None:
        """按 ID 获取会话，无权限时返回 None。"""
        conv = session.get(Conversation, conversation_id)
        if conv is None or not self._can_access(conv, user):
            return None
        return conv

    def delete_conversation(self, session: Session, user: User, conversation_id: str) -> bool:
        """删除会话（级联删除消息）。无权限或不存在返回 False。"""
        conv = self.get_conversation(session, user, conversation_id)
        if conv is None:
            return False
        session.delete(conv)
        session.commit()
        return True

    def rename_conversation(
        self, session: Session, user: User, conversation_id: str, title: str
    ) -> Conversation | None:
        """修改会话标题。无权或不存在时返回 None。"""
        conv = self.get_conversation(session, user, conversation_id)
        if conv is None:
            return None
        conv.title = title.strip()
        conv.updated_at = datetime.now(UTC)
        session.add(conv)
        session.commit()
        session.refresh(conv)
        return conv

    def _can_access(self, conv: Conversation, user: User) -> bool:
        if user.role_enum.value == "system_admin":
            return conv.tenant_id == user.tenant_id
        return conv.user_id == user.id and conv.tenant_id == user.tenant_id

    # ── 对话执行 ──────────────────────────────────
    def _assert_regeneration_authority(self, session: Session, user: User) -> None:
        from app.core.security import check_permission

        if not settings.AUTH_ENABLED and user.id == user.tenant_id == "__auth_disabled__":
            return
        expected = session.info.get("regeneration_identity", (user.id, user.tenant_id, user.role, user.token_version))
        with Session(session.get_bind()) as fresh:
            current = fresh.get(User, user.id)
            tenant = fresh.get(Tenant, user.tenant_id)
            if (current is None or not current.is_active or current.must_change_password
                    or (current.id, current.tenant_id, current.role, current.token_version) != expected
                    or tenant is None or not tenant.is_active
                    or not check_permission(current.role, "conversations", "write")):
                raise ResumeRejectedError("身份或租户状态已变更，请重新登录后再试", 403)
            conversation_id = session.info.get("regeneration_conversation")
            if conversation_id:
                conv = fresh.get(Conversation, conversation_id)
                if conv is None or not self._can_access(conv, current):
                    raise ResumeRejectedError("会话不存在或无权访问", 404)

    async def regenerate(
        self, session: Session, user: User, conversation_id: str, message_id: str,
    ) -> tuple[Conversation, str]:
        """保留原问答，将尾部助手对应的原问题作为独立新轮次执行。"""
        if len(conversation_id) > 128 or len(message_id) > 128:
            raise ResumeRejectedError("会话或消息不存在或无权访问", 404)
        self._assert_regeneration_authority(session, user)
        conv = self.get_conversation(session, user, conversation_id)
        if conv is None:
            raise ResumeRejectedError("会话或消息不存在或无权访问", 404)
        claims: list[tuple[int, str, str, str]] = []
        claim_execution(session, user.tenant_id, conversation_id, "__conversation_write__", claims)
        try:
            rows = list(session.exec(select(Message).where(col(Message.conversation_id) == conversation_id)
                                     .order_by(col(Message.created_at), col(Message.id))).all())
            target = next((row for row in rows if row.id == message_id), None)
            if target is None:
                raise ResumeRejectedError("会话或消息不存在或无权访问", 404)
            if target.role != "assistant" or not rows or rows[-1].id != message_id:
                raise ResumeRejectedError("只能重新生成当前会话最后一条助手消息，请刷新会话", 409)
            original = next((row for row in reversed(rows[:-1]) if row.role == "user"), None)
            if original is None:
                raise ResumeRejectedError("没有可重新生成的原始问题", 409)
            prepared = self.prepare_chat_attachments(
                session, user, original.content, None, None, None,
                _restored=restore_attachments(original.attachments),
            )
            session.info["regeneration_conversation"] = conversation_id
            session.info["regeneration_identity"] = (user.id, user.tenant_id, user.role, user.token_version)
            return await self.chat(session, user, original.content, conversation_id,
                                   attachments_prepared=prepared, _fresh_response=True)
        finally:
            from app.services.agent_execution_guard import release_executions

            session.info.pop("regeneration_conversation", None)
            session.info.pop("regeneration_identity", None)
            release_executions(claims)

    def _history_messages(self, conv: Conversation) -> list[ChatMessage]:
        """从会话中提取历史消息（最近 N 轮）。

        注意：此方法仅做简单窗口裁剪；记忆压缩由 MemoryManager 负责。
        """
        recent = [m for m in conv.messages if m.content][-_HISTORY_LIMIT:]
        return [ChatMessage(role=ChatRole(m.role), content=sanitize_history_text(m.content)) for m in recent]

    async def _build_memory(self, conv: Conversation) -> ConversationMemory:
        """构建对话记忆：窗口裁剪 + 必要时压缩。

        SKELETON：当前使用 MemoryManager 做内存级管理；
        可按需扩展数据库持久化与跨会话记忆。
        """
        if not settings.MEMORY_ENABLED:
            return ConversationMemory(
                recent_messages=self._history_messages(conv),
                total_messages=len(conv.messages),
            )
        try:
            all_messages = [
                ChatMessage(role=ChatRole(m.role), content=sanitize_history_text(m.content))
                for m in conv.messages
                if m.content
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
        """同步固定读域/主体，依赖构造延至本请求真正检索时。"""
        if not settings.RAG_ENABLED:
            return None
        tenant_id = user.tenant_id
        read_scope = read_scope_for(user)
        if read_scope is not None:
            read_scope.assert_subject_tenant(tenant_id)
        # 不持有可被刷新/修改的 ORM 身份快照，也不复制密码或关系对象。
        reader = User(
            id=user.id, tenant_id=tenant_id, username=user.username, hashed_password="",
            role=user.role, token_version=user.token_version, is_active=user.is_active,
            must_change_password=user.must_change_password,
        )

        def create():
            rag = RAGService(session, tenant_id, reader=reader)
            # scope 在同步边界已核验；不能因构造延迟重新解释运行中变动的读域。
            rag._read_scope = read_scope
            return rag.make_retriever()

        return ChatRetriever(create)

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

    def _fast_route(self, message: str) -> ChatRoute | None:
        """langgraph 下，简单、工具、知识库走短路径。多轮和默认编排返回 None。"""
        if settings.AGENT_ORCHESTRATION != "langgraph":
            return None
        route = route_message(message)
        if route.kind is RouteKind.MULTI:
            return None
        return route

    async def _iter_fast(
        self,
        state: AgentState,
        route: ChatRoute,
        retriever,
        tools,
        skill_ctx: SkillContext | None,
        skill_runtime: SkillRuntime | None = None,
    ) -> AsyncIterator[AgentEvent]:
        skill_prompt = skill_ctx.prompt_injection if skill_ctx and skill_ctx.prompt_injection else ""
        logger.info("对话走短路径：%s", route.kind.value)
        async for event in iter_fast_path(
            self.llm,
            self._options,
            state,
            route,
            retriever,
            tools,
            skill_prompt,
            skill_runtime=skill_runtime,
        ):
            yield event

    def _build_pipeline(
        self, retriever, tools, skill_ctx: SkillContext | None = None, trace=None,
        skill_runtime: SkillRuntime | None = None,
    ):
        """按配置构造编排器：默认自研管线，可切换 LangGraph Supervisor 子编排。

        当配置 ``AGENT_ORCHESTRATION=langgraph`` 但 ``langgraph`` 未安装时，
        记录告警并以自研管线兜底，避免直接中断服务。

        Args:
            retriever: 检索器钩子（可选）。
            tools: 工具注册表。
            skill_ctx: 技能激活上下文（可选），用于提示词注入。
            trace: 调试追踪对象（可选）。
        """
        if settings.AGENT_ORCHESTRATION != "langgraph":
            pipeline = AgentPipeline(
                self.llm,
                options=self._options,
                retriever=retriever,
                tools=tools,
                trace=trace,
                intent_llm=self._intent_llm(),
            )
            if skill_ctx and skill_ctx.prompt_injection:
                pipeline.skill_prompt_injection = skill_ctx.prompt_injection
            pipeline.skill_runtime = skill_runtime
            return pipeline
        try:
            from app.agents.supervisor import SupervisorGraph

            return SupervisorGraph(
                self.llm, options=self._options, retriever=retriever, tools=tools, skill_runtime=skill_runtime,
            )
        except ImportError as exc:
            logger.warning("AGENT_ORCHESTRATION=langgraph 不可用，回退自研管线：%s", exc)
            pipeline = AgentPipeline(
                self.llm,
                options=self._options,
                retriever=retriever,
                tools=tools,
                intent_llm=self._intent_llm(),
            )
            if skill_ctx and skill_ctx.prompt_injection:
                pipeline.skill_prompt_injection = skill_ctx.prompt_injection
            pipeline.skill_runtime = skill_runtime
            return pipeline

    def _wire_runtime(
        self, session, user, conv, tools, pipeline: AgentPipeline, message_id: str
    ) -> None:
        """PAGT-006：把独立 Executor 与（可选）状态 store 注入自研管线。

        未注入时，行动循环仍会用同一注册表构造 ToolExecutor。状态持久化受
        ``AGENT_STATE_PERSIST_ENABLED`` 开关控制。工具短路径在自己的管线上注入；
        Supervisor 仍不注入。
        ``message_id`` 绑定真实用户消息行 id，使每条用户消息独占 checkpoint 行
        （跨消息隔离、唯一约束真实生效）。
        """
        if tools is not None:
            pipeline.executor = ToolExecutor(tools)
        if not settings.AGENT_STATE_PERSIST_ENABLED:
            return
        from app.services.agent_state_service import AgentStateService

        pipeline.state_store = AgentStateService(
            session, tenant_id=user.tenant_id, user_id=user.id
        )
        pipeline.state_binding = {
            "conversation_id": conv.id,
            "message_id": message_id,
            "tenant_id": user.tenant_id,
            "user_id": user.id,
        }
        if "agent_resume_snapshot" in session.info:
            pipeline.state_binding["resume_snapshot"] = session.info["agent_resume_snapshot"]

    @staticmethod
    def _wire_supervisor_tools(session: Session, user: User, pipeline) -> None:
        from app.agents.supervisor import SupervisorGraph
        from app.services.supervisor_tools import wire_supervisor_tools

        if isinstance(pipeline, SupervisorGraph):
            wire_supervisor_tools(session, user, pipeline)

    def _prepare_user_message(
        self, session: Session, user: User, conversation_id: str | None, message: str,
        resume_message_id: str | None, claims: list[tuple[int, str, str, str]],
        attachments: str | None = None,
    ) -> tuple[Conversation, str]:
        """Authorize recovery before reusing a message; new requests use the quota path."""
        session.info.pop("agent_resume_snapshot", None)
        if conversation_id and session.info.get("regeneration_conversation") != conversation_id:
            claim_execution(session, user.tenant_id, conversation_id, "__conversation_write__", claims)
        conv: Conversation | None
        if resume_message_id is None:
            if attachments is None:
                conv, message_id = self._accept_user_message(session, user, conversation_id, message)
            else:
                conv, message_id = self._accept_user_message(session, user, conversation_id, message,
                                                             attachments=attachments)
            claim_execution(session, user.tenant_id, conv.id, message_id, claims)
        else:
            if not conversation_id:
                raise ResumeRejectedError("续跑需要 conversation_id", 400)
            if not settings.AGENT_STATE_PERSIST_ENABLED or settings.AGENT_ORCHESTRATION != "self":
                raise ResumeRejectedError("当前编排不支持续跑")
            conv = session.get(Conversation, conversation_id)
            original = session.get(Message, resume_message_id)
            if (conv is None or conv.tenant_id != user.tenant_id or conv.user_id != user.id
                    or original is None or original.conversation_id != conv.id or original.role != "user"):
                raise ResumeRejectedError("会话或消息不存在或无权访问", 404)
            if original.content != message:
                raise ResumeRejectedError("续跑消息内容与原消息不一致", 400)
            claim_execution(session, user.tenant_id, conv.id, original.id, claims)
            from app.services.agent_state_service import AgentStateService

            try:
                precheck_resume_skill_binding(session, user, conv.id, original.id)
            except SkillBindingRejected as exc:
                raise ResumeRejectedError(str(exc)) from None
            snapshot = AgentStateService(session, tenant_id=user.tenant_id, user_id=user.id).load_active(
                conv.id, original.id)
            if snapshot is None:
                raise ResumeRejectedError("没有可恢复的活动执行")
            try:
                validate_resume_skill_binding(session, user, snapshot)
            except SkillBindingRejected as exc:
                raise ResumeRejectedError(str(exc)) from None
            session.info["agent_resume_snapshot"] = snapshot
            message_id = original.id
        session.info["agent_message_id"] = message_id
        return conv, message_id

    def prepare_chat_attachments(
        self, session: Session, user: User, message: str, conversation_id: str | None,
        resume_message_id: str | None, attachments: list[AttachmentInput] | None,
        *, _restored: list[PreparedAttachment] | None = None,
    ) -> list[PreparedAttachment]:
        """在任何消息写入或模型调用前完成附件批校验及视觉能力预检。"""
        if _restored is not None:
            prepared = _restored
        elif resume_message_id is not None:
            if attachments:
                raise AttachmentValidationError("续跑必须使用原消息附件，不能提交新附件替换")
            conv = session.get(Conversation, conversation_id) if conversation_id else None
            original = session.get(Message, resume_message_id)
            if (conv is None or conv.tenant_id != user.tenant_id or conv.user_id != user.id
                    or original is None or original.conversation_id != conv.id or original.role != "user"):
                raise ResumeRejectedError("会话或消息不存在或无权访问", 404)
            if original.content != message:
                raise ResumeRejectedError("续跑消息内容与原消息不一致", 400)
            prepared = restore_attachments(original.attachments)
        else:
            prepared = prepare_attachments(attachments)
        safe_attachments = []
        for item in prepared:
            if item.text is not None:
                ctx, text = self._apply_input_security(item.text, user, check_rate_limit=False, fail_closed=True)
                if ctx is not None and ctx.blocked:
                    raise self._rejection_error(ctx)
                name_ctx, _ = self._apply_input_security(item.name, user, check_rate_limit=False, fail_closed=True)
                if name_ctx is not None and name_ctx.blocked:
                    raise self._rejection_error(name_ctx)
                item = replace(item, text=text)
            safe_attachments.append(item)
        prepared = safe_attachments
        if not message.strip() and not prepared:
            raise AttachmentValidationError("message 不能为空，或请选择有效附件")
        try:
            urls = image_urls(prepared)
            if urls:
                validate_image_inputs(self.llm, urls, model_user_text(message, prepared))
        except ContextBudgetError as exc:
            if exc.reason == "vision_capability_unverified":
                raise AttachmentValidationError("当前模型未核对图片能力，请选择已验证的视觉模型") from None
            raise AttachmentValidationError("附件超出模型输入预算，请减少内容后重试") from None
        return prepared

    @cached_chat_scope
    @guarded_chat
    async def chat(
        self, session: Session, user: User, message: str, conversation_id: str | None = None,
        *, resume_message_id: str | None = None,
        attachments: list[AttachmentInput] | None = None,
        attachments_prepared: list[PreparedAttachment] | None = None,
        _fresh_response: bool = False,
        _execution_claims: list[tuple[int, str, str, str]] | None = None,
    ) -> tuple[Conversation, str]:
        """执行一次对话（非流式），返回会话与最终回复。"""
        prepared = (attachments_prepared if attachments_prepared is not None and resume_message_id is None
                    else self.prepare_chat_attachments(session, user, message, conversation_id,
                                                       resume_message_id, attachments))
        attachment_payload = serialize_attachments(prepared)
        # 0. 安全治理：限流 + 输入过滤 + 注入检测，得到送模型的脱敏文本
        sec_ctx, safe_message = self._apply_input_security(message, user)
        if sec_ctx and sec_ctx.blocked:
            raise self._rejection_error(sec_ctx)
        conv, user_message_id = self._prepare_user_message(
            session, user, conversation_id, message, resume_message_id,
            _execution_claims if _execution_claims is not None else [], attachments=attachment_payload)
        # 1. 构建对话记忆（窗口裁剪 + 必要时压缩）
        memory = await self._build_memory(conv)
        state = AgentState(user_input=model_user_text(safe_message, prepared), user_images=image_urls(prepared),
                           history=memory.recent_messages, retrieval_query=safe_message)
        # 2. 注入记忆上下文到管线
        if memory.memory_context:
            state.context = memory.memory_context
        retriever = self._build_retriever(session, user)
        tools = await self._build_tools()
        # 4. 技能匹配与激活
        if resume_message_id is not None:
            try:
                skill_ctx = restore_skill_context(session, user, session.info["agent_resume_snapshot"])
            except SkillBindingRejected as exc:
                raise ResumeRejectedError(str(exc)) from None
        else:
            skill_ctx = await self._match_skills_async(session, user, safe_message)
        try:
            skill_runtime = make_skill_runtime(session, user, skill_ctx, tools)
        except SkillBindingRejected as exc:
            raise ResumeRejectedError(str(exc)) from None
        # 5. 调试追踪
        trace = self._create_trace()
        # 6. 短路径或按配置构造编排器
        combination = route_message(safe_message)
        combined = resume_message_id is None and combination.kind is RouteKind.COMBINED
        fast = None if combined or resume_message_id or prepared else self._fast_route(safe_message)
        try:
            if combined:
                combined_iterator = combined_events(
                    session, user, self.llm, self._options, state, combination, retriever, tools, skill_runtime,
                    trace=trace,
                )
                try:
                    async for event in combined_iterator:
                        if event.type == "error":
                            state.error_code = str(safe_error_metadata(event.metadata)["code"])
                finally:
                    await combined_iterator.aclose()
                result = state
            elif fast is not None:
                async for _event in self._iter_fast(state, fast, retriever, tools, skill_ctx, skill_runtime):
                    if _event.type == "error":
                        state.error_code = str(safe_error_metadata(_event.metadata)["code"])
                result = state
            else:
                pipeline = self._build_pipeline(retriever, tools, skill_ctx, trace=trace, skill_runtime=skill_runtime)
                if resume_message_id is not None and not isinstance(pipeline, AgentPipeline):
                    raise ResumeRejectedError("当前编排不支持恢复此执行，请重新发起请求")
                if isinstance(pipeline, AgentPipeline):
                    pipeline.max_tool_rounds = settings.AGENT_MAX_TOOL_ROUNDS
                    self._wire_runtime(session, user, conv, tools, pipeline, user_message_id)
                else:
                    self._wire_supervisor_tools(session, user, pipeline)
                # 7. 执行
                result = await pipeline.run(state)
        except ContextBudgetError:
            if _fresh_response:
                raise RegenerationFailedError() from None
            # 预算超限不是故障：给固定提示并把这次回复照常落库，不抛给路由。
            result = state
            result.answer = BUDGET_EXCEEDED_REPLY
        except SkillBindingRejected as exc:
            raise ResumeRejectedError(str(exc)) from None
        # 8. 收集追踪
        self._collect_trace(trace)
        # 9. 安全治理：输出过滤
        self._apply_output_security(result.answer, sec_ctx)
        if _fresh_response:
            self._assert_regeneration_authority(session, user)
            if result.error or result.error_code or not result.answer.strip():
                raise RegenerationFailedError()
        # 10. 回复在生成结束后写入。用户消息已在生成前写入。
        sources = self._reply_sources(session, retriever)
        self._persist_assistant(
            session,
            conv,
            result.answer,
            self._model_name(),
            sources,
            status="failed" if result.error_code == "model_unavailable" or (combined and result.error) else "complete",
            code_results=result.code_results,
            skill_names=_skill_name_list(skill_ctx),
        )
        session.refresh(conv)
        session.expire(conv, ["messages"])
        if result.error_code == "model_unavailable":
            raise ModelUnavailableError()
        # 9. 异步反思（不阻塞对话响应）
        self._maybe_reflect(conv, result)
        return conv, result.answer

    @cached_stream_scope
    @guarded_stream
    async def chat_stream(
        self, session: Session, user: User, message: str, conversation_id: str | None = None,
        *, resume_message_id: str | None = None,
        attachments: list[AttachmentInput] | None = None,
        attachments_prepared: list[PreparedAttachment] | None = None,
        _execution_claims: list[tuple[int, str, str, str]] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """流式执行对话，逐个产出管线事件（阶段 / token / 结束）。"""
        execution_bind = session.get_bind()
        prepared = (attachments_prepared if attachments_prepared is not None and resume_message_id is None
                    else self.prepare_chat_attachments(session, user, message, conversation_id,
                                                       resume_message_id, attachments))
        attachment_payload = serialize_attachments(prepared)
        # 0. 安全治理：限流 + 输入过滤 + 注入检测，得到送模型的脱敏文本
        sec_ctx, safe_message = self._apply_input_security(message, user)
        if sec_ctx and sec_ctx.blocked:
            if sec_ctx.rate_limited:
                yield AgentEvent(
                    "rate_limit",
                    json.dumps(
                        {"retry_after_seconds": sec_ctx.retry_after_seconds},
                        ensure_ascii=False,
                    ),
                )
            yield AgentEvent("error", str(self._rejection_error(sec_ctx)), metadata=safe_error_metadata({
                "code": "rate_limited" if sec_ctx.rate_limited else "request_rejected",
            }))
            return
        conv, user_message_id = self._prepare_user_message(
            session, user, conversation_id, message, resume_message_id,
            _execution_claims if _execution_claims is not None else [], attachments=attachment_payload)
        # 构建对话记忆（窗口裁剪 + 必要时压缩）
        memory = await self._build_memory(conv)
        state = AgentState(user_input=model_user_text(safe_message, prepared), user_images=image_urls(prepared),
                           history=memory.recent_messages, retrieval_query=safe_message)
        # 注入记忆上下文
        if memory.memory_context:
            state.context = memory.memory_context

        # 技能匹配与激活
        if resume_message_id is not None:
            try:
                skill_ctx = restore_skill_context(session, user, session.info["agent_resume_snapshot"])
            except SkillBindingRejected as exc:
                raise ResumeRejectedError(str(exc)) from None
        else:
            skill_ctx = await self._match_skills_async(session, user, safe_message)

        retriever = self._build_retriever(session, user)
        tools = await self._build_tools()
        try:
            skill_runtime = make_skill_runtime(session, user, skill_ctx, tools)
        except SkillBindingRejected as exc:
            raise ResumeRejectedError(str(exc)) from None
        combination = route_message(safe_message)
        combined = resume_message_id is None and combination.kind is RouteKind.COMBINED
        combined_trace = self._create_trace() if combined else None
        fast = None if combined or resume_message_id or prepared else self._fast_route(safe_message)
        pipeline = (None if fast is not None or combined else
                    self._build_pipeline(retriever, tools, skill_ctx, skill_runtime=skill_runtime))
        if resume_message_id is not None and not isinstance(pipeline, AgentPipeline):
            raise ResumeRejectedError("当前编排不支持恢复此执行，请重新发起请求")
        if isinstance(pipeline, AgentPipeline):
            pipeline.max_tool_rounds = settings.AGENT_MAX_TOOL_ROUNDS
            self._wire_runtime(session, user, conv, tools, pipeline, user_message_id)
        elif pipeline is not None:
            self._wire_supervisor_tools(session, user, pipeline)
        # 编号单独留下：停止时请求会话可能已经结束，不能再靠 conv 对象。
        conversation_id = conv.id
        yield AgentEvent("conversation", conversation_id)
        yield AgentEvent("message", user_message_id)

        collected: list[str] = []
        sources: list[dict] = []
        assistant_saved = False
        stopped = False
        events: AsyncIterator[AgentEvent] | None = None
        try:
            if combined:
                events = combined_events(
                    session, user, self.llm, self._options, state, combination, retriever, tools, skill_runtime,
                    trace=combined_trace,
                )
            elif fast is not None:
                events = self._iter_fast(state, fast, retriever, tools, skill_ctx, skill_runtime)
            else:
                assert pipeline is not None
                events = pipeline.run_stream(state)
            assert events is not None
            async for event in events:
                if event.type == "error":
                    state.error_code = str(safe_error_metadata(event.metadata)["code"])
                if event.type == "node.finished" and json.loads(event.data).get("node") == "retrieval":
                    sources = self._reply_sources(session, retriever)
                    yield protocol_event("citations", sources=sources)
                if event.type == "token":
                    collected.append(event.data)
                if event.type == "done":
                    sources = self._reply_sources(session, retriever)
                    answer = "".join(collected) or state.answer
                    self._apply_output_security(answer, sec_ctx)
                    self._persist_assistant(
                        session,
                        conv,
                        answer,
                        self._model_name(),
                        sources,
                        status="failed" if state.error else "complete",
                        code_results=state.code_results,
                        skill_names=_skill_name_list(skill_ctx),
                    )
                    assistant_saved = True
                    current = ChatService._conversation_after_reply(session, conv, conversation_id)
                    if current is not None and state.error_code != "model_unavailable":
                        self._maybe_reflect(current, state)
                    yield AgentEvent("sources", json.dumps(sources, ensure_ascii=False))
                    yield protocol_event("citations", sources=sources)
                    logger.info("rag_sources_returned source_count=%s", len(sources))
                yield event
        except (GeneratorExit, asyncio.CancelledError):
            stopped = True
            raise
        except SkillBindingRejected as exc:
            raise ResumeRejectedError(str(exc)) from None
        except ContextBudgetError:
            # 预算超限时给固定提示，异常原文不外传；补一个 done 让流有终态。
            state.answer = BUDGET_EXCEEDED_REPLY
            state.error = "context_budget_exceeded"
            self._apply_output_security(state.answer, sec_ctx)
            self._persist_assistant(session, conv, state.answer, self._model_name(), sources,
                                    status="failed", code_results=state.code_results,
                                    skill_names=_skill_name_list(skill_ctx))
            assistant_saved = True
            yield AgentEvent("error", BUDGET_EXCEEDED_REPLY,
                             metadata=safe_error_metadata({"code": "context_budget_exceeded"}))
            yield AgentEvent("done", BUDGET_EXCEEDED_REPLY)
        finally:
            self._collect_trace(combined_trace)
            if events is not None:
                close_events = getattr(events, "aclose", None)
                if close_events is not None:
                    await close_events()
            if stopped and not assistant_saved:
                # 只用已经交给页面的 token。不用 state.answer，避免把未展示的后文写进去。
                # 另开会话写入：客户端断开时，请求上的会话可能已经解除绑定。
                answer = "".join(collected)
                self._apply_output_security(answer, sec_ctx)
                if not sources:
                    try:
                        sources = self._reply_sources(session, retriever)
                    except Exception as exc:  # noqa: BLE001 — 停止时请求会话可能已关闭
                        logger.error("rag_sources_read_failed error_type=%s", type(exc).__name__)
                        sources = []
                assistant_id = self._persist_assistant_standalone(
                    conversation_id,
                    answer,
                    self._model_name(),
                    sources,
                    status="stopped",
                    code_results=state.code_results,
                    skill_names=_skill_name_list(skill_ctx),
                    bind=execution_bind,
                )
                session.info["agent_assistant_message_id"] = assistant_id

    # ── 内部辅助 ──────────────────────────────────
    def _accept_user_message(
        self, session: Session, user: User, conversation_id: str | None, message: str,
        *, attachments: str | None = None,
    ) -> tuple[Conversation, str]:
        return accept_user_message(
            session,
            tenant_id=user.tenant_id,
            user_id=user.id,
            role=user.role,
            conversation_id=conversation_id,
            message=message,
            **({"attachments": attachments} if attachments is not None else {}),
        )

    def _persist_assistant(
        self,
        session: Session,
        conv: Conversation,
        content: str,
        model: str | None,
        sources: list[dict] | None = None,
        status: str = "complete",
        code_results: list[dict] | None = None,
        skill_names: list[str] | None = None,
    ) -> None:
        payload = json.dumps(sources, ensure_ascii=False) if sources else None
        code_payload = json.dumps(code_results, ensure_ascii=False) if code_results else None
        assistant = Message(
                conversation_id=conv.id,
                role="assistant",
                content=content,
                model=model,
                sources=payload,
                status=status,
                code_results=code_payload,
                skill_names=_skill_names_payload(skill_names),
            )
        session.add(assistant)
        session.commit()
        session.info["agent_assistant_message_id"] = assistant.id

    def _persist_assistant_standalone(
        self,
        conversation_id: str,
        content: str,
        model: str | None,
        sources: list[dict] | None,
        status: str,
        code_results: list[dict] | None = None,
        skill_names: list[str] | None = None,
        bind: Engine | Connection | None = None,
    ) -> str:
        """在独立会话里写入助手消息。用于生成器被关闭、原请求会话已不可用时。"""
        from app.core.database import engine

        payload = json.dumps(sources, ensure_ascii=False) if sources else None
        code_payload = json.dumps(code_results, ensure_ascii=False) if code_results else None
        with Session(bind if bind is not None else engine) as extra:
            assistant = Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=content,
                    model=model,
                    sources=payload,
                    status=status,
                    code_results=code_payload,
                    skill_names=_skill_names_payload(skill_names),
                )
            extra.add(assistant)
            extra.commit()
            return assistant.id

    @staticmethod
    def _reply_sources(session: Session, retriever) -> list[dict]:
        """对外来源只取**实际进入模型 payload** 的命中。

        RAG-029：``last_hits`` 是阈值过滤后的全部命中，预算挤掉的块并不在模型
        看到的文本里。用它会让展示给用户的来源多于实际证据，所以**一律不回落**
        ``last_hits``：三条真实编排路径（自研管线 / Fast RAG 短路径 / Supervisor）
        组装 payload 后都必须回写 ``last_selected``，拿不到就声明为空来源
        （欠声明是安全的，夸大来源才是本卡要修的缺陷）。
        """
        if retriever is None:
            return []
        selected = getattr(retriever, "last_selected", None)
        if not isinstance(selected, list):
            return []
        return sources_from_hits(session, selected)

    def _model_name(self) -> str | None:
        return getattr(self.llm, "model", None)

    # ── 技能系统 ──────────────────────────────────

    def _match_skills(self, session: Session, user: User, message: str) -> SkillContext | None:
        """在内置、系统全局和该用户自己的私有技能里最多选用一条。"""
        if not settings.SKILL_ENABLED:
            return None
        try:
            from app.agents.skills.manager import SkillManager
            from app.services.skill_service import fence_untrusted_skill, manifests_for_chat

            mgr = SkillManager()
            for manifest in manifests_for_chat(session, user):
                mgr.register(manifest)
            matches = mgr.match(message, max_results=1)
            if not matches:
                return None
            ctx = mgr.activate(matches)
            chosen = matches[0].skill
            ctx.origin = chosen.origin
            if chosen.origin != "builtin" and ctx.prompt_injection:
                ctx.prompt_injection = fence_untrusted_skill(ctx.prompt_injection, chosen.origin)
            return ctx
        except Exception:  # noqa: BLE001 — 技能匹配失败不应影响对话
            logger.exception("技能匹配失败")
            return None

    async def _match_skills_async(self, session: Session, user: User, message: str) -> SkillContext | None:
        """Match authorized candidates, preserving existing activation and fences."""
        if not settings.SKILL_ENABLED:
            return None
        try:
            from app.agents.skills.manager import SkillManager
            from app.core.security import Role
            from app.models.user import Tenant
            from app.services.skill_service import fence_untrusted_skill, manifests_for_chat

            principal = (user.id, user.tenant_id, user.role, user.token_version)
            mgr = SkillManager()
            for manifest in manifests_for_chat(session, user):
                mgr.register(manifest)
            matches = mgr.match(message, max_results=1)
            if not matches:
                matches = await mgr.match_semantic(message, self._intent_llm(), max_results=1)
                session.expire_all()
                if not (not settings.AUTH_ENABLED and principal[0] == "__auth_disabled__"
                        and principal[1] == "__auth_disabled__" and principal[2] == Role.SYSTEM_ADMIN.value):
                    active_user = session.get(User, principal[0])
                    if (active_user is None or not active_user.is_active
                            or (active_user.id, active_user.tenant_id, active_user.role,
                                active_user.token_version) != principal):
                        raise ResumeRejectedError("当前身份已失效，请重新登录", 403)
                    tenant = session.get(Tenant, principal[1])
                    if active_user.role_enum is not Role.SYSTEM_ADMIN and (tenant is None or not tenant.is_active):
                        raise ResumeRejectedError("租户已停用", 403)
            if not matches:
                return None
            # Re-read authorization and versions after the awaited remote proposal.
            session.expire_all()
            current = {m.name: m for m in manifests_for_chat(session, user)}
            chosen = matches[0].skill
            actual = current.get(chosen.name)
            if (actual is None or not actual.enabled or actual.origin != chosen.origin
                    or actual.effective_version != chosen.effective_version):
                return None
            ctx = mgr.activate(matches)
            ctx.origin = chosen.origin
            if chosen.origin != "builtin" and ctx.prompt_injection:
                ctx.prompt_injection = fence_untrusted_skill(ctx.prompt_injection, chosen.origin)
            return ctx
        except ResumeRejectedError:
            raise
        except Exception:  # noqa: BLE001 -- no prompt/provider error details in logs
            logger.info("skill_match outcome=unavailable")
            return None

    # ── 进化系统（Reflect 反思）───────────────────

    @staticmethod
    def _conversation_after_reply(session: Session, conv: Conversation, conversation_id: str) -> Conversation | None:
        """回复提交后取回仍属于当前 Session 的会话。

        流式路由交出会话编号后，请求依赖会关闭 Session。
        ``close()`` 会摘掉已加载对象，但 Session 还能写入新消息。
        已经脱离的对象不能 ``refresh``。
        """
        from sqlalchemy.orm import object_session

        if object_session(conv) is session:
            session.refresh(conv)
            return conv
        return session.get(Conversation, conversation_id)

    def _maybe_reflect(self, conv: Conversation, state: AgentState) -> None:
        """对话结束后触发异步反思。

        仅在 EVOLUTION_ENABLED + EVOLUTION_REFLECT_ENABLED 时触发；
        反思失败不影响对话响应，仅记录日志。

        SKELETON：当前仅做 LLM 反思并记录日志；
        可按需扩展：改进点持久化、Skill 自动更新、Action Item 调度。
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
            lines.append(f"{role_label}：{sanitize_history_text(m.content)}")
        return "\n".join(lines)

    # ── 安全治理 ──────────────────────────────────

    @staticmethod
    def _apply_input_security(message: str, user: User, *, check_rate_limit: bool = True,
                              fail_closed: bool = False) -> "tuple[SecurityContext | None, str]":
        """对用户输入执行安全过滤。

        包含：输入过滤（PII 检测 + 敏感词）+ Prompt 注入检测。
        失败时仅记录日志，不阻断对话。

        Returns:
            (安全上下文, 送模型的文本)。开启输入过滤时返回脱敏后文本，
            使 PII 不会进入模型；未开启或过滤失败时原样返回 ``message``。
        """
        if not settings.SECURITY_ENABLED:
            return None, message
        # 本方法是静态方法，告警落日志必须用类名调用脱敏器。
        # 误用 self 会抛 NameError，被下方兜底 except 吞掉后整条过滤链路静默失效，
        # 恰好只影响被判定为敏感的输入——属于最危险的 fail-open。
        try:
            from app.security import (
                InputFilter,
                PromptInjectionDetector,
                SecurityContext,
                get_rate_limiter,
            )

            ctx = SecurityContext()
            sanitized = message

            # 速率限制：以「用户 + 租户」为键，先于内容检测执行，
            # 被限流的请求无需再消耗过滤与模型算力。
            if settings.SECURITY_RATE_LIMIT and check_rate_limit:
                limiter = get_rate_limiter()
                allowed, remaining = limiter.allow(f"{user.tenant_id}:{user.id}", ctx=ctx)
                if not allowed:
                    logger.warning("请求被限流: user=%s, remaining=%s", user.id, remaining)
                    return ctx, sanitized

            # 输入过滤
            if settings.SECURITY_INPUT_FILTER:
                input_filter = InputFilter()
                result = input_filter.filter(message, ctx)
                sanitized = result.sanitized_text or message
                if result.flagged:
                    logger.warning(
                        "输入安全告警: user=%s, reasons=%s",
                        user.id,
                        ChatService._sanitize_for_log("; ".join(result.reasons)),
                    )

            # 注入检测
            if settings.SECURITY_INJECTION_DETECTION:
                detector = PromptInjectionDetector(threshold=settings.SECURITY_INJECTION_THRESHOLD)
                inj_result = detector.detect(message, ctx)
                if inj_result.detected:
                    logger.warning(
                        "注入检测告警: user=%s, confidence=%.2f, matches=%s",
                        user.id,
                        inj_result.confidence,
                        ChatService._sanitize_for_log("; ".join(inj_result.matches)),
                    )

            return ctx, sanitized

        except Exception:  # noqa: BLE001 — 安全过滤失败不应影响对话
            if fail_closed:
                from app.security.types import SecurityRejectedError

                raise SecurityRejectedError("附件安全检查失败，请稍后重试", status_code=503) from None
            logger.exception("输入安全过滤失败")
            return None, message

    @staticmethod
    def _sanitize_for_log(text: str) -> str:
        """对写入日志的文本做脱敏。

        安全告警里的匹配内容可能携带用户原文（如命中的敏感片段），
        直接写入日志会让 PII 从「被过滤」变成「被记录」，
        因此落日志前统一过一遍脱敏器；脱敏器异常时退化为截断而非丢弃。
        """
        try:
            from app.security import get_log_sanitizer

            return get_log_sanitizer().sanitize(text)
        except Exception:  # noqa: BLE001 — 脱敏失败不应影响告警记录
            return text[:200]

    @staticmethod
    def _rejection_error(ctx: "SecurityContext") -> "SecurityRejectedError":
        """把被阻断的安全上下文转换为带状态码的异常。

        限流与内容阻断需要不同状态码：限流是 429（客户端应退避重试），
        内容阻断是 403（重试无意义）。合并成一种会误导调用方。
        """
        from app.security.types import SecurityRejectedError

        if ctx.rate_limited:
            return SecurityRejectedError(
                "请求过于频繁，请稍后重试",
                status_code=429,
                retry_after_seconds=ctx.retry_after_seconds,
            )
        if ctx.injection_detected:
            return SecurityRejectedError("输入被安全策略拒绝（疑似提示词注入）", status_code=403)
        return SecurityRejectedError("输入被安全策略拒绝", status_code=403)

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

    # ── 调试追踪 ──────────────────────────────────

    @staticmethod
    def _create_trace() -> "AgentTrace | None":
        """创建调试追踪对象（仅在 DEBUG_ENABLED 时）。"""
        if not settings.DEBUG_ENABLED:
            return None
        try:
            from app.debug.trace import AgentTrace

            return AgentTrace(debug_mode=True)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _collect_trace(trace: "AgentTrace | None") -> None:
        """收集追踪到全局缓存。"""
        if trace is None:
            return
        try:
            from app.debug.trace import TraceCollector

            collector = TraceCollector.get_instance()
            collector.add(trace)
            logger.debug(
                "Trace 已收集: run_id=%s, duration=%.0fms, events=%d",
                trace.run_id,
                trace.duration_ms,
                len(trace.events),
            )
        except Exception:  # noqa: BLE001
            pass
