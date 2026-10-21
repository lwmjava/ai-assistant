"""测试环境配置。

在导入应用前设置环境变量，确保测试使用独立的 SQLite 数据库与开发环境，
避免污染仓库根目录的默认数据库，并放宽生产安全校验。
"""

import ipaddress
import os
import socket
from pathlib import Path

import pytest
from pydantic_settings.sources import DotEnvSettingsSource

# Windows 上 pytest 默认在 %TEMP%/pytest-of-*/pytest-current 建目录联接；
# 进程退出清理时常见 WinError 5，堆栈出现在「7 passed」之后，易被当成失败。
# 把临时根目录放到已 gitignore 的 data/，并尽量走 --basetemp（不再建 pytest-current）。
_PYTEST_TMP = Path(__file__).resolve().parents[1] / "data" / "pytest-tmp"
_PYTEST_TMP.mkdir(parents=True, exist_ok=True)
for _env_key in ("TMP", "TEMP", "TMPDIR"):
    os.environ[_env_key] = str(_PYTEST_TMP)

os.environ.setdefault("ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_ai_assistant.db")
os.environ.setdefault("AUTH_ENABLED", "true")
# 清空初始管理员配置，避免测试库被自动填充。
os.environ.setdefault("INITIAL_ADMIN_USERNAME", "")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "")
# 下列两项在本机 .env 里常被临时改作联调用（如 langgraph 编排、密集型限流）。
# 环境变量优先级高于 .env 文件，这里钉回默认值，否则同一份代码在不同机器上
# 会跑出不同的用例结果：通不过的那几个往往只是本机调试残留，而非代码缺陷。
os.environ.setdefault("AGENT_ORCHESTRATION", "self")
os.environ.setdefault("SECURITY_RATE_LIMIT", "false")

# 离线回归不继承宿主模型密钥；各协议用例显式注入合成配置或 provider。
# 保留 RAG、能力护栏及默认 provider 行为，缺密钥沿开发环境的既有 Mock 路径。
for _credential_key in (
    "LLM_API_KEY", "LLM_INTENT_API_KEY", "LLM_FALLBACK_API_KEY",
    "LLM_RERANK_API_KEY", "EMBEDDING_API_KEY",
):
    os.environ[_credential_key] = ""

_WORKSPACE_ENV = Path(__file__).resolve().parents[1] / ".env"
_read_env_file = DotEnvSettingsSource._read_env_file


def _read_isolated_env(self, file_path):
    if Path(file_path).resolve() == _WORKSPACE_ENV:
        return {}
    return _read_env_file(self, file_path)


# 临时配置文件仍可由配置契约测试读取，仓库本机配置不进入测试 Settings。
DotEnvSettingsSource._read_env_file = _read_isolated_env

# 在测试导入应用前已设置好环境变量；此处显式建表，
# 因为 TestClient 非上下文管理器使用时不会触发 lifespan 中的 init_db。
from app.core.config import settings  # noqa: E402
from app.core.database import init_db  # noqa: E402

init_db()


@pytest.fixture(autouse=True)
def _isolate_vector_store(monkeypatch: pytest.MonkeyPatch):
    """隔离测试显式使用 local。

    字段默认值仍是 milvus。用例若要验收默认选择，应读取模型字段或在函数体内
    再改回 milvus；不要靠这个夹具证明默认行为。
    """
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "local")


@pytest.fixture(autouse=True)
def _offline_network(monkeypatch: pytest.MonkeyPatch):
    """禁止回归测试外发；允许本机 HTTP fixtures 和事件循环内部 socket。"""
    original = socket.socket.connect
    original_lookup = socket.getaddrinfo

    def is_local(host):
        if host is None or host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def lookup(host, *args, **kwargs):
        if not is_local(host):
            raise AssertionError("offline tests prohibit external DNS lookups")
        return original_lookup(host, *args, **kwargs)

    def connect(sock, address):
        if sock.family == getattr(socket, "AF_UNIX", None):
            return original(sock, address)
        host = address[0] if isinstance(address, tuple) and address else ""
        if not is_local(host):
            raise AssertionError("offline tests prohibit non-loopback network connections")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """未指定 --basetemp 时固定到 data/pytest-tmp/run，避免 AppData 下 pytest-current 联接。"""
    if getattr(config.option, "basetemp", None):
        return
    config.option.basetemp = _PYTEST_TMP / "run"


