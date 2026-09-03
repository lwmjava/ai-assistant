"""测试环境配置。

在导入应用前设置环境变量，确保测试使用独立的 SQLite 数据库与开发环境，
避免污染仓库根目录的默认数据库，并放宽生产安全校验。
"""

import os

import pytest

os.environ.setdefault("ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_ai_assistant.db")
os.environ.setdefault("AUTH_ENABLED", "true")
# 清空初始管理员配置，避免测试库被自动填充。
os.environ.setdefault("INITIAL_ADMIN_USERNAME", "")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "")

# 在测试导入应用前已设置好环境变量；此处显式建表，
# 因为 TestClient 非上下文管理器使用时不会触发 lifespan 中的 init_db。
from app.core.database import init_db

init_db()


@pytest.fixture(autouse=True)
def _reset_overrides():
    """兜底清理测试期间注册的全局覆盖，避免跨测试泄漏。

    业务 fixture（如 test_chat.py 的 ``client``）在 setup 阶段就注册
    ``app.dependency_overrides``，只有走到 ``yield`` 之后的 teardown 才会清理。
    一旦 setup 在 yield 前抛错，pytest 不会执行该 fixture 的 teardown，
    覆盖就会残留并污染后续测试（曾导致 test_health 的鉴权用例连锁失败）。
    本 fixture 是 autouse 且先于函数级 fixture 完成 setup，
    因此即使后者 setup 失败，pytest 仍会执行这里的 teardown。
    """
    yield
    from app.llm.factory import set_llm_provider_override
    from app.main import app

    app.dependency_overrides.clear()
    set_llm_provider_override(None)
