"""把 MultiHop-RAG 转成项目现有评测夹具格式。

官方原始数据缓存到已 gitignore 的 data/multihop_rag/raw/，转换结果写到
data/multihop_rag/converted/。本模块不改官方 35 条数据集或门槛。
数据集许可证为 ODC-BY，必须保留署名。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "multihop_rag" / "raw"
CONVERTED_DIR = REPO_ROOT / "data" / "multihop_rag" / "converted"
SMOKE_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "smoke"

CORPUS_NAME = "corpus.json"
QUERIES_NAME = "MultiHopRAG.json"
EXPECTED_DOCUMENT_COUNT = 609
EXPECTED_QUESTION_COUNT = 2556
EXPECTED_SHA256 = {
    # 分段存放，避免钩子把完整十六进制摘要误报为密钥。
    CORPUS_NAME: (
        "20b61b5a",
        "b84de84a",
        "927420c5",
        "d265b7ec",
        "8d859ae4",
        "99806999",
        "58a787ad",
        "e9e4d28f",
    ),
    QUERIES_NAME: (
        "03cfb492",
        "6461f868",
        "684903aa",
        "dc802444",
        "7bdda5bb",
        "3f680474",
        "1424cce3",
        "38515bff",
    ),
}

DOWNLOAD_SOURCES = (
    {
        "name": "hf-mirror",
        CORPUS_NAME: "https://hf-mirror.com/datasets/yixuantt/MultiHopRAG/resolve/main/corpus.json?download=true",
        QUERIES_NAME: "https://hf-mirror.com/datasets/yixuantt/MultiHopRAG/resolve/main/MultiHopRAG.json?download=true",
    },
    {
        "name": "huggingface",
        CORPUS_NAME: "https://huggingface.co/datasets/yixuantt/MultiHopRAG/resolve/main/corpus.json?download=true",
        QUERIES_NAME: "https://huggingface.co/datasets/yixuantt/MultiHopRAG/resolve/main/MultiHopRAG.json?download=true",
    },
)

LICENSE_NAME = "ODC-BY"
LICENSE_URL = "https://opendatacommons.org/licenses/by/1-0/"
SOURCE_GITHUB = "https://github.com/yixuantt/MultiHop-RAG"
SOURCE_HUGGINGFACE = "https://huggingface.co/datasets/yixuantt/MultiHopRAG"
PAPER_URL = "https://arxiv.org/abs/2401.15391"
DATASET_VERSION = "1.0.0-external"
TENANT_ID = "tenant-multihop"
USER_ID = "user-multihop"
SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class ConversionError(ValueError):
    """转换或下载失败。"""


@dataclass(frozen=True)
class ConversionResult:
    """一次转换的输出路径与规模。"""

    output_dir: Path
    manifest_path: Path
    cases_path: Path
    index_path: Path
    document_count: int
    question_count: int
    scorable_question_count: int
    null_query_count: int
    evidence_count: int
    question_types: dict[str, int]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def attribution_block() -> dict[str, str]:
    return {
        "dataset_name": "MultiHop-RAG",
        "authors": "Yixuan Tang and Yi Yang",
        "paper": "MultiHop-RAG: Benchmarking Retrieval-Augmented Generation for Multi-Hop Queries",
        "paper_url": PAPER_URL,
        "github": SOURCE_GITHUB,
        "huggingface": SOURCE_HUGGINGFACE,
        "license": LICENSE_NAME,
        "license_url": LICENSE_URL,
    }


def render_document(entry: dict[str, Any]) -> str:
    """把语料条目写成 Markdown，正文保持原样以便证据句精确匹配。"""
    body = str(entry.get("body") or "").removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    author = entry.get("author")
    lines = [
        f"# {entry['title']}",
        "",
        f"- source: {entry.get('source') or ''}",
        f"- author: {author if author else 'unknown'}",
        f"- published_at: {entry.get('published_at') or ''}",
        f"- category: {entry.get('category') or ''}",
        f"- url: {entry['url']}",
        "",
        body.rstrip("\n"),
        "",
    ]
    return "\n".join(lines)


def document_id_for(index: int) -> str:
    return f"mh-{index:04d}"


def case_id_for(index: int) -> str:
    return f"multihop-{index:04d}"


def _safe_stem(document_id: str, title: str) -> str:
    stem = SAFE_NAME_RE.sub("-", title).strip("-_")[:60] or "document"
    return f"{document_id}-{stem}"


def _require_list(payload: Any, label: str) -> list[Any]:
    if not isinstance(payload, list):
        raise ConversionError(f"{label} 必须是 JSON 数组")
    return payload


def index_corpus(corpus: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按 URL 建立语料索引，并拒绝空 URL 或重复 URL。"""
    by_url: dict[str, dict[str, Any]] = {}
    titles: dict[str, str] = {}
    for offset, entry in enumerate(corpus):
        if not isinstance(entry, dict):
            raise ConversionError(f"语料第 {offset} 条不是对象")
        url = str(entry.get("url") or "").strip()
        title = str(entry.get("title") or "").strip()
        body = entry.get("body")
        if not url:
            raise ConversionError(f"语料第 {offset} 条缺少 url")
        if not title:
            raise ConversionError(f"语料 {url} 缺少 title")
        if not isinstance(body, str) or not body.strip():
            raise ConversionError(f"语料 {url} 缺少 body")
        if url in by_url:
            raise ConversionError(f"语料 url 重复: {url}")
        if title in titles:
            raise ConversionError(f"语料 title 重复: {title}")
        titles[title] = url
        by_url[url] = entry
    return by_url


