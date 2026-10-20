"""PWFL-004：工作流执行失败告警与执行日志 trace_id 持久化。

覆盖：
- 一次执行最终失败产生恰好一条可查询告警记录；
- 执行日志按 trace_id 关联（同一执行的多条日志共享同一 trace_id）；
- 可按 trace_id 检索出完整执行轨迹；
- 跨租户隔离（他租户查不到本租户告警/日志）；
- 重试耗尽只写一条告警（不在每次 attempt 重复写）；
- 成功执行不产生告警，但有日志。
"""

import pytest
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.models.user import User
from app.models.workflow import ExecutionStatus, TriggerSource, Workflow, WorkflowExecution
from app.workflow.engine import WorkflowEngine


class AlwaysFailingBridge:
    """每次调用都抛异常的桥接。"""

    def __init__(self, message: str = "bridge down") -> None:
        self.message = message
        self.calls = 0

    async def execute(self, session: Session, owner: User, prompt: str) -> str:
        self.calls += 1
        raise RuntimeError(self.message)


class AlwaysSucceedingBridge:
    def __init__(self, output: str = "ok") -> None:
        self.output = output
        self.calls = 0

    async def execute(self, session: Session, owner: User, prompt: str) -> str:
        self.calls += 1
        return self.output


@pytest.fixture(autouse=True)
def _clean_pwfl004():
    """清理本卡相关数据。"""
    yield
    from app.audit.models import WorkflowExecutionAlert, WorkflowExecutionLog
    with Session(engine) as s:
        for row in s.exec(select(WorkflowExecutionAlert)).all():
            s.delete(row)
        for row in s.exec(select(WorkflowExecutionLog)).all():
            s.delete(row)
        for row in s.exec(select(WorkflowExecution)).all():
            s.delete(row)
        names = ["告警任务", "日志任务", "隔离任务", "成功任务", "重试任务"]
        for row in s.exec(select(Workflow).where(col(Workflow.name).in_(names))).all():
            s.delete(row)
        s.commit()
    settings.WORKFLOW_RETRY_MAX = 0
    settings.WORKFLOW_RETRY_BASE_SECONDS = 1.0
    settings.WORKFLOW_RETRY_MAX_DELAY_SECONDS = 8.0


def _make_workflow(s: Session, *, tenant_id: str, owner_id: str, name: str, timeout: int = 300) -> Workflow:
    wf = Workflow(
        tenant_id=tenant_id,
        owner_id=owner_id,
        name=name,
        cron_expr="0 0 1 1 *",
        prompt_template="hi",
        timeout_seconds=timeout,
    )
    s.add(wf)
    s.commit()
    s.refresh(wf)
    s.expunge(wf)
    return wf


def _make_owner(persist_identity, *, user_id: str, tenant_id: str) -> User:
    owner = User(
        id=user_id, tenant_id=tenant_id, username=user_id,
        hashed_password="", role=Role.MEMBER.value, is_active=True,
    )
    persist_identity(owner)
    return owner


