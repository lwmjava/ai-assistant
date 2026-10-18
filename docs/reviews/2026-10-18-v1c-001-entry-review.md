# V1C-001 独立入口级反例审查

日期：2026-10-18。审查 Agent：`/root/v1c001_validation`，未参与业务实现；仅编写独立反例测试及本报告。依据：原始 tasks.yaml 卡、已批准实施计划、Accepted ADR-0013、task-card-delivery 1.3.0 和适用项目规则。审查基线 HEAD 为 f350e94b3829a0d48f9a0cfce467c01bae5dd662，候选为 HEAD 加工作区；不是新提交或生产证据。

## 审查范围及结论

阅读 ChatService 完整业务 diff、新增 ChatRetriever 全文、RAGService 构造及父块展开、HybridRetriever/assemble_retrieval、CachedRagBackend/AuthorizedCacheScope，以及三消费者（Pipeline、Fast、Supervisor）的检索与错误边界。未发现本次改动引入的待修复 P1/P2 业务缺陷。该结论限于所列输入；不等于整卡关闭、用户业务接受或最终提交 CI 通过。

请求内闭包不放入全局；同步建立读域并复制非敏感主体字段；身份读域校验发生在三消费者 broad except 之前。真正检索才构造原 RAGService，普通构造异常转 unavailable 且来源清空；CancelledError 不属于捕获的 Exception，继续传播。last_selected 在惰性钩子被按预算组装后写回，ChatService 仅由该列表生成来源，不回落全量命中。RAGService 管理/摄取生命周期未被改变。

## 独立反例与实际结果

正式新增 `tests/test_chat_retrieval_lifecycle_review.py`，14 条均通过：

| 反例 | 真实操作及断言 | 证据级别/结果 |
|---|---|---|
| tenant/uploader 读域及父块来源 | 临时 SQLite 中真实 RAGService 摄取当前用户、同租户另一上传者及跨租户文档；创建同文档父子关联，经 ChatService chat/stream 执行，检查模型实际 payload，并以新 Session 回读持久化来源 | E2，8条通过；含 self 与附件强制进入 langgraph 完整编排路径；tenant 保留同租户共享，uploader 拒绝另一上传者，跨租户均不泄露 |
| 未选块不得声明来源 | 同样真实摄取，将预算设为1，ChatService 两入口完成后新 Session 回读来源为空，模型输入无父块正文 | E2，2条通过 |
| 取消不得变成 unavailable | 依赖工厂抛 asyncio.CancelledError，真实 chat/stream 均传播取消，工厂各只调用一次，持久化消息没有伪不可用披露 | E2故障注入，2条通过 |
| 身份快照绑定 | 从真实 ChatService 构建惰性钩子后，修改原脱管 User 的 id/tenant/role；真实 local 检索及来源组装仍只得到原 uploader 文档和父块；分别打开/关闭真实 RETRIEVAL_CACHE_ENABLED | E2，2条通过 |

MockEmbeddingProvider(dim=64) 与离线 MockLLM 是合成提供者，仅用于索引/权限/状态/来源链路契约，不证明生产检索或答案质量。所有文档和账户为测试生成，使用临时库；未读取 .env、未访问现有业务数据、未调用收费模型。

## 执行记录

由主 Agent 停止其他 pytest 后独占执行。解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，Windows，Python3.12.0，pytest8.4.2，Ruff0.8.4。

1. `python -m pytest tests/test_chat_retrieval_lifecycle_review.py -v --tb=short`：退出码1，14条setup错误。原因是 pytest 默认 basetemp 的既有 `data/pytest-tmp/run` 目录无法删除（WinError5）；业务断言未执行，不能计为业务失败或通过。未删除该目录，改用新的本任务隔离临时目录。
2. `python -m pytest tests/test_chat_retrieval_lifecycle_review.py -v --tb=short --basetemp=data/v1c001-review-tmp-01`：退出码0，14 passed，1 warning，8.53秒。警告来自既有 sandbox ast.NameConstant 弃用。
3. `python -m ruff check tests/test_chat_retrieval_lifecycle_review.py --no-cache`：退出码0，All checks passed。

完整命令的 `python` 均指上述项目解释器。工具调用输出保留完整运行记录；主 Agent 首轮8条的独立文件实跑日志另存 `data/v1c001-baseline/review-first.txt`，该8条为较早测试输入的历史记录，不代替本轮14条。

## 候选输入指纹及剩余门禁

SHA256（独立执行后核对）：

- app/services/chat_service.py：`E4EA4E55291152169A38BEAEB49F9E971EC52EF424FF1499212585844CC708A9`
- app/services/chat_retrieval.py：`91946079FFF339933F841927A5A8EB0AEACAA70FBA16B07808D59B2672865E9D`
- tests/test_chat_retrieval_lifecycle_review.py：`34E95E507B2D50201B6C36ED5A56938CBA7038954B2E033F46DD67BF36F462E7`

独立14条不替代主线的工厂逐项故障、身份拒绝、恢复/重新生成、Supervisor/fallback、数值及全量门禁。主 Agent 仍需汇总最终完整测试 diff、全量 pytest、Ruff/mypy、版本化 Evaluation 和最终输入指纹；业务/测试输入变化需重跑受影响检查。本卡最终提交 Ubuntu CI、独立最终人工审核及用户业务接受由主线程继续对账，未提供时不得以本报告宣称 done。没有提交、推送、合并、部署或真实索引操作。

## 最终输入只读复核补记

主 Agent 冻结最终专项后，本独立审查 Agent 再次读取完整主线测试文件、业务 diff、新增钩子、README/ADR索引/tasks变更以及 Smoke/Adversarial Evaluation。两业务文件及独立14条测试指纹均与上述独立实跑时一致，不需要重跑无关检查。本轮没有并行 pytest。

以标准库 `json`/`hashlib`/`pathlib` 独立逐项读取 `docs/reviews/2026-10-18-v1c-001-evidence.json` 的 input_sha256 并计算文件字节指纹：**514项全部匹配，mismatches=[]**，检查命令退出码0。主线最终测试文件 SHA256 为 `146D0E73E82D3635E32424D465EE4DB688A4B544BBFB7F730C7B7437368B10E0`。

主线新增覆盖对账：三依赖工厂×chat/stream×非检索路由零构造；三检索消费者各工厂故障披露且持久化无来源；构造失败有界且新请求可重试；元数据访问不初始化；RAG关闭；Supervisor ImportError回退；真实缓存开关；同步读域不一致拒绝；检索前取消零构造；原消息和回复保留的重新生成；持久化checkpoint恢复且不重复用户消息；NaN/Inf/极端/零相似度与原 eager语义对照；三检索消费者空库no_hit/无身份索引unavailable/全低分拒答。已读取专项日志 `data/v1c001-baseline/contract-final3.txt` 的汇总：85 passed、1 warning、38.31秒，主 Agent 报告实际退出码0。此85条为当前两正式测试文件的E2/E3混合覆盖；数值合成后端对照仅证明与原实现一致，不证明底层算法对所有非有限值安全或检索质量。

最终只读复核未发现新增待修复P1/P2。全量pytest在此补记时尚未结束，**未据此宣称全量通过**；Ruff/mypy为主 Agent 报告通过，最终完整输出及全量结论由总报告记录。没有将额外测试存在等同新运行成功，也没有把历史CI替代本候选CI。
