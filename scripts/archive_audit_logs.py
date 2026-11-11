"""归档并清理超过保留期的在线审计日志。

默认只做演练，打印将处理的条数，不写归档、不删除记录。
显式传入 --execute 后才会归档并移出在线表，同时写入一条清理记录。
不会打印审计记录正文。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="归档并清理超过保留期的审计日志")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际归档并移出在线表；缺省为演练",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="保留天数，缺省读取配置，不能小于 90",
    )
    parser.add_argument(
        "--archive-dir",
        type=str,
        default=None,
        help="归档目录，缺省读取配置；相对路径以项目根为准",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from app.audit.retention import archive_and_cleanup_expired_logs

    result = archive_and_cleanup_expired_logs(
        retention_days=args.retention_days,
        archive_dir=args.archive_dir,
        dry_run=not args.execute,
    )
    summary = {
        "dry_run": result.dry_run,
        "retention_days": result.retention_days,
        "cutoff": result.cutoff.isoformat(),
        "expired_count": result.expired_count,
        "archived_count": result.archived_count,
        "deleted_count": result.deleted_count,
        "archive_file": result.archive_path.as_posix() if result.archive_path else None,
        "cleanup_record_id": result.cleanup_record_id,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — CLI 出口统一返回非 0
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
