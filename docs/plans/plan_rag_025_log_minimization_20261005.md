# RAG 日志内容最小化实施计划

任务：RAG-025。日期：2026-10-05。用户已批准以本卡首推。L1，可逆代码修改，无数据迁移、外发或新依赖。

## 目标与现状

检索低分日志含 query/plan 前 200 字，Embedding 上游错误日志含响应正文，对话 sources 日志含文档摘录。异常文本可能回显文档、请求或凭据。现有通用脱敏只能识别部分模式，无法识别任意敏感正文。

采用源头内容最小化：不记录 query/plan/content/source 摘录、上游响应正文、异常消息或未经约束的配置字符串；保留稳定事件名、关联 ID、数量、阈值、状态码及异常类别。现有 JSON 日志 trace_id/user_id/tenant_id 保持可关联。不改变实际检索、答案、sources 响应、异常传播或降级行为。

替代方案：仅加强正则会漏掉任意业务正文；全部关闭日志会失去排障能力。因此沿用现有日志系统，删除正文并保留元数据，不重建日志平台。

## 文件归属与边界

- 实现 Agent A：仅 app/rag/ 内日志调用及必要的安全事件辅助组件；保留现有业务语义，不修改权限/分数/版本/清洗规则。
- 测试 Agent B：仅新增 tests/test_rag_log_minimization.py 与必要的确定性测试数据；独立构造敏感标记，不改既有测试门禁。
- 主 Agent：app/services/chat_service.py 的 sources 计数日志、本文、实现说明、独立审查记录、tasks.yaml，以及已批准的治理决策同步。公共文件单写者。已有暂存改动保留，提交通过差异隔离，不把测试整改混入本卡。
- 最终只读审查 Agent：只读整合后的任务 diff、代码与验证证据；无写权限分配。

allowed_paths 沿用 tasks.yaml scope：app/rag/、app/services/chat_service.py、app/core/、app/security/、tests/、docs/、tasks.yaml。禁止 .env、生产数据和冻结 baseline 报告。

## 非目标

不重写日志平台、不改变前端显示/API来源、不新增缓存或权限模式、不修复 QA-005 既有债务、不安装可选后端、不修改向量检索、导入恢复和模型行为。ADR/页面卡同步仅记录本次明确批准，不实施其它卡。

## 验收与验证

先运行失败用例，敏感标记分别出现在 query、plan、命中摘录、上游响应和异常文本中，检验最终 JsonLogFormatter 与普通 Formatter 输出；源头记录中也不得保留正文参数。验证成功/低分/Embedding HTTP 故障及 RAG 异常/降级日志仍保留事件、计数或错误类别，并保留 trace 关联。

实际 sources 响应和检索结果不因日志修改改变；稳定事件可用、手机号/凭据/敏感问题/文档标记不出现在最终输出。业务人工验收提供“一次检索和一次受控错误”预期日志；程序证据不以 Mock 声称检索质量提升。

项目环境：D:\DepTooL\anaconda3\envs\ai-assistant\python.exe。执行：专项测试；pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -v；pytest；ruff check .；mypy app/；YAML/ID/依赖无环；git diff --check。使用独立 pytest basetemp 和测试 DB，不并行执行共享测试库的 pytest。可选依赖的模块级跳过单独报告，涉及本卡的测试必须实际执行。

## 失败、回滚与完成

日志格式或测试失败不关闭卡；回滚仅本卡代码 diff，保留其它未提交改动。禁止恢复包含正文的日志作为常规排障开关；后续排障按关联 ID、状态和类型定位。无需索引/数据库回滚。

整合后进行独立只读审查，实际验证并记录未验证项；未满足人工业务验收则 in_review，不能假标 done。用户确认关闭后只提交本卡一次，不推送/合并/部署，不自动推进其它模块。

## 独立审查补齐范围

初审发现：检索异常经过现有Pipeline兜底logger.exception会再次打印正文；真实httpx INFO会记录完整请求URL。主Agent在已有app/security/log_redaction.py输出过滤器内补两个精确边界，不修改不在allowed_paths的Pipeline/API业务文件。管线仅异常日志改为固定事件与错误类别；httpx请求仅保留已知HTTP方法和有效状态码，不保留URL/原因正文。httpx输出保护适用于同一进程的其它HTTP请求，保留trace关联，不改其实际请求。这是现有过滤器增量，不另建日志平台。

测试Agent新增真实Pipeline异常调用及httpx真实Client+MockTransport故障用例，先确认最终普通/JSON输出泄露再修复；最终重跑受影响专项和全部回归。初审问题与复审记录见docs/reviews，不能跳过已发现缺口。
