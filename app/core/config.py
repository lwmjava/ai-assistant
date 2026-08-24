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

    # ── LLM 提供商 ──
    # 以下为代码默认值，运行时会被 .env / 环境变量覆盖
    LLM_PROVIDER: str = "openai"  # openai | ollama | mock（兼容 OpenAI 接口的均可）
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    LLM_API_KEY: str = ""  # 通过环境变量注入；为空且为开发环境时自动降级为 Mock
    LLM_DEFAULT_MODEL: str = "deepseek-chat"
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 2048
    LLM_TIMEOUT: float = 60.0

    # ── 嵌入模型（RAG 检索）──
    # 与大模型共用 OpenAI 兼容协议：OpenAI 官方与 Ollama 的嵌入接口均可用。
    RAG_ENABLED: bool = False  # 是否将检索上下文注入对话管线
    EMBEDDING_PROVIDER: str = "openai"  # openai | ollama | mock
    EMBEDDING_BASE_URL: str = "https://api.openai.com/v1"
    EMBEDDING_API_KEY: str = ""  # 通过环境变量注入；为空且为开发环境时自动降级为 Mock
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 1536

    # ── 向量库与检索 ──
    RAG_VECTOR_STORE: str = "local"  # local（SQLite + numpy）| milvus
    RAG_CHUNK_SIZE: int = 500  # 分块字符数上限
    RAG_CHUNK_OVERLAP: int = 64  # 分块重叠字符数
    RAG_TOP_K: int = 5  # 每次检索返回的最大块数
    RAG_HYBRID_RRF_K: int = 60  # 倒数排名融合（RRF）的常数 k

    # ── Agent 工具调用（Function Calling）──
    AGENT_MAX_TOOL_ROUNDS: int = 5  # 「行动」阶段单次对话最多执行的工具调用次数

    # ── Agent 编排与质量 ──
    # 编排实现：self（自研五阶段管线，默认）| langgraph（LangGraph Supervisor 子编排）
    # LangGraph 仅覆盖「多 Agent 协作」这一层，由本开关切换，不影响自研路径。
    AGENT_ORCHESTRATION: str = "self"
    AGENT_QUALITY_GATE_ENABLED: bool = False  # 质量门：低于阈值触发自纠错（默认关闭）
    AGENT_QUALITY_THRESHOLD: float = 0.6  # 质量门合格阈值
    AGENT_MAX_REVISIONS: int = 2  # 「行动」草稿不合格时的最大自纠错轮数

    # ── MCP（Model Context Protocol）客户端 ──
    # 将企业系统的 MCP 服务器工具注入 Agent 工具箱，打通「AI ↔ 企业系统」。
    MCP_ENABLED: bool = False  # 是否启用 MCP 客户端（默认关闭，避免无配置时尝试连接）
    MCP_SERVERS: str = ""  # JSON 数组：MCP 服务器清单，见 app/mcp/config.py 的格式说明

    # ── Workflow 工作流引擎（Phase 3）──
    # 定时任务（cron）触发 Agent 执行，打通「自动化」场景。
    WORKFLOW_ENABLED: bool = False  # 是否启用工作流引擎（调度器 + 路由）；默认关闭
    WORKFLOW_DEFAULT_TIMEZONE: str = "Asia/Shanghai"  # cron 解析默认时区（IANA 名称）
    WORKFLOW_SCHEDULER_INTERVAL: float = 30.0  # 调度循环扫描间隔（秒）
    WORKFLOW_MAX_EXECUTION_SECONDS: float = 300.0  # 单次任务执行超时（秒）

    # ── Milvus（可选，仅当 RAG_VECTOR_STORE=milvus 时使用）──
    MILVUS_URI: str = "http://localhost:19530"
    MILVUS_TOKEN: str = ""
    MILVUS_COLLECTION: str = "ai_assistant_chunks"

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
