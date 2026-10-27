# EVAL-003 独立技术复审（2026-10-26）

结论：发现 1 项 P1、1 项 P2，不能给出“无剩余 P1/P2”的结论。本次是独立技术审查，不代替业务接受、完整回归或发布授权；未修改实现、测试、任务状态，未提交/推送，真实网络调用为 0。

## 审查身份、版本与边界

审查者：独立子 Agent `/root/eval003_independent_review`，未参与本卡实现；未读取主 Agent 本轮审查记录或另一独立 Agent 本轮报告。采用 code-review-quality 技能的分块审查与缺陷优先原则；不采用与本次明确要求冲突的“最低发现数量”条款。

分支 feat/EVAL-002；HEAD `2549b43cf94e3a2f8ea0be0a8df618432bdd10a3`。当前工作区存在 NFR-005 未提交迁移、模型、RAG Service 等变更，均保留。故证据绑定为 HEAD 加下列工作树 SHA256，不能将历史业务 SHA `1c8da78` 的 CI 自动视为本次工作树证据。

Windows-11-10.0.22621-SP0；解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，Python 3.12.0；pytest 8.4.2、httpx 0.28.1、SQLModel 0.0.22、SQLAlchemy 2.0.36、Pydantic 2.10.4。

阅读 AGENTS.md、匹配 Global/Architecture/Runtime/RAG/Testing/Security 规则、EVAL-003 当前验收与 ADR-0023，以及治理规范第 11/14/17 节。按少于 400 行分块审查任务服务、Orchestrator、Quality、Adapter、Target、API、Reports 和前端直接调用方。

复现脚本首先执行 `tests/conftest.py` 的隔离环境初始化：屏蔽本机 .env 读取并清空宿主模型密钥，使用独立测试 SQLite；只注入合成凭据。RAG_VECTOR_STORE=local、RAG_BACKEND=native。真实质量 fixture 的索引/目标/管理员/数据集均在内存 SQLite 中创建。HTTP 传输使用 httpx.MockTransport；没有访问生产库或真实模型服务。

## P1：零预算真实检索任务绕过 Embedding 请求计数与外发确认

位置：`app/services/evaluation_orchestrator.py:165-182`，根因集中在 168 行普通 RAGService 分支；准入对应 `app/services/evaluation_jobs.py:203-218`；页面实际提交对应 `frontend/src/pages/Evaluations.tsx:86` 附近。

触发：管理员通过真实资源 RAG 检索入口，关闭 generation/judge，提交 max_external_calls=0、max_cost_yuan=0、data_egress_confirmed=False；部署已配置真实 Embedding provider，查询未命中缓存。前端检索模式正是如此构造请求。create_real 接受该任务；仅 generation/judge 分支要求确认和计费快照。

执行链：API create_job → create_real → execute_job → run_real_evaluation → _run_rag → 普通 RAGService → NativeRagBackend._retrieve → OpenAICompatibleEmbeddingProvider.embed → HTTP /embeddings。只有启用生成时才装配 QualityEmbeddingProvider；普通分支没有调用预算预占或记账，也沿用公共 Embedding 重试策略。

影响：用户明确的零外部调用预算不能阻止 Embedding 外发/费用；自动 Silver 查询包含文档片段，可能在没有该任务外发确认时进入当前 Embedding 服务。任务仍记录 external_calls_used=0，报告不能反映真实请求。这与 EVAL-003“未批准预算新增调用0、预算/用途可追溯”验收直接冲突。

最小复现命令（工作区根目录）：

```powershell
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe data/validation/eval003-independent-20261026/repro_zero_budget.py
```

实际退出码 0，输出：

```json
{"accepted_zero_budget":0,"data_egress_confirmed":false,"intercepted_http_count":1,"external_calls_used":0,"actual_network_calls":0}
```

证明范围：create_real、真实 RAGService.search/NativeRagBackend 和真实 OpenAICompatibleEmbeddingProvider 均执行；为避免依赖真实向量库，替换 vector store 为无命中 store、关闭缓存包装；在 create_real 前将隔离活动索引所有身份字段与 identity_key 设置为当前 provider 的 identity_from_provider 结果，保留正常 _read_index_id 索引核验；HTTP 由 MockTransport 截获。该证据证明会构造/尝试请求，不宣称本轮发生真实外发或费用。初次探针曾使用 fixture 的 test/test/8 索引并覆盖 _read_index_id；收到主 Agent 对夹具可达性的质疑后，已改为真实合成 provider 身份并移除该覆盖，两个反例重新运行仍为上述结果。此项补验证增强的是可达性证据，缺陷为本 Agent 独立发现。

