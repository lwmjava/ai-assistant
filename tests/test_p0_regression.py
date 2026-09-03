"""P0 缺陷回归测试。

针对安全与正确性缺陷逐条设卡，确保修复不被后续改动回退：
- MCP HTTP 传输的客户端名解析（曾因调用名与导入名不一致必抛 NameError）；
- Milvus 按文档删除的真实性与 schema 校验（曾返回恒为 1 的假计数）；
- 注入阻断的判定语义（曾把 injection_detected 漏在 blocked 之外）；
- 文档删除时的向量清理（曾从未调用，导致向量永久残留）；
- Supervisor 传给 LLM 的消息类型与调研轮数收敛（曾传裸 dict、无迭代上限）；
- 关键操作的审计埋点（生产路径曾零埋点）；
- 限流层的实际接线（曾从未挂载）。

均使用内存替身，不依赖外部服务与真实模型。
"""

import sys
import types

import pytest
from sqlmodel import Session, select

from app.audit.models import AuditAction, AuditLog
from app.core.config import settings
from app.core.database import engine
from app.core.security import Role, hash_password
from app.llm.base import ChatMessage, LLMOptions, LLMProvider
from app.mcp.client import MCPClient
from app.mcp.config import MCPServerConfig
from app.models.user import User
from app.rag.vectorstore.milvus import MilvusUnavailableError, MilvusVectorStore
from app.security.types import SecurityContext
from app.services.chat_service import ChatService


# ── 替身 ────────────────────────────────────────────
class _FakeAsyncCtx:
    """返回固定值的异步上下文管理器，用于替换 MCP 传输。"""

    def __init__(self, value) -> None:
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc_info) -> bool:
        return False


class _RecordingLLM(LLMProvider):
    """记录收到的消息并回固定文本的 LLM 替身。"""

    model = "fake-recording"

    def __init__(self, reply: str = "FINISH") -> None:
        self.replies = reply
        self.calls: list[list] = []

    async def chat(self, messages, options: LLMOptions | None = None) -> str:
        self.calls.append(messages)
        return self.replies

    async def stream_chat(self, messages, options: LLMOptions | None = None):
        for ch in self.replies:
            yield ch


class _FakeField:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeSchema:
    def __init__(self, fields: list[str]) -> None:
        self.fields = [_FakeField(n) for n in fields]


class _FakeMilvusCollection:
    """记录 query / delete 调用的集合替身。"""

    def __init__(self, rows: list[dict], fields: list[str] | None = None) -> None:
        self._rows = rows
        self.schema = _FakeSchema(fields or ["id", "tenant_id", "document_id", "embedding"])
        self.queries: list[str] = []
        self.deletes: list[str] = []
        self.indexes: list = []

    def query(self, expr: str, output_fields: list[str]) -> list[dict]:
        self.queries.append(expr)
        return self._rows

    def delete(self, expr: str) -> None:
        self.deletes.append(expr)

    def create_index(self, field: str, params: dict) -> None:
        self.indexes.append(field)

    def load(self) -> None:
        return None


def _install_fake_mcp(monkeypatch) -> None:
    """向 sys.modules 注入最小 mcp 包，使 connect 无需真实依赖即可走通。"""
    mcp = types.ModuleType("mcp")

    class ClientSession:
        def __init__(self, read, write) -> None:
            self.initialized = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info) -> bool:
            return False

        async def initialize(self) -> None:
            self.initialized = True

    mcp.ClientSession = ClientSession
    mcp.StdioServerParameters = lambda **kwargs: kwargs

    client_pkg = types.ModuleType("mcp.client")
    stdio_mod = types.ModuleType("mcp.client.stdio")
    stdio_mod.stdio_client = lambda params: _FakeAsyncCtx(("r", "w"))
    sse_mod = types.ModuleType("mcp.client.sse")
    sse_mod.sse_client = lambda url, headers=None: _FakeAsyncCtx(("r", "w"))
    streamable_mod = types.ModuleType("mcp.client.streamable_http")
    # 只提供新版名字：若代码仍调用旧名 streamablehttp_client，此处会抛 NameError。
    streamable_mod.streamable_http_client = lambda url, headers=None: _FakeAsyncCtx(
        ("r", "w", "_")
    )

    client_pkg.stdio = stdio_mod
    client_pkg.sse = sse_mod
    client_pkg.streamable_http = streamable_mod
    mcp.client = client_pkg

    for name, module in (
        ("mcp", mcp),
        ("mcp.client", client_pkg),
        ("mcp.client.stdio", stdio_mod),
        ("mcp.client.sse", sse_mod),
        ("mcp.client.streamable_http", streamable_mod),
    ):
        monkeypatch.setitem(sys.modules, name, module)