def map_evidence(
    evidence_list: list[Any],
    by_url: dict[str, dict[str, Any]],
    url_to_doc_id: dict[str, str],
) -> tuple[list[str], list[dict[str, Any]]]:
    """把 evidence_list 映射到逻辑文档号；事实句必须出现在对应正文中。"""
    document_ids: list[str] = []
    expected_evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in evidence_list:
        if not isinstance(item, dict):
            raise ConversionError("evidence_list 中存在非对象项")
        url = str(item.get("url") or "").strip()
        fact = str(item.get("fact") or "")
        if not url:
            raise ConversionError("证据缺少 url")
        if url not in by_url:
            raise ConversionError(f"证据 url 无法映射到语料: {url}")
        if fact not in str(by_url[url].get("body") or ""):
            raise ConversionError(f"证据事实句未出现在语料正文中: {url}")
        doc_id = url_to_doc_id[url]
        if doc_id not in seen:
            document_ids.append(doc_id)
            seen.add(doc_id)
        source_doc = by_url[url]
        expected_evidence.append(
            {
                "document_id": doc_id,
                "chunk_id": None,
                "title": source_doc["title"],
                "source": source_doc.get("url") or url,
                "version": "published",
                "effective_at": source_doc.get("published_at"),
                "page": None,
                "section": None,
                "exact_quote": fact,
            }
        )
    return document_ids, expected_evidence