建议：使真实检索的 Embedding 同样经过明确授权、有限调用/费用预算与持久预占，不应要求用户打开答案生成才能给检索计费。零预算在首次 Embedding 之前拒绝；对公共检索/Agent RAG 内部的嵌入和隐式重试补预算覆盖。增加正式回归：真实 provider 配置下，零预算/未确认外发的任务传输计数必须为0；授权任务计数与实际尝试数一致。

## P2：可预期 Embedding HTTP 失败逃逸，任务不进入终态并停止调度

位置：`app/services/evaluation_jobs.py:548-555`（遗漏外部请求异常收敛）；后果入口 `app/services/evaluation_jobs.py:600-607`。

触发：真实检索运行时 Embedding 服务返回 HTTP400（401、其他 HTTPStatusError、部分传输/解析错误亦属于需覆盖的相邻路径）。OpenAICompatibleEmbeddingProvider.raise_for_status 抛出 httpx.HTTPStatusError；该异常不属于 execute_job 捕获的 OSError/RuntimeError。

影响：异常越过 finished_at/commit，任务保留 running、finished_at=None。实际 dispatcher._run 把它当意外 worker 故障，调用 shutdown，停止领取后续 queued 任务；新提交任务也无法唤醒该 stopped dispatcher，需重启恢复。ADR 对意外基础设施异常停止调度的决定不意味着正常外部 HTTP 失败可以失去任务终态；当前验收明确要求“外部失败有确定终态”。

最小复现命令：

```powershell
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe data/validation/eval003-independent-20261026/repro_external_failure.py
```

实际退出码 0（脚本断言缺陷现状），输出：

```json
{"exception":"HTTPStatusError","status":"running","finished_at":"None","intercepted_http_count":1,"actual_network_calls":0}
```

脚本复用前例的真实检索/Embedding 链，改由 MockTransport 返回400，并从 execute_job 调用；未执行 dispatcher.shutdown，其对队列的后果由真实 `_run` 代码与现有 dispatcher 意外故障测试交叉确认，避免把推断写成已跑的端到端结果。

建议：在任务 runner 边界将已知外部/协议/输入异常转换成稳定脱敏错误码并持久 failed/finished_at，保留真正无法安全处理的基础设施错误停止策略。新增正式回归：Embedding HTTP400/401/429/5xx/timeout/坏JSON/维度错误进入确定终态，且下一排队任务可执行；不要吞掉取消异常或输出响应正文。

## 实际验证与覆盖

执行以下专项，退出码0：

```powershell
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/test_evaluation_jobs.py tests/test_evaluation_runtime_guards.py tests/test_evaluation_quality.py tests/test_evaluation_orchestrator.py -q
```

结果 `48 passed in 12.67s`。该结果仅证明当前已有断言通过；上面两个独立反例暴露了现有覆盖缺口，不能由绿灯抵消。两个复现均退出0，依赖 MockTransport，没有新增付费调用。早期 `rg app/services/evaluation*.py` 及 `rg app/rag/embeddings/*.py` 在 Windows 的未展开 glob 查询返回路径语法错误，后改用目录加 `-g`/直接文件读取；不属于业务测试失败。

已静态覆盖：创建幂等/冲突、权限快照与撤销复核、任务取消/超时/重启/有限队列、Quality预占与usage/配置漂移、RAG/Agent/Workflow实际路由、资源适配/执行主体、报告路径与哈希/准入、前端请求与报告直达。已动态覆盖上述四组现有专项与两个隔离反例。

审查限制：这是约10分钟预算内的有限独立审查，`review_incomplete`；未完成所有 API/Reports/前端测试、完整身份/取消竞态矩阵、Supervisor/工具内部所有网络调用、多个Workflow目标语义、真实模型质量、浏览器业务接受、完整后端/前端回归、Linux和远端CI复核。未启动本地full。历史CI run40254924700未在本轮查询，因此只视作历史记录；不判其失败，但也不将其升级为当前工作树已通过证据。本次未审计 NFR-005 变更正确性。

本报告仅登记当前证据与未修复缺陷；不追溯改写历史 done，也不自行关闭/重开任务。修复后需要在正式 tests/evals 落地反例，并由主 Agent 按实际输入完成受影响范围复核。

