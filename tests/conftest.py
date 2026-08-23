"""测试环境配置。

在导入应用前设置环境变量，确保测试使用独立的 SQLite 数据库与开发环境，
避免污染仓库根目录的默认数据库，并放宽生产安全校验。
"""

import os

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
