# RAG 老 Office 格式支持 Implementation Plan

**Goal:** 为 `.doc / .xls / .ppt` 增加解析支持，混合方案：`.xls` 用 `xlrd`，`.doc/.ppt` 用 LibreOffice 转现代格式后复用现有解析器。

**Tech Stack:** Python 3.11+, `xlrd`, LibreOffice（doc/ppt 转换器），复用 `python-docx` / `python-pptx`

---

### Task 1: 依赖与文档

- [ ] 在 `pyproject.toml` 与 `requirements.txt` 增加 `xlrd`
- [ ] 本地安装 `xlrd`

### Task 2: 先写失败测试

- [ ] `tests/test_legacy_office.py`
  - `test_xls_parser_extracts_sheet_rows`
  - `test_doc_parser_converts_and_reuses_docx_parser`
  - `test_ppt_parser_converts_and_reuses_pptx_parser`
  - `test_doc_parser_reports_missing_libreoffice`
  - `test_doc_parser_reports_conversion_failure`
  - `test_registry_supports_legacy_extensions`
- [ ] 运行测试确认失败（模块 / 解析器尚不存在）

### Task 3: 实现 legacy_office 并接线

- [ ] 新增 `app/rag/document_parsers/legacy_office.py`
  - `_libreoffice_binary()` / `_convert_with_libreoffice()`
  - `XlsDocumentParser` / `DocDocumentParser` / `PptDocumentParser`
- [ ] 在 `service.py` 注册新解析器
- [ ] 在 `registry.py` 更新支持格式提示

### Task 4: 导入任务回归

- [ ] `tests/test_rag_import_jobs.py` 增加 `.doc` 成功导入或失败任务回归

### Task 5: 部署与文档

- [ ] `Dockerfile` 安装 LibreOffice（writer + impress，`--no-install-recommends`）
- [ ] `README.md` 更新格式支持、部署要求、错误语义

### Task 6: 回归与检查

- [ ] `pytest tests/test_legacy_office.py tests/test_document_parsers.py tests/test_rag_import_jobs.py`
- [ ] `ruff check` 本次相关文件