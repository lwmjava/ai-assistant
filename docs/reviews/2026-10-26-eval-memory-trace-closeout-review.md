# 评测、运行历史与记忆九项修复独立复审

审查者：`/root/independent_review`，未参与本轮业务实现。授权：用户委托本批次独立复审和受托业务验收。输入：`feat/EVAL-002`，提交 `2da42007588ab7b3e6c5006c3787a428abd3f429`；主 Agent 同时修复报告页面，审查不修改业务实现。审查范围限于 `implementation_eval_003.md` 九项修复及直接调用方，不扩展 backlog，不产生真实模型费用。

## 结论与发现

初审发现一项 P2，已由实现者修复并完成下述独立复核。最终结论：本轮九项修复及追加的两项报告页面修复，在已覆盖范围内无未闭合 P1/P2；独立技术复审通过。真实业务接受和最终提交 CI 仍由批次主记录判定，不能由本结论替代。

- **P2（已修复）：空白压缩响应丢失已有摘要。** `app/memory/manager.py:112-120` 原先在压缩器返回空白摘要时只恢复 `pending[-window:]`，仍返回新的空摘要和前移后的 `compressed_count`。已有较早消息仅存在于旧摘要，当前请求因而丢失这段记忆；若调用方直接续用返回快照，还会跳过没有有效摘要的前缀。最小复现：旧摘要非空、游标 2、总消息 10、窗口 4、阈值 5、keep_recent 2，模型返回空格；结果摘要为空、游标 8、窗口 4。异常抛出用例通过不覆盖这一边界。实现者在 `MemoryCompressor.compress` 中对 strip 后空响应返回旧快照（无旧摘要则返回空快照），由管理器既有回退保留待处理窗口；`tests/test_memory_overflow.py` 增加无旧摘要/已有摘要两种正式回归，独立复核通过。

曾检查“发起管理员所属租户被停用仍允许跨租户评测”，但**不作为缺陷**：`app/api/deps.py` 明确平台管理员可在所属租户停用时进入管理接口并切换租户；运行守卫此行为与现有公共准入一致。不能为满足审查发现数量而改变既有权限边界。

## 覆盖

| 修复边界 | 独立核对内容 | 结论 |
|---|---|---|
| 迁移对账 | 不任意 stamp 最新 head；旧结构只确认具体修订后正常 upgrade；head 与结构不一致重新执行幂等迁移；现有行保留 | 未发现新增 P1/P2 |
| 资源快照 | 新会话重读文档/活动索引、目标租户、Agent 用户及 Workflow 所有者；逐例/调用前后对比冻结身份 | 未发现新增 P1/P2 |
| 主体撤销 | 发起管理员角色/启用/强制改密/令牌版本，执行主体身份与目标租户；对照平台管理员既有豁免 | 未发现新增 P1/P2 |
| Agent 共用管线 | 正常同步与评测调用 `_run_selected_agent`；路由/快速路径/Skill 组装；L0 注册表与无普通会话写 | 未发现新增 P1/P2 |
| 缺测指标 | 未提供 route/tool 期望、追问和工具有效性无测量返回 null；完成率独立命名 | 未发现新增 P1/P2 |
| 调用中取消 | `_guarded_call` 轮询守卫、取消并等待 provider/tool 收尾；下一次调用拒绝；计数不虚增 | 未发现新增 P1/P2 |
| 压缩失败窗口 | 异常回退保留 pending 完整窗口；空响应与已有摘要反例 | P2 已修复并独立复核 |
| 配置与快照 | Chat 消费 MemoryConfig；策略指纹、摘要前缀、过期/令牌/会话绑定与复用 | 未发现新增 P1/P2 |
| 部分流终态 | 正常/快速流关闭收尾，实际调用计数落库，failed 配 run_cancelled/run_interrupted | 未发现新增 P1/P2 |

## 实际验证

环境：Windows，`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`；验证器创建独立 SQLite 库，未触碰用户数据库。命令：

```powershell
python scripts/validate_delivery.py targeted --test tests/test_evaluation_runtime_guards.py --test tests/test_migration_reconcile.py --test tests/test_memory_snapshots.py --test tests/test_trace_call_counts.py
```

