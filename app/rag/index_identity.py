"""Embedding 索引身份与受控切换（ADR-0008）。

向量的可比性不只取决于维度：同维度不同模型产出的向量空间互不通用，
归一化与度量约定变了，数值同样不可比。把 provider / model / 部署标识 /
维度 / 索引版本 / 归一化 / 度量绑定成一个身份，写入与查询都按身份核对，
才能阻止「同维度异模型混用」这类静默错误。

身份里**不含密钥与连接串**，可以安全落库、落日志、进 health 响应。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.core.config import settings
from app.models.rag import IndexStatus

__all__ = [
    "EmbeddingIndexIdentity",
    "IndexIdentityError",
    "IndexStatus",
    "current_backend",
    "identity_from_provider",
]


class IndexIdentityError(RuntimeError):
    """索引身份不匹配：当前模型与索引登记的身份不可混用。"""


@dataclass(frozen=True)
class EmbeddingIndexIdentity:
    """一个向量索引的完整身份。"""

    backend: str
    provider: str
    model: str
    dim: int
    index_version: str = "1"
    deployment: str = ""
    normalization: str = "l2"
    metric: str = "cosine"

    def __post_init__(self) -> None:
        if self.dim <= 0:
            raise ValueError("dim must be positive")
        for name in ("backend", "provider", "model", "index_version"):
            if not getattr(self, name):
                raise ValueError(f"{name} must not be empty")

    def key(self) -> str:
        """稳定身份标识。字段以 ``|`` 分隔，避免某个字段含 ``:`` 时产生歧义。"""
        return "|".join(
            (
                self.backend,
                self.provider,
                self.model,
                self.deployment,
                str(self.dim),
                self.index_version,
                self.normalization,
                self.metric,
            )
        )

    def fingerprint(self) -> str:
        """身份短指纹，用于集合名与日志，不含密钥或连接串。"""
        return hashlib.sha256(self.key().encode("utf-8")).hexdigest()[:12]

    def collection_name(self, base: str | None = None) -> str:
        """Milvus 集合名：身份变了集合名就变，杜绝原地混写。"""
        root = base or settings.MILVUS_COLLECTION
        return f"{root}__v{self.index_version}__{self.fingerprint()}"

    def describe(self) -> dict[str, str | int]:
        """可观测字段，供 health / 管理态展示。"""
        return {
            "backend": self.backend,
            "provider": self.provider,
            "model": self.model,
            "deployment": self.deployment,
            "dim": self.dim,
            "index_version": self.index_version,
            "normalization": self.normalization,
            "metric": self.metric,
            "fingerprint": self.fingerprint(),
        }


def current_backend() -> str:
    """当前配置生效的向量库后端名。"""
    return "milvus" if settings.RAG_VECTOR_STORE.strip().lower() == "milvus" else "local"


def identity_from_provider(
    provider: object,
    *,
    backend: str | None = None,
    index_version: str | None = None,
    normalization: str | None = None,
    metric: str | None = None,
) -> EmbeddingIndexIdentity:
    """从嵌入 provider 构造身份。

    ``deployment`` 只在 provider 显式给出时才填；**不猜测、不回退到模型名**，
    否则会把不同部署的同一模型误判为同一索引。
    """
    model = str(getattr(provider, "model", "") or "unknown")
    dim = int(getattr(provider, "dim", 0) or 0)
    deployment = str(getattr(provider, "deployment", "") or "")
    if dim <= 0:
        raise IndexIdentityError(
            f"嵌入 provider {type(provider).__name__} 未声明有效维度，无法建立索引身份"
        )
    return EmbeddingIndexIdentity(
        backend=backend or current_backend(),
        provider=type(provider).__name__,
        model=model,
        dim=dim,
        index_version=index_version or settings.EMBEDDING_INDEX_VERSION,
        deployment=deployment,
        normalization=normalization or settings.EMBEDDING_NORMALIZATION,
        metric=metric or settings.EMBEDDING_METRIC,
    )
