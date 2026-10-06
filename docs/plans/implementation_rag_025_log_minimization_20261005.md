# RAG 日志内容最小化实现说明

任务：RAG-025。日期：2026-10-05。状态：done；实现、最终验证与独立审查完成，2026-10-06 用户页面验收并明确批准关闭。计划：[实施计划](plan_rag_025_log_minimization_20261005.md)。

## 实际行为

用户照常检索、上传文档和接收带 sources 的对话回复。检索低分仍按原阈值过滤，页面/持久化来源内容不变；Embedding 的 HTTP 异常仍向调用方抛出，导入失败状态与原有降级返回不变。

RAG 日志移除 query/plan、文档摘录、Embedding 响应正文/模型配置字符串、未知 backend/splitter 配置和原始异常消息/回溯。保留固定事件名、计数、阈值、HTTP 状态、内部文档/任务 ID及错误类别；JSON formatter 的 trace_id/user_id/tenant_id 继续关联请求。sources 日志只记录 source_count，SSE sources 与持久化仍保持原文。

上传 API 不在本卡业务修改范围。现有 RedactingLogFilter 对其 logger 的四种旧消息映射为固定事件，仅保留 error_type，清除文件名参数和异常回溯；未知消息输出 rag_api_event。同一记录经 logger/handler 重复过滤仍保留第一次事件与错误类型。其它 logger 的既有通用脱敏行为保持。

独立审查进一步发现 Pipeline 兜底会再次输出检索异常，httpx INFO会记录完整请求URL。补丁在同一允许的security输出边界处理：Pipeline仅异常日志记录固定事件/错误类型；httpx全进程请求日志只保留allowlist方法和有效状态码，不记录URL或原因正文，未知模板为固定http_client_event。实际请求/异常失败状态不变，普通与JSON格式器均验证不泄漏。

这仅是日志安全修复，不证明实际检索质量提高，也不改变 API 的错误/来源契约。既有导入错误信息与数据库 ImportJobTrace 仍按原业务行为处理；本文不把持久化 Trace 改造写成已完成。

## 文件与协作

实现 Agent：app/rag/retriever.py、embeddings/openai_compatible.py、backend/factory.py、backend/langchain_backend.py、backend/llamaindex_backend.py、import_jobs.py、import_scheduler.py、service.py、vectorstore/milvus.py，共9文件，仅日志及异常绑定名变化。

测试 Agent：tests/test_rag_log_minimization.py，18项确定性 Observed Regression v1（初稿15项，审查新增真实Pipeline同步/流式和httpx链路3项），使用不匹配常规凭据正则的合成正文标记，检验源 LogRecord 与普通/JSON 输出，不伪造 Gold 或模型质量结果。输出边界用例经真实logger/handler/filter，保留真实业务状态断言。

主 Agent：app/services/chat_service.py、app/security/log_redaction.py、计划/本文/审查与任务证据。另按用户明确批准同步权限 ADR、ADR-0005、设计映射及新增页面 RAG-042；这些是治理记录，不宣称相关功能已实现。

## 验证记录

项目解释器：D:\DepTooL\anaconda3\envs\ai-assistant\python.exe。测试使用 conftest 隔离测试 DB 与 data/pytest-tmp，不运行真实供应商或操作生产数据。

| 实际命令 | 结果/报告 |
|---|---|
| pytest tests/test_rag_log_minimization.py -q --basetemp=data/pytest-tmp/rag025-tests-red | 首次失败基线7failed/1passed，确认正文泄露；之后增加边界用例。 |
| pytest tests/test_rag_log_minimization.py -q -k api --basetemp=data/pytest-tmp/rag025-api-red | API过滤实现前5failed/10deselected，确认缓存回溯和文件名泄露。 |
| pytest tests/test_rag_log_minimization.py tests/test_log_redaction.py tests/test_embeddings.py tests/test_rag_threshold.py -q --basetemp=data/pytest-tmp/rag025-targeted-01 | 32passed，退出0；data/rag025-targeted.txt。 |
| pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -v --basetemp=data/pytest-tmp/rag025-rag-01 | 82passed/1skipped，退出0；data/rag025-rag.txt。 |
| pytest -q --basetemp=data/pytest-tmp/rag025-full-01 | 首次全量2failed/588passed/2skipped；data/rag025-full.txt；新测试直接修改logger.level导致INFO缓存未失效，已改setLevel并恢复，未减少断言。 |
| pytest tests/test_logging.py tests/test_log_redaction.py tests/test_rag_log_minimization.py -q --basetemp=data/pytest-tmp/rag025-tests-log-sequence | 修复测试隔离后31passed，退出0（测试Agent实际执行）。 |
| pytest -q --basetemp=data/pytest-tmp/rag025-full-02 | 测试隔离修复后590passed/2skipped/1warning，退出0；data/rag025-full-final.txt。这是审查补丁之前的结果。 |
| pytest tests/test_rag_log_minimization.py -q -k 'pipeline_retrieval_failure or real_httpx' --basetemp=data/pytest-tmp/rag025-review-red | 审查补丁前3failed/15deselected，确认上层异常/真实HTTPX URL泄露（测试Agent实际执行）。 |
| pytest tests/test_rag_log_minimization.py tests/test_log_redaction.py tests/test_logging.py tests/test_embeddings.py tests/test_rag_threshold.py -q --basetemp=data/pytest-tmp/rag025-reviewed-targeted | 最终专项及相关回归41passed，退出0；data/rag025-targeted-final.txt。 |
| pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -v --basetemp=data/pytest-tmp/rag025-rag-02 | 补丁后82passed/1skipped，退出0；data/rag025-rag-final.txt。 |
| pytest -q --basetemp=data/pytest-tmp/rag025-full-03 | 补丁后最终593passed/2skipped/1warning，118.51s，退出0；data/rag025-full-reviewed.txt。 |
| ruff check --no-cache . | 全量通过，退出0；data/rag025-ruff-final.txt。 |
| mypy --cache-dir data/mypy-rag025-02 app/ | 补丁后172源文件通过，退出0；data/rag025-mypy-final.txt。 |
| 项目解释器 yaml.safe_load / ID唯一 / 依赖存在与无环 / index快照比对 | 183卡通过，旧暂存index逐项一致；未操作暂存。 |
| git diff --check | 修改ADR行尾空格后通过。 |