# ── MCP 传输 ────────────────────────────────────────
async def test_mcp_http_transport_uses_imported_client_name(monkeypatch) -> None:
    """HTTP 传输必须调用已导入的 streamable_http_client，而非未定义的旧名。"""
    _install_fake_mcp(monkeypatch)
    client = MCPClient(
        MCPServerConfig(name="demo", transport="http", url="http://mcp.test/mcp")
    )
    await client.connect()
    assert client.connected is True


async def test_mcp_sse_transport_connects(monkeypatch) -> None:
    """SSE 传输同样应能建立会话。"""
    _install_fake_mcp(monkeypatch)
    client = MCPClient(
        MCPServerConfig(name="demo", transport="sse", url="http://mcp.test/sse")
    )
    await client.connect()
    assert client.connected is True


# ── Milvus 按文档删除 ───────────────────────────────
def _milvus_store_with(rows: list[dict], fields: list[str] | None = None):
    store = MilvusVectorStore.__new__(MilvusVectorStore)
    store.session = None
    store._collection = _FakeMilvusCollection(rows, fields)
    return store


async def test_milvus_delete_returns_real_count() -> None:
    """删除计数必须来自实际命中的分块，不能恒为 1。"""
    store = _milvus_store_with([{"id": "c1"}, {"id": "c2"}, {"id": "c3"}])
    removed = await store.delete_by_document("doc-1", "tenant-1")
    assert removed == 3
    assert store._collection.deletes, "命中后必须真正发起删除"


async def test_milvus_delete_no_hit_skips_delete() -> None:
    """无命中时返回 0 且不下发删除请求。"""
    store = _milvus_store_with([])
    removed = await store.delete_by_document("doc-1", "tenant-1")
    assert removed == 0
    assert store._collection.deletes == []


async def test_milvus_delete_uses_document_id_field() -> None:
    """删除表达式必须作用于 document_id 字段，否则向量会永久残留。"""
    store = _milvus_store_with([{"id": "c1"}])
    await store.delete_by_document("doc-1", "tenant-1")
    expr = store._collection.deletes[0]
    assert 'document_id == "doc-1"' in expr
    assert 'tenant_id == "tenant-1"' in expr


def _install_fake_pymilvus(monkeypatch, collection) -> None:
    """注入最小 pymilvus 包：带 schema 构造时抛错，模拟「集合已存在」分支。"""
    pymilvus = types.ModuleType("pymilvus")

    class FieldSchema:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class CollectionSchema:
        def __init__(self, fields, description: str = "") -> None:
            self.fields = fields

    class DataType:
        VARCHAR = "VARCHAR"
        FLOAT_VECTOR = "FLOAT_VECTOR"

    class connections:  # noqa: N801 - 对齐 pymilvus 的模块级接口命名
        @staticmethod
        def connect(**kwargs) -> None:
            return None

    def Collection(name, schema=None):  # noqa: N802 - 对齐 pymilvus 的工厂命名
        if schema is not None:
            raise RuntimeError(f"collection {name} already exists")
        return collection

    pymilvus.FieldSchema = FieldSchema
    pymilvus.CollectionSchema = CollectionSchema
    pymilvus.DataType = DataType
    pymilvus.connections = connections
    pymilvus.Collection = Collection
    monkeypatch.setitem(sys.modules, "pymilvus", pymilvus)


async def test_milvus_rejects_collection_without_document_id(monkeypatch) -> None:
    """缺少 document_id 的旧集合必须显式报错，而不是静默失败。

    必须走真实的 _connect 路径：schema 校验发生在集合加载阶段，
    直接替换 _connect 会绕过校验，等于没给这条防线设卡。
    """
    legacy = _FakeMilvusCollection([], fields=["id", "tenant_id", "embedding"])
    _install_fake_pymilvus(monkeypatch, legacy)

    store = MilvusVectorStore.__new__(MilvusVectorStore)
    store.session = None
    store._collection = None

    with pytest.raises(MilvusUnavailableError, match="document_id"):
        await store.delete_by_document("doc-1", "tenant-1")


# ── 安全阻断语义 ────────────────────────────────────
def test_injection_not_blocking_by_default() -> None:
    """默认仅告警：检测到注入不阻断，避免线上行为突变。"""
    ctx = SecurityContext(injection_detected=True, injection_confidence=0.9)
    assert ctx.blocked is False


def test_injection_blocks_when_switch_enabled(monkeypatch) -> None:
    """开启开关后，注入必须进入阻断判定。"""
    monkeypatch.setattr(settings, "SECURITY_BLOCK_ON_INJECTION", True)
    ctx = SecurityContext(injection_detected=True)
    assert ctx.blocked is True


