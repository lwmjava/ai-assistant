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
from urllib.parse import urlsplit

from app.core.config import settings
from app.models.rag import IndexStatus

__all__ = [
    "ACTUAL_NORMALIZATION",
    "ACTUAL_METRIC",
    "EmbeddingIndexIdentity",
    "IndexIdentityError",
    "IndexStatus",
    "SUPPORTED_METRIC",
    "SUPPORTED_NORMALIZATION",
    "current_backend",
    "deployment_from_base_url",
    "fingerprint_of_key",
    "identity_from_provider",
    "identity_matches_row",
]

# ── 实际生效的检索约定（不是配置项）──────────────────────────
# Local：检索时对候选矩阵与查询向量做 L2 归一化后点积（= 余弦）。
# Milvus：建索引与检索固定 COSINE。两者语义都是余弦。
# 配置里只允许声明这一组；声明其它值一律拒绝，不允许「登记了却不生效」。
SUPPORTED_NORMALIZATION = "l2"
SUPPORTED_METRIC = "cosine"
# 归一化实际发生在查询时，写入侧保存的是 provider 原样向量。
ACTUAL_NORMALIZATION = "l2_at_query_time"
ACTUAL_METRIC = "cosine"


class IndexIdentityError(RuntimeError):
    """索引身份不匹配：当前模型与索引登记的身份不可混用。"""


def deployment_from_base_url(url: str) -> str:
    """从 ``base_url`` 提取不含凭据的部署标识：``scheme://host[:port]/path``。

    两个 OpenAI 兼容端点即使 model/dim 相同也往往指向完全不同的向量空间，
    只有把 host/path 纳入身份才能区分。这里**剥离 userinfo 与查询串**——
    身份里绝不能出现密钥或完整连接串；host 统一小写，去掉结尾斜杠。
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if not parsed.scheme and not parsed.netloc:
        parsed = urlsplit(f"//{raw}")
    netloc = parsed.netloc.rpartition("@")[2].strip().lower()
    if not netloc:
        return ""
    scheme = (parsed.scheme or "https").strip().lower()
    path = (parsed.path or "").rstrip("/")
    return f"{scheme}://{netloc}{path}"


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
        # 归一化与度量先整理成形再去校验与入库：只按小写比较却原样存进 key()，
        # 会把登记成 ``L2`` 的行与运行时的 ``l2`` 永久判为不同索引，
        # 而且 health 的小写判定还会把它报成 supported——双重误导。
        object.__setattr__(self, "normalization", self.normalization.strip().lower())
        object.__setattr__(self, "metric", self.metric.strip().lower())
        # 归一化与度量只允许声明真实生效的那一组：登记了却按另一套算法执行，
        # 会让身份看起来可信而向量实际不可比，比不登记更危险。
        if self.normalization != SUPPORTED_NORMALIZATION:
            raise IndexIdentityError(
                f"不支持的 normalization={self.normalization!r}："
                f"当前实现只按 {SUPPORTED_NORMALIZATION} 执行（{ACTUAL_NORMALIZATION}），"
                "请改为 l2 或先实现对应的写入/检索行为"
            )
        if self.metric != SUPPORTED_METRIC:
            raise IndexIdentityError(
                f"不支持的 metric={self.metric!r}："
                f"当前实现只按 {SUPPORTED_METRIC} 执行，请改为 cosine 或先实现对应度量"
            )

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


def fingerprint_of_key(identity_key: str) -> str:
    """登记行里只有完整 ``identity_key``；短指纹由它派生，与 :meth:`fingerprint` 一致。"""
    return hashlib.sha256((identity_key or "").encode("utf-8")).hexdigest()[:12]


def identity_matches_row(identity: EmbeddingIndexIdentity, row: object) -> bool:
    """完整身份比对：直接比 ``key()``，不做字段挑选。

    只比 model/dim/version 会让「同维异模型 / 异部署 / 异度量」悄悄通过，
    因此所有核对点都必须走这里。
    """
    return identity.key() == str(getattr(row, "identity_key", "") or "")


def current_backend() -> str:
    """当前配置生效的向量库后端名。"""
    return "milvus" if settings.RAG_VECTOR_STORE.strip().lower() == "milvus" else "local"


def identity_from_provider(
    provider: object,
    *,
    model: str | None = None,
    dim: int | None = None,
    index_version: str | None = None,
    backend: str | None = None,
    normalization: str | None = None,
    metric: str | None = None,
) -> EmbeddingIndexIdentity:
    """从嵌入 provider 构造身份。运行时与脚本共用同一个生产函数。

    ``provider`` 取**配置标签** ``settings.EMBEDDING_PROVIDER``：OpenAI 官方、
    Ollama 与各类兼容服务都落在同一个实现类上，用 ``type(provider).__name__``
    无法区分真实服务提供商。真正能区分向量空间的是部署端点，因此
    ``deployment`` 由 :func:`deployment_from_base_url` 从 ``base_url`` 派生
    （去凭据），provider 未声明 ``base_url`` 时留空——**不猜测、不回退到模型名**。
    """
    resolved_model = model or str(getattr(provider, "model", "") or "unknown")
    resolved_dim = int(dim or getattr(provider, "dim", 0) or 0)
    if resolved_dim <= 0:
        raise IndexIdentityError(
            f"嵌入 provider {type(provider).__name__} 未声明有效维度，无法建立索引身份"
        )
    deployment = str(getattr(provider, "deployment", "") or "") or deployment_from_base_url(
        str(getattr(provider, "base_url", "") or "")
    )
    provider_label = (settings.EMBEDDING_PROVIDER or "").strip().lower() or type(
        provider
    ).__name__
    return EmbeddingIndexIdentity(
        backend=backend or current_backend(),
        provider=provider_label,
        model=resolved_model,
        dim=resolved_dim,
        index_version=index_version or settings.EMBEDDING_INDEX_VERSION,
        deployment=deployment,
        normalization=normalization or settings.EMBEDDING_NORMALIZATION,
        metric=metric or settings.EMBEDDING_METRIC,
    )
