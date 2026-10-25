# 真实资源统一评测平台实施计划

日期：2026-10-24  
规格：`docs/superpowers/specs/2026-10-24-real-resource-evaluation-design.md`  
覆盖任务：EVAL-003、EVAL-004、EVAL-005；兼容复核 TRACE-001、MEM-003、MEM-002

## 1. 基线与纠偏范围

- 基线分支：`feat/EVAL-002`
- 基线提交：`c4968996b4e8b4ef40f773e7857f6be2e0942c7f`
- 起始工作树包含六张任务卡的未提交候选实现，不重置、不覆盖用户改动。
- 变更前完整验证证据 `data/validation/20261024T003042-5987236549f2/result.json` 为 3442 passed、3 skipped，Ruff、mypy、前端四项均通过；该证据只说明纠偏前候选，相关输入修改后失效。
- 当前 EVAL-003 的固定仓库数据集与零费用 runner 仅保留为 `internal_smoke`，不再出现在产品评测入口，也不生成真实质量结论。
- 不执行真实付费模型调用、不外发真实数据、不操作生产库、不删除历史任务/报告、不提交或推送 Git。

## 2. 架构与兼容策略

复用现有 `EvaluationJob` 状态机、并发/取消/幂等骨架、`RAGService`、`ChatService`、`WorkflowEngine` 和报告只读能力。新增统一编排器、显式适配器注册表和三个内置适配器；第一阶段不动态导入插件。

旧任务与旧报告按 `internal_smoke` 或 `legacy` 分类只读保留。数据库只做向前迁移：新增目标快照、执行主体、预算/选项、进度和逐用例结果字段/表；downgrade 检测到数据时不得静默丢弃。

平台能力统一以 `system_admin` 为唯一准入角色。当前 `admin` 通过角色获得能力，不硬编码用户名。目标租户、发起人和评测执行主体分别固定并审计；Agent 执行主体必须是目标租户内启用中的真实用户。

## 3. 实施顺序

### 阶段 A：权限收口、页面分离与真实目标目录

1. 增加统一平台管理员依赖，评测、报告、运行历史、审计、系统状态 API 全部改为 `system_admin`；补 `system_viewer`、`tenant_admin`、`member`、`viewer` 的 403 反例。
2. 前端拆分 `/evaluations` 与 `/evaluation-reports`，两页均使用管理员路由守卫；侧边栏单独展示“测评”和“测评报告”，运行历史、审计、系统状态一并只对管理员显示。
3. 新增目标目录服务：
   - RAG：目标租户已发布、当前、未删除、属于活动索引的真实文档集合；
   - Agent：唯一真实目标“系统默认 Agent”，并返回可选择的启用租户用户；
   - Workflow：目标租户现存 Workflow 及有效 owner 状态。
4. 目标查询必须显式传 `tenant_id`，创建任务时重复校验，不能依赖平台管理员登录租户。

阶段验收：五类平台页面/API 权限矩阵通过；页面和报告路由彻底分离；选择租户后只能看到该租户真实资源，不出现虚构知识库或 Agent。

### 阶段 B：编排协议、持久化与迁移

1. 建立 `EvaluationAdapter` 协议与 `EvaluationAdapterRegistry`，只注册 `rag.builtin.v1`、`agent.default.v1`、`workflow.builtin.v1`。
2. 扩展任务契约：`target_type`、`target_id`、`adapter_name/version`、`metric_schema_version`、目标快照、执行主体快照、运行选项、预算快照、进度、调用量和费用；旧 runner 字段只用于兼容读取。
3. 新增逐用例结果模型，保存断言、指标、脱敏证据、稳定错误码、耗时、调用量和费用；不保存完整系统 Prompt、密钥、敏感正文或原始异常。
4. 编排器负责预检、预算、状态机、逐例调度、取消、漂移检测和报告落盘；适配器不自行管理任务生命周期。
5. 重启仅恢复仍满足幂等和快照校验的待执行用例；不能安全恢复时进入 `interrupted`。

阶段验收：注册表拒绝未知适配器；同幂等键同请求复用、异请求 409；迁移从空库和已有前序结构均可前进；历史任务可读且产品列表默认隐藏 `internal_smoke`。

### 阶段 C：RAG 真实文档评测

1. RAG 适配器固定文档版本、内容哈希、分块计划、活动索引、Embedding provider/model/dimension、向量与检索后端。
2. 扩展数据集为目标类型契约：人工导入的问题必须绑定期望 `document_id`、`chunk_id` 和文档版本；审核后标记 Gold。AI 生成入口从真实分块生成问题/答案要点/证据并标记 Silver，只有在明确预算确认、真实模型可用时执行。
3. 默认运行真实 `RAGService` 检索，计算 Recall@5、Recall@10、MRR、nDCG@5、命中数和延迟；保存逐题候选、来源、排名和版本的脱敏投影。
4. 生成质量与 Judge 使用独立显式开关和预算上限；无密钥时纯检索仍可运行，需要模型的步骤返回稳定不可用状态，禁止 Mock 代替。
5. 现有单查询策略对照改名为“快速诊断”，保留真实索引调用，但不进入批量质量趋势。

阶段验收：可对系统内已发布文档运行真实批量检索；指标用固定输入单测核验；文档/索引漂移、跨租户 ID、未发布/已删资源均拒绝；报告记录真实索引与 Embedding 身份。

### 阶段 D：系统默认 Agent 真实评测