@pytest.fixture(autouse=True)
def _reset_embedding_index_registry():
    """清空向量索引登记表，避免跨用例泄漏。

    索引登记是**全局**状态：一旦某个用例摄取过文档，就会登记并激活一个索引，
    之后的检索只读该索引名下的分块。而不同用例故意使用不同维度的 Mock 嵌入，
    共享测试库时会互相冲突（后一个用例的写入被判为身份不符而拒绝）。
    这在生产不是问题——生产只有一套固定的模型配置——但测试必须隔离。

    清理不留孤儿分块：索引行一旦删掉，挂在它名下的分块的 ``index_id`` 就变成
    悬空引用——检索不到，还会让下游的严格判定误触发。索引行清空后所有带
    ``index_id`` 的分块都是孤儿，一并清理。
    """
    from sqlmodel import Session, col, select

    from app.core.database import engine
    from app.models.rag import DocumentChunk, EmbeddingIndex

    with Session(engine) as session:
        for row in session.exec(select(EmbeddingIndex)).all():
            session.delete(row)
        # 清空后不存在任何索引行，凡带 index_id 的分块都成了悬空引用：既检索不到，
        # 又会让「库里有数据却没有生效索引」的严格判定在别的用例里误触发
        # （曾让 test_health_ok 因残留分块被判 degraded）。这里连同清理。
        # 身份未知的分块（index_id 为 NULL）不动，它们是历史隔离用例的被测对象。
        for chunk in session.exec(
            select(DocumentChunk).where(col(DocumentChunk.index_id).is_not(None))
        ).all():
            session.delete(chunk)
        session.commit()
    yield


@pytest.fixture(autouse=True)
def _reset_overrides():
    """兜底清理测试期间注册的全局覆盖，避免跨测试泄漏。

    业务 fixture（如 test_chat.py 的 ``client``）在 setup 阶段就注册
    ``app.dependency_overrides``，只有走到 ``yield`` 之后的 teardown 才会清理。
    一旦 setup 在 yield 前抛错，pytest 不会执行该 fixture 的 teardown，
    覆盖就会残留并污染后续测试（曾导致 test_health 的鉴权用例连锁失败）。
    本 fixture 是 autouse 且先于函数级 fixture 完成 setup，
    因此即使后者 setup 失败，pytest 仍会执行这里的 teardown。
    """
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    AppStatus.should_exit = False
    yield
    AppStatus.should_exit_event = None
    AppStatus.should_exit = False
    from app.llm.factory import set_llm_provider_override
    from app.main import app

    app.dependency_overrides.clear()
    set_llm_provider_override(None)


@pytest.fixture
def legacy_plan_mode(monkeypatch):
    """Explicit rollback coverage for fixtures implementing the old text planner.

    This is opt-in per test; new Plan tests and unmarked API tests keep the strict
    default. Existing action-loop assertions are retained under the approved
    constructor rollback mode, rather than accepting invalid new plans.
    """
    from app.agents.pipeline import AgentPipeline

    original = AgentPipeline.__init__

    def initialize(self, *args, **kwargs):
        kwargs.setdefault("structured_plans_enabled", False)
        original(self, *args, **kwargs)

    monkeypatch.setattr(AgentPipeline, "__init__", initialize)


@pytest.fixture
def persist_identity():
    """Explicitly persist successful test principals, then remove only newly created rows."""
    from sqlmodel import Session

    from app.core.database import engine
    from app.models.user import Tenant, User

    created_users: set[str] = set()
    created_tenants: set[str] = set()

    def persist(user: User) -> User:
        with Session(engine) as session:
            tenant = session.get(Tenant, user.tenant_id)
            if tenant is None:
                session.add(Tenant(id=user.tenant_id, name=user.tenant_id))
                created_tenants.add(user.tenant_id)
            else:
                assert tenant.is_active, "Success fixture cannot reactivate a tenant"
            current = session.get(User, user.id)
            if current is None:
                session.add(user)
                created_users.add(user.id)
                session.commit()
                session.refresh(user)
                session.expunge(user)
            else:
                assert (current.tenant_id, current.username, current.role,
                        current.token_version, current.is_active) == (
                            user.tenant_id, user.username, user.role,
                            user.token_version, user.is_active)
                session.commit()
        return user

    yield persist
    with Session(engine) as session:
        for user_id in created_users:
            row = session.get(User, user_id)
            if row is not None:
                session.delete(row)
        session.commit()
        for tenant_id in created_tenants:
            tenant = session.get(Tenant, tenant_id)
            if tenant is not None:
                session.delete(tenant)
        session.commit()
