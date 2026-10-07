"""RAG-015 local/Milvus 切换验收。

默认只打印计划；显式 ``--apply`` 才会创建临时 SQLite、上传测试语料并访问
Milvus。脚本不会修改 ``.env``，也不会把旧的五门脚本结果当成本次证据。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import socket
import socketserver
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = REPO_ROOT / "evals" / "reports" / "rag-015-local-milvus-switch-20261007.json"
FROZEN_BASELINE = REPO_ROOT / "evals" / "reports" / "rag-v0.1-baseline-20260919.json"
TENANT_ID = "__auth_disabled__"
CORPUS = (
    {
        "logical_id": "cedar-warranty",
        "filename": "cedar-warranty.txt",
        "text": "Cedar orchard warranty requires an amber receipt and lunar serial number.",
    },
    {
        "logical_id": "harbor-maintenance",
        "filename": "harbor-maintenance.txt",
        "text": "Harbor turbine maintenance uses a cobalt wrench and a weekly pressure log.",
    },
    {
        "logical_id": "alpine-calibration",
        "filename": "alpine-calibration.txt",
        "text": "Alpine sensor calibration uses a violet beacon and a quartz reference.",
    },
)
QUERIES = (
    "amber receipt warranty",
    "cobalt wrench maintenance",
    "quartz beacon calibration",
    "unseen neutral phrase",
)

# 反例注入的假 Milvus 向量库（跨会话共享）。脚本结束前必须关掉它的会话：
# SQLAlchemy 的 engine.dispose() 只回收池内连接，会话未 close 时连接仍被占用，
# 临时 SQLite 文件会处于「被占用」状态而导致清理失败。
_SHARED_FAKE_STORES: list[Any] = []


class AcceptanceCheckError(AssertionError):
    """验收判定失败，和基础设施阻塞分开记录。"""


def dry_run_plan(milvus_uri: str, report: Path) -> dict[str, Any]:
    """返回不产生写操作的执行计划。"""
    return {
        "mode": "dry-run",
        "writes_performed": False,
        "milvus_uri": milvus_uri,
        "report": report.as_posix(),
        "steps": [
            "在全新临时 SQLite 中以默认 local 上传固定语料，并断言检索命中",
            "将进程内 RAG_VECTOR_STORE 切为 milvus，上传同一语料并直接查询 Milvus 集合",
            "记录自动写入判定后，显式补种 Milvus 向量，仅用于跨库差异采样",
            "切回 local，联合核对 SQL 分块存在且检索命中，禁止把丢数据当 no_hit",
            "比较两库命中集合、排序、RRF 分数与 similarity 范围并写入新报告",
        ],
        "apply_command": (
            "python scripts/milvus_switch_check.py --apply "
            f"--milvus-uri {milvus_uri}"
        ),
    }


def assert_milvus_contains(expected_chunk_ids: list[str], milvus_rows: list[dict[str, Any]]) -> None:
    """必须以 Milvus 查询结果证明上传向量存在，SQL 行不能替代该证据。"""
    expected = set(expected_chunk_ids)
    observed = {str(row.get("id")) for row in milvus_rows if row.get("id")}
    missing = sorted(expected - observed)
    if not expected:
        raise AcceptanceCheckError("Milvus 路径上传后 SQL 中没有可向量化分块")
    if missing:
        raise AcceptanceCheckError(
            "Milvus 集合缺少上传产生的向量："
            f"expected={len(expected)} observed={len(observed)} missing={missing[:5]}；"
            "仅有 DocumentChunk 不能算 Milvus 写入通过"
        )


def assert_local_preserved(
    expected_document_ids: list[str],
    local_chunk_ids: list[str],
    hit_document_ids: list[str],
    retrieval_status: str,
) -> None:
    """切回 local 后同时证明数据存在且可检索，显式区分 no_hit 与数据丢失。"""
    if not local_chunk_ids:
        raise AcceptanceCheckError("切回 local 后 SQL 中没有原 local 分块：数据已丢失，不得记为普通空知识库")
    if retrieval_status == "no_hit":
        raise AcceptanceCheckError("切回 local 后返回 no_hit，但 SQL 中仍有 local 分块：不得静默当成空知识库")
    missing = sorted(set(expected_document_ids) - set(hit_document_ids))
    if missing:
        raise AcceptanceCheckError(f"切回 local 后未检索到原 local 文档：missing={missing}")


def assert_milvus_dimension_matches(
    expected_dim: int, milvus_rows: list[dict[str, Any]]
) -> None:
    """证明集合里的向量维度与登记索引一致。

    只核对「集合里有几条」会漏掉维度不符：条数对得上但维度不对，检索出来的
    排序同样不可信，因此维度必须单独判。
    """
    if not milvus_rows:
        raise AcceptanceCheckError("集合中没有向量，无法核对维度")
    observed = sorted({int(row.get("dim", 0)) for row in milvus_rows})
    if observed != [expected_dim]:
        raise AcceptanceCheckError(
            f"Milvus 集合向量维度 {observed} 与登记索引 dim={expected_dim} 不一致："
            "维度不符的向量不可比"
        )


def assert_sparse_side_not_empty(hits: list[dict[str, Any]], indexed_chunks_with_tokens: int) -> None:
    """证明稀疏侧词项真的写进去了。

    只写稠密向量、稀疏侧全零时，BM25 每路得分恒为 0，RRF 会退化成「稠密排序
    + 载入顺序」，看起来仍能出结果，实际融合是假的。判据不看命中里的词项
    （ChunkResult 不透出 token），而是看检索取到的分块在 SQL 侧是否带词项。

    Args:
        hits: Milvus 侧检索命中。
        indexed_chunks_with_tokens: 该索引名下 SQL 中带 BM25 词项的分块数。

    Raises:
        AcceptanceCheckError: 有命中但没有任何分块带词项时抛出。
    """
    if not hits:
        raise AcceptanceCheckError("没有命中，无法判定稀疏侧是否为空")
    if indexed_chunks_with_tokens <= 0:
        raise AcceptanceCheckError(
            f"Milvus 检索返回 {len(hits)} 条命中，但索引名下没有任何分块带 BM25 词项："
            "稀疏侧没有生效，融合结果不可信（稠密单路伪装成混合检索）"
        )


def assert_milvus_row_count_matches_sql(expected_count: int, observed_count: int) -> None:
    """证明集合里「属于当前租户」的向量条数与 SQL 分块数一致。

    只按上传产生的 id 查集合会漏掉「集合里还留着旧身份向量」：这些向量按当前
    身份查不到，却会被租户级统计算进去，条数对不上说明清理没做干净。
    """
    if expected_count <= 0:
        raise AcceptanceCheckError("SQL 侧没有已向量化分块，无法核对集合条数")
    if observed_count != expected_count:
        raise AcceptanceCheckError(
            f"Milvus 集合里当前租户的向量条数 {observed_count} 与 SQL 已向量化分块数 "
            f"{expected_count} 不一致：集合里混了不属于当前索引的向量（旧身份残留 / 清理失败）"
        )


def assert_similarity_not_placeholder(hits: list[dict[str, Any]]) -> None:
    """证明 similarity 不是固定占位值。

    ``app/rag/vectorstore/milvus.py`` 当前把 similarity 写死为 1.0。多命中场景
    下如果所有相似度都恒等于 1.0，说明真实度量没有接上，必须失败而不是放行。
    """
    if len(hits) < 2:
        raise AcceptanceCheckError(
            "跨库样本少于 2 条命中，无法判定 similarity 是否为占位值："
            "样本不足以证明真实度量已接上"
        )
    values = [float(hit["similarity"]) for hit in hits]
    if len(set(values)) == 1 and values[0] == 1.0:
        raise AcceptanceCheckError(
            f"Milvus 侧 similarity 恒为 1.0（{len(values)} 条命中），"
            "仍为占位值，不能作为真实余弦相似度"
        )


def assert_milvus_recall_not_silently_empty(
    hits: list[dict[str, Any]], indexed_chunk_count: int
) -> None:
    """SQL 侧已向量化但 Milvus 检索零命中时失败，禁止把故障读成空知识库。"""
    if indexed_chunk_count <= 0:
        raise AcceptanceCheckError("Milvus 索引名下没有已向量化分块，无法判定召回是否静默为空")
    if not hits:
        raise AcceptanceCheckError(
            f"Milvus 索引名下已有 {indexed_chunk_count} 条已向量化分块，检索却零命中："
            "不得把检索故障当成空知识库"
        )


def assert_l2_normalized(embeddings: list[list[float]]) -> dict[str, float | None]:
    """验证所有非零写入向量均为 L2 单位向量，并返回范数范围。"""
    if not embeddings:
        raise AcceptanceCheckError("没有可验证归一化的写入向量")
    norms = [math.sqrt(sum(float(value) ** 2 for value in vector)) for vector in embeddings]
    invalid = [norm for norm in norms if not math.isclose(norm, 1.0, rel_tol=1e-6, abs_tol=1e-6)]
    if invalid:
        raise AcceptanceCheckError(f"写入向量未按 L2 归一化：norms={invalid[:5]}")
    return summarize_range(norms)


def summarize_range(values: list[float]) -> dict[str, float | None]:
    """以稳定 JSON 结构记录分值范围。"""
    if not values:
        return {"min": None, "max": None}
    return {"min": min(values), "max": max(values)}


def compare_results(
    local_hits: list[dict[str, Any]], milvus_hits: list[dict[str, Any]]
) -> dict[str, Any]:
    """比较逻辑命中集合、排序以及两类分数范围。"""
    local_order = [str(hit["logical_id"]) for hit in local_hits]
    milvus_order = [str(hit["logical_id"]) for hit in milvus_hits]
    local_set = set(local_order)
    milvus_set = set(milvus_order)
    shared = sorted(local_set & milvus_set)
    return {
        "local_order": local_order,
        "milvus_order": milvus_order,
        "only_local": sorted(local_set - milvus_set),
        "only_milvus": sorted(milvus_set - local_set),
        "rank_differences": {
            item: {"local": local_order.index(item) + 1, "milvus": milvus_order.index(item) + 1}
            for item in shared
            if local_order.index(item) != milvus_order.index(item)
        },
        "score_range": {
            "local": summarize_range([float(hit["score"]) for hit in local_hits]),
            "milvus": summarize_range([float(hit["score"]) for hit in milvus_hits]),
        },
        "similarity_range": {
            "local": summarize_range([float(hit["similarity"]) for hit in local_hits]),
            "milvus": summarize_range([float(hit["similarity"]) for hit in milvus_hits]),
        },
    }


@dataclass
class _FakeHit:
    """模拟 pymilvus 命中对象：只提供 MilvusVectorStore 实际读取的字段。"""

    id: str
    entity: dict[str, Any]
    distance: float = 0.0


@dataclass
class _FakeMilvusSpec:
    """假 Milvus 的行为开关。全部默认表示「行为正确的假 Milvus」。"""

    zero_rows: bool = False
    drop_ids: bool = False
    upsert_dim_mismatch: bool = False
    write_dim: int | None = None
    search_hits: int | None = None
    stale_rows: bool = False
    sparse_tokens_missing: bool = False
    similarity: float | None = None
    real_similarity: bool = False
    indexes: list[Any] = field(default_factory=list)


class _FakeMilvusCollection:
    """内存版集合，接口与 pymilvus Collection 的调用面一致。

    只用于反例：证明脚本在「端口通但不是 Milvus / 空集合 / 向量不一致 /
    零召回 / similarity 占位」这些假象下都会失败，而不是恒真通过。
    """

    def __init__(self, name: str, dim: int, spec: _FakeMilvusSpec) -> None:
        self.name = name
        self.dim = dim
        self.spec = spec
        self._rows: dict[str, dict[str, Any]] = {}
        self.indexes = spec.indexes
        self._stale_seeded = False

    def flush(self) -> None:
        return None

    def load(self) -> None:
        return None

    def release(self) -> None:
        return None

    def create_index(self, field_name: str, index_params: dict) -> None:
        return None

    def upsert(self, entities: list[dict[str, Any]]) -> None:
        # 维度不符的真实表现是写入被拒；反例要能分别观察「写入被拒」和
        # 「写入成功但维度不对」两种假象，因此由 spec 决定在哪一步暴露。
        dims = sorted({len(entity.get("embedding") or []) for entity in entities})
        if self.spec.upsert_dim_mismatch and any(dim != self.dim for dim in dims):
            raise RuntimeError(
                f"DataNotMatchException: 向量维度 {dims} 与集合 dim={self.dim} 不一致"
            )
        for entity in entities:
            stored = dict(entity)
            if self.spec.write_dim is not None and entity.get("embedding"):
                stored["embedding"] = list(entity["embedding"])[: self.spec.write_dim]
            self._rows[str(entity["id"])] = stored
        self._seed_stale_rows()

    def _seed_stale_rows(self) -> None:
        """在集合里塞进「不属于当前索引身份」的旧向量。

        身份换了但没换集合时，旧身份的向量会留在同一个集合里：按当前身份查
        不到它们，但按租户统计条数时它们会被算进去，于是「集合条数」和
        「SQL 分块数」对不上。真实服务端不会带任何标记，反例只靠条数暴露。
        """
        if not self.spec.stale_rows or self._stale_seeded:
            return
        self._stale_seeded = True
        for offset in range(2):
            stale_id = f"stale-{self.name}-{offset}"
            self._rows[stale_id] = {
                "id": stale_id,
                "tenant_id": TENANT_ID,
                "document_id": "stale-document",
                "index_id": "stale-index",
                "content": "stale vector from a retired identity",
                "source": "stale.txt",
                "tokens": "[]",
                "embedding": [1.0] + [0.0] * (self.dim - 1),
            }

    def query(self, expr: str, output_fields: list[str] | None = None) -> list[dict[str, Any]]:
        """按 id / document_id 表达式返回行；``output_fields`` 做与服务端一致的投影。"""
        if self.spec.zero_rows:
            return []
        if "id in [" in expr:
            requested = re.findall(r'"([^"]+)"', expr)
            candidates = [self._rows.get(item) for item in requested]
        else:
            document_match = re.search(r'document_id == "([^"]+)"', expr)
            wanted = document_match.group(1) if document_match else None
            candidates = [
                row for row in self._rows.values() if wanted is None or row.get("document_id") == wanted
            ]
        rows = [row for row in candidates if row]
        if self.spec.drop_ids and len(rows) > 1:
            rows = rows[: len(rows) // 2]
        projected = [{key: row.get(key) for key in (output_fields or list(row))} for row in rows]
        # ``dim`` 是诊断字段：服务端不会主动回它，调用方须先请求 embedding 字段
        # 再自行计算；维度不符必须能被观察到，否则「条数对得上、维度不对」
        # 这种假象会一路放行。
        for item, source in zip(projected, rows, strict=False):
            item["dim"] = len(source.get("embedding") or [])
        return projected

    def search(self, data, anns_field, param, limit, expr=None, output_fields=None) -> list[list[_FakeHit]]:
        """返回命中：``entity`` 内容与 ``DocumentChunk`` 回查所需字段一致。"""
        if self.spec.search_hits == 0:
            return [[]]
        rows = list(self._rows.values())
        if self.spec.search_hits is not None:
            rows = rows[: self.spec.search_hits]
        return [
            [
                _FakeHit(
                    id=str(row.get("id")),
                    entity={
                        "id": str(row.get("id")),
                        "content": row.get("content", ""),
                        "source": row.get("source"),
                        "document_id": str(row.get("document_id", "")),
                        "tokens": row.get("tokens", "[]"),
                    },
                )
                for row in rows[:limit]
            ]
        ]


def _make_fake_store(session: Any, spec: _FakeMilvusSpec) -> Any:
    """构造「假 Milvus」向量库：真实走 SQL、真实走融合，只替换集合 IO。"""

    from app.rag.vectorstore.milvus import MilvusVectorStore

    class _FakeMilvusStore(MilvusVectorStore):
        def __init__(self, session: Any) -> None:
            super().__init__(session)
            self._fake_collection: _FakeMilvusCollection | None = None

        def _connect(self, identity: Any = None, index: Any = None) -> Any:
            target_index = index if index is not None else self._resolve_index(identity)
            if self._fake_collection is None:
                self._fake_collection = _FakeMilvusCollection(target_index.name, target_index.dim, spec)
            return self._fake_collection

        async def add(self, chunks: list, identity: Any = None) -> None:
            """写链路补齐后的行为：摄取落库后把向量也写进集合。

            当前的 ``app/`` 还没接上这个调用点，因此反例里由假库主动补写，
            用来回答「等 add() 接上之后，脚本能不能验出 similarity 占位」。
            真实调用仍然透传给父类，两侧的写入语义保持一致。
            """
            from sqlmodel import col, select

            from app.models.rag import DocumentChunk
            from app.rag.index_registry import active_index

            if chunks:
                await super().add(chunks, identity)
            index = active_index(self.session, "milvus")
            if index is None:
                return
            rows = self.session.exec(
                select(DocumentChunk).where(col(DocumentChunk.index_id) == index.id)
            ).all()
            await super().add(list(rows), identity)
            if spec.sparse_tokens_missing:
                # 模拟「只写了稠密向量、稀疏侧词项没落库」：SQL 里的 BM25 词项
                # 被清空，融合只剩稠密一路，看起来仍能出结果，实际是假混合检索。
                for row in rows:
                    row.tokens = None
                self.session.commit()

        async def hybrid_search(self, *args: Any, **kwargs: Any) -> list[Any]:
            """按 spec 改写融合前的中间结果，用于放大反例。"""
            results = list(await super().hybrid_search(*args, **kwargs))
            if spec.similarity is not None:
                for item in results:
                    item.similarity = spec.similarity
            elif spec.real_similarity:
                # 真实实现目前把 similarity 写死为 1.0。反例里按「已修好的实现」
                # 回填真实余弦，才能让其它反例被自己的判据抓住，而不是都撞在
                # similarity 占位这一条上。
                query = list(args[0] if args else kwargs.get("query_embedding") or [])
                query_norm = math.sqrt(sum(value * value for value in query)) or 1.0
                for item in results:
                    row = self._fake_collection._rows.get(item.id) if self._fake_collection else None
                    vector = row.get("embedding") if row else None
                    if vector is None:
                        continue
                    dot = sum(float(a) * float(b) for a, b in zip(vector, query, strict=False))
                    norm = math.sqrt(sum(float(value) ** 2 for value in vector)) or 1.0
                    item.similarity = round(dot / (norm * query_norm), 12)
            return results

    return _FakeMilvusStore(session)


def counterexample_scenarios(fake_port: int = 19531) -> dict[str, _FakeMilvusSpec | None]:
    """反例清单：``None`` 表示该场景不使用假 Milvus（改用假端口）。"""
    return {
        "bare_listener_not_milvus": None,
        "collection_exists_but_zero_rows": _FakeMilvusSpec(zero_rows=True),
        "collection_rows_mismatch_dim": _FakeMilvusSpec(drop_ids=True, upsert_dim_mismatch=True),
        "collection_rows_match_but_dim_wrong": _FakeMilvusSpec(write_dim=32),
        "milvus_search_returns_zero_hits": _FakeMilvusSpec(search_hits=0, real_similarity=True),
        "dense_only_sparse_all_zero": _FakeMilvusSpec(sparse_tokens_missing=True, real_similarity=True),
        "collection_has_stale_identity_vectors": _FakeMilvusSpec(
            stale_rows=True, real_similarity=True
        ),
        "similarity_is_placeholder": _FakeMilvusSpec(similarity=1.0),
    }


def _start_bare_listener(port: int) -> tuple[socketserver.ThreadingTCPServer, threading.Thread]:
    """起一个只接受连接、不按 gRPC 应答的裸监听，模拟「端口通但不是 Milvus」。"""

    class _SilentHandler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            return None

    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), _SilentHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _run_counterexample_child(scenario: str, fake_port: int) -> tuple[int, dict[str, Any]]:
    """在独立子进程里跑单个反例，返回该子进程的真实退出码与报告。"""
    import subprocess

    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False, encoding="utf-8") as handle:
        report_path = Path(handle.name)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--apply",
        "--scenario",
        scenario,
        "--fake-port",
        str(fake_port),
        "--milvus-uri",
        f"http://127.0.0.1:{fake_port}",
        "--collection-prefix",
        "rag015_fake_milvus",
        "--report",
        str(report_path),
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    report_path.unlink(missing_ok=True)
    if completed.returncode not in (0, 1):
        raise RuntimeError(
            f"反例子进程异常退出：rc={completed.returncode} stderr={completed.stderr[-500:]}"
        )
    return completed.returncode, payload


def _path_status(report: dict[str, Any], key: str) -> str:
    return str(report.get("paths", {}).get(key, {}).get("status", "missing"))


def _diagnostic(report: dict[str, Any]) -> str:
    """把报告里的失败原因压成一行可诊断文本。"""
    parts = [
        f"{key}={_path_status(report, key)}({report['paths'][key].get('error', '')})"
        for key in ("default_local", "milvus_upload_visible", "switch_back_local")
        if _path_status(report, key) != "pass"
    ]
    cross = report.get("cross_backend", {})
    if cross.get("status") != "completed":
        parts.append(f"cross_backend={cross.get('status')}({cross.get('error', '')})")
    return " | ".join(parts) or "no_failure_reported"


def _counterexample_scenario_spec(name: str) -> _FakeMilvusSpec | None:
    """按名字取反例开关；``None`` 表示不用假 Milvus（裸监听场景）。"""
    return counterexample_scenarios().get(name)


def _install_fake_milvus_hook(spec: _FakeMilvusSpec | None) -> dict[str, Any] | None:
    """把假 Milvus 挂成注入钩子；``None`` 表示保持真实实现。

    假库必须是**全局单实例**：每次 HTTP 请求都是新会话，若按会话新建实例，
    上传写进去的向量在下一步查询里必然查不到，反例会退化成假失败。
    """
    if spec is None:
        return None
    shared: list[Any] = []

    def _make_shared_store(session: Any) -> Any:
        if not shared:
            store = _make_fake_store(session, spec)
            shared.append(store)
            _SHARED_FAKE_STORES.append(store)
        return shared[0]

    return {"make_store": _make_shared_store}


def run_counterexample_scenarios(
    *,
    fake_port: int = 19531,
    report_path: Path | None = None,
    in_process: bool = False,
) -> tuple[int, dict[str, Any]]:
    """逐个跑假 Milvus 反例，断言脚本对每个反例都必须非 0 退出。

    作用：在真实 Milvus 可用之前先堵住「脚本恒真」这个最坏情况——任何假象
    若被判为通过，说明脚本没有真正检验 Milvus。

    每个场景默认在**独立子进程**里跑：TempDB、settings 与向量库实例互不残留，
    退出码是该子进程真实的 ``sys.exit`` 值。``in_process=True`` 用于单测中
    直接调用（此时只关心“是否被判为通过”）。

    入参：fake_port 为裸监听端口；report_path 为反例报告输出路径；
    in_process 为 True 时在当前进程内顺序执行。
    出参：(退出码, 报告)。任一场景被判通过即退出码非 0。
    """
    from app.core.config import settings

    names = list(counterexample_scenarios())
    results: list[dict[str, Any]] = []
    server: socketserver.ThreadingTCPServer | None = None
    for name in names:
        spec = _counterexample_scenario_spec(name)
        if spec is None and in_process:
            server, _thread = _start_bare_listener(fake_port)
        hooks = _install_fake_milvus_hook(spec)
        try:
            if in_process:
                exit_code, report = asyncio.run(
                    run_apply(
                        milvus_uri=f"http://127.0.0.1:{fake_port}",
                        collection_prefix="rag015_fake_milvus",
                        fake_port=fake_port,
                        hooks=hooks,
                    )
                )
            else:
                exit_code, report = _run_counterexample_child(name, fake_port)
        except Exception as exc:  # noqa: BLE001 - 连假 Milvus 都可能抛，仍需记入报告
            exit_code = 1
            report = {
                "paths": {},
                "cross_backend": {"status": "blocked", "error": f"{type(exc).__name__}: {exc}"},
            }
        detected = exit_code != 0
        results.append(
            {
                "scenario": name,
                "fake_spec": None if spec is None else vars(spec),
                "used_fake_milvus": spec is not None,
                "isolation": "in_process" if in_process else "subprocess",
                "exit_code": exit_code,
                "detected": detected,
                "path_statuses": {
                    key: _path_status(report, key)
                    for key in ("default_local", "milvus_upload_visible", "switch_back_local")
                },
                "cross_backend_status": report.get("cross_backend", {}).get("status", "missing"),
                "diagnostic": _diagnostic(report),
            }
        )
        settings.RAG_VECTOR_STORE = "local"
    if server is not None:
        server.shutdown()
        server.server_close()

    undetected = [item["scenario"] for item in results if not item["detected"]]
    payload = {
        "schema_version": "rag-015-fake-milvus-v1",
        "run_at": datetime.now(UTC).isoformat(),
        "fake_port": fake_port,
        "python": sys.version.split()[0],
        "scenarios": results,
        "detected_count": sum(1 for item in results if item["detected"]),
        "undetected_scenarios": undetected,
        "result": "all_detected" if not undetected else "script_can_pass_on_fake_milvus",
    }
    if report_path is not None:
        _write_report(report_path, payload)
    return (0 if not undetected else 1), payload


def _tcp_target(uri: str) -> tuple[str, int]:
    parsed = urlsplit(uri if "://" in uri else f"http://{uri}")
    return parsed.hostname or "127.0.0.1", parsed.port or 19530


def _tcp_error(uri: str) -> str | None:
    host, port = _tcp_target(uri)
    try:
        with socket.create_connection((host, port), timeout=3):
            return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def _ids_expr(ids: list[str]) -> str:
    quoted = ", ".join(json.dumps(item) for item in ids)
    return f"id in [{quoted}]"


def _logical_id(content: str) -> str:
    digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
    for item in CORPUS:
        if hashlib.sha256(item["text"].encode("utf-8")).hexdigest() == digest:
            return str(item["logical_id"])
    return f"unknown:{digest[:12]}"


def _configure_environment(database_path: Path, milvus_uri: str, collection_prefix: str) -> None:
    os.environ["ENV"] = "development"
    os.environ["AUTH_ENABLED"] = "false"
    os.environ["DATABASE_URL"] = f"sqlite:///{database_path.resolve().as_posix()}"
    os.environ["EMBEDDING_PROVIDER"] = "mock"
    os.environ["EMBEDDING_DIM"] = "64"
    os.environ["EMBEDDING_NORMALIZATION"] = "l2"
    os.environ["EMBEDDING_METRIC"] = "cosine"
    os.environ["RAG_VECTOR_STORE"] = "local"
    os.environ["RAG_BACKEND"] = "native"
    os.environ["RAG_CHUNK_STRATEGY"] = "structured"
    os.environ["RAG_CHUNK_SIZE"] = "500"
    os.environ["RAG_CHUNK_OVERLAP"] = "64"
    os.environ["RAG_HYBRID_RRF_K"] = "60"
    os.environ["RAG_EFFECTIVE_DATE_FILTER"] = "false"
    os.environ["MILVUS_URI"] = milvus_uri
    os.environ["MILVUS_COLLECTION"] = collection_prefix
    os.environ["INITIAL_ADMIN_USERNAME"] = ""
    os.environ["INITIAL_ADMIN_PASSWORD"] = ""


async def _upload_corpus(client: Any, backend_label: str, store: Any = None) -> list[dict[str, str]]:
    """上传固定语料；``store`` 给出时模拟写链路已补齐（摄取后同步写外部集合）。"""
    uploaded: list[dict[str, str]] = []
    for item in CORPUS:
        response = client.post(
            "/api/rag/documents/upload",
            files={
                "file": (
                    f"{backend_label}-{item['filename']}",
                    item["text"].encode("utf-8"),
                    "text/plain",
                )
            },
        )
        if response.status_code != 200:
            raise AcceptanceCheckError(
                f"{backend_label} 上传失败：status={response.status_code} body={response.text[:300]}"
            )
        body = response.json()
        uploaded.append({"logical_id": str(item["logical_id"]), "document_id": str(body["id"])})
        if store is not None:
            await store.add([])
    return uploaded


def _http_search(client: Any, query: str, top_k: int = 5) -> tuple[list[dict[str, Any]], str]:
    response = client.post("/api/rag/search", json={"query": query, "top_k": top_k})
    if response.status_code != 200:
        raise AcceptanceCheckError(
            f"检索失败：query={query!r} status={response.status_code} body={response.text[:300]}"
        )
    return list(response.json()), response.headers.get("X-Retrieval-Status", "missing")


def _count_indexed_chunks(session: Any) -> dict[str, int]:
    """统计当前生效 Milvus 索引名下的分块：已向量化数与带 BM25 词项数。"""
    from sqlmodel import col, select

    from app.models.rag import Document, DocumentChunk
    from app.rag.index_registry import active_index

    index = active_index(session, "milvus")
    if index is None:
        return {"vectorized": 0, "with_tokens": 0}
    stmt = (
        select(DocumentChunk)
        .join(Document, col(Document.id) == col(DocumentChunk.document_id))
        .where(col(DocumentChunk.index_id) == index.id)
        .where(col(Document.deleted_at).is_(None))
        .where(col(DocumentChunk.tenant_id) == TENANT_ID)
    )
    rows = list(session.exec(stmt).all())
    return {
        "vectorized": len([row for row in rows if row.embedding]),
        "with_tokens": len([row for row in rows if row.tokens]),
    }


def _load_chunks(session: Any, document_ids: list[str]) -> list[Any]:
    from sqlmodel import col, select

    from app.models.rag import DocumentChunk

    return list(
        session.exec(select(DocumentChunk).where(col(DocumentChunk.document_id).in_(document_ids))).all()
    )


def _make_store(session: Any, hooks: dict[str, Any] | None) -> Any:
    """创建 Milvus 向量库；``hooks['make_store']`` 存在时用它注入假 Milvus。"""
    if hooks and hooks.get("make_store") is not None:
        return hooks["make_store"](session)
    from app.rag.vectorstore.milvus import MilvusVectorStore

    return MilvusVectorStore(session)


def _install_store_factory_hook(hooks: dict[str, Any] | None) -> None:
    """把假 Milvus 注入向量库工厂，使 HTTP 上传与检索也走同一个假实例。

    只在注入了假 Milvus 的反例里调用：生产路径不受影响。两个关键点：

    - **全局单实例**：每次请求都是新会话，按会话缓存会得到多个互不相通的假集合，
      上传写进去的向量在后续查询里必然查不到，反例会退化成假失败。
    - **只在 milvus 后端生效**：local 路径必须仍然走真实 LocalVectorStore，
      否则「默认 local / 切回 local」两条路径也会跑在假 Milvus 上。
    """
    if not hooks or hooks.get("make_store") is None:
        return
    from app.rag import service as rag_service
    from app.rag.vectorstore.factory import get_vector_store

    def _factory(session: Any) -> Any:
        """只在后端切到 milvus 时注入假库；local 两条路径仍用真实本地实现。"""
        from app.core.config import settings

        if settings.RAG_VECTOR_STORE.strip().lower() != "milvus":
            return get_vector_store(session)
        return hooks["make_store"](session)

    rag_service.get_vector_store = _factory  # type: ignore[assignment]


def _uninstall_store_factory_hook(hooks: dict[str, Any] | None) -> None:
    """恢复真实向量库工厂，避免假 Milvus 泄漏到后续场景。"""
    if not hooks or hooks.get("make_store") is None:
        return
    from app.rag import service as rag_service
    from app.rag.vectorstore.factory import get_vector_store

    rag_service.get_vector_store = get_vector_store  # type: ignore[assignment]


async def _search_backend(
    backend: str, queries: tuple[str, ...], hooks: dict[str, Any] | None = None
) -> dict[str, list[dict[str, Any]]]:
    from sqlmodel import Session

    from app.core.config import settings
    from app.core.database import engine
    from app.rag.embeddings.mock import MockEmbeddingProvider, tokenize
    from app.rag.vectorstore.factory import get_vector_store

    settings.RAG_VECTOR_STORE = backend
    provider = MockEmbeddingProvider(dim=64)
    output: dict[str, list[dict[str, Any]]] = {}
    with Session(engine) as session:
        store = get_vector_store(session)
        if backend == "milvus" and hooks and hooks.get("make_store") is not None:
            store = _make_store(session, hooks)
        for query in queries:
            vector = (await provider.embed([query]))[0]
            hits = await store.hybrid_search(
                vector,
                tokenize(query),
                TENANT_ID,
                top_k=len(CORPUS),
                rrf_k=60,
            )
            output[query] = [
                {
                    "logical_id": _logical_id(hit.content),
                    "chunk_id": hit.id,
                    "score": float(hit.score),
                    "similarity": float(hit.similarity),
                }
                for hit in hits
            ]
    return output


async def run_apply(
    *,
    milvus_uri: str = "http://127.0.0.1:19530",
    collection_prefix: str = "rag015_switch_check",
    fake_port: int | None = None,
    hooks: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """执行三条切换路径与跨库采样。

    Args:
        milvus_uri: Milvus 连接地址。
        collection_prefix: 集合名前缀，实际集合名再加本次 run_id。
        fake_port: 反例专用。给出时把 TCP 预检指向该端口（用于「端口通但
            不是 Milvus」这类场景），预检失败也继续跑后续路径并记录真实错误。
        hooks: 反例专用。``make_store`` 存在时用它替换 Milvus 向量库实例。

    Returns:
        (进程退出码, 报告字典)。三条路径全 pass 才返回 0。
    """
    run_id = uuid.uuid4().hex[:10]
    probe_uri = f"http://127.0.0.1:{fake_port}" if fake_port is not None else milvus_uri
    report: dict[str, Any] = {
        "schema_version": "rag-015-switch-v1",
        "run_at": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "milvus_uri": milvus_uri,
        "collection_prefix": f"{collection_prefix}_{run_id}",
        "corpus": list(CORPUS),
        "queries": list(QUERIES),
        "paths": {},
        "cross_backend": {},
        "limitations": [
            "MockEmbeddingProvider 仅用于确定性链路对照，不代表生产语义质量。",
            "范数检查证明本次 Mock 样本为单位向量，不等同于生产写入路径已主动执行 L2 归一化。",
            "Milvus 跨库样本由脚本显式补种以保证样本齐备；上传写链路是否真的写入，由 milvus_upload_visible 单独判定。",
            "Milvus 侧 similarity 取自 pymilvus 返回的 distance（COSINE 度量下的余弦相似度），精度为 float32，与本地 numpy 结果存在约 1e-8 量级差异。",
        ],
    }
    tcp_error = _tcp_error(probe_uri)
    report["milvus_preflight"] = {
        "status": "pass" if tcp_error is None else "blocked",
        "probe_uri": probe_uri,
        "error": tcp_error,
    }
    # 反例要证明「端口通但服务端不是 Milvus」时脚本会失败，因此预检失败也继续。
    milvus_reachable = tcp_error is None or fake_port is not None

    temp_ctx = tempfile.TemporaryDirectory(prefix="rag015-switch-")
    with temp_ctx as temp_dir:
        temp_root = Path(temp_dir)
        database_path = temp_root / "rag015-switch.db"
        _configure_environment(database_path, milvus_uri, report["collection_prefix"])
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))

        from fastapi.testclient import TestClient
        from sqlmodel import Session

        from app.core.config import settings
        from app.core.database import engine, init_db
        from app.main import app
        from app.rag import document_storage

        # 环境变量只在首次导入 settings 时生效；反例循环里同一个进程会跑多次，
        # 因此这里按配置对象再钉一次，避免上一轮的残留值影响本轮。
        settings.AUTH_ENABLED = False
        settings.RAG_VECTOR_STORE = "local"
        settings.MILVUS_URI = milvus_uri
        settings.MILVUS_COLLECTION = report["collection_prefix"]
        document_storage._PROJECT_ROOT = temp_root
        init_db(auto_migrate=False)
        client = TestClient(app)

        settings.RAG_VECTOR_STORE = "local"
        try:
            local_docs = await _upload_corpus(client, "local")
            query_results, retrieval_status = _http_search(client, "amber receipt warranty")
            local_doc_ids = [item["document_id"] for item in local_docs]
            hit_ids = [str(item["document_id"]) for item in query_results]
            if local_doc_ids[0] not in hit_ids:
                raise AcceptanceCheckError(
                    f"默认 local 上传后未命中 cedar-warranty：hits={hit_ids}"
                )
            with Session(engine) as session:
                local_chunks = _load_chunks(session, local_doc_ids)
            local_norm_range = assert_l2_normalized(
                [json.loads(chunk.embedding) for chunk in local_chunks if chunk.embedding]
            )
            report["paths"]["default_local"] = {
                "status": "pass",
                "configured_backend": settings.RAG_VECTOR_STORE,
                "uploaded_document_ids": local_doc_ids,
                "sql_chunk_count": len(local_chunks),
                "embedding_normalization": "l2",
                "embedding_norm_range": local_norm_range,
                "retrieval_status": retrieval_status,
                "hit_document_ids": hit_ids,
            }
        except Exception as exc:  # noqa: BLE001 - 报告要保留其它路径证据
            local_docs = []
            local_chunks = []
            report["paths"]["default_local"] = {
                "status": "fail",
                "error": f"{type(exc).__name__}: {exc}",
            }

        milvus_docs: list[dict[str, str]] = []
        milvus_chunks: list[Any] = []
        collection = None
        if not milvus_reachable:
            report["paths"]["milvus_upload_visible"] = {
                "status": "blocked",
                "error": tcp_error,
            }
        else:
            # 假 Milvus 只在 milvus 阶段注入：HTTP 上传面也走同一个工厂。
            _install_store_factory_hook(hooks)
            settings.RAG_VECTOR_STORE = "milvus"
            try:
                import pymilvus

                report["pymilvus_version"] = pymilvus.__version__
                with Session(engine) as upload_session:
                    upload_store = _make_store(upload_session, hooks) if hooks else None
                    milvus_docs = await _upload_corpus(client, "milvus", upload_store)
                milvus_doc_ids = [item["document_id"] for item in milvus_docs]
                with Session(engine) as session:
                    milvus_chunks = _load_chunks(session, milvus_doc_ids)
                    expected_ids = [chunk.id for chunk in milvus_chunks if chunk.embedding]
                    norm_range = assert_l2_normalized(
                        [json.loads(chunk.embedding) for chunk in milvus_chunks if chunk.embedding]
                    )
                    store = _make_store(session, hooks)
                    collection = store._connect()
                    target_index = store._resolve_index()
                    collection.flush()
                    # 真实服务端的 query 不会默认回传向量；必须先显式请求 embedding，
                    # 再由回传数据算出实际维度，维度判据不能依赖假 Milvus 的注入。
                    rows = [
                        {**row, "dim": len(row.get("embedding") or [])}
                        for row in (
                            collection.query(
                                expr=_ids_expr(expected_ids),
                                output_fields=["id", "document_id", "embedding"],
                            )
                            or []
                        )
                    ]
                    try:
                        assert_milvus_contains(expected_ids, rows)
                        assert_milvus_dimension_matches(target_index.dim, rows)
                        assert_milvus_row_count_matches_sql(
                            len(expected_ids),
                            len(
                                collection.query(
                                    expr=f'tenant_id == "{TENANT_ID}"',
                                    output_fields=["id"],
                                )
                                or []
                            ),
                        )
                    except AcceptanceCheckError as exc:
                        report["paths"]["milvus_upload_visible"] = {
                            "status": "fail",
                            "uploaded_document_ids": milvus_doc_ids,
                            "sql_vectorized_chunk_count": len(expected_ids),
                            "embedding_normalization": "l2",
                            "embedding_norm_range": norm_range,
                            "milvus_matching_vector_count": len(rows),
                            "error": str(exc),
                        }
                    else:
                        report["paths"]["milvus_upload_visible"] = {
                            "status": "pass",
                            "uploaded_document_ids": milvus_doc_ids,
                            "sql_vectorized_chunk_count": len(expected_ids),
                            "embedding_normalization": "l2",
                            "embedding_norm_range": norm_range,
                            "milvus_matching_vector_count": len(rows),
                        }
            except Exception as exc:  # noqa: BLE001 - 连接/版本失败单独报告
                report["paths"]["milvus_upload_visible"] = {
                    "status": "blocked",
                    "error": f"{type(exc).__name__}: {exc}",
                }

        # 切回前必须摘掉假 Milvus 注入：HTTP 检索面也走同一个工厂，
        # 不摘掉会把「切回 local」这条路径跑在假 Milvus 上，得到假结论。
        _uninstall_store_factory_hook(hooks)
        settings.RAG_VECTOR_STORE = "local"
        try:
            local_doc_ids = [item["document_id"] for item in local_docs]
            with Session(engine) as session:
                current_local_chunks = _load_chunks(session, local_doc_ids)
            hits, retrieval_status = _http_search(client, "amber receipt warranty")
            hit_ids = [str(item["document_id"]) for item in hits]
            assert_local_preserved(
                [local_doc_ids[0]] if local_doc_ids else [],
                [chunk.id for chunk in current_local_chunks],
                hit_ids,
                retrieval_status,
            )
            report["paths"]["switch_back_local"] = {
                "status": "pass",
                "sql_chunk_count": len(current_local_chunks),
                "retrieval_status": retrieval_status,
                "hit_document_ids": hit_ids,
                "data_vs_empty_distinguished": True,
            }
        except Exception as exc:  # noqa: BLE001 - 报告要保留 Milvus 结果
            report["paths"]["switch_back_local"] = {
                "status": "fail",
                "data_vs_empty_distinguished": True,
                "error": f"{type(exc).__name__}: {exc}",
            }

        if milvus_reachable and milvus_chunks:
            try:
                settings.RAG_VECTOR_STORE = "milvus"
                with Session(engine) as session:
                    store = _make_store(session, hooks)
                    seeded = _load_chunks(session, [item["document_id"] for item in milvus_docs])
                    await store.add(
                        [
                            SimpleNamespace(
                                id=chunk.id,
                                tenant_id=chunk.tenant_id,
                                document_id=chunk.document_id,
                                content=chunk.content,
                                source=chunk.source,
                                tokens=chunk.tokens,
                                embedding=chunk.embedding,
                            )
                            for chunk in seeded
                            if chunk.embedding
                        ]
                    )
                    collection = store._connect()
                    collection.flush()
                    indexed = _count_indexed_chunks(session)
                local_results = await _search_backend("local", QUERIES, hooks)
                milvus_results = await _search_backend("milvus", QUERIES, hooks)
                indexed_chunks = indexed["vectorized"]
                comparisons = {
                    query: compare_results(local_results[query], milvus_results[query])
                    for query in QUERIES
                }
                report["cross_backend"] = {
                    "status": "completed",
                    "milvus_seed_method": "explicit_store_add_after_acceptance_observation",
                    "counts_as_upload_write_proof": False,
                    "milvus_indexed_chunk_count": indexed_chunks,
                    "milvus_indexed_chunks_with_tokens": indexed["with_tokens"],
                    "queries": comparisons,
                    "queries_with_hit_set_difference": sum(
                        bool(item["only_local"] or item["only_milvus"])
                        for item in comparisons.values()
                    ),
                    "queries_with_rank_difference": sum(
                        bool(item["rank_differences"]) for item in comparisons.values()
                    ),
                }
                # 两类「看着通过了其实没验到」的假象必须在采样阶段就拦下：
                # 索引里有向量却零召回（故障被读成空知识库）、similarity 全为 1.0（占位没修）。
                assert_milvus_recall_not_silently_empty(
                    milvus_results[QUERIES[0]], indexed_chunks
                )
                assert_sparse_side_not_empty(
                    milvus_results[QUERIES[0]], indexed["with_tokens"]
                )
                assert_similarity_not_placeholder(milvus_results[QUERIES[0]])
            except Exception as exc:  # noqa: BLE001 - 差异采样失败不覆盖三路径证据
                report["cross_backend"] = {
                    "status": "blocked",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        else:
            report["cross_backend"] = {
                "status": "blocked",
                "error": tcp_error or "Milvus 上传阶段没有生成可补种的 SQL 分块",
            }

        if collection is not None and not hasattr(collection, "spec"):
            try:
                from pymilvus import utility

                name = collection.name
                if name.startswith(f"{collection_prefix}_{run_id}"):
                    collection.release()
                    utility.drop_collection(name)
                    report["milvus_cleanup"] = {"status": "pass", "collection": name}
                else:
                    report["milvus_cleanup"] = {
                        "status": "refused",
                        "collection": name,
                        "reason": "集合名不属于本次唯一前缀",
                    }
            except Exception as exc:  # noqa: BLE001 - 清理失败必须留痕
                report["milvus_cleanup"] = {
                    "status": "fail",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        _uninstall_store_factory_hook(hooks)
        while _SHARED_FAKE_STORES:
            store = _SHARED_FAKE_STORES.pop()
            try:
                store.session.close()
            except Exception:  # noqa: BLE001 - 假库会话收尾失败不影响结论
                pass
        engine.dispose()

    settings.RAG_VECTOR_STORE = "local"
    report["final_backend"] = settings.RAG_VECTOR_STORE
    path_statuses = [item.get("status") for item in report["paths"].values()]
    # 跨库校验失败同样不能判通过：similarity 占位、零召回这类假象只在这一步暴露，
    # 只看三条路径会把「占位值」和「空知识库假象」一起放过去。
    cross_status = report.get("cross_backend", {}).get("status")
    report["all_acceptance_paths_passed"] = path_statuses == ["pass", "pass", "pass"] and (
        cross_status == "completed"
    )
    report["result"] = "pass" if report["all_acceptance_paths_passed"] else "fail_or_blocked"
    # 临时目录清理失败不应改变验收结论：它只是收尾动作，真正的结果已在 report 里。
    try:
        temp_ctx.cleanup()
    except Exception as exc:  # noqa: BLE001 - 清理失败只记录，不覆盖三条路径结论
        report["temp_cleanup"] = {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}
    else:
        report["temp_cleanup"] = {"status": "pass"}
    return (0 if report["all_acceptance_paths_passed"] else 1), report


def _write_report(path: Path, report: dict[str, Any]) -> None:
    if path.resolve() == FROZEN_BASELINE.resolve():
        raise SystemExit(f"拒绝覆盖冻结基线：{FROZEN_BASELINE}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # 必须按字节写：write_text 在 Windows 上会把 \n 翻成 \r\n，报告每次重生成
    # 都会制造一次整文件 diff（core.autocrlf=false 时 git 不做归一化）。
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    path.write_bytes(payload.encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG-015 local/Milvus 切换验收")
    parser.add_argument("--apply", action="store_true", help="执行真实上传、Milvus 查询与切回检查")
    parser.add_argument("--milvus-uri", default="http://127.0.0.1:19530")
    parser.add_argument("--collection-prefix", default="rag015_switch_check")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--counterexample",
        action="store_true",
        help="跑假 Milvus 反例，证明脚本对每个假象都会失败（不需要真实 Milvus）",
    )
    parser.add_argument("--fake-port", type=int, default=19531, help="反例用的裸监听端口")
    parser.add_argument(
        "--scenario",
        default=None,
        help="单个反例场景；与 --apply 同用时注入对应假 Milvus（反例子进程专用）",
    )
    args = parser.parse_args()

    if not args.apply:
        print(json.dumps(dry_run_plan(args.milvus_uri, args.report), ensure_ascii=False, indent=2))
        return 0

    if args.counterexample:
        exit_code, report = run_counterexample_scenarios(
            fake_port=args.fake_port, report_path=args.report
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"反例报告已写入：{args.report}")
        return exit_code

    hooks = _install_fake_milvus_hook(_counterexample_scenario_spec(args.scenario))
    server: socketserver.ThreadingTCPServer | None = None
    if args.scenario is not None and hooks is None:
        server, _thread = _start_bare_listener(args.fake_port)
    try:
        exit_code, report = asyncio.run(
            run_apply(
                milvus_uri=args.milvus_uri,
                collection_prefix=args.collection_prefix,
                fake_port=args.fake_port if args.scenario is not None else None,
                hooks=hooks,
            )
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
    _write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"报告已写入：{args.report}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
