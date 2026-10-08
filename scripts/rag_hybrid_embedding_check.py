"""Budgeted synthetic-corpus embedding comparison against a real Milvus service."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PRICE_PER_TOKEN = .0005 / 1000
MAX_ATTEMPTS = 20
MAX_INPUT_TOKENS = 100_000
MAX_YUAN = .10
TEXTS = ["Cedar warranty requires an amber receipt.",
         "Harbor maintenance requires a cobalt wrench.",
         "Alpine calibration requires a quartz beacon.",
         "Which receipt is needed for Cedar warranty?"]


class Budget:
    """Persist conservative reservations before each HTTP attempt, never refund failures."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, "
                        "reserved_tokens INTEGER, usage_tokens INTEGER, status TEXT, "
                        "request_id TEXT, response_model TEXT)")
        self.db.commit()

    def reserve(self, texts: list[str]) -> int:
        tokens = sum(len(t.encode("utf-8")) + 32 for t in texts)
        self.db.execute("BEGIN IMMEDIATE")
        count, total = self.db.execute(
            "SELECT count(*), coalesce(sum(reserved_tokens),0) FROM attempts").fetchone()
        if count >= MAX_ATTEMPTS or total + tokens > MAX_INPUT_TOKENS or (
                total + tokens) * PRICE_PER_TOKEN > MAX_YUAN:
            self.db.rollback()
            raise ValueError("authorized budget exhausted")
        cursor = self.db.execute("INSERT INTO attempts (reserved_tokens,status) VALUES (?,?)",
                                 (tokens, "reserved_before_http"))
        attempt = int(cursor.lastrowid)
        self.db.commit()
        return attempt

    def finish(self, attempt: int, usage: int | None, status: str, request_id="", model=""):
        reserved = self.db.execute("SELECT reserved_tokens FROM attempts WHERE id=?",
                                   (attempt,)).fetchone()[0]
        if usage is None and status == "success":
            status = "unknown_usage"
        if usage is not None and (type(usage) is not int or usage < 0 or usage > reserved):
            status = "usage_invalid_or_exceeds_reservation"
            usage = None
        self.db.execute("UPDATE attempts SET usage_tokens=?,status=?,request_id=?,response_model=? "
                        "WHERE id=?", (usage, status, request_id, model, attempt))
        self.db.commit()
        if status != "success":
            raise ValueError(status)

    def report(self) -> dict:
        rows = self.db.execute("SELECT id,reserved_tokens,usage_tokens,status,request_id,response_model "
                               "FROM attempts ORDER BY id").fetchall()
        keys = ("attempt", "reserved_tokens", "usage_tokens", "status", "request_id", "response_model")
        tokens = sum(r[1] for r in rows)
        all_known = all(r[3] == "success" and type(r[2]) is int for r in rows)
        known_cost = sum(r[2] for r in rows) * PRICE_PER_TOKEN if all_known else None
        return {"limits": {"http_attempts": MAX_ATTEMPTS, "input_tokens": MAX_INPUT_TOKENS,
                           "yuan": MAX_YUAN}, "price_yuan_per_token": PRICE_PER_TOKEN,
                "reserved_cost_upper_bound_yuan": tokens * PRICE_PER_TOKEN,
                "known_usage_price_estimate_yuan": known_cost,
                "usage_complete": all_known,
                "actual_invoice": "not_available", "attempts": [dict(zip(keys, r)) for r in rows]}


