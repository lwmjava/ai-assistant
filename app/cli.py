"""命令行入口：迁移数据库，以及创建系统管理员。"""

import argparse
import logging
import sys

from app.core.config import settings
from app.services.auth_service import (
    MIN_CLI_PASSWORD_LENGTH,
    DatabaseNotMigratedError,
    DefaultTenantInactiveError,
    PasswordTooShortError,
    SetupClosedError,
    UsernameTakenError,
    create_cli_superuser,
)

logger = logging.getLogger(__name__)


def _migrate(yes: bool) -> int:
    """升级到当前迁移版本。没有待执行项时说明已是最新。"""
    from app.core.migration import _cli_migrate

    _cli_migrate(yes=yes)
    return 0


def _create_superuser(username: str) -> int:
    """从 INITIAL_ADMIN_PASSWORD 读取密码并创建系统管理员。不打印密码。"""
    if not username.strip():
        print("需要提供用户名。", file=sys.stderr)
        return 1
    password = settings.INITIAL_ADMIN_PASSWORD
    if len(password) < MIN_CLI_PASSWORD_LENGTH:
        print("密码至少需要 8 位。", file=sys.stderr)
        logger.info("拒绝创建系统管理员 username=%s reason=password_too_short", username)
        return 1
    try:
        create_cli_superuser(username=username, password=password)
    except PasswordTooShortError:
        print("密码至少需要 8 位。", file=sys.stderr)
        logger.info("拒绝创建系统管理员 username=%s reason=password_too_short", username)
        return 1
    except SetupClosedError:
        print("已有系统管理员，拒绝创建。", file=sys.stderr)
        logger.info("拒绝创建系统管理员 username=%s reason=admin_exists", username)
        return 1
    except UsernameTakenError:
        print("用户名已存在。", file=sys.stderr)
        logger.info("拒绝创建系统管理员 username=%s reason=username_taken", username)
        return 1
    except DefaultTenantInactiveError:
        print("名为 default 的租户已停用，不能创建系统管理员。", file=sys.stderr)
        logger.info("拒绝创建系统管理员 username=%s reason=tenant_inactive", username)
        return 1
    except DatabaseNotMigratedError:
        print("数据库还没有完成迁移。", file=sys.stderr)
        logger.info("拒绝创建系统管理员 username=%s reason=not_migrated", username)
        return 1
    except Exception as exc:
        print(f"创建系统管理员失败（{type(exc).__name__}）。", file=sys.stderr)
        logger.info("拒绝创建系统管理员 username=%s reason=%s", username, type(exc).__name__)
        return 1
    print(f"已创建系统管理员 '{username}'。")
    return 0


def main(argv: list[str] | None = None) -> int:
    """解析 migrate 与 admin create-superuser。返回进程退出码。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(prog="ai-assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    migrate = sub.add_parser("migrate", help="将数据库升级到当前版本")
    migrate.add_argument("--yes", "-y", action="store_true", help="跳过确认")

    admin = sub.add_parser("admin", help="管理员账号")
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)
    create = admin_sub.add_parser("create-superuser", help="创建系统管理员")
    create.add_argument("--username", required=True, help="用户名")

    args = parser.parse_args(argv)
    if args.command == "migrate":
        return _migrate(args.yes)
    return _create_superuser(args.username)


if __name__ == "__main__":
    raise SystemExit(main())
