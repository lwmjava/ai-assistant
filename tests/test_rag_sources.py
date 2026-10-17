"""PRAG-003 Citation 产品化：来源结构含摘录与分块定位字段。"""

from app.api.routes.chat import SourceOut
from app.rag.service import sources_from_hits
from app.rag.vectorstore.base import ChunkResult


class _FakeSession:
    def get(self, *_args, **_kw):
        return None


def _hit(cid: str = "c1", content: str = "知识库原文片段") -> ChunkResult:
    return ChunkResult(
        id=cid,
        content=content,
        source="手册.pdf",
        document_id="d1",
        score=0.5,
        similarity=0.9,
    )


def test_citation_sources_include_excerpt_chunk_document() -> None:
    out = sources_from_hits(_FakeSession(), [_hit()])
    assert out and out[0]["filename"] == "手册.pdf"
    assert out[0]["chunk_id"] == "c1"
    assert out[0]["document_id"] == "d1"
    assert out[0]["excerpt"] == "知识库原文片段"


def test_citation_source_excerpt_truncated() -> None:
    long = " ".join(["段落"] * 400)
    out = sources_from_hits(_FakeSession(), [_hit(cid="c9", content=long)])
    assert len(out[0]["excerpt"]) <= 240
    # 折叠空白：连续多个空格被压缩
    assert "  " not in out[0]["excerpt"]


def test_citation_sourceout_schema_has_fields() -> None:
    fields = set(SourceOut.model_fields)
    assert {"filename", "page", "section"}.issubset(fields)
    assert {"excerpt", "chunk_id", "document_id"}.issubset(fields)
    assert SourceOut.model_fields["excerpt"].is_required() is False


def test_citation_sources_dedupe_by_chunk() -> None:
    out = sources_from_hits(_FakeSession(), [_hit("c1", "a"), _hit("c2", "b")])
    assert len(out) == 2
    assert [s["chunk_id"] for s in out] == ["c1", "c2"]
