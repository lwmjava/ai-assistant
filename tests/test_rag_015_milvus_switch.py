from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from app.core.database import engine, init_db
from scripts.milvus_switch_check import (
    AcceptanceCheckError,
    _diagnostic,
    _run_counterexample_child,
    assert_l2_normalized,
    assert_local_preserved,
    assert_milvus_contains,
    assert_milvus_dimension_matches,
    assert_milvus_recall_not_silently_empty,
    assert_milvus_row_count_matches_sql,
    assert_similarity_not_placeholder,
    assert_sparse_side_not_empty,
    compare_results,
    counterexample_scenarios,
    dry_run_plan,
)

_FAKE_PORT = 19531


@pytest.fixture()
def session():
    init_db()
    with Session(engine) as s:
        yield s


def test_dry_run_only_prints_plan_and_does_not_create_report(tmp_path: Path) -> None:
    report = tmp_path / "report.json"

    plan = dry_run_plan("http://127.0.0.1:19530", report)

    assert plan["mode"] == "dry-run"
    assert plan["writes_performed"] is False
    assert "--apply" in plan["apply_command"]
    assert not report.exists()


def test_milvus_presence_requires_rows_from_milvus_not_sql_only() -> None:
    with pytest.raises(AcceptanceCheckError, match="仅有 DocumentChunk 不能算 Milvus 写入通过"):
        assert_milvus_contains(["sql-chunk-a"], [])


def test_milvus_presence_rejects_partial_collection_write() -> None:
    with pytest.raises(AcceptanceCheckError, match="missing=.*sql-chunk-b"):
        assert_milvus_contains(
            ["sql-chunk-a", "sql-chunk-b"],
            [{"id": "sql-chunk-a"}],
        )


def test_switch_back_rejects_silent_no_hit_when_local_data_exists() -> None:
    with pytest.raises(AcceptanceCheckError, match="不得静默当成空知识库"):
        assert_local_preserved(
            ["local-document"],
            ["local-chunk"],
            ["local-document"],
            "no_hit",
        )


def test_switch_back_requires_original_local_document_hit() -> None:
    with pytest.raises(AcceptanceCheckError, match="missing=.*local-document"):
        assert_local_preserved(
            ["local-document"],
            ["local-chunk"],
            ["other-document"],
            "ok",
        )


def test_write_vectors_must_be_l2_normalized() -> None:
    assert assert_l2_normalized([[0.6, 0.8]]) == {"min": 1.0, "max": 1.0}
    with pytest.raises(AcceptanceCheckError, match="未按 L2 归一化"):
        assert_l2_normalized([[1.0, 1.0]])


def test_milvus_dimension_must_match_registered_index() -> None:
    assert_milvus_dimension_matches(64, [{"dim": 64}, {"dim": 64}])
    with pytest.raises(AcceptanceCheckError, match="维度不符的向量不可比"):
        assert_milvus_dimension_matches(64, [{"dim": 32}])


def test_milvus_zero_recall_is_reported_as_failure_not_empty_knowledge() -> None:
    with pytest.raises(AcceptanceCheckError, match="不得把检索故障当成空知识库"):
        assert_milvus_recall_not_silently_empty([], 3)


def test_sparse_side_all_zero_is_reported_as_fake_hybrid() -> None:
    with pytest.raises(AcceptanceCheckError, match="稠密单路伪装成混合检索"):
        assert_sparse_side_not_empty([{"logical_id": "a"}], 0)


def test_stale_vectors_break_row_count_against_sql() -> None:
    assert_milvus_row_count_matches_sql(3, 3)
    with pytest.raises(AcceptanceCheckError, match="旧身份残留"):
        assert_milvus_row_count_matches_sql(3, 5)


def test_similarity_fixed_at_one_is_reported_as_placeholder() -> None:
    with pytest.raises(AcceptanceCheckError, match="仍为占位值"):
        assert_similarity_not_placeholder(
            [{"similarity": 1.0}, {"similarity": 1.0}],
        )
    assert_similarity_not_placeholder([{"similarity": 0.9}, {"similarity": 0.4}])


def test_cross_backend_comparison_records_sets_ranks_and_ranges() -> None:
    local = [
        {"logical_id": "a", "score": 0.03, "similarity": 0.9},
        {"logical_id": "b", "score": 0.02, "similarity": 0.1},
    ]
    milvus = [
        {"logical_id": "b", "score": 0.04, "similarity": 1.0},
        {"logical_id": "c", "score": 0.01, "similarity": 1.0},
    ]

    comparison = compare_results(local, milvus)

    assert comparison["only_local"] == ["a"]
    assert comparison["only_milvus"] == ["c"]
    assert comparison["rank_differences"] == {"b": {"local": 2, "milvus": 1}}
    assert comparison["score_range"]["local"] == {"min": 0.02, "max": 0.03}
    assert comparison["similarity_range"]["milvus"] == {"min": 1.0, "max": 1.0}