def test_rate_limited_always_blocks() -> None:
    """限流不受注入开关影响，命中即阻断。"""
    assert SecurityContext(rate_limited=True).blocked is True


# ── 向量清理接线 ────────────────────────────────────
async def test_delete_document_cleans_vectors(session: Session) -> None:
    """删除文档必须调用向量库清理，且先于主库记录删除。"""
    from app.models.rag import Document
    from app.rag.vectorstore.base import VectorStore

    class _RecordingStore(VectorStore):
        def __init__(self) -> None:
            self.deleted: list[tuple[str, str]] = []

        async def add(self, chunks: list) -> None:
            return None

        async def hybrid_search(self, *args, **kwargs):
            return []

        async def delete_by_document(self, document_id: str, tenant_id: str) -> int:
            self.deleted.append((document_id, tenant_id))
            return 1

        async def count(self, tenant_id: str) -> int:
            return 0

    from app.rag.service import RAGService

    owner = User(
        id="vec-owner",
        tenant_id="vec-tenant",
        username="vec-owner",
        hashed_password="",
        role=Role.TENANT_ADMIN.value,
        token_version=0,
        is_active=True,
    )
    doc = Document(tenant_id=owner.tenant_id, user_id=owner.id, title="待删", chunk_count=0)
    session.add(doc)
    session.commit()

    store = _RecordingStore()
    rag = RAGService(session, owner.tenant_id, vector_store=store)
    assert await rag.delete_document(doc.id, owner) is True
    assert store.deleted == [(doc.id, owner.tenant_id)]


# ── Supervisor 类型与收敛 ───────────────────────────
async def test_supervisor_sends_chatmessage_objects() -> None:
    """传给 LLM 的必须是 ChatMessage，裸 dict 会让 provider 抛 AttributeError。"""
    from app.agents.supervisor import SupervisorGraph

    graph = SupervisorGraph.__new__(SupervisorGraph)
    llm = _RecordingLLM("FINISH")
    graph.llm = llm
    graph.options = LLMOptions()
    graph.max_revisions = 2

    await graph._node_supervisor({"user_input": "你好", "plan": "", "research": ""})
    assert llm.calls, "supervisor 节点应发起一次 LLM 调用"
    assert all(isinstance(m, ChatMessage) for m in llm.calls[0])


async def test_supervisor_draft_sends_chatmessage_objects() -> None:
    """draft 节点同样必须使用 ChatMessage。"""
    from app.agents.supervisor import SupervisorGraph

    graph = SupervisorGraph.__new__(SupervisorGraph)
    llm = _RecordingLLM("最终回答")
    graph.llm = llm
    graph.options = LLMOptions()
    graph.max_revisions = 2

    await graph._node_draft({"user_input": "你好", "plan": "", "research": "调研"})
    assert all(isinstance(m, ChatMessage) for m in llm.calls[0])


def test_supervisor_route_forces_draft_after_max_revisions() -> None:
    """调研轮数用尽后必须强制收敛，否则 supervisor↔research 会无限循环。"""
    from app.agents.supervisor import SupervisorGraph

    graph = SupervisorGraph.__new__(SupervisorGraph)
    graph.max_revisions = 2

    assert graph._route({"next": "research", "revisions": 0}) == "research"
    assert graph._route({"next": "research", "revisions": 2}) == "draft"
    assert graph._route({"next": "draft", "revisions": 5}) == "draft"


# ── 审计埋点 ────────────────────────────────────────
def _create_login_user(session: Session, username: str) -> User:
    """创建登录审计用的测试用户。

    测试库是长期存在的 SQLite 文件，重复执行会因用户名唯一约束失败，
    因此先清理同名用户及其审计日志，保证用例可反复运行。
    """
    existing = session.exec(select(User).where(User.username == username)).first()
    if existing is not None:
        for log in session.exec(
            select(AuditLog).where(AuditLog.user_id == existing.id)
        ).all():
            session.delete(log)
        session.delete(existing)
        session.commit()

    user = User(
        id=f"user-{username}",
        tenant_id="audit-tenant",
        username=username,
        hashed_password=hash_password("pass-12345"),
        role=Role.MEMBER.value,
        token_version=0,
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user


def test_login_failure_writes_audit_log(session: Session) -> None:
    """登录失败必须留痕，否则撞库行为无从发现。"""
    from fastapi.testclient import TestClient

    from app.main import app

    username = "audit-fail-user"
    _create_login_user(session, username)
    with TestClient(app) as client:
        resp = client.post(
            "/api/auth/login", json={"username": username, "password": "wrong-password"}
        )
    assert resp.status_code == 401

    logs = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.USER_LOGIN.value)
    ).all()
    assert any(
        log.details and "success" in log.details and "false" in log.details.lower()
        for log in logs
    ), "失败的登录尝试应写入审计日志"


