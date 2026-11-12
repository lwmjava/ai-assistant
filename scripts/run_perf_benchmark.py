"""在隔离环境跑完五项性能指标并写出报告。

默认在本进程内启动应用，使用本地向量库与 Mock 模型，不访问生产或共享在线栈，
也不发起付费模型调用。报告会写明 /health 中的 Embedding 身份和机器规格；
Mock 或非清单机的结果不会被写成上线达标。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def _isolate_environment(work_dir: Path) -> None:
    """在导入应用前钉死隔离环境，避免读入本机密钥或生产库。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ENV"] = "development"
    os.environ["DATABASE_URL"] = "sqlite:///" + (work_dir / "bench.db").as_posix()
    os.environ["AUTH_ENABLED"] = "true"
    os.environ["INITIAL_ADMIN_USERNAME"] = ""
    os.environ["INITIAL_ADMIN_PASSWORD"] = ""
    os.environ["AGENT_ORCHESTRATION"] = "self"
    os.environ["SECURITY_RATE_LIMIT"] = "false"
    os.environ["RAG_VECTOR_STORE"] = "local"
    os.environ["RAG_ENABLED"] = "true"
    os.environ.setdefault("LLM_PROVIDER", "mock")
    for key in (
        "LLM_API_KEY",
        "LLM_INTENT_API_KEY",
        "LLM_FALLBACK_API_KEY",
        "LLM_RERANK_API_KEY",
        "EMBEDDING_API_KEY",
    ):
        os.environ[key] = ""

    from pydantic_settings.sources import DotEnvSettingsSource

    workspace_env = ROOT / ".env"
    original = DotEnvSettingsSource._read_env_file

    def _read_isolated_env(self, file_path):
        if Path(file_path).resolve() == workspace_env.resolve():
            return {}
        return original(self, file_path)

    DotEnvSettingsSource._read_env_file = _read_isolated_env


def _enable_sqlite_wal() -> None:
    from sqlalchemy import text

    from app.core.database import engine

    if not str(engine.url).startswith("sqlite"):
        return
    with engine.connect() as connection:
        connection.execute(text("PRAGMA journal_mode=WAL"))
        connection.execute(text("PRAGMA busy_timeout=30000"))
        connection.commit()


def _build_client():
    from fastapi.testclient import TestClient
    from sqlmodel import Session

    from app.api.deps import get_current_user
    from app.core.database import engine, init_db
    from app.core.security import Role
    from app.llm.factory import set_llm_provider_override
    from app.llm.mock import MockLLMProvider
    from app.main import app
    from app.models.user import Tenant, User
    from app.rag.embeddings.factory import set_embedding_override
    from app.rag.embeddings.mock import MockEmbeddingProvider

    init_db()
    _enable_sqlite_wal()
    user = User(
        id="perf-user",
        tenant_id="perf-tenant",
        username="perf",
        hashed_password="",
        role=Role.TENANT_ADMIN.value if hasattr(Role, "TENANT_ADMIN") else Role.MEMBER.value,
        token_version=0,
        is_active=True,
    )
    with Session(engine) as session:
        if session.get(Tenant, user.tenant_id) is None:
            session.add(Tenant(id=user.tenant_id, name=user.tenant_id))
        current = session.get(User, user.id)
        if current is None:
            session.add(user)
            session.commit()
            session.refresh(user)
            session.expunge(user)
        else:
            session.expunge(current)
            user = current

    app.dependency_overrides[get_current_user] = lambda: user
    set_llm_provider_override(MockLLMProvider())
    set_embedding_override(MockEmbeddingProvider(dim=64))
    return TestClient(app), user


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="跑完五项性能指标的隔离压测并写出报告")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs" / "evaluations")
    parser.add_argument("--stem", default="")
    parser.add_argument("--api-samples", type=int, default=20)
    parser.add_argument("--first-token-samples", type=int, default=8)
    parser.add_argument("--retrieval-samples", type=int, default=20)
    parser.add_argument("--conversations", type=int, default=50)
    parser.add_argument("--work-dir", type=Path, default=ROOT / "data" / "perf-benchmark")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _isolate_environment(args.work_dir)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from app.core.config import settings
    from app.core.database import init_db
    from app.llm.factory import set_llm_provider_override
    from app.rag.embeddings.factory import set_embedding_override
    from scripts.perf_benchmark import (
        collect_machine_specs,
        run_benchmark,
        snapshot_health,
        write_report,
    )

    # 再次钉死隔离向量库，防止本机默认 milvus 配置在 Settings 已加载后仍被使用。
    settings.RAG_VECTOR_STORE = "local"
    init_db()
    client, _user = _build_client()
    try:
        health = snapshot_health(client)
        machine = collect_machine_specs()
        report = run_benchmark(
            client,
            health=health,
            machine=machine,
            api_samples=args.api_samples,
            first_token_samples=args.first_token_samples,
            retrieval_samples=args.retrieval_samples,
            conversations=args.conversations,
        )
        date_key = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        stem = args.stem or f"perf-five-metrics-{date_key}"
        json_path, markdown_path = write_report(report, args.output_dir, stem)
        print(f"wrote {json_path}")
        print(f"wrote {markdown_path}")
        print(f"all_measured={report['all_measured']} release_evidence_valid={report['release_evidence_valid']}")
        return 0 if report["all_measured"] else 1
    finally:
        set_embedding_override(None)
        set_llm_provider_override(None)
        from app.main import app

        app.dependency_overrides.clear()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