# ── 核心：失败产生一条告警 ──────────────────────────────
@pytest.mark.asyncio
async def test_failure_creates_one_alert(persist_identity, monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 0)
    owner = _make_owner(persist_identity, user_id="fa-owner", tenant_id="fa-tenant")
    with Session(engine) as s:
        wf = _make_workflow(s, tenant_id="fa-tenant", owner_id="fa-owner", name="告警任务")

    bridge = AlwaysFailingBridge("permanent failure")
    with Session(engine) as s:
        execution = await WorkflowEngine(bridge=bridge).execute(
            s, wf, owner, triggered_by=TriggerSource.MANUAL.value
        )
        assert execution.status == ExecutionStatus.FAILED.value

    # 从新会话查询告警（持久化证明）
    from app.audit.models import WorkflowExecutionAlert
    with Session(engine) as s:
        alerts = list(s.exec(
            select(WorkflowExecutionAlert).where(WorkflowExecutionAlert.workflow_id == wf.id)
        ).all())
    assert len(alerts) == 1, f"一次失败应产生恰好一条告警，实际 {len(alerts)} 条"
    alert = alerts[0]
    assert alert.tenant_id == "fa-tenant"
    assert alert.workflow_id == wf.id
    assert alert.execution_id == execution.id
    valid_reasons = (
        "retry_exhausted", "bridge_error", "timeout", "cancelled",
        "owner_invalid", "tenant_unavailable", "setup_error",
    )
    assert alert.reason in valid_reasons
    assert alert.trace_id, "告警应携带 trace_id"
    assert alert.workflow_name == "告警任务"

    # 端到端关联：告警的 trace_id 应与执行日志的 trace_id 一致
    from app.audit.models import WorkflowExecutionLog
    with Session(engine) as s:
        logs_for_exec = list(s.exec(
            select(WorkflowExecutionLog).where(WorkflowExecutionLog.execution_id == execution.id)
        ).all())
    assert len(logs_for_exec) >= 1, "失败执行应有至少一条日志"
    log_trace_ids = {log.trace_id for log in logs_for_exec}
    assert alert.trace_id in log_trace_ids, \
        f"告警 trace_id={alert.trace_id} 应与执行日志 trace_id 一致，实际日志 trace_ids={log_trace_ids}"


# ── 核心：执行日志按 trace_id 关联 ──────────────────────────────
@pytest.mark.asyncio
async def test_execution_logs_linked_by_trace_id(persist_identity, monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 0)
    owner = _make_owner(persist_identity, user_id="lg-owner", tenant_id="lg-tenant")
    with Session(engine) as s:
        wf = _make_workflow(s, tenant_id="lg-tenant", owner_id="lg-owner", name="日志任务")

    bridge = AlwaysSucceedingBridge("done")
    with Session(engine) as s:
        execution = await WorkflowEngine(bridge=bridge).execute(
            s, wf, owner, triggered_by=TriggerSource.MANUAL.value
        )
        assert execution.status == ExecutionStatus.SUCCESS.value

    # 按 trace_id 检索日志
    from app.workflow.execution_log import get_logs_by_trace_id
    with Session(engine) as s:
        # 先找到该执行的告警或日志里的 trace_id
        from app.audit.models import WorkflowExecutionLog
        logs_for_exec = list(s.exec(
            select(WorkflowExecutionLog).where(WorkflowExecutionLog.execution_id == execution.id)
        ).all())
    assert len(logs_for_exec) >= 2, "成功执行至少应有开始和结束两条日志"
    trace_ids = {log.trace_id for log in logs_for_exec}
    assert len(trace_ids) == 1, "同一执行的所有日志应共享同一 trace_id"
    trace_id = trace_ids.pop()
    assert trace_id, "trace_id 不应为空"

    # 用 get_logs_by_trace_id 查询
    with Session(engine) as s:
        retrieved = get_logs_by_trace_id(s, trace_id)
    assert len(retrieved) == len(logs_for_exec), "按 trace_id 检索应返回该执行全部日志"
    assert all(log.execution_id == execution.id for log in retrieved)


# ── 重试耗尽只写一条告警 ──────────────────────────────
@pytest.mark.asyncio
async def test_retry_exhausted_single_alert(persist_identity, monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 2)
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_BASE_SECONDS", 0.1)
    owner = _make_owner(persist_identity, user_id="rt-owner", tenant_id="rt-tenant")
    with Session(engine) as s:
        wf = _make_workflow(s, tenant_id="rt-tenant", owner_id="rt-owner", name="重试任务")

    bridge = AlwaysFailingBridge("still down")

    async def sleeper(_: float) -> None:
        pass

    with Session(engine) as s:
        execution = await WorkflowEngine(bridge=bridge).execute(
            s, wf, owner, triggered_by=TriggerSource.MANUAL.value, sleeper=sleeper
        )
        assert execution.status == ExecutionStatus.FAILED.value

    from app.audit.models import WorkflowExecutionAlert
    with Session(engine) as s:
        alerts = list(s.exec(
            select(WorkflowExecutionAlert).where(WorkflowExecutionAlert.execution_id == execution.id)
        ).all())
    assert len(alerts) == 1, f"重试耗尽只应写一条告警，实际 {len(alerts)} 条"
    assert bridge.calls == 3  # 初始 + 2 次重试