@pytest.mark.parametrize("scenario", sorted(counterexample_scenarios()))
def test_fake_milvus_counterexample_must_not_pass(scenario: str) -> None:
    """每个假 Milvus 反例都必须让脚本非 0 退出，并给出可诊断错误。

    这些用例不需要真实 Milvus：假库由脚本内部注入，退出码取自在独立子进程里
    真实跑出来的 ``sys.exit`` 值。若某个反例被判为通过，说明脚本存在恒真风险。
    """
    exit_code, report = _run_counterexample_child(scenario, _FAKE_PORT)

    assert exit_code != 0, f"反例 {scenario} 被脚本判为通过（exit=0），脚本存在恒真风险"
    diagnostic = _diagnostic(report)
    assert diagnostic != "no_failure_reported", f"反例 {scenario} 非 0 退出但没有留下可诊断错误"


# ── 真实代码路径的守护（不依赖真实 Milvus） ────────────────────────────
# 上面一组用例验的是「脚本里的断言助手」，这一次验的是 app 侧真实实现。
# 缺了它们，下面两处缺陷只有在**真实 Milvus 可用**时才能被发现，
# 日常 CI（无 Milvus）会一路放行——变异测试已证明这两处此前均无守护。


@pytest.mark.asyncio
async def test_ingest_calls_vector_store_add(session, monkeypatch) -> None:
    """摄取必须真正调用向量库的 ``add()``。

    外部向量库的写入长期没有调用点：本地实现把分块直接落在主库、``add`` 是空操作，
    因此「Milvus 集合里一根向量都没有」在默认后端下完全不可见；这里注入一个记录型
    store，让该缺陷在没有真实 Milvus 时也能被抓到。
    """

    class _RecordingStore:
        def __init__(self) -> None:
            self.added: list[list] = []

        async def add(self, chunks: list) -> None:
            self.added.append(list(chunks))

        async def hybrid_search(self, *args, **kwargs):
            return []

        async def delete_by_document(self, document_id: str, tenant_id: str) -> int:
            return 0

        async def count(self, tenant_id: str) -> int:
            return 0

    from app.rag.embeddings.mock import MockEmbeddingProvider
    from app.rag.service import RAGService

    store = _RecordingStore()
    rag = RAGService(
        session,
        "rag015-write",
        embedding_provider=MockEmbeddingProvider(dim=64),
        vector_store=store,
    )
    doc = await rag.ingest_text(
        "写入链路必须被真正调用，否则外部向量库里一根向量都不会有。" * 20,
        title="写入守护",
        source="rag015",
        user_id="rag015-user",
    )

    assert store.added, "摄取没有调用向量库 add()，外部向量库将永远是空集合"
    rows = store.added[-1]
    assert len(rows) == doc.chunk_count
    assert rows, "没有分块被交给向量库"
    assert all(getattr(row, "embedding", None) for row in rows), "只有带向量的分块该被写入"


@pytest.mark.asyncio
async def test_milvus_similarity_returns_real_cosine_not_placeholder(session, monkeypatch) -> None:
    """Milvus 命中必须携带真实余弦相似度，而不是 1.0 占位。

    占位值会让「所有候选同样相关」这种假信息流向下游排序与检索解释。距离为
    Milvus 在 COSINE 度量下返回的值，由注入的假集合给出互不相同的三个数字。
    """
    from types import SimpleNamespace

    from sqlmodel import select

    from app.models.rag import DocumentChunk
    from app.rag.embeddings.mock import MockEmbeddingProvider
    from app.rag.service import RAGService
    from app.rag.vectorstore import milvus as milvus_module
    from app.rag.vectorstore.milvus import MilvusVectorStore

    tenant = "rag015-sim"
    rag = RAGService(session, tenant, embedding_provider=MockEmbeddingProvider(dim=64))
    doc = await rag.ingest_text(
        "相似度必须来自向量距离。占位会让排序失去意义。三者互不相同便于检测。" * 20,
        title="相似度守护",
        source="rag015",
        user_id="rag015-user",
    )
    chunks = session.exec(
        select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
    ).all()
    assert len(chunks) >= 2, f"期望多块样本以区分真实距离与占位值，实际 {len(chunks)} 块"
    expected = {chunk.id: 0.2 + 0.3 * index for index, chunk in enumerate(chunks)}
    index_id = chunks[0].index_id

    class _FakeCollection:
        indexes: list = []

        def search(self, **kwargs):
            return [[
                SimpleNamespace(entity={"id": chunk_id}, distance=distance)
                for chunk_id, distance in expected.items()
            ]]

    async def _passthrough(fn, *args, **kwargs):
        return fn(*args)

    monkeypatch.setattr(milvus_module, "run_blocking_with_deadline", _passthrough)

    store = MilvusVectorStore(session)
    monkeypatch.setattr(store, "_connect", lambda *a, **k: _FakeCollection())
    monkeypatch.setattr(store, "_resolve_index", lambda *a, **k: SimpleNamespace(id=index_id, name="fake"))

    results = await store.hybrid_search(
        [0.1, 0.2], ["向量"], tenant, len(chunks), identity=None
    )

    assert results, "没有命中，无法检验相似度取值"
    similarities = {result.id: result.similarity for result in results}
    assert len({round(value, 6) for value in similarities.values()}) > 1, (
        "相似度彼此相同，疑似仍是占位值"
    )
    assert similarities == pytest.approx({key: expected[key] for key in similarities})