def embed(settings, budget: Budget) -> list[list[float]]:
    from urllib.parse import urlsplit

    import httpx

    parsed = urlsplit(settings.EMBEDDING_BASE_URL)
    if parsed.scheme != "https" or parsed.hostname != "dashscope.aliyuncs.com":
        raise ValueError("authorized embedding endpoint mismatch")
    if settings.EMBEDDING_MODEL != "text-embedding-v3" or settings.EMBEDDING_DIM != 1024:
        raise ValueError("authorized embedding model or dimension mismatch")
    if not settings.EMBEDDING_API_KEY:
        raise ValueError("embedding credentials missing")
    attempt = budget.reserve(TEXTS)
    try:
        with httpx.Client(transport=httpx.HTTPTransport(retries=0), timeout=30.) as client:
            response = client.post(settings.EMBEDDING_BASE_URL.rstrip("/") + "/embeddings",
                                   headers={"Authorization": "Bearer " + settings.EMBEDDING_API_KEY},
                                   json={"model": "text-embedding-v3", "input": TEXTS,
                                         "dimensions": 1024, "encoding_format": "float"})
            if response.status_code != 200:
                budget.finish(attempt, None, f"http_{response.status_code}")
            payload = response.json()
            usage = payload.get("usage", {}).get("prompt_tokens")
            budget.finish(attempt, usage, "success" if type(usage) is int else "unknown_usage",
                          response.headers.get("x-request-id", payload.get("id", "")),
                          payload.get("model", ""))
            data = sorted(payload["data"], key=lambda x: x["index"])
            vectors = [r["embedding"] for r in data]
            if len(vectors) != len(TEXTS) or any(len(v) != 1024 for v in vectors):
                raise ValueError("embedding response shape mismatch")
            if any(not all(isinstance(x, int | float) and math.isfinite(x) for x in v)
                   for v in vectors):
                raise ValueError("embedding contains invalid values")
            return vectors
    except Exception as exc:
        # Only exception types are exposed by the caller; response bodies and URLs stay private.
        current = budget.db.execute("SELECT status FROM attempts WHERE id=?", (attempt,)).fetchone()[0]
        if current == "reserved_before_http":
            budget.db.execute("UPDATE attempts SET status=? WHERE id=?",
                              (f"failed_{type(exc).__name__}", attempt))
            budget.db.commit()
        raise


def corpus_hash() -> str:
    return hashlib.sha256(json.dumps(TEXTS).encode("utf-8")).hexdigest()