def test_login_success_writes_audit_log(session: Session) -> None:
    """登录成功同样需要审计留痕。"""
    from fastapi.testclient import TestClient

    from app.main import app

    username = "audit-ok-user"
    user = _create_login_user(session, username)
    with TestClient(app) as client:
        resp = client.post(
            "/api/auth/login", json={"username": username, "password": "pass-12345"}
        )
    assert resp.status_code == 200

    logs = session.exec(
        select(AuditLog).where(
            AuditLog.action == AuditAction.USER_LOGIN.value,
            AuditLog.user_id == user.id,
        )
    ).all()
    assert logs, "成功登录应写入审计日志"


# ── 限流接线 ────────────────────────────────────────
def test_rate_limit_blocks_second_request(monkeypatch) -> None:
    """开启限流后，超出桶容量的请求必须被标记并阻断。"""
    from app.security import reset_security_singletons

    monkeypatch.setattr(settings, "SECURITY_ENABLED", True)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT", True)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT_CAPACITY", 1.0)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT_RATE", 0.0)
    monkeypatch.setattr(settings, "SECURITY_INPUT_FILTER", False)
    monkeypatch.setattr(settings, "SECURITY_INJECTION_DETECTION", False)
    reset_security_singletons()

    user = User(
        id="rl-user",
        tenant_id="rl-tenant",
        username="rl",
        hashed_password="",
        role=Role.MEMBER.value,
        token_version=0,
        is_active=True,
    )
    ctx, _ = ChatService._apply_input_security("第一条", user)
    assert not (ctx and ctx.rate_limited)

    ctx2, _ = ChatService._apply_input_security("第二条", user)
    assert ctx2 is not None and ctx2.rate_limited is True
    assert ctx2.blocked is True

    err = ChatService._rejection_error(ctx2)
    assert err.status_code == 429
    reset_security_singletons()


def test_rejection_error_distinguishes_causes() -> None:
    """限流回 429、内容阻断回 403，避免客户端把限流当成资源不存在。"""
    assert ChatService._rejection_error(SecurityContext(rate_limited=True)).status_code == 429
    assert (
        ChatService._rejection_error(SecurityContext(injection_detected=True)).status_code
        == 403
    )


# ── 脱敏文本生效 ────────────────────────────────────
def test_input_filter_sanitized_text_is_used(monkeypatch) -> None:
    """过滤开启时返回的必须是脱敏文本，否则 PII 仍会进入模型。"""
    from app.security import reset_security_singletons

    monkeypatch.setattr(settings, "SECURITY_ENABLED", True)
    monkeypatch.setattr(settings, "SECURITY_INPUT_FILTER", True)
    monkeypatch.setattr(settings, "SECURITY_INJECTION_DETECTION", False)
    monkeypatch.setattr(settings, "SECURITY_RATE_LIMIT", False)
    reset_security_singletons()

    user = User(
        id="pii-user",
        tenant_id="pii-tenant",
        username="pii",
        hashed_password="",
        role=Role.MEMBER.value,
        token_version=0,
        is_active=True,
    )
    ctx, text = ChatService._apply_input_security("我的手机号是 13800138000", user)
    # 命中敏感内容时上下文不能为 None：一旦过滤内部抛错被兜底吞掉，
    # 整条安全链路会静默降级为「放行原文」，属于最危险的 fail-open。
    assert ctx is not None
    assert ctx.input_flagged is True
    assert "13800138000" not in text
    assert "***" in text
    reset_security_singletons()


@pytest.mark.parametrize(
    "text",
    [
        "我的手机号是13800138000",  # 紧贴中文，无空格
        "联系我13800138000谢谢",  # 前后均为中文
        "手机号：13800138000",  # 中文标点
    ],
)
def test_pii_detected_adjacent_to_cjk(monkeypatch, text: str) -> None:
    """中文语境下 PII 必须能检出。

    Python 的 \\b 是 Unicode 词边界，汉字同属词字符，
    用 \\b 包裹的号码模式在「中文+号码」连写时会完全失效。
    """
    from app.security import InputFilter

    monkeypatch.setattr(settings, "SECURITY_ENABLED", True)
    result = InputFilter().filter(text)
    assert "phone" in result.pii_detected
    assert "13800138000" not in result.sanitized_text


@pytest.fixture()
def session():
    with Session(engine) as s:
        yield s
