# 受预算评测任务实施计划

## 基线与范围

### 2026-10-24 真实生成与 Judge 扩展

用户批准生成与 Judge 复用当前国内 OpenAI-compatible 服务；首次验证只允许合成或确认脱敏数据，最多 10 条用例、30 次请求、5 元。价格无法可靠核算时只接线，不发真实请求。采用独立评测计费会话复用现有兼容客户端构造与上下文护栏，避免改变公共聊天返回契约；沿用固定零费用 runner 会无法满足真实评测，改变全局 LLM 返回协议会扩大调用方影响，均不采用。

新增 `app/services/evaluation_quality.py`、接入已有 `evaluation_orchestrator.py`（本次授权范围补充）；其余沿任务允许路径。首期 RAG 检索→答案生成→可选 Judge；Agent/Workflow 原执行链保留，质量开关对其显式拒绝。Judge 必须伴随生成，参考答案要点缺失时不输出正确性分数。Judge 使用版本化严格 JSON 分数，无自由文本落盘，不把自评分当业务接受，不新增发布质量阈值。

配置默认关闭；价格必须配置引用、完整端点/模型身份和输入/输出每百万 token 最高单价。任务绑定价格、端点/模型与 Prompt 哈希，运行中漂移拒绝。请求显式确认数据外发并限制 10 条/30 次/5 元；输入按 UTF-8 保守计数且上限 8000，输出最多 1024；每次请求先持久化调用计数和最坏预占，取消/失败/usage 缺失保留预占且不重试。费用是按配置价计算的上界/结算值，不冒称供应商账单。生成内容仅在内存用于 Judge，落盘只含哈希、引用 ID、分数与 usage。

代码核对补充：RAG 检索的 Embedding 同样属于外部调用，不能漏出费用信封；评测专用 Embedding 子类沿用当前端点/模型/维度/输入策略，逐次调用复用同一计费会话，无隐式重试。最高 Embedding 单价及来源/身份必须单独匹配现配置，否则整个质量任务在创建前拒绝。每例最小请求预算为 2（检索+生成）或 3（另含 Judge）。引用合法性使用独立指标 `citation_id_validity`，不冒充语义引用正确率；质量指标 Schema 为 `evaluation-rag-quality-v1`。费用响应新增 `CNY` 单位，前端按元展示。

验证覆盖未知价格零请求、上限/边界、缺 usage、供应商异常、非法 Judge 输出/注入、取消、配置漂移、越权与调用方；使用隔离库及 `validate_delivery.py`。本地旧证据随源码变更失效，最终完整检查、独立审查和同 SHA Linux CI 未齐保持 in_review。回滚关闭质量开关，保留任务/报告，不回退权限或删除数据。本轮不提交或推送 Git。

- 基线：分支 `feat/EVAL-002`，HEAD `c4968996b4e8b4ef40f773e7857f6be2e0942c7f`。
- 开发前专项：隔离 SQLite 下 `pytest tests/test_evaluation_reports.py tests/test_evaluation_datasets.py tests/test_evaluation_api.py -q`，36 passed，退出码 0。
- 仅修改 EVAL-003 allowed_paths；不调用真实模型、不外发数据、不覆盖冻结报告、不提交或发布。

## 接口与状态

1. `POST /api/v1/evaluations/jobs` 创建任务，请求包含已批准数据集版本、runner、零费用模式和幂等键；返回持久任务。
2. `GET /api/v1/evaluations/jobs` 与 `GET /api/v1/evaluations/jobs/{id}` 返回受权分页/详情。
3. `POST /api/v1/evaluations/jobs/{id}/cancel` 请求合作式取消。
4. Service 承担身份/租户/版本/预算校验、状态转换、并发 lease、固定 runner 调度、报告路径/哈希绑定和错误收敛。
5. 启动恢复将非终态任务标为 interrupted；不自动重试，不扩大预算。

## 数据模型

`evaluation_jobs` 保存任务 ID、租户、提交人、数据集/版本、runner/mode、状态、幂等键与请求指纹、输入哈希、预算上限、报告 ID/相对路径/哈希、脱敏错误码、取消/开始/结束时间。唯一约束为 tenant + submitted_by + idempotency_key；检查约束限制 runner、mode、状态和非负预算。

## Runner 与失败边界