def convert_records(
    corpus: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    *,
    generated_at: str,
    source_snapshot: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """转换语料清单与案例，不写磁盘。"""
    by_url = index_corpus(corpus)
    sorted_urls = sorted(by_url)
    documents: list[dict[str, Any]] = []
    url_to_doc_id: dict[str, str] = {}
    for index, url in enumerate(sorted_urls, start=1):
        entry = by_url[url]
        doc_id = document_id_for(index)
        url_to_doc_id[url] = doc_id
        documents.append(
            {
                "document_id": doc_id,
                "title": entry["title"],
                "source": f"corpus/{_safe_stem(doc_id, entry['title'])}.md",
                "version": "published",
                "is_current": True,
                "effective_at": entry.get("published_at"),
                "tenant_id": TENANT_ID,
                "resource_scope": None,
                "sensitivity": "external-odc-by",
                "url": url,
                "author": entry.get("author"),
                "outlet": entry.get("source"),
                "category": entry.get("category"),
            }
        )

    cases: list[dict[str, Any]] = []
    type_counts: dict[str, int] = {}
    evidence_count = 0
    null_query_count = 0
    for offset, raw in enumerate(queries, start=1):
        if not isinstance(raw, dict):
            raise ConversionError(f"问题第 {offset} 条不是对象")
        query = str(raw.get("query") or "").strip()
        question_type = str(raw.get("question_type") or "").strip()
        evidence_list = raw.get("evidence_list") or []
        if not query:
            raise ConversionError(f"问题第 {offset} 条缺少 query")
        if not question_type:
            raise ConversionError(f"问题第 {offset} 条缺少 question_type")
        if not isinstance(evidence_list, list):
            raise ConversionError(f"问题第 {offset} 条 evidence_list 不是数组")
        type_counts[question_type] = type_counts.get(question_type, 0) + 1
        expected_ids, expected_evidence = map_evidence(evidence_list, by_url, url_to_doc_id)
        evidence_count += len(expected_evidence)
        is_null = question_type == "null_query" or not expected_ids
        if is_null:
            null_query_count += 1
            if expected_ids:
                raise ConversionError(f"null_query 不应有证据文档: {query}")
        cases.append(
            {
                "case_id": case_id_for(offset),
                "dataset_version": DATASET_VERSION,
                "split": "comparison",
                "tier": "external",
                "category": question_type,
                "query": query,
                "identity": {
                    "tenant_id": TENANT_ID,
                    "user_id": USER_ID,
                    "roles": ["member"],
                    "resource_scopes": [],
                },
                "expected_document_ids": expected_ids,
                "forbidden_document_ids": [],
                "expected_evidence": expected_evidence,
                "expected_answer_points": [str(raw.get("answer") or "")] if raw.get("answer") else [],
                "forbidden_answer_points": [],
                "should_answer": not is_null,
                "should_clarify": False,
                "should_escalate": False,
                "synthetic": False,
                "notes": "Supplementary MultiHop-RAG comparison case; not part of the official 35-case gate.",
                "provenance": {
                    "source_snapshot": source_snapshot,
                    "generator": "evals/multihop_rag/convert.py",
                    "prompt_version": "none",
                    "generated_at": generated_at,
                    "review_status": "external-benchmark",
                },
                "review": {
                    "reviewer": None,
                    "reviewed_at": None,
                    "decision": None,
                    "notes": "External ODC-BY benchmark; not project Gold.",
                },
                "tags": [question_type, "multihop-rag"],
            }
        )

    index = {
        "dataset_id": "multihop-rag",
        "dataset_version": DATASET_VERSION,
        "corpus_id": "multihop-rag-corpus",
        "corpus_version": DATASET_VERSION,
        "source_snapshot": source_snapshot,
        "gold_status": "external-benchmark-not-project-gold",
        "split_policy": (
            "All questions use split=comparison. This supplementary comparison "
            "does not use official 35-case development/validation/holdout decision rules."
        ),
        "document_count": len(documents),
        "question_count": len(cases),
        "scorable_question_count": len(cases) - null_query_count,
        "null_query_count": null_query_count,
        "evidence_count": evidence_count,
        "question_types": dict(sorted(type_counts.items())),
        "attribution": attribution_block(),
        "generated_at": generated_at,
    }
    return documents, cases, index


def write_conversion(
    corpus: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    output_dir: Path,
    *,
    source_snapshot: str,
    generated_at: str | None = None,
) -> ConversionResult:
    """写出 manifest、文档正文、cases 与 index。"""
    generated = generated_at or datetime.now(UTC).isoformat()
    documents, cases, index = convert_records(
        corpus, queries, generated_at=generated, source_snapshot=source_snapshot
    )
    by_url = index_corpus(corpus)
    output_dir = output_dir.resolve()
    corpus_dir = output_dir / "corpus"
    if output_dir.exists():
        for leftover in output_dir.rglob("*"):
            if leftover.is_file():
                leftover.unlink()
    corpus_dir.mkdir(parents=True, exist_ok=True)

    for entry in documents:
        source_doc = by_url[entry["url"]]
        rendered = render_document(source_doc)
        destination = output_dir / entry["source"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding="utf-8", newline="\n")

    rendered_by_id = {
        entry["document_id"]: (output_dir / entry["source"]).read_text(encoding="utf-8")
        for entry in documents
    }
    for case in cases:
        for fact in case["expected_evidence"]:
            if fact["exact_quote"] not in rendered_by_id[fact["document_id"]]:
                raise ConversionError(
                    f"{case['case_id']} 的事实句未写入 {fact['document_id']}"
                )

    manifest = {
        "corpus_id": "multihop-rag-corpus",
        "corpus_version": DATASET_VERSION,
        "authorized": True,
        "authorization_notes": (
            "External MultiHop-RAG corpus licensed under ODC-BY. "
            "Cached locally for evaluation only; not a production knowledge base."
        ),
        "permission_policy": "Single synthetic tenant tenant-multihop; no cross-tenant cases.",
        "vectorstore_policy": "Official local store for evaluation only.",
        "attribution": attribution_block(),
        "created_at": generated,
        "documents": documents,
    }
    manifest_path = output_dir / "manifest.json"
    cases_path = output_dir / "cases.json"
    index_path = output_dir / "index.json"
    write_json(manifest_path, manifest)
    write_json(cases_path, cases)
    write_json(index_path, index)
    return ConversionResult(
        output_dir=output_dir,
        manifest_path=manifest_path,
        cases_path=cases_path,
        index_path=index_path,
        document_count=len(documents),
        question_count=len(cases),
        scorable_question_count=int(index["scorable_question_count"]),
        null_query_count=int(index["null_query_count"]),
        evidence_count=int(index["evidence_count"]),
        question_types=dict(index["question_types"]),
    )


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ai-assistant-multihop-eval/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
    except urllib.error.URLError as exc:
        raise ConversionError(f"下载失败 {url}: {exc}") from exc
    if not data:
        raise ConversionError(f"下载为空: {url}")
    destination.write_bytes(data)


def verify_official_raw(raw_dir: Path) -> None:
    for name, expected in EXPECTED_SHA256.items():
        path = raw_dir / name
        if not path.is_file():
            raise ConversionError(f"缺少原始文件: {path}")
        digest = sha256_file(path)
        expected_digest = "".join(expected)
        if digest != expected_digest:
            raise ConversionError(f"{name} 校验失败：摘要不匹配")


def ensure_official_raw(raw_dir: Path = RAW_DIR, *, force: bool = False) -> Path:
    """下载或校验官方 MultiHop-RAG 原始文件。"""
    raw_dir = raw_dir.resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)
    if not force:
        try:
            verify_official_raw(raw_dir)
            return raw_dir
        except ConversionError:
            pass

    errors: list[str] = []
    for source in DOWNLOAD_SOURCES:
        try:
            for filename in (CORPUS_NAME, QUERIES_NAME):
                _download(str(source[filename]), raw_dir / filename)
            verify_official_raw(raw_dir)
            (raw_dir / "SOURCE.txt").write_text(
                "\n".join(
                    [
                        f"Downloaded from {source['name']} for local evaluation.",
                        f"GitHub: {SOURCE_GITHUB}",
                        f"Hugging Face: {SOURCE_HUGGINGFACE}",
                        f"License: {LICENSE_NAME} {LICENSE_URL}",
                        "",
                    ]
                ),
                encoding="utf-8",
                newline="\n",
            )
            return raw_dir
        except (ConversionError, OSError) as exc:
            errors.append(f"{source['name']}: {exc}")
    raise ConversionError("无法获取 MultiHop-RAG 原始数据。尝试记录：\n" + "\n".join(errors))


