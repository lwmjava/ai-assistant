"""中高风险动作先落审批，再由发起人本人决定。

对话回复不会变成批准。过期、拒绝、撤权和取消都不执行。
同一次批准最多执行一次；重复调用只返回已经记下的结果。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, select

from app.agents.tools.base import ToolRegistry
from app.audit.models import AuditAction, AuditLog
from app.models.approval import ActionApproval
from app.models.user import Tenant, User

APPROVAL_TTL = timedelta(minutes=30)
_OPEN = frozenset({"pending", "approved"})
_SENSITIVE = ("token", "passwd", "credential", "authorization")
_BOUND: ContextVar[Session | None] = ContextVar("approval_session", default=None)
_HELD: ContextVar[str | None] = ContextVar("held_approval_id", default=None)


class ApprovalError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


@dataclass
class ApprovalView:
    id: str
    resource: str
    action: str
    subject: str
    parameter_summary: str
    risk: str
    status: str
    expires_at: datetime
    execution_count: int
    result_code: str


def bind_approval_session(session: Session | None) -> None:
    """测试或同一请求里复用已有会话。未绑定时执行器自己打开会话。"""
    _BOUND.set(session)


def note_held_approval(approval_id: str) -> None:
    _HELD.set(approval_id)


def clear_approval_context() -> None:
    _HELD.set(None)
    _BOUND.set(None)


def current_held_approval() -> str | None:
    return _HELD.get()


def reject_chat_phrase(text: str) -> None:
    """对话里的同意、拒绝或任何原文都不能改变审批状态。"""
    del text
    raise ApprovalError(409, "对话回复不能代替审批")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _digest(arguments: dict) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _summary(arguments: dict) -> str:
    parts: list[str] = []
    for key in sorted(arguments):
        lowered = key.lower()
        if any(part in lowered for part in _SENSITIVE):
            parts.append(f"{key}=[redacted]")
            continue
        text = str(arguments[key])
        if len(text) > 40:
            text = f"[len:{len(text)}]"
        parts.append(f"{key}={text}")
    return ", ".join(parts)[:500]


def _subject(action: str, arguments: dict) -> str:
    path = arguments.get("path")
    if isinstance(path, str) and path and len(path) <= 80:
        return f"{action} {path}"[:120]
    return action[:120]


def _view(row: ActionApproval) -> ApprovalView:
    return ApprovalView(
        id=row.id,
        resource=row.resource,
        action=row.action,
        subject=row.subject,
        parameter_summary=row.parameter_summary,
        risk=row.risk,
        status=row.status,
        expires_at=row.expires_at,
        execution_count=row.execution_count,
        result_code=row.result_code,
    )


def _audit(session: Session, user: User, row: ActionApproval, operation: str) -> None:
    session.add(
        AuditLog(
            user_id=user.id,
            tenant_id=user.tenant_id,
            action=AuditAction.OTHER.value,
            resource_type="approval",
            resource_id=row.id,
            details=json.dumps(
                {
                    "operation": operation,
                    "status": row.status,
                    "action": row.action,
                    "risk": row.risk,
                },
                ensure_ascii=False,
            ),
        )
    )


def _refresh(session: Session, row: ActionApproval, user: User, clock: Callable[[], datetime]) -> None:
    if row.status not in _OPEN:
        return
    tenant = session.get(Tenant, user.tenant_id)
    withdrawn = (
        user.token_version != row.token_version
        or not user.is_active
        or tenant is None
        or not tenant.is_active
    )
    if withdrawn or row.expires_at <= clock():
        row.status = "revoked" if withdrawn else "expired"
        row.decided_at = clock()
        _audit(session, user, row, row.status)
        session.add(row)
        session.commit()


def _visible(session: Session, user: User, approval_id: str) -> ActionApproval:
    row = session.get(ActionApproval, approval_id)
    if row is None or row.tenant_id != user.tenant_id or row.requester_id != user.id:
        raise ApprovalError(404, "审批不存在")
    return row


def open_pending(
    session: Session,
    user: User,
    *,
    resource: str,
    action: str,
    risk: str,
    arguments: dict,
    clock: Callable[[], datetime] | None = None,
) -> ActionApproval:
    """同一发起人、同一动作和同一参数摘要复用未过期的待审批，不重复建单。"""
    current = clock or _now
    digest = _digest(arguments)
    encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    rows = session.exec(
        select(ActionApproval).where(
            col(ActionApproval.tenant_id) == user.tenant_id,
            col(ActionApproval.requester_id) == user.id,
            col(ActionApproval.action) == action,
            col(ActionApproval.parameter_digest) == digest,
            col(ActionApproval.status) == "pending",
        )
    ).all()
    for row in rows:
        _refresh(session, row, user, current)
        if row.status == "pending":
            return row
    row = ActionApproval(
        tenant_id=user.tenant_id,
        requester_id=user.id,
        token_version=user.token_version,
        resource=resource,
        action=action,
        subject=_subject(action, arguments),
        parameter_summary=_summary(arguments),
        parameter_digest=digest,
        arguments_json=encoded,
        risk=risk,
        status="pending",
        expires_at=current() + APPROVAL_TTL,
    )
    session.add(row)
    _audit(session, user, row, "pending")
    session.commit()
    session.refresh(row)
    return row


def list_approvals(
    session: Session, user: User, clock: Callable[[], datetime] | None = None,
) -> list[ApprovalView]:
    current = clock or _now
    rows = session.exec(
        select(ActionApproval)
        .where(
            col(ActionApproval.tenant_id) == user.tenant_id,
            col(ActionApproval.requester_id) == user.id,
        )
        .order_by(col(ActionApproval.created_at).desc())
    ).all()
    views: list[ApprovalView] = []
    for row in rows:
        _refresh(session, row, user, current)
        views.append(_view(row))
    return views


def get_approval(
    session: Session, user: User, approval_id: str, clock: Callable[[], datetime] | None = None,
) -> ApprovalView:
    current = clock or _now
    row = _visible(session, user, approval_id)
    _refresh(session, row, user, current)
    return _view(row)


def approve_approval(
    session: Session, user: User, approval_id: str, clock: Callable[[], datetime] | None = None,
) -> ApprovalView:
    """只改变状态，不调用工具。"""
    current = clock or _now
    row = _visible(session, user, approval_id)
    _refresh(session, row, user, current)
    if row.status == "approved":
        return _view(row)
    if row.status != "pending":
        raise ApprovalError(409, "当前状态不能批准")
    row.status = "approved"
    row.decided_at = current()
    _audit(session, user, row, "approved")
    session.add(row)
    session.commit()
    session.refresh(row)
    return _view(row)


def reject_approval(
    session: Session, user: User, approval_id: str, clock: Callable[[], datetime] | None = None,
) -> ApprovalView:
    current = clock or _now
    row = _visible(session, user, approval_id)
    _refresh(session, row, user, current)
    if row.status == "rejected":
        return _view(row)
    if row.status != "pending":
        raise ApprovalError(409, "当前状态不能拒绝")
    row.status = "rejected"
    row.decided_at = current()
    _audit(session, user, row, "rejected")
    session.add(row)
    session.commit()
    session.refresh(row)
    return _view(row)


def cancel_approval(
    session: Session, user: User, approval_id: str, clock: Callable[[], datetime] | None = None,
) -> ApprovalView:
    current = clock or _now
    row = _visible(session, user, approval_id)
    _refresh(session, row, user, current)
    if row.status == "cancelled":
        return _view(row)
    if row.status not in _OPEN:
        raise ApprovalError(409, "当前状态不能取消")
    row.status = "cancelled"
    row.decided_at = current()
    _audit(session, user, row, "cancelled")
    session.add(row)
    session.commit()
    session.refresh(row)
    return _view(row)


def _result_code(observation: object) -> str:
    from app.agents.tools.outcome import ToolOutput

    if isinstance(observation, str):
        try:
            data = json.loads(observation)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("status"), str):
            return data["status"]
    if isinstance(observation, ToolOutput):
        return observation.status or "ok"
    return "ok"


async def execute_approval(
    session: Session,
    user: User,
    approval_id: str,
    registry: ToolRegistry,
    *,
    clock: Callable[[], datetime] | None = None,
    before_call: Callable[[], None] | None = None,
) -> ApprovalView:
    """消费一次已批准的动作。已执行过则直接返回原结果。"""
    current = clock or _now
    row = _visible(session, user, approval_id)
    _refresh(session, row, user, current)
    if row.status == "executed":
        return _view(row)
    if row.status != "approved":
        raise ApprovalError(409, "当前状态不能执行")
    try:
        stored = json.loads(row.arguments_json)
    except json.JSONDecodeError:
        stored = None
    if not isinstance(stored, dict) or _digest(stored) != row.parameter_digest:
        row.status = "revoked"
        row.decided_at = current()
        _audit(session, user, row, "tampered")
        session.add(row)
        session.commit()
        raise ApprovalError(409, "审批参数已变化，未执行")
    if before_call is not None:
        before_call()
    row.status = "executed"
    row.execution_count = 1
    row.executed_at = current()
    session.add(row)
    session.commit()
    tool = registry.get(row.action)
    timeout = tool.contract.timeout_seconds if tool is not None and tool.contract is not None else 30.0
    code = "tool_not_found"
    if tool is not None:
        try:
            async with asyncio.timeout(timeout):
                observation = await tool.execute(stored)
            code = _result_code(observation)
        except TimeoutError:
            code = "timeout"
        except Exception:  # noqa: BLE001 — 失败也已经消耗这一次执行
            code = "error"
    row.result_code = code
    _audit(session, user, row, "executed")
    session.add(row)
    session.commit()
    session.refresh(row)
    return _view(row)


def held_approval_effect(
    session: Session, approval_id: str, tenant_id: str, user_id: str,
) -> str | None:
    """恢复检查。查不到表时返回 None，不把原有检查点变成失败。"""
    try:
        row = session.get(ActionApproval, approval_id)
        user = session.get(User, user_id)
    except Exception:  # noqa: BLE001 — 旧库没有这张表时沿用原来的恢复
        session.rollback()
        return None
    if row is None or user is None or row.tenant_id != tenant_id or row.requester_id != user_id:
        return "blocked"
    _refresh(session, row, user, _now)
    if row.status in {"pending", "approved"}:
        return row.status
    if row.status == "executed":
        return "allow"
    return "blocked"


def hold_tool_call(
    user_id: str,
    tenant_id: str,
    *,
    resource: str,
    action: str,
    risk: str,
    arguments: dict,
) -> str | None:
    """为当前主体记下待审批。写失败时返回空，调用方仍不得执行工具。"""
    from app.core.database import Session as DbSession
    from app.core.database import engine

    bound = _BOUND.get()
    own = bound is None
    session = bound if bound is not None else DbSession(engine)
    try:
        user = session.get(User, user_id)
        if user is None or user.tenant_id != tenant_id or not user.is_active:
            return None
        row = open_pending(
            session,
            user,
            resource=resource,
            action=action,
            risk=risk,
            arguments=arguments,
        )
        note_held_approval(row.id)
        return row.id
    except Exception:  # noqa: BLE001 — 不能因为记账失败而放行工具
        session.rollback()
        return None
    finally:
        if own:
            session.close()
