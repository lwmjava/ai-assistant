"""对测试租户执行数据合规清除。

默认只演练：统计将删除的行数与文件数，不写确认、不做备份、不删除。
显式传入 --execute，并同时提供确认标识、不可逆声明、隔离数据库与报告目录后，
才会备份、清除并写出验证报告。不会打印数据库连接或记录正文。
本脚本不是 Agent 工具，也不提供 HTTP 接口。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlmodel import Session, create_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="清除测试租户的业务数据、向量分块与源文件")
    parser.add_argument("--tenant-id", required=True, help="目标租户标识")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际执行清除；缺省为演练",
    )
    parser.add_argument(
        "--confirm-tenant-id",
        default=None,
        help="必须与 --tenant-id 完全一致，仅执行时需要",
    )
    parser.add_argument(
        "--i-understand-irreversible",
        action="store_true",
        help="确认该清除不可逆，仅执行时需要",
    )
    parser.add_argument(
        "--isolated-database",
        action="store_true",
        help="声明目标库是隔离数据库，而不是默认应用库或生产库",
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="目标数据库 URL；执行时不会打印该值",
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        help="确认记录与验证报告目录；执行时必填",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="清除前快照目录；缺省写到报告目录下",
    )
    parser.add_argument(
        "--knowledge-root",
        default=None,
        help="知识库源文件根目录；执行时必填",
    )
    parser.add_argument(
        "--confirmed-by",
        default=None,
        help="人工确认人标识，只写入确认记录，不作为鉴权",
    )
    parser.add_argument(
        "--environment",
        default=None,
        help="逻辑环境名；production/prod 会被拒绝",
    )
    return parser


def _open_session(database_url: str) -> Session:
    from app.core.tenants.purge import import_persistence_models

    import_persistence_models()
    connect_args = {"check_same_thread": False, "timeout": 30} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    return Session(engine)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from app.core.tenants.purge import describe_database_target, purge_tenant

    session = _open_session(args.database_url)
    try:
        result = purge_tenant(
            session,
            args.tenant_id,
            dry_run=not args.execute,
            confirm_tenant_id=args.confirm_tenant_id,
            i_understand_irreversible=args.i_understand_irreversible,
            confirmed_by=args.confirmed_by,
            allow_isolated_database=args.isolated_database,
            database_url=args.database_url,
            environment=args.environment,
            report_dir=args.report_dir,
            backup_dir=args.backup_dir,
            knowledge_root=args.knowledge_root,
        )
    finally:
        session.close()

    summary = {
        "dry_run": result.dry_run,
        "tenant_id": result.tenant_id,
        "tenant_name": result.tenant_name,
        "verified": result.verified,
        "planned_counts": result.planned_counts,
        "deleted_counts": result.deleted_counts,
        "remaining_counts": result.remaining_counts,
        "files_planned": result.files_planned,
        "files_deleted": result.files_deleted,
        "files_remaining": result.files_remaining,
        "vector_backend": result.vector_backend,
        "confirmation_path": result.confirmation_path.as_posix() if result.confirmation_path else None,
        "backup_dir": result.backup_dir.as_posix() if result.backup_dir else None,
        "report_path": result.report_path.as_posix() if result.report_path else None,
        "audit_confirmation_id": result.audit_confirmation_id,
        "audit_purge_id": result.audit_purge_id,
        "database": describe_database_target(args.database_url),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.execute and not result.verified:
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — CLI 出口统一返回非 0
        print(
            json.dumps(
                {"error": type(exc).__name__, "message": str(exc), "code": getattr(exc, "code", None)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)
