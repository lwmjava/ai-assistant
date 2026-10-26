# 最小评测平台组合验收

工作分支 `feat/EVAL-002`。本记录覆盖真实资源组合验收，以已批准的[真实资源设计](../superpowers/specs/2026-10-24-real-resource-evaluation-design.md)和[质量执行约束](../plans/implementation_eval_003.md)为准。下文2026-10-24/25记录属于历史候选；当前受托验收以2026-10-26批次为准，任务状态以 tasks.yaml 为准。验收通过不等于生产发布授权。

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

## 明确未实现或未验收（2026-10-25历史候选）

2026-10-25授权补齐TRACE两文件后的最终本地证据：专项408 passed、评测/聊天调用方补充172 passed、Ruff/mypy均exit0；独立Agent专项147 passed及Fast Route调用方13 passed，输入匹配。原两个卡外计数缺口已闭合，完整命令、版本指纹和验收范围见[本轮记录](../plans/implementation_trace_001.md)。这次接受的是材料一致性和已知缺陷修复，不替代以下整卡条件。

- 本批未引入重排、Query Rewrite、第二套评测器或跨会话长期记忆。自动 Silver 构造已实现，不能继续列为未实现；它不等于 Gold 或真实业务质量达标。
- 2026-10-24 已在有限授权下执行真实合成 Smoke（Embedding、生成、Judge）；费用、失败预占和边界见 EVAL-003 实施记录。该授权不是后续任意调用授权，此样本不是代表性真实业务质量接受。
- 旧候选完整验证 `data/validation/20261024T144752-f27f6cda9f5c/result.json` 为3500 passed/3 skipped及静态/前端检查通过，仅是历史证据。本次修复改变后端/测试/Evaluation 输入，不沿用它声明新候选全量通过。最新专项和独立复验查 tasks.yaml 所引实施记录；必要完整回归由最终候选同 SHA CI 补齐。
- 浏览器 D3 操作接受、代表性真实业务质量接受及最终同 SHA Backend/Frontend/Agent official baseline（以及适用 RAG 门禁）CI 尚未全部完成。无界队列缺陷已修复并专项复验，不再作为未修代码缺陷重复列出；依赖卡的整卡关闭仍待其必要证据。
- 上述关闭缺口存在时 EVAL-005 保持 `in_review`，不得标记 `done`；独立 Agent 复验不能冒充真人业务接受。

## 2026-10-26 真实数据受托验收

用户明确委托一次完成六卡收尾、独立复审与真实数据业务验收，真实模型总预算不超过人民币1元。本节为AI受托业务场景验收，不冒称真人审核，也不将Silver标为Gold。基线提交 `2da42007588ab7b3e6c5006c3787a428abd3f429`；本轮业务候选还包含报告导航、冻结身份投影及存量Workflow数据集约束修复，最终提交/CI由同批实施记录绑定。本文不自行关闭任务。

### 数据、隔离和费用

