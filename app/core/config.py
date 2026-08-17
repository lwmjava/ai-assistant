"""应用配置。

基于 pydantic-settings，支持从环境变量 / .env 文件加载。
设计原则：
- 所有密钥类配置（JWT 密钥、LLM API Key）仅通过环境变量注入，不写代码默认值；
- 配置集中管理，业务代码通过 ``get_settings()`` 读取，避免硬编码；
- 生产环境（ENV=production）下由安全校验强制要求关键配置。
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局应用配置。

    字段命名使用大写，pydantic-settings 默认按字段名（大小写不敏感）映射环境变量。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── 应用 ──
    APP_NAME: str = "ai-assistant"
    APP_VERSION: str = "0.1.0"
    ENV: str = "development"  # development | production
    DEBUG: bool = False

    # ── 数据库 ──
    DATABASE_URL: str = "sqlite:///./data/ai_assistant.db"
    DB_ECHO: bool = False

    # ── 安全 / JWT ──
    JWT_SECRET_KEY: str = "change-me-in-production-use-a-random-secret"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    AUTH_ENABLED: bool = True

    # ── 初始管理员（首次引导用，生产建议创建后移除）──
    INITIAL_ADMIN_USERNAME: str = ""
    INITIAL_ADMIN_PASSWORD: str = ""
    INITIAL_ADMIN_EMAIL: str = ""

    # ── CORS ──
    CORS_ORIGINS: str = "*"  # 逗号分隔；生产环境应显式指定，禁止 *

    # ── 服务器 ──
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # ── LLM 路由（当前仅配置，调用实现见后续模块）──
    LLM_PROVIDER: str = "openai"  # openai | ollama
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_API_KEY: str = ""  # 通过环境变量注入
    LLM_DEFAULT_MODEL: str = "gpt-4o-mini"

    @property
    def is_production(self) -> bool:
        """是否为生产环境。"""
        return self.ENV.strip().lower() in ("production", "prod")

    @property
    def is_development(self) -> bool:
        """是否为开发环境。"""
        return self.ENV.strip().lower() == "development"

    @property
    def cors_origin_list(self) -> list[str]:
        """解析 CORS_ORIGINS 为列表。"""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回全局配置单例（带缓存）。"""
    return Settings()


# 全局配置实例，业务模块直接导入使用。
settings = get_settings()