- 固定允许 retrieval/smoke、generation/synthetic、agent/smoke；外部模式在持久化前拒绝。
- 子进程使用参数数组和当前解释器，不使用 shell；输出只写唯一报告路径和隔离数据库路径。
- 取消、超时、非零退出、缺失/过大/非法报告分别映射稳定错误码；不保存原始进程输出。
- 并发默认 1、范围 1..8；总时限默认 900 秒。容量满时任务保持 queued，由显式调度轮次领取，不建立无界内存队列。

## 实施和验证顺序

1. 增加失败测试与模型/迁移。
2. 实现 Service 状态机、runner 注册表和恢复。
3. 接入 API、启动恢复和前端任务页。
4. 增加 Evaluation Case、OpenAPI/README/任务记录。
5. 运行评测专项、直接调用方、Ruff、mypy、前端四项；完整回归及最终 SHA CI 未齐时保持 in_review。

## 2026-10-24 扩展验证记录

- 起始分支/HEAD 仍为 `feat/EVAL-002` / `c4968996b4e8b4ef40f773e7857f6be2e0942c7f`；保护起始工作树内 EVAL/TRACE/MEM 等已有修改，不提交或推送。
- 开发前专项：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe scripts/validate_delivery.py targeted --test tests/test_evaluation_jobs.py --test tests/test_evaluation_adapters.py --test tests/test_evaluation_orchestrator.py --test tests/test_evaluation_api.py`，22 passed、退出码 0；证据 `data/validation/20261024T141529-891630d242b4/result.json`。只说明起始专项，不替代后续候选验证。
- 开发中首次新增用例执行出现 6 个失败，原因是测试 fixture 未声明测试模型能力；补显式 fixture 能力声明，保留生产护栏。其后专项 75 passed；补 Embedding 统一计费、取消与真实超时反例后专项 80 passed、退出码 0（`data/validation/20261024T142614-6886e1395912/result.json`）。随后补充发起租户漂移、响应大小/非法数值收敛及集成反例，早期专项不充当最终候选证据；最终完整验证运行 `data/validation/20261024T142827-c52b0f95102e` 已固定这些输入，真实探针未再修改业务输入。
- 首次直接 Ruff/mypy 使用默认缓存分别遇到权限错误，mypy 同时发现一处可空分数列表类型错误；修复类型、Ruff 使用 `--no-cache`、mypy 使用 `--cache-dir data/validation/eval003-quality-mypy` 后通过。最终以完整验证器候选结果为准，不把缓存权限错误当业务断言失败。

### 已授权真实合成探针

2026-10-24 22:33（Asia/Shanghai），当前配置实际为 DeepSeek 官方 `deepseek-flash`；Embedding 为阿里云北京 `text-embedding-v3`、1024 维。未读取或修改 `.env` 内容、未输出凭据，未使用现有业务库；使用明确 `local` 的独立内存 SQLite/SQLModel 索引。临时探针 `data/validation/eval003-quality-live/probe.py` 创建一条合成退货政策和一条合成用例，复用任务 Service、计费 Embedding、真实 RAGService 和生成/Judge。它是 Smoke，不是 Gold 或真实业务质量指标。

价格核对：2026-10-24 [DeepSeek 官方中文价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) 的最高非缓存输入 2 元/百万、输出 8 元/百万；[阿里云官方 Embedding 页](https://help.aliyun.com/zh/model-studio/embedding) 北京 text-embedding-v3 为 0.0005 元/千输入 token，即 0.5 元/百万。只在探针进程显式启用和注入已核对价，主服务配置仍默认关闭。

实际命令：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe data/validation/eval003-quality-live/probe.py`。受限环境首次执行 exit 1、任务 `failed/quality_request_failed`，1 次 Embedding 尝试 usage 未知，保留 0.000076 元预占；提权联网后同命令 exit 0。成功运行 4 次请求（源文档 Embedding、查询 Embedding、生成、Judge），任务 succeeded、报告 passed；合计尝试 5 次，1 条合成用例，未隐式重试，未超 10 例/30 次/5 元。

