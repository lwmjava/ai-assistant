# 最小评测平台组合验收

候选基线：`c4968996b4e8b4ef40f773e7857f6be2e0942c7f`，工作分支 `feat/EVAL-002`。本记录覆盖 EVAL-001～004 的真实资源组合候选，以已批准的[真实资源设计](../superpowers/specs/2026-10-24-real-resource-evaluation-design.md)和[质量执行约束](../plans/implementation_eval_003.md)为准。当前为未提交工作树，任务状态以 tasks.yaml 为准；专项通过不代表整卡验收或发布通过。

## D3 操作路径

1. 以 `system_admin` 打开 `/evaluations`，先选择启用的目标租户。`tenant_admin`、`system_viewer`、`member`、`viewer` 不显示平台菜单，直接访问页面/API 均拒绝；不要使用这些角色执行后续成功步骤。
2. 选择 RAG、系统默认 Agent 或 Workflow 及该租户内的真实资源。Agent 必须选择该租户内启用的真实用户作为执行主体；Workflow 使用现有隔离执行入口，禁用 webhook 等外部写副作用。
3. 选择适用的已批准数据集版本，或创建、导入并按来源/Gold 规则审核。RAG 未指定数据集时可从所选文档当前索引生成可追溯 Silver 用例；不要求内容哈希与仓库固定 runner 数据一致。AI 生成用例不得直接标 Gold。
4. RAG 检索评测与可选生成/Judge 分开确认。Agent/Workflow 必须填写外部调用上限；RAG 生成/Judge 必须启用质量配置、确认脱敏数据可外发，且当前端点/模型/价格可核算。上限为10例、30次请求、5元，Embedding 同样计费；Judge 必须伴随生成。未知价格、能力或预算不足应在外部请求前拒绝。本文不新增费用或数据外发授权；没有适用授权时仅检查拒绝路径，真实质量接受保留待验收。
5. 点击“启动真实测评”，确认资源、租户、执行主体、测试集及预算快照。检查运行状态、幂等重复请求、排队取消、运行取消及失败终态。容量满时任务仅保留数据库 queued，不向内存无限排队；槽位释放后领取，进程重启收敛 interrupted，不隐式重试。
6. 在 RAG 的“检索快速诊断”输入同一查询，只选择页面提供的实际后端，让两侧至少一个 `{backend, top_k}` 不同。身份漂移或 backend fallback 应拒绝比较；单次诊断不证明批量质量提升。
7. 通过“查看报告”或 `/evaluation-reports` 查看历史报告、趋势和失败切片；核对任务/输入/模型用途、报告哈希、逐例来源及用量。红色报告仍显示失败，缺测保持未知，HTTP 成功不改变门禁结论。固定仓库 runner 仅为内部 Smoke，不作为页面真实质量入口。

## 自动验收矩阵

| 场景 | 自动证据 | 当前结果 |
|---|---|---|
| 报告协议、缺测、失败与脱敏 | `tests/test_evaluation_reports.py`、`tests/test_evaluation_api.py` | 专项通过；红色生成报告保持 `failed` |
| 数据集版本、审核、冻结、Gold 与租户隔离 | `tests/test_evaluation_datasets.py`、`tests/test_evaluation_targets.py` | 依最终候选指纹核验；跨租户拼接必须拒绝 |
| 任务预算、幂等、有界领取、取消、重启终态、报告哈希 | `tests/test_evaluation_jobs.py` | 内部 Smoke 零调用；真实任务沿显式预算，不隐式重试 |
| 真实资源执行、生成/Judge/Embedding费用与拒绝 | `tests/test_evaluation_adapters.py`、`tests/test_evaluation_orchestrator.py`、`tests/test_evaluation_quality.py` | 预算/身份/配置漂移反例不以真实调用或质量结论冒充 |
| 策略白名单、fallback、身份漂移、候选映射 | `tests/test_evaluation_comparison.py` | 专项通过；不修改默认检索配置 |
| 平台角色、页面分离与失败/空态 | `tests/test_platform_admin_access.py`、`frontend/src/App.forbidden.test.ts`、`frontend/src/pages/Evaluations.test.ts`、`frontend/src/pages/EvaluationReports.test.ts` | 浏览器走查待完成；不固定引用已过期测试数量 |
| 非调试调用计数、脱敏与未知Token | `tests/test_run_history.py`、`tests/test_trace_call_counts.py` | 计数独立于详细事件开关，读取历史不重放调用 |
| API 契约 | `docs/swagger.json`、`tests/test_openapi_coverage.py` | OpenAPI 已刷新；最终组合检查待执行 |

## 明确未实现或未验收

2026-10-25授权补齐TRACE两文件后的最终本地证据：专项408 passed、评测/聊天调用方补充172 passed、Ruff/mypy均exit0；独立Agent专项147 passed及Fast Route调用方13 passed，输入匹配。原两个卡外计数缺口已闭合，完整命令、版本指纹和验收范围见[本轮记录](../plans/implementation_trace_001.md)。这次接受的是材料一致性和已知缺陷修复，不替代以下整卡条件。

- 本批未引入重排、Query Rewrite、第二套评测器或跨会话长期记忆。自动 Silver 构造已实现，不能继续列为未实现；它不等于 Gold 或真实业务质量达标。
- 2026-10-24 已在有限授权下执行真实合成 Smoke（Embedding、生成、Judge）；费用、失败预占和边界见 EVAL-003 实施记录。该授权不是后续任意调用授权，此样本不是代表性真实业务质量接受。
- 旧候选完整验证 `data/validation/20261024T144752-f27f6cda9f5c/result.json` 为3500 passed/3 skipped及静态/前端检查通过，仅是历史证据。本次修复改变后端/测试/Evaluation 输入，不沿用它声明新候选全量通过。最新专项和独立复验查 tasks.yaml 所引实施记录；必要完整回归由最终候选同 SHA CI 补齐。
- 浏览器 D3 操作接受、代表性真实业务质量接受及最终同 SHA Backend/Frontend/Agent official baseline（以及适用 RAG 门禁）CI 尚未全部完成。无界队列缺陷已修复并专项复验，不再作为未修代码缺陷重复列出；依赖卡的整卡关闭仍待其必要证据。
- 上述关闭缺口存在时 EVAL-005 保持 `in_review`，不得标记 `done`；独立 Agent 复验不能冒充真人业务接受。

## 回滚

组合验收本身只增加测试、版本化用例与说明。回滚这些材料不删除报告、数据集、任务或业务数据；EVAL-003/004 的业务回滚分别按其实施说明执行。