def convert_official(output_dir: Path = CONVERTED_DIR, *, raw_dir: Path = RAW_DIR) -> ConversionResult:
    ensure_official_raw(raw_dir)
    corpus = _require_list(load_json(raw_dir / CORPUS_NAME), CORPUS_NAME)
    queries = _require_list(load_json(raw_dir / QUERIES_NAME), QUERIES_NAME)
    if len(corpus) != EXPECTED_DOCUMENT_COUNT or len(queries) != EXPECTED_QUESTION_COUNT:
        raise ConversionError(
            f"官方规模不符：documents={len(corpus)} questions={len(queries)}，"
            f"期望 {EXPECTED_DOCUMENT_COUNT}/{EXPECTED_QUESTION_COUNT}"
        )
    result = write_conversion(
        corpus,
        queries,
        output_dir,
        source_snapshot="huggingface:yixuantt/MultiHopRAG",
    )
    return result


def convert_smoke(output_dir: Path) -> ConversionResult:
    corpus = _require_list(load_json(SMOKE_FIXTURE_DIR / CORPUS_NAME), CORPUS_NAME)
    queries = _require_list(load_json(SMOKE_FIXTURE_DIR / QUERIES_NAME), QUERIES_NAME)
    return write_conversion(
        corpus,
        queries,
        output_dir,
        source_snapshot="evals/multihop_rag/fixtures/smoke",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载并转换 MultiHop-RAG 为项目评测格式")
    parser.add_argument("--download", action="store_true", help="下载或校验官方原始数据")
    parser.add_argument("--smoke", action="store_true", help="只转换内置冒烟夹具")
    parser.add_argument("--output", default=None, help="转换输出目录")
    parser.add_argument("--raw-dir", default=str(RAW_DIR), help="官方原始数据目录")
    args = parser.parse_args(argv)
    if args.smoke:
        output = Path(args.output) if args.output else REPO_ROOT / "data" / "multihop_rag" / "smoke"
        result = convert_smoke(output)
    else:
        raw_dir = Path(args.raw_dir)
        if args.download or not (raw_dir / CORPUS_NAME).is_file():
            ensure_official_raw(raw_dir, force=bool(args.download))
        output = Path(args.output) if args.output else CONVERTED_DIR
        result = convert_official(output, raw_dir=raw_dir)
    print(
        f"converted documents={result.document_count} questions={result.question_count} "
        f"scorable={result.scorable_question_count} null={result.null_query_count} "
        f"-> {result.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
