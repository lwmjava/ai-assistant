"""命令行入口：配置、数据库、管理员与部署生命周期。"""

import argparse
import logging
import sys

logger = logging.getLogger(__name__)


def _initialize(env_file: str) -> int:
    """首次配置生成不导入 Settings，避免预先读取部署配置。"""
    from app.core.bootstrap_config import initialize_config

    try:
        initialize_config(env_file)
    except OSError:
        print("无法创建初始化配置：目标必须不存在，且父目录可写；请使用 --env-file 指定新文件。", file=sys.stderr)
        return 1
    print("已创建初始化配置。请安全保存该文件。")
    return 0


def _migrate(yes: bool) -> int:
    """升级到当前迁移版本。没有待执行项时说明已是最新。"""
    from app.core.migration import _cli_migrate

    _cli_migrate(yes=yes)
    return 0


def _create_superuser(username: str) -> int:
    """从 INITIAL_ADMIN_PASSWORD 读取密码并创建系统管理员。不打印密码。"""
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
    """解析管理命令，返回进程退出码。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(prog="ai-assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    initialize = sub.add_parser("init", help="生成首次启动配置，不覆盖已有文件")
    initialize.add_argument("--env-file", default=".env", help="新配置文件路径，默认当前目录 .env")

    migrate = sub.add_parser("migrate", help="将数据库升级到当前版本")
    migrate.add_argument("--yes", "-y", action="store_true", help="跳过确认")

    admin = sub.add_parser("admin", help="管理员账号")
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)
    create = admin_sub.add_parser("create-superuser", help="创建系统管理员")
    create.add_argument("--username", required=True, help="用户名")

    from app.cli_lifecycle import add_commands, execute

    add_commands(sub)

    args = parser.parse_args(argv)
    if args.command == "init":
        return _initialize(args.env_file)
    if args.command == "migrate":
        return _migrate(args.yes)
    if args.command in {"start", "stop", "status", "logs"}:
        return execute(args)
    return _create_superuser(args.username)


if __name__ == "__main__":
    raise SystemExit(main())