成功运行真实 usage：Embedding 输入 28+15 tokens；生成输入 134/输出 94；Judge 输入 228/输出 37。按上述最高价格计算成功调用费用 0.0017935 元，加首次失败保守预占共 0.0018695 元，不冒称账单实扣。报告 `data/validation/eval003-quality-live/report.json` 的 SHA256 为 `0228a0deda7f6eceff57b450f8a3b4218bb920d84e3e228d00a34d7e81ee0254`；数据集哈希 `85876cae65500415ed75d15fdc4c17acfa340918d2df0800441cb711d888d4fa`，Prompt 哈希 `e800419afb87dfca22ed824aa957f393748c7b1b917dc4d82174ab00b4d8636c`。生成答案正文未落盘。此样本 Judge 分数不构成业务质量接受。

环境：Windows 11 10.0.22621、Python 3.12.0（conda ai-assistant）；机器 CPU/物理内存查询被环境拒绝，规格未知，因此不报告性能或质量提升。完整候选的文件 SHA256 与依赖版本由验证器绑定；同 SHA Linux CI、独立审查、页面人工走查与真实业务质量接受仍待完成。

实际依赖读取：pytest 8.4.2、Ruff 0.8.4、mypy 1.14.1、SQLModel 0.0.22、Pydantic 2.10.4、SQLAlchemy 2.0.36、httpx 0.28.1。验证器隔离 SQLite 并清空 LLM/Embedding 密钥，正式自动测试不复用真实费用授权；真实探针是独立的显式联网命令。

### 首次完整回归发现与修复

`python scripts/validate_delivery.py full --frontend` 首轮 `data/validation/20261024T142827-c52b0f95102e/result.json` exit 1：3498 passed、3 skipped、1 failed。失败为 `tests/test_chat_feedback_migration.py::test_feedback_upgrade_is_idempotent[True]`，隔离重现（`20261024T144419-d27c19fc4d89`）：旧快照迁移降级删除 `case_total` 等列时遗留 CHECK，SQLite 重建时报 `no such column: case_total`。该失败保留，不计通过。

在本卡允许的迁移 `f7e3a5b6c8d9` 中，仅删除依赖待移除字段的 CHECK，保留预算等其他约束；增加 `test_snapshot_downgrade_drops_only_checks_for_removed_columns`，同时验证具名/匿名 CHECK 与保留预算约束。反例 fixture 使用与 SQLAlchemy create_all 一致的多行 DDL，避免 SQLite 方言反射将单行多约束误读为同一个约束；断言未放宽。

修复后的隔离专项 `data/validation/20261024T144657-21ab825093a4/result.json` exit 0，88 passed；前端 typecheck/Vitest/lint/build 均 exit 0。命令为 `python scripts/validate_delivery.py targeted` 加上述评测六文件、`tests/test_chat_feedback_migration.py`、`tests/test_real_evaluation_constraint_migration.py`、`tests/test_migration_reconcile.py` 和 `--frontend`；所有 Python 命令使用本记录中的 conda 解释器。全项目 `python -m ruff check --no-cache .` 与 `python -m mypy app/ --no-incremental --cache-dir data/validation/eval003-quality-final-mypy` 均 exit 0（mypy 256 源文件）。最终完整回归已重新固定候选输入运行 `data/validation/20261024T144752-f27f6cda9f5c`；不沿用首轮失败证据声明完成。迁移修改不影响真实探针使用的内存 create_all 路径，生成/检索/Judge 代码未再变更，因此真实链路证据仍适用。

### 最终候选结果与未验收项