# ── 跨租户隔离 ──────────────────────────────
@pytest.mark.asyncio
async def test_cross_tenant_isolation(persist_identity, monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 0)
    owner_a = _make_owner(persist_identity, user_id="iso-owner-a", tenant_id="iso-tenant-a")
    _make_owner(persist_identity, user_id="iso-owner-b", tenant_id="iso-tenant-b")
    with Session(engine) as s:
        wf_a = _make_workflow(s, tenant_id="iso-tenant-a", owner_id="iso-owner-a", name="隔离任务")

    bridge = AlwaysFailingBridge("boom")
    with Session(engine) as s:
        await WorkflowEngine(bridge=bridge).execute(s, wf_a, owner_a)

    from app.audit.models import WorkflowExecutionLog
    from app.workflow.execution_log import get_logs_by_trace_id, list_alerts
    with Session(engine) as s:
        alerts_for_a = list_alerts(s, tenant_id="iso-tenant-a")
        alerts_for_b = list_alerts(s, tenant_id="iso-tenant-b")
        # 拿到租户 A 的执行日志 trace_id
        logs_a = list(s.exec(
            select(WorkflowExecutionLog).where(WorkflowExecutionLog.tenant_id == "iso-tenant-a")
        ).all())
        assert logs_a, "租户 A 应有执行日志"
        trace_id_a = logs_a[0].trace_id
        # 用租户 B 的 tenant_id 查询租户 A 的 trace_id → 应为空
        logs_via_b = get_logs_by_trace_id(s, trace_id_a, tenant_id="iso-tenant-b")
        # 用租户 A 的 tenant_id 查询 → 应非空
        logs_via_a = get_logs_by_trace_id(s, trace_id_a, tenant_id="iso-tenant-a")

    assert any(a.workflow_id == wf_a.id for a in alerts_for_a), "本租户应查到告警"
    assert not any(a.workflow_id == wf_a.id for a in alerts_for_b), "他租户不应查到本租户告警"
    assert len(logs_via_b) == 0, "他租户通过 trace_id 不应查到本租户日志"
    assert len(logs_via_a) >= 1, "本租户通过 trace_id 应查到本租户日志"


# ── 成功执行不产生告警，但有日志 ──────────────────────────────
@pytest.mark.asyncio
async def test_success_no_alert_but_has_logs(persist_identity, monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_RETRY_MAX", 0)
    owner = _make_owner(persist_identity, user_id="ok-owner", tenant_id="ok-tenant")
    with Session(engine) as s:
        wf = _make_workflow(s, tenant_id="ok-tenant", owner_id="ok-owner", name="成功任务")

    bridge = AlwaysSucceedingBridge("all good")
    with Session(engine) as s:
        execution = await WorkflowEngine(bridge=bridge).execute(s, wf, owner)
        assert execution.status == ExecutionStatus.SUCCESS.value

    from app.audit.models import WorkflowExecutionAlert, WorkflowExecutionLog
    with Session(engine) as s:
        alerts = list(s.exec(
            select(WorkflowExecutionAlert).where(WorkflowExecutionAlert.execution_id == execution.id)
        ).all())
        logs = list(s.exec(
            select(WorkflowExecutionLog).where(WorkflowExecutionLog.execution_id == execution.id)
        ).all())

    assert len(alerts) == 0, "成功执行不应产生告警"
    assert len(logs) >= 2, "成功执行应有开始和结束日志"
