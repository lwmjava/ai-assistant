"""嵌入模型抽象层。

定义将文本映射为向量的最小接口，屏蔽 OpenAI / Ollama / Mock 等具体实现差异。
检索与管线只依赖本模块定义的抽象，便于替换与测试。
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence


class EmbeddingProvider(ABC):
    """嵌入模型接口。

    实现方需提供 ``embed``：将若干文本转换为等长的浮点向量列表。
    """

    model: str = "unknown"
    dim: int = 0

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量将文本转换为向量，返回与输入等长、维度一致的向量列表。"""