最终命令：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe scripts/validate_delivery.py full --frontend`，exit 0；证据 `data/validation/20261024T144752-f27f6cda9f5c/result.json` 为 `passed`，绑定最终 app/tests/alembic/config/依赖/版本化评测与前端输入 SHA256。基线仍为 HEAD `c4968996b4e8b4ef40f773e7857f6be2e0942c7f` 加该清单内工作树候选，不存在伪造的新提交 SHA。

- 不筛选完整 pytest：3500 passed、3 skipped、5 warnings，exit 0。
- `python -m ruff check .`：exit 0；`python -m mypy app/`：256 源文件，exit 0。
- 前端 `npm run typecheck`、`npm run test`（24 文件/187 用例）、`npm run lint`、`npm run build`：全部 exit 0。
- OpenAPI 已导出；任务 YAML 和生命周期 Evaluation YAML 可解析；CRLF 感知的 Git whitespace 检查通过。纯文档结果补记不改变上述检查输入。

技术验证已完成；真实模型合成 Smoke 已完成，但它不构成真实业务质量接受。独立执行链审查、页面人工走查、最终提交同 SHA 的 Ubuntu Backend/Frontend/Agent official baseline 及适用 RAG official baseline CI 尚未完成，任务继续 `in_review`，未提交/推送、未合并、未生产启用。回滚关闭 `EVALUATION_QUALITY_ENABLED` 并受控收尾活动任务，保留任务/逐例/报告；迁移回滚不执行重要数据删除。

本次观察：首次开工专项为北京时间 22:15；主要等待为两轮完整回归（首轮发现并隔离迁移缺陷，第二轮确认修复），业务接受与 Token 消耗未知，验收后 7 天缺陷观察尚未开始。未新增独立流程报表。

## 2026-10-25 无界排队修复

用户明确要求修复独立验收发现的无界内存队列。沿用已批准的持久 queued 与有限并发方案，不新增队列依赖、权限或预算。仅在 `EvaluationJobDispatcher` 内按空闲槽位查询数据库 queued，排除已领取项，按 created_at/id 排序限量提交；任务完成（含状态机内正常失败）后继续领取。排队取消后不再执行；停机停止领取并发送活动取消。未被状态机捕获的 worker 异常停止本实例调度，仅记录异常类并保留非终态供启动恢复，避免领取同一 queued 形成隐式重试。没有新增无限增长的待办或失败 ID 集合。

改动为 `app/services/evaluation_jobs.py`、`tests/test_evaluation_jobs.py`、生命周期 Evaluation 1.1.1、ADR-0023 及本记录/任务卡。API/schema/模型、依赖、前端与 runner/计费契约未改变。回滚为受控停机并回退本次调度器修复，保留数据库任务和报告；回退会重新暴露原无界排队缺陷，不能作为生产修复。未调用外部模型、修改生产数据、提交或推送。

基线仍为 Windows / 分支 `feat/EVAL-002` / HEAD `c4968996b4e8b4ef40f773e7857f6be2e0942c7f` 加原有混合未提交修改。开发前运行 conda `ai-assistant` 解释器的 `scripts/validate_delivery.py targeted --test tests/test_evaluation_jobs.py --test tests/test_evaluation_api.py --test tests/test_evaluation_orchestrator.py`，14 passed、exit 0，证据 `data/validation/20261025T010314-4fceb3fea28f`；此前完整3500证据只作为旧基线，本次输入变化后不能当新候选全量通过。

失败证据保留：新增容量1/2回归在原实现分别看到8个内存标记，2 failed/3 passed、exit 1（`20261025T010401-94a38bbd5afc`）。首次实现错误地在单项取消时停止调度，专项失败（`20261025T010449-ba074359f978`），已修正为仅停机停止。文件SQLite并发专项首次92 passed/1 failed（`20261025T010634-0fd0aead1df2`）：Windows相同created_at下UUID次序与插入次序不同，新测试错误地假定插入顺序；独立Agent核对隔离库后，测试改用实际created_at/id规则，未放宽容量/取消断言。首次Ruff发现测试import顺序，已修正；普通Git whitespace将已有CRLF判为尾空白，改用 `git -c core.whitespace=cr-at-eol diff --check` exit 0，未批量格式化用户文件。

最终专项命令（解释器均为 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`）：

```powershell
python scripts/validate_delivery.py targeted --test tests/test_evaluation_jobs.py --test tests/test_evaluation_quality.py --test tests/test_evaluation_orchestrator.py --test tests/test_evaluation_api.py --test tests/test_evaluation_adapters.py --test tests/test_platform_admin_access.py --test tests/test_evaluation_reports.py --test tests/test_evaluation_datasets.py
python -m ruff check --no-cache .
python -m mypy app/ --no-incremental --cache-dir data/validation/eval003-bounded-final-mypy
```

专项94 passed、10.56秒、exit 0（`data/validation/20261025T011039-6b9609d0957c/result.json`），输入SHA256及平台/解释器/依赖/隔离配置由执行器保存。新增正式回归使用文件SQLite和真实线程池，覆盖容量1/2、重复通知、数据库积压自动领取、排队取消、失败补位、停机取消/保留积压、启动恢复，以及执行前异常不重试/日志脱敏。Ruff exit 0；mypy 256源文件、exit 0，静态日志归同运行目录 `ruff.log` / `mypy.log`，补充检查记录 `supplemental-checks.json` 绑定同一输入清单。

