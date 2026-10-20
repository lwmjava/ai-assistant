# 工作流超时与取消最终独立技术审核（第一轮）

日期：2026-10-20（Asia/Shanghai）。实际审核身份：独立子 Agent `/root/final_audit`，本轮未参与本卡实现、计划审查或此前工程/迁移审查。使用 task-card-delivery 1.3.0 步骤10。仅新增本文，没有修改源码、测试、配置、依赖或冻结输入，没有提交、推送或操作真实数据库。

## 结论与权限边界

本地技术审核通过，未发现未修复的 P1/P2 业务缺陷。该结论限于当前候选的 Windows 隔离集成证据，不代表整卡完成或用户业务接受。最终提交对应的 Ubuntu Backend/Frontend/Agent official CI 仍未验证，用户也未授权 Agent 代行业务接受，任务应保持 `in_review`。不建议标记 done、发布或自动推送。

已实际读取 AGENTS.md、推进指导、Harness 治理、适用规则00/01/07/08/09/10/11、类型检查复盘、tasks.yaml 原始契约、批准计划及追加迁移授权记录、ADR-0017、完整业务差异（相对8cc3b06；另核对相对b7fab9d的追加修复）、最终实现说明、关闭对账、工程/迁移审查及 complete-evidence/complete-inputs。读取手动API、WorkflowEngine、控制注册、scheduler和WorkflowBridge调用方；没有以主Agent摘要代替原始契约。

## 独立实际验证

根据原始要求自主选择真实迁移、HTTP、Engine、Bridge/ChatService及原重试入口组合重新运行，未增加永久测试或改动现有断言。E2为真实代码加隔离SQLite、合作式合成长任务/故障注入；不是Gold、真实模型质量或生产性能证明。

| 原始要求或失败边界 | 本轮实际路径与结果 | 证据类别 |
|---|---|---|
| 可配置超时且超时标记、释放占用 | HTTP创建/更新默认300与1/3600持久化；总超时及退避纳入时限；新Session回读failed/TIMEOUT、结束时间和耗时；finally完成才移除活动绑定 | E2通过 |
| 接口手动取消 | HTTP /run、历史查询、cancel、新Session终态回读；重复取消等待真实清理；活动删除409、清理后删除成功 | E2通过 |
| 不影响cron调用者及父取消传播 | 真实scheduler._trigger取消后调用者继续；连续父取消不打断子任务finally；先发生TIMEOUT保留 | E2通过 |
| 权限租户/身份绑定 | 匿名401、只读403、跨租户/错绑定404、无本进程任务409；系统管理员兼容；相同URL/ID的两个内存Engine不能互相取消；退避后owner/tenant撤销阻止再次调用 | E2通过 |
| 非有限数与数值边界 | 原始HTTP NaN/Infinity/-Infinity创建和更新均422且无写入；严格整数范围及null/bool等拒绝 | E2通过 |
| 事务失败及资源清理 | 失败事务先rollback；终态commit不可用时HTTP503、不泄漏内部错误、实际资源释放，旧running可保留，不伪造持久化成功 | E2通过相应失败反馈 |
| 旧库兼容与不改执行历史结构 | 直接升级/降级/再升级、create_all兼容；父修订及更早混合修订通过auto_migrate补列，默认300、旧任务/历史保持；head缺列开发False/生产拒绝 | E2通过 |
| 前置重试契约 | 本轮同时运行原test_workflow_retry.py，成功重试、上限、数值及身份行为通过 | E2通过；不单凭done字段认定前置 |

实际命令（指定conda；独立测试库及basetemp；conftest离线保护生效）：

```powershell
$env:DATABASE_URL='sqlite:///./data/pwfl002-final-audit.db'
$env:PYTHONUTF8='1'
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest tests/test_workflow_timeout_migration.py tests/test_migration_reconcile.py tests/test_workflow_timeout_review.py tests/test_workflow_timeout_cancel.py tests/test_workflow_cancel_api.py tests/test_workflow_retry.py -q --basetemp=data/pytest-tmp/pwfl002-final-audit
```

退出码0：**53 passed、1 warning、14.35秒**。警告是失败事务注入的重复ORM身份SAWarning；没有失败或跳过。原始输出在本轮工具执行记录（exec session 71662）。该组合不重复累计重叠测试，也不替代全量回归。

## 独立指纹核对与证据归属

候选为HEAD `b7fab9d6db4646ee3a08cc69c1f627b4cc418ee9`加当前已冻结输入；业务差异基线 `8cc3b060e45d0f6686d99e94ce1ee71bf10c2f3e`。使用指定解释器经stdin运行Python，读取complete-inputs.json的files，逐项计算 `hashlib.sha256(Path(p).read_bytes()).hexdigest()` 并断言等于清单：**728项，变化0，exit0**。另逐项核对complete-evidence.json中14份日志SHA256，全部一致，exit0。既有报告中的最终全量3154 passed/3 skipped/4 warnings、905.94秒、exit0及Ruff/mypy、前端四门属于主流程已执行并绑定同输入的证据，本审核不冒称自行重跑全量或前端。

抽查关键SHA256：

| 文件 | SHA256 |
|---|---|
| app/core/migration.py | cd7e8b1960ec3dde77067d0f2e713f3fadd03d161da355e599b2ae67e2b27d75 |
| app/workflow/engine.py | 24d43db0e7fb715049b12db1e04729be1bbae2056ffeb42e9d69d3ba79c40fed |
| app/workflow/control.py | 8372c1b44ff90940a35a48e9a68aff5db970d7990806662652c3b8bacaaca85f |
| app/api/routes/workflow.py | 80f1284efd172339cb685b9be6f627d73fc023314e84b26b3d7b20d9325ce7e0 |
| tests/test_workflow_timeout_migration.py | 0fd4f362b7a302305c176eb85f81a9a9e6c836482bafabab084083823cb61777 |

## 未验证项与关闭对账

关闭对账中的原始两项交付与两项非目标同代码和本轮实际结果相符；旧库缺列不再是未修复阻断。未引入历史字段、跨进程强杀、前端按钮或新的Agent Runtime。当前只交付接口，未改页面，浏览器交互验收不适用。合作式取消不能保证终止同步阻塞、持续吞取消的下游或撤回既有外部副作用；终态数据库不可写时没有恢复队列，这些限制已如实记录。

全量中的三个Windows平台跳过不能替代Linux证据；最终Ubuntu CI、用户业务接受仍缺。独立技术审核现已出具，但不解除这些门禁。前置PWFL-001用户明确接受关闭的证据沿用其关闭复核；本轮通过原重试入口复验，没有发现未决P1/P2。

实际技术审核无失败；未运行真实收费模型、生产迁移、跨worker取消或发布演练，未作其通过声明。回滚按批准计划先排空活动执行再回退代码，可保留配置列和执行历史；真实迁移降级仍需明确授权。后续若改变冻结检查输入，相关证据必须重新验证；仅新增本文不改变现有728项输入。
