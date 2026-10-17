"""RAG-032 CLI 级端到端覆盖：`scripts/rebuild_embedding_index.py` 五个子命令（复审 M-06）。

H-07（脚本自造身份、deployment 恒空，导致 prepare → activate 在默认配置下必然
rc=3）之所以能溜过首轮整改，根因就是**整个 tests/ 从来没有人调用过这个脚本**。
这里把五个子命令全部钉住：走 ``argparse`` 的 ``main()``，用 monkeypatch 换掉
provider（不连任何真实服务），对 rc 与 stdout / stderr 原文做断言。

默认配置的特征必须被覆盖到：provider 声明 ``base_url`` 时，运行时身份会派生出
非空的 ``deployment``——脚本登记出来的行必须带着同一个 deployment，否则 activate
永远比对不过。
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlmodel import Session, col, select

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import engine, init_db  # noqa: E402
from app.models.rag import (  # noqa: E402
    Document,
    DocumentChunk,
    EmbeddingIndex,
    IndexStatus,
)
from app.rag.embeddings.base import EmbeddingInputPolicy, EmbeddingProvider  # noqa: E402
from app.rag.index_identity import identity_from_provider  # noqa: E402
from app.rag.index_registry import active_index, ensure_index  # noqa: E402
from app.rag.service import RAGService  # noqa: E402
from app.rag.vectorstore.local import LocalVectorStore  # noqa: E402
from scripts import rebuild_embedding_index as cli  # noqa: E402

# 仓库默认配置的 OpenAI 兼容端点（app/core/config.py 的 EMBEDDING_BASE_URL 默认值）。
# 身份从这里派生 deployment，是本批全部用例的场景前提。
_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class _StubProvider(EmbeddingProvider):
    """离线 provider 桩：声明 base_url（身份要派生 deployment）+ 无限输入策略。

    必须显式声明离线无限输入策略，否则 RAGService 会因「输入上限未核实」把所有
    分块标成 not_vectorized，检索侧就没有向量可比。
    """

    def __init__(self, model: str, dim: int = 8, base_url: str = _DEFAULT_BASE_URL) -> None:
        self.model = model
        self.dim = dim
        self.base_url = base_url
        self.input_policy = EmbeddingInputPolicy(
            max_input_tokens=None,
            counting_method="offline-unlimited",
            source="test-fixture",
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.1] * self.dim for _ in texts]  # type: ignore[return-value]


@pytest.fixture
def runtime_provider(monkeypatch: pytest.MonkeyPatch):
    """把当前「生效 provider」挂到一个可换位的盒子上，供脚本的 import 链取用。

    脚本内部是函数级 ``from app.rag.embeddings.factory import get_embedding_provider``，
    因此 patch 工厂模块的属性即可生效。
    """
    init_db()
    box: dict[str, EmbeddingProvider] = {"provider": _StubProvider("model-a")}
    monkeypatch.setattr(
        "app.rag.embeddings.factory.get_embedding_provider", lambda: box["provider"]
    )
    # provider 标签取自配置，钉死以免其它卡调整默认配置后身份键漂移。
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
    return box


@pytest.fixture
def session():
    init_db()
    db = Session(engine)
    for row in db.exec(select(EmbeddingIndex)).all():
        db.delete(row)
    db.commit()
    yield db
    for chunk in db.exec(select(DocumentChunk)).all():
        db.delete(chunk)
    for doc in db.exec(select(Document)).all():
        db.delete(doc)
    for row in db.exec(select(EmbeddingIndex)).all():
        db.delete(row)
    db.commit()
    db.close()


def _cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: Sequence[str],
) -> tuple[int, str, str]:
    """跑一次 CLI：返回 (rc, stdout, stderr)。"""
    monkeypatch.setattr(sys, "argv", ["rebuild_embedding_index.py", *argv])
    code = cli.main()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _index_by_key(session: Session, key: str) -> EmbeddingIndex:
    row = session.exec(
        select(EmbeddingIndex).where(col(EmbeddingIndex.identity_key) == key)
    ).first()
    assert row is not None, f"登记表中找不到身份 {key}"
    return row


def _ingest(session: Session, provider: EmbeddingProvider, text: str = "甲内容乙内容") -> Document:
    """真实摄取一篇文档（会落摄取快照，供 rebuild 回放）。"""
    tenant = f"cli-{uuid.uuid4().hex[:8]}"

    async def run() -> Document:
        service = RAGService(session, tenant, embedding_provider=provider)
        return await service.ingest_text(text, title="甲文档", source="s", user_id="u1")

    return asyncio.run(run())


def _search(session: Session, provider: EmbeddingProvider, tenant: str, text: str):
    return asyncio.run(
        LocalVectorStore(session).hybrid_search(
            [0.1] * provider.dim,
            [text],
            tenant,
            5,
            identity=identity_from_provider(provider),
        )
    )


# ── prepare ───────────────────────────────────────────────


def test_prepare_registers_runtime_deployment_not_empty_string(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """H-07 登记主动脉：脚本 prepare 出来的身份必须带**非空 deployment**。

    此前 ``_identity()`` 写死 ``deployment=""``，而运行时由 base_url 派生出
    ``https://dashscope.aliyuncs.com/compatible-mode/v1``，两者永不相等 →
    prepare 成功（rc=0）但登记的是错身份，activate 必然 rc=3。
    """
    provider = _StubProvider("text-embedding-v3", 1024)
    runtime_provider["provider"] = provider
    runtime = identity_from_provider(provider)
    assert runtime.deployment == _DEFAULT_BASE_URL, "前提：默认配置下 deployment 非空"

    rc, out, err = _cli(
        monkeypatch, capsys,
        ["prepare", "--model", "text-embedding-v3", "--dim", "1024", "--json"],
    )

    assert rc == 0, err
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["identity"]["deployment"] == _DEFAULT_BASE_URL
    assert payload["identity"]["model"] == "text-embedding-v3"
    assert payload["identity_key"] == runtime.key()
    assert payload["fingerprint"] == runtime.fingerprint()
    assert payload["id"], "L-05：--json 必须输出真实登记内容，不能是 {}"
    assert payload["status"] == IndexStatus.PREPARING.value
    row = _index_by_key(session, runtime.key())
    assert row.deployment == _DEFAULT_BASE_URL


def test_prepare_refuses_a_model_that_differs_from_runtime(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """H-07 修法：--model/--dim/--version 是对当前 provider 的**断言**，不是另造身份。"""
    runtime_provider["provider"] = _StubProvider("text-embedding-v3", 1024)

    rc, out, err = _cli(monkeypatch, capsys, ["prepare", "--model", "text-embedding-v4"])

    assert rc == 3
    assert "目标身份与当前生效 provider 不一致" in err
    assert "--model=text-embedding-v4" in err
    assert "text-embedding-v3" in err, "错误信息必须给出实际值，运维才知道该改什么"
    # 拒绝时不得留下半个 preparing 登记行
    assert session.exec(select(EmbeddingIndex)).all() == []


# ── rebuild → activate → rollback 主流程 ─────────────────────


def test_rebuild_activate_rollback_full_lifecycle(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """模块 docstring 宣称的主流程：prepare → rebuild --apply → activate → rollback。

    这条链路在 H-07 修复前走不通：prepare 登记的身份缺 deployment，activate 必拒。
    """
    old_provider = _StubProvider("model-a")
    runtime_provider["provider"] = old_provider
    doc = _ingest(session, old_provider)
    old_index = active_index(session)
    assert old_index is not None
    old_chunks = session.exec(
        select(DocumentChunk).where(col(DocumentChunk.index_id) == old_index.id)
    ).all()
    assert old_chunks

    # 1) 切模型后登记新索引（旧索引继续服务）
    new_provider = _StubProvider("model-b")
    runtime_provider["provider"] = new_provider
    rc, out, err = _cli(monkeypatch, capsys, ["prepare", "--json"])
    assert rc == 0, err
    target_id = json.loads(out.strip().splitlines()[-1])["id"]
    target = session.get(EmbeddingIndex, target_id)
    assert target is not None and target.status == IndexStatus.PREPARING.value
    assert active_index(session).id == old_index.id, "prepare 不得切换读路径"

    # 2) 全量重建到目标索引
    rc, out, err = _cli(monkeypatch, capsys, ["rebuild", "--apply"])
    assert rc == 0, err
    assert "成功 1，失败 0" in out
    session.refresh(target)
    assert target.chunk_count >= 1
    assert target.status == IndexStatus.PREPARING.value
    assert active_index(session).id == old_index.id, "重建期间旧索引必须继续服务"

    # 3) 显式激活
    rc, out, err = _cli(monkeypatch, capsys, ["activate", "--index-id", target_id])
    assert rc == 0, err
    assert target.name in out
    session.refresh(target)
    assert target.status == IndexStatus.ACTIVE.value
    session.refresh(old_index)
    assert old_index.status == IndexStatus.RETIRED.value
    assert [h.id for h in _search(session, new_provider, doc.tenant_id, "甲内容")]

    # 4) 回退：必须先把配置切回旧模型，否则脚本会拒绝
    runtime_provider["provider"] = old_provider
    rc, out, err = _cli(monkeypatch, capsys, ["rollback", "--index-id", old_index.id])
    assert rc == 0, err
    assert active_index(session).id == old_index.id
    assert [
        h.id
        for h in _search(session, old_provider, doc.tenant_id, "甲内容")
        if h.id in {c.id for c in old_chunks}
    ], "回退后旧向量的分块必须真的能命中"


def test_rollback_is_refused_when_config_still_points_to_new_model(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """ADR-0008：回退必须配套模型配置，不能只改集合名。"""
    old_provider = _StubProvider("model-a")
    runtime_provider["provider"] = old_provider
    _ingest(session, old_provider)
    old_index = active_index(session)
    assert old_index is not None

    runtime_provider["provider"] = _StubProvider("model-b")
    rc, out, err = _cli(monkeypatch, capsys, ["rollback", "--index-id", old_index.id])

    assert rc == 3
    assert "索引身份错误" in err
    assert active_index(session).id == old_index.id


# ── activate 的三道拒绝门（CLI 层）────────────────────────


def _prepared_index_with_chunks(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys, new_provider
) -> EmbeddingIndex:
    """准备一个「已重建完成」的目标索引：prepare + rebuild --apply。"""
    runtime_provider["provider"] = new_provider
    rc, out, err = _cli(monkeypatch, capsys, ["prepare", "--json"])
    assert rc == 0, err
    index_id = json.loads(out.strip().splitlines()[-1])["id"]
    rc, out, err = _cli(monkeypatch, capsys, ["rebuild", "--apply"])
    assert rc == 0, err
    return session.get(EmbeddingIndex, index_id)


def test_activate_rejects_empty_index_via_cli(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """prepare 出来的索引还没写数据 → 不得激活（激活等于静默 no_hit）。"""
    runtime_provider["provider"] = _StubProvider("model-a")
    _ingest(session, runtime_provider["provider"])
    active = active_index(session)
    assert active is not None

    new_provider = _StubProvider("model-b")
    runtime_provider["provider"] = new_provider
    rc, out, err = _cli(monkeypatch, capsys, ["prepare", "--json"])
    assert rc == 0, err
    index_id = json.loads(out.strip().splitlines()[-1])["id"]

    rc, out, err = _cli(monkeypatch, capsys, ["activate", "--index-id", index_id])

    assert rc == 3
    assert "空索引" in err
    assert active_index(session).id == active.id


def test_activate_rejects_failed_index_via_cli(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    runtime_provider["provider"] = _StubProvider("model-a")
    _ingest(session, runtime_provider["provider"])
    active = active_index(session)
    assert active is not None

    target = _prepared_index_with_chunks(
        session, runtime_provider, monkeypatch, capsys, _StubProvider("model-b")
    )
    target.status = IndexStatus.FAILED.value
    target.notes = "重建失败测试"
    session.add(target)
    session.commit()

    rc, out, err = _cli(monkeypatch, capsys, ["activate", "--index-id", target.id])

    assert rc == 3
    assert "failed" in err
    assert active_index(session).id == active.id


def test_activate_rejects_identity_drift_via_cli(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """目标索引是按 model-b 重建的，配置切回 model-a 后激活必须被拒。"""
    runtime_provider["provider"] = _StubProvider("model-a")
    _ingest(session, runtime_provider["provider"])
    active = active_index(session)
    assert active is not None

    target = _prepared_index_with_chunks(
        session, runtime_provider, monkeypatch, capsys, _StubProvider("model-b")
    )
    runtime_provider["provider"] = _StubProvider("model-a")

    rc, out, err = _cli(monkeypatch, capsys, ["activate", "--index-id", target.id])

    assert rc == 3
    assert "索引身份错误" in err
    assert target.identity_key in err and identity_from_provider(
        runtime_provider["provider"]
    ).key() in err
    assert active_index(session).id == active.id


def test_rebuild_failure_keeps_old_index_and_marks_target_failed(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """重建失败必须是「本次失败 + 旧索引不变」，不能污染读路径。"""
    runtime_provider["provider"] = _StubProvider("model-a")
    doc = _ingest(session, runtime_provider["provider"])
    old_index = active_index(session)
    assert old_index is not None
    before = len(
        session.exec(
            select(DocumentChunk).where(col(DocumentChunk.index_id) == old_index.id)
        ).all()
    )
    # 抽掉快照 → rebuild 无法回放该文档
    from app.models.rag import DocumentIngestionSnapshot

    snap = session.get(DocumentIngestionSnapshot, doc.id)
    assert snap is not None
    session.delete(snap)
    session.commit()

    new_provider = _StubProvider("model-b")
    runtime_provider["provider"] = new_provider
    rc, out, err = _cli(monkeypatch, capsys, ["prepare", "--json"])
    assert rc == 0, err
    index_id = json.loads(out.strip().splitlines()[-1])["id"]

    rc, out, err = _cli(monkeypatch, capsys, ["rebuild", "--apply"])

    assert rc == 1
    assert "无摄取快照" in err
    target = session.get(EmbeddingIndex, index_id)
    assert target is not None and target.status == IndexStatus.FAILED.value
    assert active_index(session).id == old_index.id
    assert (
        len(
            session.exec(
                select(DocumentChunk).where(col(DocumentChunk.index_id) == old_index.id)
            ).all()
        )
        == before
    ), "重建失败不得动旧索引名下的分块"


def test_rebuild_default_is_dry_run(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """不加 --apply 只打印计划：不调用嵌入接口，也不写任何索引行。"""
    runtime_provider["provider"] = _StubProvider("model-a")
    _ingest(session, runtime_provider["provider"])

    rc, out, err = _cli(monkeypatch, capsys, ["rebuild"])

    assert rc == 0
    assert "未执行" in out
    assert len(session.exec(select(EmbeddingIndex)).all()) == 1, "干跑不得登记新索引"


# ── adopt ─────────────────────────────────────────────────


def _legacy_chunk(session: Session, tenant: str, dim: int = 8) -> DocumentChunk:
    doc_id = f"d-{uuid.uuid4().hex[:8]}"
    session.add(
        Document(
            id=doc_id,
            tenant_id=tenant,
            user_id="u1",
            title="历史",
            source="s",
            content_hash=f"h-{uuid.uuid4().hex[:8]}",
            is_current=True,
            chunk_count=1,
        )
    )
    row = DocumentChunk(
        id=f"c-{uuid.uuid4().hex[:8]}",
        tenant_id=tenant,
        document_id=doc_id,
        content="历史内容",
        source="s",
        embedding=json.dumps([0.1] * dim),
        tokens=json.dumps(["历史"]),
        index_id=None,
    )
    session.add(row)
    session.commit()
    return row


def test_adopt_cli_requires_confirm_evidence_and_matching_declared_model(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """adopt 三道门都在 CLI 层：确认 → 证据 → 声明模型一致。"""
    provider = _StubProvider("model-a")
    runtime_provider["provider"] = provider
    _ingest(session, provider)
    active = active_index(session)
    assert active is not None
    tenant = f"cli-adopt-{uuid.uuid4().hex[:8]}"
    legacy = _legacy_chunk(session, tenant)

    rc, out, err = _cli(monkeypatch, capsys, ["adopt"])
    assert rc == 1
    assert "--confirm" in out

    rc, out, err = _cli(monkeypatch, capsys, ["adopt", "--confirm"])
    assert rc == 3 and "--evidence" in err

    rc, out, err = _cli(
        monkeypatch, capsys,
        ["adopt", "--confirm", "--evidence", "ops", "--declared-model", "model-z"],
    )
    assert rc == 3 and "model-z" in err

    rc, out, err = _cli(
        monkeypatch, capsys,
        [
            "adopt", "--confirm",
            "--evidence", "声明人=张三; 依据=部署记录; 原模型=model-a v1",
            "--declared-model", "model-a",
        ],
    )
    assert rc == 0, err
    assert "证据已写入索引 notes" in out
    session.refresh(active)
    assert active.notes is not None and "声明人=张三" in active.notes
    session.refresh(legacy)
    assert legacy.index_id == active.id


# ── status ────────────────────────────────────────────────


def test_status_prints_active_index_and_legacy_count(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    runtime_provider["provider"] = _StubProvider("model-a")
    _ingest(session, runtime_provider["provider"])
    active = active_index(session)
    assert active is not None
    _legacy_chunk(session, f"cli-status-{uuid.uuid4().hex[:8]}")

    rc, out, err = _cli(monkeypatch, capsys, ["status"])

    assert rc == 0
    assert f"active={active.name}" in out
    assert "legacy_chunks=1" in out
    assert IndexStatus.ACTIVE.value in out


# ── 库练习：activate 的检索校验（M-09）在 CLI 层的表现 ─────────


def test_activate_refuses_index_whose_chunks_are_unreachable_via_cli(
    session: Session, runtime_provider: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """目标索引名下有分块但检索一条都命中不了 → CLI 层就必须拒绝激活。"""
    provider = _StubProvider("model-a")
    runtime_provider["provider"] = provider
    _ingest(session, provider)
    old_index = active_index(session)
    assert old_index is not None

    new_provider = _StubProvider("model-b")
    runtime_provider["provider"] = new_provider
    target = ensure_index(session, identity_from_provider(new_provider))
    session.commit()
    # 目标索引名下的分块挂在「已下线」文档上：名字之间存在，检索一条都出不来
    tenant = f"cli-dead-{uuid.uuid4().hex[:8]}"
    doc = Document(
        id=f"d-{uuid.uuid4().hex[:8]}",
        tenant_id=tenant,
        user_id="u1",
        title="下线文档",
        source="s",
        content_hash=f"h-{uuid.uuid4().hex[:8]}",
        is_current=False,
        chunk_count=1,
    )
    session.add(doc)
    session.add(
        DocumentChunk(
            id=f"c-{uuid.uuid4().hex[:8]}",
            tenant_id=tenant,
            document_id=doc.id,
            content="拿不到的内容",
            source="s",
            embedding=json.dumps([0.1] * 8),
            tokens=json.dumps(["拿不到"]),
            index_id=target.id,
        )
    )
    session.commit()

    rc, out, err = _cli(monkeypatch, capsys, ["activate", "--index-id", target.id])

    assert rc == 3
    assert "抽样检索命中数为 0" in err
    assert target.deployment == _DEFAULT_BASE_URL, "前提：目标身份带 runtime deployment"
    assert active_index(session).id == old_index.id, "拒绝后必须完好保留旧的切换状态"