独立审查/受托验收者 `/root/auth005_acceptance` 核对真实API通知与取消入口、锁内限量领取、重复通知、取消/停机/异常边界，并独立运行同一conda解释器的 `scripts/validate_delivery.py targeted --test tests/test_evaluation_jobs.py`：7 passed、4.32秒，collection/pytest/summary均exit 0，证据 `data/validation/20261025T011102-dfeb06a1ff0c/result.json`。最终源码SHA256 `f8df50871c7afd66a97fde03eb33d2f38a91f91d30ad3bfe306e0b1f06eec577`；测试 `707329b9260904db9c40af00ab662d63596dbbc61569caa9676cc563e49f7804`；Evaluation `3e0db0bf8dfb273b005e6f895122fbdcf29d51d1a96900af109401c4be25b1d2`。原无界队列P2及新增异常重领边界已闭合，本次范围无未修复P1/P2；这是用户委托的Agent复验，不冒充真人业务接受。之后只补本记录、ADR和任务事实，未改验证输入；summary再次核验匹配，不重跑有效专项。

纯后端调度实现，没有响应结构或前端契约变化，前端检查不因本修复重跑；完整pytest允许由最终SHA Ubuntu CI承担。本轮不触及共享conftest/测试选择器/验证执行器/CI行为，无新增未覆盖跨模块失败，故不重复本地全量。必要最终SHA CI、页面走查和代表性真实业务质量接受仍缺，保持 `in_review`；本轮专项和独立复验不能替代这些关闭条件。

## 2026-10-25 用户委托页面验收

结论：页面走查已完成，**暂不接受**，11个已走查场景通过、2个P2待修；不修改业务实现，任务仍in_review。验收由主Agent实际操作浏览器完成，属于用户委托的Agent页面验收，不冒充真人业务接受。前文“页面走查待完成”是此前缺口；本轮已执行但发现缺陷，不能据此关闭任务。

### 候选与环境

分支 `feat/EVAL-002`、HEAD `c4968996b4e8b4ef40f773e7857f6be2e0942c7f` 加现有混合工作树。浏览器测试真实Vite/React页面、FastAPI路由、EvaluationJobService/Dispatcher、RAGService和LocalVectorStore，没有伪造前端网络响应、任务状态或报告结果。浏览器使用Codex内置浏览器，gstack browse可执行文件未安装，未安装新依赖。前端 `http://127.0.0.1:18766`，代理隔离后端 `http://127.0.0.1:18765`，仅监听loopback。

运行脚本/证据目录 `data/validation/eval003-ui-20261025`，数据库为其中的ui.db；没有使用data/ai_assistant.db，也未读取本机.env。DotEnv读取在该临时进程显式禁用，凭据仅运行时生成并用于隔离登录，不进入源码/本记录或截图。明确配置 `RAG_VECTOR_STORE=local`、`RAG_BACKEND=native`、Embedding `mock / mock-embedding / 64维`、LLM mock、并发1、质量配置关闭，并禁止非loopback连接。所有测试文本为合成退货政策/Silver用例，没有客户数据；不把页面的100%样本指标当真实检索/生产质量结论。

启动命令（后端使用conda ai-assistant/Python3.12.0）：

```powershell
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe data/validation/eval003-ui-20261025/server.py
$env:VITE_PROXY_TARGET = 'http://127.0.0.1:18765'
# 在frontend目录
.\node_modules\.bin\vite.cmd --host 127.0.0.1 --port 18766 --strictPort
```

启动成功，Alembic隔离库head为f8a4b6c7d9e0。设置没有降低产品认证/权限/预算门禁；临时初始化数据使用正式摄取创建合成文档，预置已批准的Silver数据版本。真实质量选项的提交只验证已关闭配置的前置拒绝，未启用付费功能、未发外部请求。

### 实际页面场景

