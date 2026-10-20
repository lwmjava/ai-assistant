"""工作流执行日志与失败告警的数据层查询函数（PWFL-004）。

提供按租户、工作流、执行 ID、trace_id 的查询能力；
不新增 API 路由——「可查询」由这些函数 + 测试证明。
"""

from collections.abc import Sequence

from sqlmodel import Session, col, select

from app.audit.models import WorkflowExecutionAlert, WorkflowExecutionLog


def list_alerts(
    session: Session,
    *,
    tenant_id: str | None = None,
    workflow_id: str | None = None,
    execution_id: str | None = None,
    limit: int = 50,
) -> Sequence[WorkflowExecutionAlert]:
    """查询执行失败告警，按创建时间倒序。

    Args:
        session: 数据库会话。
        tenant_id: 租户隔离过滤；None 表示不过滤（系统管理员场景）。
        workflow_id: 按工作流过滤。
        execution_id: 按执行 ID 过滤。
        limit: 返回上限。
    """
    stmt = select(WorkflowExecutionAlert).order_by(col(WorkflowExecutionAlert.created_at).desc()).limit(limit)
    if tenant_id is not None:
        stmt = stmt.where(col(WorkflowExecutionAlert.tenant_id) == tenant_id)
    if workflow_id is not None:
        stmt = stmt.where(col(WorkflowExecutionAlert.workflow_id) == workflow_id)
    if execution_id is not None:
        stmt = stmt.where(col(WorkflowExecutionAlert.execution_id) == execution_id)
    return session.exec(stmt).all()


def get_logs_by_trace_id(
    session: Session,
    trace_id: str,
    *,
    tenant_id: str | None = None,
) -> Sequence[WorkflowExecutionLog]:
    """按 trace_id 检索一次执行的全部日志，按创建时间正序。

    Args:
        session: 数据库会话。
        trace_id: 执行级 trace_id（必填）。
        tenant_id: 租户隔离过滤；None 表示不过滤（系统管理员场景）。
    """
    stmt = (
        select(WorkflowExecutionLog)
        .where(col(WorkflowExecutionLog.trace_id) == trace_id)
        .order_by(col(WorkflowExecutionLog.created_at).asc())
    )
    if tenant_id is not None:
        stmt = stmt.where(col(WorkflowExecutionLog.tenant_id) == tenant_id)
    return session.exec(stmt).all()


def list_logs(
    session: Session,
    *,
    tenant_id: str | None = None,
    workflow_id: str | None = None,
    execution_id: str | None = None,
    limit: int = 100,
) -> Sequence[WorkflowExecutionLog]:
    """查询执行日志，按创建时间倒序。"""
    stmt = select(WorkflowExecutionLog).order_by(col(WorkflowExecutionLog.created_at).desc()).limit(limit)
    if tenant_id is not None:
        stmt = stmt.where(col(WorkflowExecutionLog.tenant_id) == tenant_id)
    if workflow_id is not None:
        stmt = stmt.where(col(WorkflowExecutionLog.workflow_id) == workflow_id)
    if execution_id is not None:
        stmt = stmt.where(col(WorkflowExecutionLog.execution_id) == execution_id)
    return session.exec(stmt).all()
