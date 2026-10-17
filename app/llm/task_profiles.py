"""用途配置描述；嵌入与重排不会被当作文本生成接口。"""

from dataclasses import dataclass, field

from app.core.config import settings


@dataclass(frozen=True)
class ModelTaskProfile:
    task: str
    provider: str
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    model: str
    timeout: float
    dim: int | None = None
    enabled: bool = False


def get_task_profile(task: str) -> ModelTaskProfile:
    """解析四个配置用途；重排描述不代表已经启用重排执行。"""
    if task == "embedding":
        return ModelTaskProfile(
            task, settings.EMBEDDING_PROVIDER.strip().lower(), settings.EMBEDDING_BASE_URL,
            settings.EMBEDDING_API_KEY, settings.EMBEDDING_MODEL, settings.EMBEDDING_TIMEOUT_SECONDS,
            dim=settings.EMBEDDING_DIM, enabled=bool(settings.EMBEDDING_API_KEY.strip()),
        )
    if task == "rerank":
        return ModelTaskProfile(
            task, settings.LLM_RERANK_PROVIDER.strip().lower(), settings.LLM_RERANK_BASE_URL,
            settings.LLM_RERANK_API_KEY, settings.LLM_RERANK_MODEL, settings.LLM_TIMEOUT,
            enabled=False,
        )
    if task == "chat":
        return ModelTaskProfile(
            task, settings.LLM_PROVIDER.strip().lower(), settings.LLM_BASE_URL,
            settings.LLM_API_KEY, settings.LLM_DEFAULT_MODEL, settings.LLM_TIMEOUT,
            enabled=bool(settings.LLM_API_KEY.strip()),
        )
    if task == "intent":
        own = bool(settings.LLM_INTENT_API_KEY.strip())
        return ModelTaskProfile(
            task, (settings.LLM_INTENT_PROVIDER or "openai").strip().lower() if own
            else settings.LLM_PROVIDER.strip().lower(),
            (settings.LLM_INTENT_BASE_URL or settings.LLM_BASE_URL) if own else settings.LLM_BASE_URL,
            settings.LLM_INTENT_API_KEY if own else settings.LLM_API_KEY,
            (settings.LLM_INTENT_MODEL or settings.LLM_DEFAULT_MODEL) if own else settings.LLM_DEFAULT_MODEL,
            settings.LLM_TIMEOUT, enabled=bool((settings.LLM_INTENT_API_KEY if own else settings.LLM_API_KEY).strip()),
        )
    raise ValueError("未知的模型用途")