- 原库以SQLite只读连接备份到忽略目录 `data/validation/closeout-real-20261026/acceptance.db`；所有评测任务、登录口令替换、摘要、对话、工作流和迁移只作用隔离副本。原库前后SHA256均为 `468bc15fd4b06544450708368308de02fef10636d982e4a13ffe2eaf27aaeb8c`。未修改 `.env`，未迁移、删除或覆盖用户原库。
- 选择系统内真实《2026-08-05-RAG模块深度分析》，原文档ID `e82aa688fc0c4c03bae97e85f569e554`、40个真实向量块。含个人简历的另一文档被排除；所选技术正文扫描邮箱、手机号、长Token模式均无命中。只在隔离副本保留所选文档，未使用Mock Embedding或Mock LLM。
- 当前生产配置为Milvus但本机服务未监听，因此按授权显式使用local进行离线隔离验收，复用已有真实向量；不声称Milvus生产闭环或生产检索质量已经通过。Embedding为OpenAI兼容服务、`text-embedding-v3`、1024维；生成/意图/摘要使用 `deepseek-flash`。机器CPU/内存规格未采集，不将本节单样本延迟用作性能指标。
- 生成用真实章节摘录由正式摄取API新建，正文是原文第9号块的500字符逐字摘录，没有AI改写：源chunk `35799056a2fa477b8bc98323100dfe11`，内容SHA256 `10d9358d5d92437261c95a0192b7c81814ef05de8511559a54719544483588f8`，新资源 `272288c374cb420e83cef0ae58e6f23d`。原文档与冻结向量不变，来源链保存在 `excerpt-provenance.json`。
- 核价来源：[DeepSeek官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)与[阿里云text-embedding-v3价格](https://help.aliyun.com/zh/model-studio/text-embedding-v3)。保守按输入2元/百万、输出8元/百万、北京Embedding 0.5元/百万计算。临时HTTP预算拦截器只允许这两个实际端点/模型，按完整UTF8请求字节加512的输入上界和最多1024输出，在网络发送前持久预占；失败不退预占，无自动重试，累计超过0.6元即拒绝。其余0.4元留给本批CI预算核对。
- 本机29次外部请求累计保守预占 **0.2037865元**，这是计入失败的上界，不能冒称供应商账单实际扣款。RAG生成/Judge任务自己的已结算usage成本为0.00345元。完整请求不落盘，账本只记录用途、模型、上界、哈希和预占额。验收结束后付费开关撤销，服务保留供零费用页面走查。

### 实际结果

| 场景 | 实际结果与证据 |
|---|---|
| 正式鉴权与真实资源 | 隔离副本正式 `/auth/login` 登录，真实启用租户成员可作为默认Agent执行主体；平台管理员成功取真实文档，成员请求平台接口403 |
| RAG检索 | 真实原文档、真实Embedding与既有向量完成检索任务，任务和报告均通过；正式重复创建返回同一job，未重复执行 |
| 真实检索策略比较 | 正式比较API对同一真实问题执行native/top5与native/top10，返回5/10个候选；前5项chunk顺序一致、rank连续，全部document/chunk/source与实际活动索引行一致，Embedding身份为openai/text-embedding-v3/1024，比较前后活动索引/默认配置不变；成员请求403。5项检查通过，不推导性能或质量提升 |
| RAG生成与Judge | 真实原文摘录正式摄取、单条Silver、显式预算及数据外发确认，任务与报告均通过。报告 `job-c95a1a5468a34dd4b8ca313a876d9efc`；引用ID合法率1，Judge辅助faithfulness/correctness均1。它只证明此代表性技术问题通过，不证明整个知识库质量达标 |
| 长原文预算拒绝 | 原文档top10正文超过8000输入边界时任务以 `quality_input_limit` 明确失败，未发送生成请求；保留已发生Embedding用量，不放宽上限、不把失败改成通过 |
| 默认Agent真实管线 | 使用真实启用成员，正式任务实际调用意图/检索/回答链，最新任务报告通过，`pipeline_completion_rate=1`；未声明未测的路由/工具准确率，缺测保持null |
| Workflow真实执行与隔离 | 用户原库Workflow为0，未虚构“已有流程”。按本批授权在隔离副本新建真实技术文档问答Workflow（禁用调度、无webhook），经现有WorkflowEngine与真实Agent执行，报告 `job-2b6715b199f4432c9d7dfe35f0dfc376` completion/确定性断言均1；副本 `workflow_executions` 未新增，证明评测执行历史隔离 |
| 存量库约束与前向迁移 | 首次正式Workflow数据集创建复现500，旧CHECK仅含三类。修复后重启隔离服务，由正式auto_migrate从 `f8a4b6c7d9e0` 前向升级 `f9b5c7d8e0f1`，同一创建API成功；原40块、此前评测和记忆行保留，不手工篡改Schema，不作用原库 |
| 真实记忆压缩与复用 | 用所选真实技术文档事实构建隔离会话32条消息，经正式Chat接口实际LLM压缩，持久快照游标28；第二次真实回答复用同摘要与游标，答案事实符合文档；12项memory/trace检查通过 |
| Trace与历史 | 两次正式真实聊天落库为succeeded，非debug的实际LLM计数均1，未知Token为null；管理员读取列表/详情不增加任何外部调用，成员访问历史403，跨租户会话404 |
| 双主体审计 | 两条Agent创建审计同时记录发起管理员与真实执行用户，归属一致；成员访问审计403 |
| 删除与失效 | 正式删除仅本批临时会话，快照以 `conversation_deleted` 失效；软删除按原产品语义仍允许所有者查看保留正文，但后续Chat执行404且外部调用0。此边界未改成物理删除 |

辅助原始证据位于忽略目录 `data/validation/closeout-real-20261026/`：`api-acceptance.json`、`excerpt-workflow-acceptance.json`、`comparison-acceptance.json`、`memory-trace-acceptance.json`、`audit-delete-acceptance.json`、`cost-ledger.json`、`source-fingerprint.json`。本节保留可长期阅读的来源、哈希、上界、场景和结论；凭据、原始问题正文、模型答案和用户库副本不进入正式记录。正式回归与独立复审由同批实施记录及 `tests/` 证明，临时探针不替代回归。

失败归类：最初两次LLM失败来自临时预算包装未同步HTTP字节流，已修复包装并保留费用预占；初版记忆fixture误设未来时间导致前缀变化，改为过去时间后复验通过，旧证据保留。这两项不归咎于产品实现。真实长文输入上限属于预期拒绝；Workflow存量约束属于已复现产品缺陷，须以正式迁移回归和最终CI共同确认闭合。幂等/取消/撤销/过期/未知价格反例沿用当前同输入自动专项，不再重复付费。

主Agent实际浏览器走查完成：从完成任务点击真实检索报告及生成报告，任务/报告ID和family对应；刷新保留详情，类型切换清除旧选择；冻结数据版本、text-embedding-v3/1024/local/index身份显示正常，生成趋势显示deepseek-flash。Workflow报告展示真实执行通过。成员culture无测评/报告/历史/审计/状态菜单，直接URL为403；console error为0。截图ui-completed-tasks/ui-retrieval-report/ui-generation-report/ui-workflow-report/ui-member-forbidden.png保存在同一临时证据目录。非当前列表直达及非法参数/不存在报告另有正式前端回归。

独立复审见[本批记录](../reviews/2026-10-26-eval-memory-trace-closeout-review.md)：九项修复及两页面P2、空白摘要/官方输出上限/存量约束后续差异均已独立复核，无剩余P1/P2。最终组合专项299 passed、前端192 tests及typecheck/lint/build、Ruff无缓存、mypy256文件均exit0（详见implementation_eval_003.md）。最终同SHA CI仍待核验，不提前done；不对未执行的生产Milvus、真人Gold审核或跨会话长期记忆作完成声明。

## 回滚

组合验收本身只增加测试、版本化用例与说明。回滚这些材料不删除报告、数据集、任务或业务数据；EVAL-003/004 的业务回滚分别按其实施说明执行。
