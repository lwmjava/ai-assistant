# 后端失败项整改实现说明

## 功能与契约

用户已批准八组整改方案。原11个 pytest 失败、128个 Ruff错误、47个 mypy错误均已处理；全量验证通过。本轮提前消除失败，不实施 backlog 的 NFR-001 CI 建设，QA-005 保持 in_review，不自动关闭 RAG-014 或标记生产发布合格。

- 流式对话首事件和中途异常返回既有 SSE error 事件，用户只看到“生成失败，请稍后重试”；日志记录异常类型，不记录异常正文。迭代完成、异常或取消后关闭流。保留既有配额/会话错误映射；新增中途异常与生成器关闭测试。
- 测试生命周期重置 sse_starlette AppStatus 事件，不修改生产 SSE 全局状态；不同 TestClient 事件循环不共享旧事件。
- 旧删除断言改为已批准软删90天语义，要求不立即删除向量且 deleted_at 已写入；到期、重试和租户隔离由现有清理补偿测试覆盖。
- Citation 断言包含每条来源的 chunk_id/document_id/excerpt/page/section，保留同文档不同分块；缺失文档不返回来源，不降低断言强度。
- Alembic 日志配置不禁用已有 logger，BM25 原有排序与原因日志断言均通过。
- 注入评测固定 structured 策略，避免本机 parent_child 配置改变评测前提。第一项因此恢复事实正文；第二项发现本机相似度阈值0.4令攻击块未进入执行层测试，改用该两项专用确定性向量样本，让所有样本相似度为1；不降低阈值、不改生产 Mock/provider/检索算法。攻击样本明确进入不可信上下文后仍验证工具调用被拒绝；仅证明防护链路，不宣称真实召回质量。

## 类型与格式

SQLModel查询使用 col()，TextClause 使用 Session.execute；基类 stream_chat 声明返回 AsyncIterator 的普通抽象方法，对应实现异步生成器；ChatService.chat_stream 使用 AsyncGenerator 注解支持关闭。补技能 ToolRegistry import；MCP会话显式动态类型用于可选协议对象，不修改接口或忽略整个文件；Webhook先收窄空URL，空值不回调；日志参数以Mapping声明；Unix专属终止API在对应平台路径动态获取；成员建表从SQLModel metadata取表；消除同作用域变量重复声明；API注解准确反映JSONResponse分支，公开response_model保持不变。

项目conda安装并在dev extra固定 types-PyYAML==6.0.12.20260906、types-croniter==6.2.4.20260711；初次下载发生SSL重试，最终安装成功。不放宽mypy配置或检查范围，不新增全文件ignore。生成脚本仅格式/导入排序整理，六个生成器与HEAD的AST在规范化导入顺序后一致；没有执行生成器重写tasks或评测集。Milvus门槛脚本内部异常名添加Error后缀，未运行真实Milvus门槛。

主要代码：app/api/routes/chat.py、app/services/chat_service.py、app/llm/base.py、app/agents/pipeline.py、app/agents/skills/manager.py、app/agents/tools/sandbox/{proc,sandbox}.py、app/security/log_redaction.py、app/mcp/client.py、app/workflow/{engine,scheduler}.py、app/services/{admin_users,auth_service,invitations,tenant_admin}.py、app/evolution/distiller.py、app/api/routes/{health,rag,workflow}.py、alembic/env.py。测试：tests/conftest.py、tests/test_chat.py、tests/test_p0_regression.py、tests/test_rag.py、tests/eval/test_rag006_pipeline.py。格式涉及其他已有迁移、docs/scripts/evals与smoke测试文件；当前工作区还包含此前RAG-014未提交改动，不全部归因于本卡。

## 命令与结果

解释器：D:/DepTooL/anaconda3/envs/ai-assistant/python.exe。pytest显式DATABASE_URL=sqlite:///./data/test_ai_assistant_qa005.db，EMBEDDING_PROVIDER=mock；独立RAG回归使用test_ai_assistant_qa005_rag.db。未操作运行知识库、索引或配置.env。

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| 开工前全量 `-m pytest -q`（data/rag014-schema-full.txt） | 1 | 563 passed / 11 failed / 2 skipped |
| 第一轮定向 pytest（Chat/Controls/P0/RAG/注入） | 1 | 85 passed / 2 failed；只剩注入测试 |
| `-m pytest tests/eval/test_rag006_pipeline.py -q --basetemp=data/pytest-tmp/qa005-injection-02` | 0 | 7 passed |
| `-m pytest -q --basetemp=data/pytest-tmp/qa005-full-01` | 0 | 575 passed / 2 skipped / 1 warning，130.10s |
| `-m pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py -v --basetemp=data/pytest-tmp/qa005-rag-final-01` | 0 | 82 passed / 1 skipped |
| `-m ruff check . --no-cache` | 0 | All checks passed |
| `-m mypy app/ --cache-dir data/mypy-qa005-final` | 0 | Success: no issues found in 172 source files |
| 项目环境 YAML/ID/依赖/无环与 git diff --check | 0 | 182卡，依赖目标存在；NFR-001仍backlog |

全量结果在data/qa005-{full-final,rag-final,ruff-final,mypy-final}.txt。日志类别最后统一为脱敏类型后定向复跑首事件/中途异常（--basetemp=data/pytest-tmp/qa005-final-error-log-01），退出码0、2 passed；再次全量Ruff和diff检查退出码0。skip不计通过；唯一warning是ast.NameConstant弃用提示，不作为错误隐藏。前端未改，未运行前端构建、真实LLM/Milvus或生产性能评测。

遇到的执行问题如实记录：Python通过PowerShell stdin的中文转义造成两次脚本定位失败，改为ASCII定位后修复；默认Ruff缓存权限拒绝使用--no-cache；两个scripts文件普通沙箱写入拒绝，提升权限完成已授权格式/命名修正；一次批量字符串格式尝试未通过AST校验，在写入该文件前停止，后按实际长行定位并校验语义等价。没有用这些执行失败替代业务断言。

## 自查、恢复与残余边界

核对生产参数、权限、租户与删除路径未放宽；确认Citation严格断言及恶意工具拒绝断言保留、全量检查范围未收缩。此为本轮实现自查，不伪称独立审查。回滚本批代码/测试/类型声明即可，保留RAG-014已备份数据库字段，不执行数据库降级。新框架、ACL、重排器、生产发布与前端改造未实施。独立审查和人工业务验收仍需另记录；不因测试全绿自动关闭其他卡。
