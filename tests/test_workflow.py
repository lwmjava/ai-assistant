"""Workflow 工作流引擎测试。

覆盖：
- cron 计算（croniter 可用时）；
- RuntimeBridge 以 owner 身份执行 Prompt；
- API 全链路：创建 / 列表 / 详情 / 更新 / 删除 / 手动触发 / 执行历史 / 启停；
- 未启用时 503 门控；
- owner 失效自动停用；
- 执行成功回调 webhook（mock httpx）；
- 调度器可启停。

复用项目测试基建：独立 test db（conftest 已 init_db）、MockLLMProvider、依赖覆盖。
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from unittest.mock import AsyncMock, patch

from app.api.deps import get_current_user
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
from app.workflow.bridge import WorkflowBridge
from app.workflow.engine import WorkflowEngine, next_run_time
from app.workflow.scheduler import (
    _is_scheduler_runnable,
    start_scheduler,
    stop_scheduler,
)


@pytest.fixture(autouse=True)
def _clean_db():
    """测试间清理工作流数据（test db 专用）。"""
    yield
    with Session(engine) as s:
        for e in s.exec(select(WorkflowExecution)).all():
            s.delete(e)
        for w in s.exec(select(Workflow)).all():
            s.delete(w)
        s.commit()


@pytest.fixture()
def client():
    """注入 member 身份 + Mock LLM，且不进入 lifespan（避免后台调度器干扰）。"""
    settings.WORKFLOW_ENABLED = True
    fake_user = User(
        id="wf-user",
        tenant_id="wf-tenant",
        username="wf",
        hashed_password="",
        role=Role.MEMBER.value,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user
    set_llm_provider_override(MockLLMProvider())
    c = TestClient(app)  # 不进入 lifespan，避免启动调度器后台任务
    yield c
    app.dependency_overrides.clear()
    set_llm_provider_override(None)
    settings.WORKFLOW_ENABLED = False


# ── cron 计算 ──────────────────────────────────
def test_next_run_time_is_future_and_aware():
    pytest.importorskip("croniter")
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    nxt = next_run_time("* * * * *", "Asia/Shanghai", after=now)
    assert nxt.tzinfo is not None
    delta = (nxt - now).total_seconds()
    assert 0 < delta <= 60


def test_next_run_time_raises_without_croniter(monkeypatch):
    import app.workflow.engine as engine_mod
    from app.workflow.engine import CroniterNotAvailableError

    def _blocked():
        raise CroniterNotAvailableError("no croniter")

    monkeypatch.setattr(engine_mod, "_require_croniter", _blocked)
    with pytest.raises(CroniterNotAvailableError):
        next_run_time("* * * * *", "Asia/Shanghai")


# ── bridge ──────────────────────────────────
@pytest.mark.asyncio
async def test_bridge_executes_with_owner():
    set_llm_provider_override(MockLLMProvider())
    owner = User(
        id="b-owner",
        tenant_id="b-tenant",
        username="b",
        hashed_password="",
        role=Role.MEMBER.value,
        is_active=True,
    )
    with Session(engine) as s:
        out = await WorkflowBridge().execute(s, owner, "hello")
        assert isinstance(out, str) and out
    set_llm_provider_override(None)


# ── owner 失效 ──────────────────────────────────
@pytest.mark.asyncio
async def test_owner_inactive_suspends_workflow():
    set_llm_provider_override(MockLLMProvider())
    owner = User(
        id="bad-owner",
        tenant_id="bad-tenant",
        username="bad",
        hashed_password="",
        role=Role.MEMBER.value,
        is_active=False,
    )
    with Session(engine) as s:
        wf = Workflow(
            tenant_id="bad-tenant",
            owner_id="bad-owner",
            name="bad",
            cron_expr="0 0 1 1 *",
            prompt_template="hi",
        )
        s.add(wf)
        s.commit()
        s.refresh(wf)
        execution = await WorkflowEngine().execute(
            s, wf, owner, triggered_by=TriggerSource.MANUAL.value
        )
        assert execution.status == ExecutionStatus.FAILED.value
        s.refresh(wf)
        assert wf.suspended_owner is True
        assert wf.enabled is False
    set_llm_provider_override(None)


# ── webhook 回调 ──────────────────────────────────
@pytest.mark.asyncio
async def test_webhook_called_on_success():
    set_llm_provider_override(MockLLMProvider())
    owner = User(
        id="wh-owner",
        tenant_id="wh-tenant",
        username="wh",
        hashed_password="",
        role=Role.MEMBER.value,
        is_active=True,
    )
    with Session(engine) as s:
        wf = Workflow(
            tenant_id="wh-tenant",
            owner_id="wh-owner",
            name="wh",
            cron_expr="0 0 1 1 *",
            prompt_template="hi",
            webhook_url="http://example.com/hook",
        )
        s.add(wf)
        s.commit()
        s.refresh(wf)
        # 验证成功后触发 webhook 回调分支（不实际发请求，避免 mock 全局 httpx 干扰其他模块）。
        with patch.object(WorkflowEngine, "_fire_webhook", new=AsyncMock()) as mock_fw:
            execution = await WorkflowEngine().execute(
                s, wf, owner, triggered_by=TriggerSource.MANUAL.value
            )
        assert execution.status == ExecutionStatus.SUCCESS.value
        assert mock_fw.called
        # 回调应携带正确的工作流与执行记录
        assert mock_fw.call_args.args[0].id == wf.id
    set_llm_provider_override(None)


# ── API 全链路 ──────────────────────────────────
def test_create_and_manual_run_and_history(client: TestClient) -> None:
    resp = client.post(
        "/api/workflows",
        json={"name": "t", "cron_expr": "0 0 1 1 *", "prompt_template": "do something"},
    )
    assert resp.status_code == 201
    wf = resp.json()
    wid = wf["id"]
    assert wf["owner_id"] == "wf-user"
    assert wf["timezone"] == settings.WORKFLOW_DEFAULT_TIMEZONE

    run = client.post(f"/api/workflows/{wid}/run")
    assert run.status_code == 201
    ex = run.json()
    assert ex["status"] == "success"
    assert ex["output"]
    assert ex["triggered_by"] == "manual"
    assert ex["duration_ms"] is not None

    hist = client.get(f"/api/workflows/{wid}/executions")
    assert hist.status_code == 200
    assert len(hist.json()) >= 1

    client.delete(f"/api/workflows/{wid}")


def test_crud_and_toggle(client: TestClient) -> None:
    created = client.post(
        "/api/workflows",
        json={"name": "t2", "cron_expr": "0 0 1 1 *", "prompt_template": "p"},
    )
    wid = created.json()["id"]

    listing = client.get("/api/workflows")
    assert listing.status_code == 200
    assert any(x["id"] == wid for x in listing.json())

    detail = client.get(f"/api/workflows/{wid}")
    assert detail.status_code == 200

    updated = client.put(f"/api/workflows/{wid}", json={"name": "t2-updated"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "t2-updated"

    toggled = client.post(f"/api/workflows/{wid}/toggle", json={"enabled": False})
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False

    # 删除需 tenant_admin 及以上（与 conversations/knowledge_bases 对称，member 不可删）。
    admin = User(
        id="wf-admin",
        tenant_id="wf-tenant",
        username="wfadmin",
        hashed_password="",
        role=Role.TENANT_ADMIN.value,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: admin
    deleted = client.delete(f"/api/workflows/{wid}")
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True

    missing = client.get(f"/api/workflows/{wid}")
    assert missing.status_code == 404


def test_disabled_returns_503(client: TestClient) -> None:
    settings.WORKFLOW_ENABLED = False
    resp = client.post(
        "/api/workflows",
        json={"name": "x", "cron_expr": "0 0 1 1 *", "prompt_template": "p"},
    )
    assert resp.status_code == 503
    settings.WORKFLOW_ENABLED = True


# ── 调度器 ──────────────────────────────────
@pytest.mark.asyncio
async def test_scheduler_runnable_and_start_stop():
    settings.WORKFLOW_ENABLED = True
    assert _is_scheduler_runnable() is True

    await start_scheduler()
    from app.workflow.scheduler import _task

    assert _task is not None and not _task.done()

    await stop_scheduler()
    from app.workflow.scheduler import _task as t2

    assert t2 is None or t2.done()
    settings.WORKFLOW_ENABLED = False
