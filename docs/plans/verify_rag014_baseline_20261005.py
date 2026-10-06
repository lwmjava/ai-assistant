"""隔离进程复核 HEAD 服务下的失败；不修改工作树或原业务代码。"""
import os
import subprocess
import sys

os.environ["DATABASE_URL"] = "sqlite:///./data/test_ai_assistant_rag014_head.db"
os.environ["EMBEDDING_PROVIDER"] = "mock"

import app.rag.service as service  # noqa: E402

baseline = subprocess.check_output(["git", "show", "HEAD:app/rag/service.py"], text=True, encoding="utf-8")
exec(compile(baseline, "HEAD/app/rag/service.py", "exec"), service.__dict__)

import pytest  # noqa: E402

sys.exit(pytest.main([
    "tests/eval/test_rag006_pipeline.py",
    "tests/test_chat.py::test_chat_stream_reports_unexpected_failure",
    "tests/test_chat.py::test_chat_stream_returns_sse",
    "tests/test_chat.py::test_code_result_persists_on_stream",
    "tests/test_chat_controls.py::test_stream_emits_conversation_id",
    "tests/test_p0_regression.py::test_delete_document_cleans_vectors",
    "tests/test_rag.py::test_hybrid_search_bm25_all_zero_preserves_dense_order",
    "tests/test_rag.py::test_hybrid_search_bm25_all_zero_reason_empty_query_tokens",
    "tests/test_rag.py::test_hybrid_search_bm25_all_zero_reason_empty_doc_tokens",
    "tests/test_rag.py::test_sources_keep_filename_and_existing_location",
    "-q", "--basetemp=data/pytest-tmp/rag014-head-01",
]))
