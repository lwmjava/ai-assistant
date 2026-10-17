"""嵌入模型抽象层。

定义将文本映射为向量的最小接口，屏蔽 OpenAI / Ollama / Mock 等具体实现差异。
检索与管线只依赖本模块定义的抽象，便于替换与测试。
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingInputPolicy:
    """Versioned input limit; a missing counter never implies verified capacity.

    Offline fixtures can explicitly declare an unlimited policy. Estimates must
    identify their method and margin, and are not exact model token counts.
    """

    max_input_tokens: int | None
    counter: Callable[[str], int] | None = None
    counting_method: str = "unverified"
    source: str = "unconfigured"
    version: str = "input-policy-v1"
    safety_margin: int = 0
    max_batch_size: int | None = None

    def __post_init__(self) -> None:
        if self.max_input_tokens is not None and self.max_input_tokens <= 0:
            raise ValueError("max_input_tokens must be positive")
        if self.safety_margin < 0:
            raise ValueError("safety_margin must be non-negative")
        if self.max_batch_size is not None and self.max_batch_size <= 0:
            raise ValueError("max_batch_size must be positive")

    def check(self, text: str) -> str | None:
        if self.max_input_tokens is None:
            return None if self.counting_method == "offline-unlimited" else "input_limit_unverified"
        if self.counter is None:
            return "input_count_unverified"
        count = self.counter(text)
        if count < 0:
            raise ValueError("input counter returned a negative count")
        if count + self.safety_margin > self.max_input_tokens:
            return "input_limit_exceeded"
        return None

    def metadata(self) -> dict:
        return {
            "version": self.version,
            "max_input_tokens": self.max_input_tokens,
            "counting_method": self.counting_method,
            "safety_margin": self.safety_margin,
            "source": self.source,
        }


class EmbeddingDimensionError(RuntimeError):
    """嵌入模型返回维度与配置 ``EMBEDDING_DIM`` 不一致。"""


class EmbeddingProvider(ABC):
    """嵌入模型接口。

    实现方需提供 ``embed``：将若干文本转换为等长的浮点向量列表。
    """

    model: str = "unknown"
    dim: int = 0
    input_policy: EmbeddingInputPolicy | None = None

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量将文本转换为向量，返回与输入等长、维度一致的向量列表。"""
