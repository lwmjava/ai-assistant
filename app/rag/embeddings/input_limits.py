"""Verified model limits and explicitly approved conservative input counting.

This small table serves chunk ingestion only. It is not the general model
capability registry or generation budget planned under RAG-028.
"""

from urllib.parse import urlsplit

from app.rag.embeddings.base import EmbeddingInputPolicy

DASHSCOPE_LIMIT_SOURCE = (
    "https://www.alibabacloud.com/help/en/model-studio/text-embedding-synchronous-api"
)


def utf8_byte_count(text: str) -> int:
    """Conservative estimate approved 2026-10-06; not exact tokenizer output."""
    return len(text.encode("utf-8"))


def resolve_input_policy(base_url: str, model: str) -> EmbeddingInputPolicy | None:
    """Do not reuse model names as proof of an arbitrary endpoint's capacity."""
    endpoint = urlsplit(base_url)
    if (
        endpoint.scheme == "https"
        and endpoint.hostname == "dashscope.aliyuncs.com"
        and endpoint.path.rstrip("/") == "/compatible-mode/v1"
        and model == "text-embedding-v3"
    ):
        return EmbeddingInputPolicy(
            max_input_tokens=8192,
            counter=utf8_byte_count,
            counting_method="utf8-bytes-conservative-estimate",
            safety_margin=32,
            max_batch_size=10,
            source=DASHSCOPE_LIMIT_SOURCE,
            version="dashscope-v3-utf8-margin32-20261006",
        )
    return None