| 场景 | 观察结果 |
|---|---|
| 空租户/未选资源 | 显示无可评测真实资源，启动禁用 |
| 页面启动与轮询 | 单条任务525610e1…显示running，自动变为succeeded 1/1 |
| 满并发排队与槽位释放 | running与queued 0/1000同时出现；前任务结束后排队任务开始 |
| 排队取消 | ad635996…直接cancelled 0/1000，未执行用例 |
| 运行取消 | e329e2ac…先cancel_requested，再cancelled，保留644/1000；另一任务保留658/1000 |
| 质量预算/确认/配置拒绝 | 预算/确认不全时启动禁用；完整填写离线合成测试信息后，质量配置关闭使服务端在创建前拒绝，页面显示请求不符合策略 |
| 红色质量报告 | afd5a1ac…任务执行成功，但报告明确失败；逐例失败及0外部调用可见，没有把HTTP成功改为质量通过 |
| 报告反查任务 | 报告详情显示job_id、dataset_version_id、dataset_hash及逐例证据 |
| 快照漂移失败 | 将隔离的ui-fault-v1在queued期间变更输入hash，536928ff…最终failed 0/1，页面显示dataset_snapshot_changed |
| 进程重启 | 独占验收后端被终止后重启，4ef6ac11…由持久running恢复interrupted 250/1000，错误process_restarted，未自动重试；既有终态记录保留 |
| 非平台管理员 | ui-tenant-admin登录后无测评菜单，直接/evaluations显示403 |

重启最初尝试读取Win32_Process因环境权限拒绝，未执行该停止分支；随后使用工具拥有的后端进程会话控制信号终止，仅影响本轮临时服务。重启前后jobs快照证实running→interrupted，没有手工伪造任务状态。隔离JWT每进程随机生成导致重新登录，这是测试装配行为，不作为产品重启认证缺陷。仅旧的同幂等键重复请求反例沿用已存在专项，不声称从页面重放过相同请求键。

临时 `ui-cases.json` 保存13项观察（11 passed/2 failed），`jobs-before-restart.json`、`jobs.json` 保存10个合成任务；任务外部调用合计0。浏览器错误日志无未捕获JS错误（最终标签页采集0条）；预期401/403/策略拒绝不等于应用崩溃。

### 未修复P2

1. **完成任务缺少对应报告入口。** `/evaluations`的任务卡完成后仅有任务ID、状态和进度，既没有“查看报告”，也不展示report_id/report_hash；只能到另一个列表手工匹配任务ID。已批准的真实资源设计§4.1第7步及验收操作路径要求完成后查看对应报告。入口在 `frontend/src/pages/Evaluations.tsx` 任务列表（约237行）。复现：选择合成文档+已批准单条版本→启动→succeeded→检查任务卡。修复应按后端绑定report_id直达报告详情并保留对应关系，不让用户猜报告ID。
2. **报告页面把已有来源身份标成缺失。** `/evaluation-reports`详情存在`dataset_version_id=ui-small-v1`，但趋势读取`metadata.dataset_version`并显示“数据版本未记录”；原报告target_snapshot.targets[].metadata已有 `mock/mock-embedding/64/local/index_id`，真实报告投影却未保留这些字段，趋势继续显示“模型未记录”。入口为 `frontend/src/pages/EvaluationReports.tsx:109` 及 `app/services/evaluation_reports.py:135` 的真实报告metadata投影。复现：打开job-525610e1…的详情/历史，对照其持久报告dataset.version_id和target_snapshot。修复应统一真实报告字段映射并显示实际Embedding/索引/后端身份，未知时才显示未知；不能补造模型或性能数据。

两个复现已进入 `docs/ai-prompts/evals/evaluation-job-lifecycle-v1.yaml` 1.1.3 的Observed Regression场景；本轮仅登记要求和已观察失败，不把这些Case冒称自动测试通过。没有修改前后端业务、依赖或CI执行行为，故不重复业务回归；新增Evaluation输入意味着旧指纹结果不作为新最终门禁。最终SHA必要CI及代表性真实业务质量接受仍缺。

长期截图：[完成任务卡](eval003-ui-20261025/completed-task.png)、[报告详情与错误的来源历史](eval003-ui-20261025/report-detail.png)、[重启后的任务状态](eval003-ui-20261025/task-states.png)。其余截图/临时脚本/指纹在隔离运行目录。未提交、推送或生产启用。

机器可读结论与最终前后端输入SHA256见 `data/validation/eval003-ui-20261025/page-acceptance.json`。YAML解析/Case ID唯一性与CRLF感知whitespace检查exit0。页面验收后已停止本轮两个临时服务、关闭测试标签页并删除运行时登录明文凭据；工具控制终止服务的exit1是预期清理，不作为业务测试失败。隔离库、合成任务报告和脱敏截图保留用于复现。