`data/validation/20261026T011015-da3334a976c0/result.json`：passed，collection/pytest exit 0，61 passed、1 warning，pytest 10.10 秒。该证据绑定当时输入，后续修复须以新输入重绑；不能复用它证明新代码通过。

追加命令选取 `tests/test_evaluation_adapters.py`、`tests/test_run_history.py`、`tests/test_evaluation_orchestrator.py`：28 passed、1 warning，pytest 8.49 秒；`data/validation/20261026T011122-a65e31237abe/result.json` 因主 Agent 并行输入变化为 **invalidated / exit 1**，不记作正式门禁通过。

空白摘要反例为独立内存复现，未修改代码、没有网络调用；可复现缺陷须由实现者加入正式测试。未运行重复全量、不替代最终提交 CI，不将 Mock 专项当真实业务验收。页面两项 P2 由主 Agent 修复，真实数据业务验收与最终 SHA CI 由批次主记录统一维护。

## 最终复核与退出

按主 Agent 请求，追加核对空白摘要修复、后端报告 metadata 投影和四个前端页面/测试文件，不再重复全系统审查：

- 报告直达：仅 succeeded 且 report_id 非空的任务显示链接；runner 映射真实 family；URLSearchParams 编码 report ID；报告页读取 URL，直接查询详情不依赖列表第一页；generation/workflow 默认指标匹配；错误和非平台管理员沿现有拒绝路径。未发现剩余 P1/P2。
- 报告身份：从冻结 dataset.version_id 提供前端兼容别名；从目标快照白名单投影 Embedding provider/model/dim、索引和向量后端；混合模型不伪造同一模型，空值保持未知；命名空间模型可用，正文/凭据不投影。未发现剩余 P1/P2。
- 空摘要：旧摘要及游标完整保留；无旧摘要不创建虚假压缩游标；pending 窗口不缩成 keep_recent。正式测试覆盖两种情况。

最终独立专项命令：

```powershell
python scripts/validate_delivery.py targeted --test tests/test_evaluation_reports.py --test tests/test_memory_overflow.py --test tests/test_memory_snapshots.py --test tests/test_evaluation_adapters.py --test tests/test_run_history.py --test tests/test_evaluation_orchestrator.py
```

`data/validation/20261026T011320-51e1aae658b2/result.json`：passed，collection/pytest exit 0，66 passed、1 warning，pytest 7.09 秒。它重绑了空摘要、报告投影及直接调用方；此前 guard/migration/trace 实现和对应测试未改变。前端独立执行 `D:/DepTooL/nodejs/npm.cmd run test -- --run src/pages/Evaluations.test.ts src/pages/EvaluationReports.test.ts`：exit 0，2 文件、10 tests passed；存在 MemoryRouter SSR useLayoutEffect 警告，不影响断言。该测试证明渲染/查询参数接线，交互浏览器走查由主 Agent 完成，不能冒称本审查者已亲自做浏览器验收。

最终复核业务与测试输入 SHA256：

| 文件 | SHA256 |
|---|---|
| app/memory/compressor.py | b080ed5a3ecc8496ffca7a09a8e06cc6bdc18b3e923a8a1ba95b95d675d49677 |
| tests/test_memory_overflow.py | caa1713f03958f9bdc10ccc5ac5678c22516b18a057aefac324540044baed534 |
| app/services/evaluation_reports.py | 9897e59abfc17c11b68767844294ab7e3876ec5f69f55ee117ec9e659277042b |
| tests/test_evaluation_reports.py | 15fdb7175cb0d5da81738495b70674432fe37f463310d0435c73100765fcf83e |
| frontend/src/pages/Evaluations.tsx | 898b7b2686e05e09fe3f0e86ee9cb52bca7fc1b245ef102f408f08d708730838 |
| frontend/src/pages/EvaluationReports.tsx | 2809ac82d14c4e8048ad27f02ceea66b3af70de687bae278bb8f91324725f951 |
| frontend/src/pages/Evaluations.test.ts | 4e3194a80afc75b7c0862bfaf8eb2d5f267ee1e365a01f3938b0e76342d47596 |
| frontend/src/pages/EvaluationReports.test.ts | 901112c9e514cf4ea6603a6d3acfeff3e1eb2ef01a42becf118c5c12a6ad168b |