## 输入指纹（SHA256）

以下使用审查结束时工作树内容生成。复现脚本与报告 JSON 位于被忽略 data/validation，仅作临时复现，不替代正式回归。

- `app/services/evaluation_jobs.py` `a4c50bfc06e1a34c16f35a327756538040d079833a92faf91bada51dd73f131f`
- `app/services/evaluation_orchestrator.py` `881f477c29992ec08c0ad14dde69c9008399271836b67524ff6800f840a04a02`
- `app/services/evaluation_quality.py` `b0c57e0b336d86bad45fc4eac3656f24bb2ce1583aad08117ca43da881b68b99`
- `app/services/evaluation_adapters.py` `92a95a65e1dee3928addcf82c792f2984beadc700b6d54c54c42a043ffad3bd7`
- `app/services/evaluation_targets.py` `448dc6d624a83cecc1382ed88d9f5a91f26ea4a2985c1ff6feb3a87c57361bfd`
- `app/services/evaluation_silver.py` `863d165626d741a4bdf30881885b1307b7244c9ce2404cba4f6aa3e479d30a3a`
- `app/services/evaluation_reports.py` `9897e59abfc17c11b68767844294ab7e3876ec5f69f55ee117ec9e659277042b`
- `app/api/routes/evaluation.py` `e897d36a8dd6c1320bac7fb835fc55f2e12175610a2bdfd39c0407fddb1d6529`
- `app/schemas/evaluation.py` `a897fddff0523b3168a0782f92ee30e4b4fdf0a598d653b846c1358d222d1d18`
- `app/core/config.py` `c5934fc740c4650c41d4f25ab740de49ad579cbe3a33227ef1fceadd7d2cc43a`
- `app/rag/service.py` `37e1694801a4fc0f6dad43fa51a3923a274c99fe3b79d409f6cf50e43259f0be`
- `app/rag/backend/native.py` `4cf99a6e3573aa1e4067d0ef64bc5a7212b42a9e640c5971982b4b8ecd3c8de5`
- `app/rag/embeddings/factory.py` `1a12fc366822d0cfc3386be17e416a3611316d62981765e13d8b1dccff4db526`
- `app/rag/embeddings/openai_compatible.py` `c23c42eec4e9df7738208eff9e880ee0a7eea1c3b9bd6b72c38fddbaa44111d0`
- `app/rag/resilience.py` `d5d4b9462b91fc0006a37d52acbc99a8fc8085344896c27693f086a350116cbc`
- `app/models/evaluation.py` `d6c1f99c0ccf215b0ef497033dae6ebb9fe810be9429863651bcacf3c540fc2f`
- `app/models/rag.py` `4fad7ae16391c279488ff97238a777e5c242d33d9c5eaf2a77dec2f3e55ecc52`
- `app/models/user.py` `109b0f296ef8c3704587d09c9971c68d7fed9369000bdf2917cf2949c4bacb29`
- `tests/conftest.py` `c90aac2c8724116166cd2b869d1b90cacdae180c4b78fefc8533932d9fc95d4a`
- `tests/test_evaluation_jobs.py` `707329b9260904db9c40af00ab662d63596dbbc61569caa9676cc563e49f7804`
- `tests/test_evaluation_runtime_guards.py` `fc183bb15dfd7d9761c2872dc2d9ee754d1f9f1fdb9cbddb0d9bce1da584f849`
- `tests/test_evaluation_quality.py` `33282ead9c1fbdbc9be2253da9f133140f8e42783fffbf8f7e2e9c0cd6ddb6ff`
- `tests/test_evaluation_orchestrator.py` `2ef3794136c6acc3814f99333530519f874172cc569ed89b123fbd4ec52a057a`
- `frontend/src/pages/Evaluations.tsx` `898b7b2686e05e09fe3f0e86ee9cb52bca7fc1b245ef102f408f08d708730838`
- `frontend/src/pages/EvaluationReports.tsx` `2809ac82d14c4e8048ad27f02ceea66b3af70de687bae278bb8f91324725f951`
- `data/validation/eval003-independent-20261026/repro_zero_budget.py` `8ac37a58ce9c352cea4bf2a3e91fc138b2501a84965b859aa1673fc55d3e91f3`
- `data/validation/eval003-independent-20261026/repro_external_failure.py` `b8d3558522a14829161ccdb3111a96ea8a09043a0f82fd53c926faade309bf94`