1. Agent 适配器只列出“系统默认 Agent”，固定源码、Prompt、模型、编排、Tool 和 Skill 版本。
2. 平台管理员创建任务时选择目标租户中启用的真实用户作为执行主体；快照用户 ID、角色和 `token_version`，同时审计发起人和执行主体。
3. 复用真实 `ChatService`/`AgentPipeline`，以评测上下文隔离普通会话；L0 只读能力可执行，L1～L3 副作用拦截并作为断言证据，不触发 Workflow/Webhook/业务写入。
4. 默认计算路由、Skill、Tool/参数、安全拒绝、步骤、Token、延迟和失败分布；答案语义指标仅在显式 Judge 预算下运行。

阶段验收：执行主体停用、移出租户、角色或撤销版本变化均失败；真实管线被调用；禁止动作没有副作用；不生成普通用户会话历史。

### 阶段 E：Workflow 真实执行链评测

1. Workflow 适配器冻结名称、Prompt 模板、owner、超时、重试/调度配置和实现版本哈希。
2. 复用 Workflow 模板解析、owner 上下文、Agent 管线、超时和有限重试；评测上下文禁止 cron 注册、Webhook 和 L1～L3 副作用。
3. 逐例输入模板变量及断言，输出步骤完成率、断言通过率、审批/副作用判断、重试、超时、耗时、Token 和错误类别。
4. 结果只写评测用例表并标识 `trigger_source=evaluation`，不写普通 `WorkflowExecution` 历史。

阶段验收：真实 Workflow 入口被调用但没有调度、Webhook 或业务写；owner 失效、模板错误、超时、重试和资源漂移有确定终态。

### 阶段 F：统一报告、组合验收与记录同步

1. 报告页展示统一摘要和 RAG/Agent/Workflow 专属明细；逐例展示脱敏期望、实际、证据和失败类别。
2. 仅同目标类型、指标 Schema、测试集口径和关键配置报告允许比较；再次测评只回填配置并创建新任务。
3. 同步 OpenAPI、产品说明、ADR-0023、版本化 Evaluation Case、任务卡实际结果与未验证项。
4. 完成独立权限/执行链审查。无真实模型费用授权时，只验收确定性和已有索引路径，并把生成/Judge 标记为未运行，不能声称生成质量通过。

## 4. 主要文件边界

预计新增：

- `app/services/evaluation_orchestrator.py`
- `app/services/evaluation_adapters/` 下协议、注册表及三个内置适配器
- `app/services/evaluation_targets.py`
- `app/services/evaluation_metrics.py`
- `frontend/src/pages/EvaluationReports.tsx`
- 新的 Alembic 向前迁移和对应测试/Evaluation Case

预计修改：现有 evaluation models/schemas/routes/jobs/reports/datasets、RAG/Agent/Workflow 的受控评测接点、权限矩阵与平台路由、前端 API/types/navigation/pages、Swagger、ADR、任务卡和本计划。对 TRACE/MEM 只修改平台访问守卫或被直接调用的兼容点。

## 5. 失败边界与回滚

- 任一目标、租户、执行主体、权限版本、活动索引或资源哈希变化都停止受影响任务，不混合版本聚合。
- 达到调用或费用上限后不再启动下一次调用；外部失败不进行无界重试。
- 报告持久化失败时任务不得成功；错误仅暴露稳定码和脱敏说明。
- 通过功能开关停止新建真实评测任务，保留历史任务、逐例结果和报告读取；适配器可单独停用。
- 回滚不删除数据库表、任务、报告、测试集或历史 Smoke 证据。权限放宽不属于技术回滚，必须另行批准。

## 6. 验证计划

### 2026-10-24 候选验证结果

最终候选证据：`data/validation/20261024T060202-7961c44bf6d2/result.json`，输入文件及 SHA256 由验证器绑定；基线 HEAD 仍为 `c4968996b4e8b4ef40f773e7857f6be2e0942c7f`。Windows、conda ai-assistant 解释器执行 `python scripts/validate_delivery.py full --frontend`，退出码 0：完整 pytest 3461 passed、3 skipped；Ruff、mypy（255 个源文件）、前端 typecheck、Vitest（187 项）、lint、build 全部退出码 0。

实现包含真实资源目录、内置适配器注册、RAG 文档生成确定性 Silver、真实检索、隔离 Agent/Workflow 执行、双主体审计、逐例报告及管理员页面准入。Agent 执行主体快照包含角色和 token_version，并在运行时复核。生成质量和 Judge 尚未接入，开启请求明确拒绝；真实模型费用调用与真实业务数据质量验收未执行。

首次全量发现的 SQLite 降级索引/约束残留和报告非法 scope 问题已修复并通过回归；随后类型问题已修复，最终完整验证重新执行。尚缺独立执行链审查、页面人工走查、最终提交同 SHA Linux CI 和业务接受，三张 EVAL 卡及关联 TRACE/MEM 卡继续保持 in_review。未提交或推送 Git。仅补充本段验收说明不改变上述检查输入。

开发阶段按阶段执行最小专项，并覆盖直接调用方、跨租户、撤权、取消、预算、重试和旧 fixture。最终候选至少执行：

```powershell
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_evaluation_api.py tests/test_evaluation_jobs.py tests/test_evaluation_reports.py tests/test_evaluation_datasets.py tests/test_evaluation_comparison.py -q
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_run_history.py tests/test_memory_policy.py tests/test_memory_snapshots.py -q
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m ruff check .
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m mypy app/
cd frontend
npm run typecheck
npm test
npm run lint
npm run build
```

因为首次改变验证基础设施、公共权限和三条执行链，本地不筛选完整 `pytest` 仍是关闭前强制项；最终提交还需要同 SHA 的 Ubuntu Backend、Frontend、Agent official baseline 和适用 RAG official baseline。未提交、未推送、Linux CI 或人工真实数据验收缺失时，相关任务保持 `in_review`。