除上述范围未发现项不自动实现。实际免费/付费真实模型质量、用户数据库操作、浏览器业务场景和最终 SHA 的 Linux CI 不在本次独立技术复审实测范围；需要主批次的独立证据。最终审查完成后仅补说明不重新轮审；相关业务输入再变化只复核受影响部分。技术独立审查、受托业务接受与发布授权分别记录。

## 追加预算边界差异复核

用户给定批次真实模型费用上限 1 元，主 Agent 据此在 `scripts/run_agent_chain_eval.py` 的官方 route/tools 请求中明确设置 `CALL_MAX_OUTPUT_TOKENS=1024`。本次仅审查这两处 LLMOptions 及对应测试，不重跑九项审查。差异未改变冻结用例、阈值、工具协议、评分函数或恢复策略；所有请求（包括允许的恢复请求）复用显式上限。既有 `finish_reason=length/content_filter` 的空响应不得恢复重试，仍按失败进入评分；增加上限没有把空响应或畸形工具响应改成通过。注意既有 provider 对**非空**响应并非一概因 length 拒绝，本复核不夸称所有截断均被拒绝。

独立命令 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe scripts/validate_delivery.py targeted --test tests/eval/test_agent_eval_runner.py`：`data/validation/20261026T011700-e0f55f183c9f/result.json` passed、collection/pytest exit 0，41 passed、10.71 秒；没有付费调用。源码 SHA256 `1f23b598948d099bb355e6f5014af6261a5919917555947d5ebb878e5cb8fca1`，测试 SHA256 `391bb5cfe620eefb8285d5fc456c8203fa9b8fe989e672ca711d1bfc5be65f99`。新增差异未发现 P1/P2。

1024 仅限制每请求声明的输出上限，不独立证明批次总价：主批次仍须核对固定输入上界、12 个逻辑请求及最多 1 次恢复、供应商最高单价、其他 CI/业务验收请求和供应商对输出限制的执行。预算分配与实际费用由主批次记录；本复核不授权超预算请求，也不以 Mock 专项代替最终同 SHA official CI。

## 追加存量数据集约束修复复核

真实副本验收发现旧数据库的数据集 family CHECK 不含 workflow，主 Agent 新增向前迁移 `f9b5c7d8e0f1` 并在 head/shape 对账中核对 workflow 家族。独立审查仅覆盖此新差异及两个测试文件，未重新轮审旧模块。

迁移只替换数据集 family 值域约束，许可集合与现有模型/服务契约一致；不改变用户、角色、租户或操作准入。通过反射保留现有列、主键、唯一约束、索引与其他 CHECK，匿名 CHECK 在批处理前命名；已有 workflow 值域时直接返回，重复 upgrade 幂等。head 误标旧结构沿现有幂等向前迁移恢复，不仅修改版本戳。downgrade 明确保留加宽约束，避免已有 workflow 数据及关联版本因回滚而被删除；回滚代码不等于收窄数据库许可值域。SQLite batch recreate 会重建该表，实际用户库运行前仍应遵守备份/授权；本审查只运行隔离库，不执行用户库迁移。现有项目表没有迁移外自建触发器/FK 行为的覆盖承诺，不据测试宣称任意手工改造数据库都可无损升级。

独立命令：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe scripts/validate_delivery.py targeted --test tests/test_real_evaluation_constraint_migration.py --test tests/test_migration_reconcile.py`。`data/validation/20261026T012400-9ace6054087a/result.json`：passed，collection/pytest exit 0，10 passed、9.05 秒。覆盖 named/anonymous 旧 family CHECK、重复升级、旧行保留、索引/唯一约束/其他 CHECK 保留、未知 family 仍拒绝，以及 head 已到最新但 family 仍旧的连续 auto_migrate。未发现新增 P1/P2；正式验收需继续在副本完成真实 Workflow 测评，并取得最终提交 Linux CI。

对应 SHA256：迁移 `cfd25d2cf77745837880a3aba0740fea975a46e0ba85939254a19f89d47e6a3d`；`app/core/migration.py` `49d81edcd3aaab263fecbdae761974e7cb0934ec884319630a5e23bdfc397b42`；约束迁移测试 `2ba4bc0febd8cf40f6104aac0a152930acb4a8c40257a3695789716da9c5336c`；对账测试 `30ede684f892d383dbfc19a7f05f4225bfd37c005ddfd4ed3e04527d4d4f34db`。