两处全量可选依赖跳过来自 llama_index 与 mcp 模块级 importorskip；RAG专项的1次是同一后端跳过，不累计成3次。18项本卡日志用例实际执行，不依赖这些模块跳过。未安装可选依赖或修复其测试组织，不宣称全后端真实集成完成。

实现 Agent 额外核验：相对开工保存的9文件基线，移除日志表达式、统一异常绑定后AST一致，作为无业务改动的辅助证据；不能替代运行测试。

## 业务验收步骤与预期

无需重新上传全部文档。后端加载本次代码后，进行一次知识库检索和一次带知识来源的流式对话：页面来源正文仍显示；后台保留 `rag_search_backend` 或相关事件，sources仅显示 `rag_sources_returned source_count=N`，不出现所输入问题或来源摘录。低分路径出现 `rag_low_similarity_filtered kept=X/Y threshold=...`，不带query/plan。

受控故障已有18项可复现测试，不要求用户修改真实密钥或破坏数据库：运行项目解释器 `-m pytest tests/test_rag_log_minimization.py -q --basetemp=data/pytest-tmp/rag025-manual`，预期18passed；覆盖Embedding HTTP失败、导入/调度失败、后端回退及上传API、真实Pipeline与HTTPX最终日志。错误日志保留status/批量数/exception_type/error_type或任务ID，合成正文标记不出现，业务错误行为不变。

独立只读审查已通过，见[审查记录](../reviews/2026-10-05-RAG025日志最小化独立审查.md)，两项P1已补测试并修复；最终全量结果已补齐。仅待上述页面来源/后台日志业务核对后由用户确认关闭，不重复要求已关闭014/023验收。未改前端，未执行前端构建/页面自动化，不虚构页面实测证据。

## 回滚与未做事项

回滚仅本卡差异，不覆盖既有RAG-014/QA-005暂存。无需数据库/索引回滚；后续排障不能常规重新开启正文日志。未改变权限、智能切分、模型窗口、前端显示/下载、真实向量库切换或生产配置；未推送、合并、部署。用户批准的其它卡继续按准入逐张实施，Deferred和跨模块阻塞保持。

实现代码已按用户“提交当前仓库所有文件”的明确要求，包含在本地提交 `08a8100`；本次关闭仅提交任务状态与验收记录，不推送、合并或部署。

## 2026-10-06 用户验收与关闭

用户提供会话后台日志、会话回复及来源、知识库检索、检索后台日志四张截图：页面仍显示回答与来源摘录；展示日志没有输入问题或文档摘录，保留事件、计数、维度及关联 ID；检索 backend 为 langchain，fallback=false。未出现 sources 计数事件不被伪记为已看到；sources 输出保留与无正文日志由已有自动化测试及页面证据共同核对。维度不匹配 skipped=23 属 RAG-032 索引治理范围，不影响本卡日志验收。

用户随后明确要求“关闭 RAG-025”。用户未报告再次执行故障专项结果，不冒称用户运行。主 Agent 在关闭时使用项目解释器复跑 `-m pytest tests/test_rag_log_minimization.py -q --basetemp=data/pytest-tmp/rag025-close`，18 passed in 3.61s，退出码 0；项目环境 YAML/ID/依赖及 done 状态检查通过。源码、独立审查、回归和人工业务验收齐备，tasks.yaml 更新为 done。
