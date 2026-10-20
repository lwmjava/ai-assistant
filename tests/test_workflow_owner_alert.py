"""FLOW-005：创建者失效时写一条本租户可查询告警。

覆盖：
- 引擎路径：创建者被禁用 / 移出租户 → suspended_owner 且落库告警；
- 调度器路径：创建者账号不存在 → 落库告警；
- 查询 API：本租户 tenant_admin 可查（含工作流名/原创建者/原因）；
- 多租户隔离：他租户 tenant_admin 查不到本租户告警；
- 幂等：重复触发不产生重复告警。
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.audit.models import (
    WorkflowExecutionAlert,
    WorkflowExecutionLog,
    WorkflowOwnerAlert,
)
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role
from app.llm.factory import set_llm_provider_override
from app.llm.mock import MockLLMProvider
from app.main import app
from app.models.user import User
from app.models.workflow import (
    ExecutionStatus,
    TriggerSource,
    Workflow,
    WorkflowExecution,
)
from app.workflow.engine import WorkflowEngine


@pytest.fixture(autouse=True)
def _clean_owner_alert():
    """清理本用例产生的告警 / 执行 / 工作流，并复位认证覆盖与 Mock LLM。

    经引擎执行路径会同时写入 PWFL-004 的 ``workflow_execution_alerts`` /
    ``workflow_execution_logs``；按本用例产生的 workflow_id 级联清理这两张表，
    避免在共享测试库残留行（不清理他用例的行）。
    """
    yield
    with Session(engine) as s:
        workflow_ids = list(s.exec(select(Workflow.id)).all())
        if workflow_ids:
            for model in (WorkflowExecutionAlert, WorkflowExecutionLog, WorkflowOwnerAlert):
                for row in s.exec(
                    select(model).where(model.workflow_id.in_(workflow_ids))
                ).all():
                    s.delete(row)
        for row in s.exec(select(WorkflowExecution)).all():
            s.delete(row)
        for row in s.exec(select(Workflow)).all():
            s.delete(row)
        s.commit()
    app.dependency_overrides.clear()
    set_llm_provider_override(None)
    settings.WORKFLOW_ENABLED = False


def _make_workflow(s: Session, *, tenant_id: str, owner_id: str, name: str) -> Workflow:
    wf = Workflow(
        tenant_id=tenant_id,
        owner_id=owner_id,
        name=name,
        cron_expr="0 0 1 1 *",
        prompt_template="hi",
    )
    s.add(wf)
    s.commit()
    s.refresh(wf)
    return wf


def _alerts_for(s: Session, workflow_id: str) -> list[WorkflowOwnerAlert]:
    return list(
        s.exec(select(WorkflowOwnerAlert).where(WorkflowOwnerAlert.workflow_id == workflow_id)).all()
    )


# ── 引擎路径：禁用 / 移出租户 ──────────────────────────────
@pytest.mark.asyncio
async def test_suspended_owner_alert_when_disabled(persist_identity):
    set_llm_provider_override(MockLLMProvider())
    owner = User(
        id="da-owner", tenant_id="da-tenant", username="da",
        hashed_password="", role=Role.MEMBER.value, is_active=True,
    )
    persist_identity(owner)
    with Session(engine) as s:
        wf = _make_workflow(s, tenant_id="da-tenant", owner_id="da-owner", name="每日摘要")
        # 禁用创建者（DB 行置为停用；传入 engine 的对象仍为旧快照，引擎每次重载身份）
        row = s.get(User, "da-owner")
        assert row is not None
        row.is_active = False
        s.add(row)
        s.commit()

        execution = await WorkflowEngine().execute(
            s, wf, owner, triggered_by=TriggerSource.MANUAL.value
        )
        assert execution.status == ExecutionStatus.FAILED.value
        s.refresh(wf)
        assert wf.suspended_owner is True
        assert wf.enabled is False

        alerts = _alerts_for(s, wf.id)
        assert len(alerts) == 1, "创建者禁用后应落库一条可查询告警"
        alert = alerts[0]
        assert alert.tenant_id == "da-tenant"
        assert alert.workflow_id == wf.id
        assert alert.workflow_name == "每日摘要"
        assert alert.owner_id == "da-owner"
        assert alert.owner_username == "da"
        assert alert.reason == "owner_disabled"


@pytest.mark.asyncio
async def test_suspended_owner_alert_when_moved_out(persist_identity):
    set_llm_provider_override(MockLLMProvider())
    owner = User(
        id="mo-owner", tenant_id="mo-tenant", username="mo",
        hashed_password="", role=Role.MEMBER.value, is_active=True,
    )
    persist_identity(owner)
    with Session(engine) as s:
        wf = _make_workflow(s, tenant_id="mo-tenant", owner_id="mo-owner", name="周报任务")
        # 把创建者移到别的租户
        row = s.get(User, "mo-owner")
        assert row is not None
        row.tenant_id = "other-tenant"
        s.add(row)
        s.commit()

        execution = await WorkflowEngine().execute(
            s, wf, owner, triggered_by=TriggerSource.MANUAL.value
        )
        assert execution.status == ExecutionStatus.FAILED.value
        s.refresh(wf)
        assert wf.suspended_owner is True

        alerts = _alerts_for(s, wf.id)
        assert len(alerts) == 1
        assert alerts[0].reason == "owner_moved_out"
        assert alerts[0].tenant_id == "mo-tenant"


# ── 调度器路径：创建者账号不存在 ──────────────────────────────
@pytest.mark.asyncio
async def test_suspended_owner_alert_when_deleted_by_scheduler():
    set_llm_provider_override(MockLLMProvider())
    with Session(engine) as s:
        wf = _make_workflow(s, tenant_id="sc-tenant", owner_id="ghost-owner", name="幽灵任务")

    from app.workflow.scheduler import _trigger

    await _trigger(wf)

    with Session(engine) as s:
        alerts = _alerts_for(s, wf.id)
        assert len(alerts) == 1, "调度器发现创建者不存在应落库告警"
        assert alerts[0].reason == "owner_deleted"
        assert alerts[0].workflow_name == "幽灵任务"
        assert alerts[0].owner_username is None


# ── 幂等：重复触发不重复告警 ──────────────────────────────
@pytest.mark.asyncio
async def test_suspended_owner_alert_not_duplicated(persist_identity):
    set_llm_provider_override(MockLLMProvider())
    owner = User(
        id="id-owner", tenant_id="id-tenant", username="id",
        hashed_password="", role=Role.MEMBER.value, is_active=True,
    )
    persist_identity(owner)
    with Session(engine) as s:
        wf = _make_workflow(s, tenant_id="id-tenant", owner_id="id-owner", name="去重任务")
        row = s.get(User, "id-owner")
        assert row is not None
        row.is_active = False
        s.add(row)
        s.commit()

        await WorkflowEngine().execute(s, wf, owner, triggered_by=TriggerSource.MANUAL.value)
        # 第二次触发：创建者仍失效，但告警不应重复
        await WorkflowEngine().execute(s, wf, owner, triggered_by=TriggerSource.MANUAL.value)

        alerts = _alerts_for(s, wf.id)
        assert len(alerts) == 1, "重复触发不应产生重复告警"


# ── 查询 API：本租户 tenant_admin 可查 / 跨租户隔离 ──────────
async def _suspend_via_engine(persist_identity, *, tenant_id: str, owner_id: str, name: str) -> str:
    set_llm_provider_override(MockLLMProvider())
    owner = User(
        id=owner_id, tenant_id=tenant_id, username="alower",
        hashed_password="", role=Role.MEMBER.value, is_active=True,
    )
    persist_identity(owner)
    with Session(engine) as s:
        wf = _make_workflow(s, tenant_id=tenant_id, owner_id=owner_id, name=name)
        row = s.get(User, owner_id)
        assert row is not None
        row.is_active = False
        s.add(row)
        s.commit()
        await WorkflowEngine().execute(s, wf, owner, triggered_by=TriggerSource.MANUAL.value)
        return wf.id


@pytest.mark.asyncio
async def test_suspended_owner_alert_api_tenant_admin_lists(persist_identity):
    wid = await _suspend_via_engine(persist_identity, tenant_id="api-tenant", owner_id="api-owner", name="接口任务")

    admin = User(
        id="api-admin", tenant_id="api-tenant", username="ta",
        hashed_password="", role=Role.TENANT_ADMIN.value, is_active=True,
    )
    persist_identity(admin)
    settings.WORKFLOW_ENABLED = True
    app.dependency_overrides[get_current_user] = lambda: admin
    client = TestClient(app)

    resp = client.get("/api/v1/workflows/alerts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    item = next((a for a in body if a["workflow_id"] == wid), None)
    assert item is not None, "本租户 tenant_admin 应能查到该告警"
    assert item["tenant_id"] == "api-tenant"
    assert item["workflow_name"] == "接口任务"
    assert item["owner_id"] == "api-owner"
    assert item["reason"] == "owner_disabled"


@pytest.mark.asyncio
async def test_suspended_owner_alert_api_cross_tenant_isolation(persist_identity):
    wid = await _suspend_via_engine(persist_identity, tenant_id="iso-a", owner_id="iso-owner-a", name="A租户任务")

    other_admin = User(
        id="iso-admin-b", tenant_id="iso-b", username="tb",
        hashed_password="", role=Role.TENANT_ADMIN.value, is_active=True,
    )
    persist_identity(other_admin)
    settings.WORKFLOW_ENABLED = True
    app.dependency_overrides[get_current_user] = lambda: other_admin
    client = TestClient(app)

    resp = client.get("/api/v1/workflows/alerts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert not any(a["workflow_id"] == wid for a in body), \
        "他租户 tenant_admin 不应查到本租户告警"
    assert all(a["tenant_id"] == "iso-b" for a in body)