def save_manifest(vector_path: Path, source_report: Path) -> None:
    report = json.loads(source_report.read_text(encoding="utf-8"))
    source_vector = Path(report["vectors"])
    if source_vector.read_bytes() != vector_path.read_bytes():
        raise ValueError("source vectors differ")
    manifest = {"vectors_sha256": hashlib.sha256(vector_path.read_bytes()).hexdigest(),
                "corpus_sha256": corpus_hash(), "source_report": str(source_report.resolve()),
                "source_report_sha256": hashlib.sha256(source_report.read_bytes()).hexdigest(),
                "embedding": report["embedding"], "dataset_version": report["dataset_version"]}
    vector_path.with_suffix(".metadata.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def load_authorized_vectors(vector_path: Path) -> tuple[list[list[float]], Path]:
    manifest = json.loads(vector_path.with_suffix(".metadata.json").read_text(encoding="utf-8"))
    if hashlib.sha256(vector_path.read_bytes()).hexdigest() != manifest["vectors_sha256"]:
        raise ValueError("vectors fingerprint mismatch")
    if manifest["corpus_sha256"] != corpus_hash():
        raise ValueError("synthetic corpus fingerprint mismatch")
    source = Path(manifest["source_report"])
    if hashlib.sha256(source.read_bytes()).hexdigest() != manifest["source_report_sha256"]:
        raise ValueError("source report fingerprint mismatch")
    report = json.loads(source.read_text(encoding="utf-8"))
    if report.get("corpus_sha256") != manifest["corpus_sha256"]:
        raise ValueError("source report corpus mismatch")
    metadata = report["embedding"]
    if report["result"] != "pass" or report["reused_vectors"]:
        raise ValueError("source is not an original successful embedding evaluation")
    if metadata != manifest["embedding"] or metadata["provider"] != "dashscope-openai-compatible" or (
            metadata["model"] != "text-embedding-v3" or metadata["dim"] != 1024):
        raise ValueError("authorized vector identity mismatch")
    if manifest["dataset_version"] != "hybrid-semantics-real-embedding-synthetic-v1":
        raise ValueError("authorized dataset version mismatch")
    attempts = report["budget"]["attempts"]
    if not attempts or any(a["status"] != "success" or type(a["usage_tokens"]) is not int
                           for a in attempts):
        raise ValueError("source embedding usage is not verified")
    if hashlib.sha256(Path(report["vectors"]).read_bytes()).hexdigest() != manifest["vectors_sha256"]:
        raise ValueError("source vector artifact fingerprint mismatch")
    vectors = json.loads(vector_path.read_text(encoding="utf-8"))
    if len(vectors) != len(TEXTS) or any(len(v) != 1024 for v in vectors):
        raise ValueError("saved vector dimensions mismatch")
    if any(type(x) not in (float, int) or not math.isfinite(x) for v in vectors for x in v):
        raise ValueError("saved vectors invalid")
    return vectors, source


def real_cases(vectors: list[list[float]]) -> list[dict]:
    rows = [{"name": name, "vector": vectors[i], "tokens": tokens}
            for i, (name, tokens) in enumerate((
                ("cedar", ["cedar", "warranty", "receipt"]),
                ("harbor", ["harbor", "maintenance", "wrench"]),
                ("alpine", ["alpine", "calibration", "beacon"])))]
    return [{"name": "real_overlap", "query_vector": vectors[3],
             "tokens": ["cedar", "receipt"], "top_k": 3, "rows": rows},
            {"name": "real_no_overlap", "query_vector": vectors[3],
             "tokens": ["unseenzz"], "top_k": 3, "rows": rows},
            {"name": "real_filtered", "query_vector": vectors[3], "tokens": [],
             "top_k": 3, "uploader": "owner", "rows": [rows[0],
                 {**rows[1], "uploader": "other"}, {**rows[2], "tenant": "foreign"}]}]


def verify_real(report: dict):
    for sample in report["slices"]:
        lhs, rhs = sample["local"], sample["milvus"]
        assert [r["name"] for r in lhs] == [r["name"] for r in rhs]
        assert lhs
        for a, b in zip(lhs, rhs):
            assert math.isclose(a["score"], b["score"], abs_tol=1e-12)
            assert math.isclose(a["similarity"], b["similarity"], abs_tol=1e-5)
        if sample["name"] == "real_filtered":
            assert [r["name"] for r in lhs] == ["cedar"]
        if sample["name"] == "real_no_overlap":
            assert all(math.isclose(r["score"], 1 / (61 + i), abs_tol=1e-12)
                       for i, r in enumerate(rhs))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ledger", type=Path,
                        default=ROOT / "evals/artifacts/rag-036-embedding-budget.sqlite")
    parser.add_argument("--vectors", type=Path, help="Reuse saved authorized vectors without HTTP")
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps({"mode": "dry-run", "max_attempts": 20, "max_yuan": .10}))
        return 0
    run_dir = ROOT / "evals/artifacts" / f"hybrid-real-{uuid4().hex}"
    run_dir.mkdir(parents=True)
    os.environ["DATABASE_URL"] = f"sqlite:///{(run_dir / 'evaluation.db').as_posix()}"
    os.environ["ENV"] = "development"
    from sqlmodel import Session

    from app.core.config import settings
    from app.core.database import engine, init_db
    from app.rag.index_identity import deployment_from_base_url
    from scripts.rag_hybrid_semantics_check import compare

    settings.MILVUS_URI = "http://127.0.0.1:19530"
    settings.MILVUS_INDEX_TYPE = "FLAT"
    settings.RAG_EFFECTIVE_DATE_FILTER = False
    budget = Budget(args.ledger)
    report = {"result": "unverified", "database": str(run_dir / "evaluation.db"),
              "corpus_sha256": corpus_hash(),
              "embedding_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "price_source": "https://help.aliyun.com/zh/model-studio/embedding",
              "price_verified_date": "2026-10-08"}
    exit_code = 1
    try:
        init_db()
        source_report = None
        if args.vectors:
            vectors, source_report = load_authorized_vectors(args.vectors)
        else:
            vectors = embed(settings, budget)
        vector_path = run_dir / "vectors.json"
        vector_path.write_text(json.dumps(vectors), encoding="utf-8")
        report["vectors"] = str(vector_path)
        report["reused_vectors"] = args.vectors is not None
        metadata = {"provider": "dashscope-openai-compatible", "model": "text-embedding-v3",
                    "dim": 1024, "deployment": deployment_from_base_url(settings.EMBEDDING_BASE_URL)}
        if source_report is not None:
            metadata = json.loads(source_report.read_text(encoding="utf-8"))["embedding"]
        with Session(engine) as session:
            report.update(asyncio.run(compare(session, cases=real_cases(vectors), embedding=metadata)))
        report["dataset_version"] = "hybrid-semantics-real-embedding-synthetic-v1"
        report["limitations"] = ["Small synthetic semantics slice, not an overall quality benchmark.",
                                  "Fixed-vector keyword-window counterexample is reported separately.",
                                  "Index switch and rebuild acceptance remains a prerequisite outside this slice."]
        verify_real(report)
        report["result"] = "pass"
        exit_code = 0
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    finally:
        report["budget"] = budget.report()
        budget.db.close()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if report["result"] == "pass":
            save_manifest(vector_path, source_report or args.report)
        engine.dispose()
    print(json.dumps({"result": report["result"], "report": str(args.report)}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
