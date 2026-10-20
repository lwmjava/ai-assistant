# FLOW-003 独立实施计划审查

日期：2026-10-20。审查身份：`/root/flow003_plan_review`，未参与实现或测试编写。对象：`docs/plans/plan_flow_003_20261020.md`，基线 `150afa8a8df771f60beb0fede8fe7038c40c500a`。本轮只审计划，未运行 pytest、未修改业务代码；不构成实现验收或用户业务接受。

已实际读取 task-card-delivery、plan-eng-review 技能，AGENTS、迭代指导与 Harness 规范、适用全局/架构/测试/可靠性/Brownfield 规则，原始任务 FLOW-003、FLOW-001 相关约束，PRD §5.6.2、锁定拆分计划第6节、真实 scheduler/engine/bridge/models 与 tests/test_workflow.py。按项目要求将正式评审写入 reviews；不执行技能中与本次 Windows 只读审查范围无关的全局安装、遥测或计划文件改写。

## 结论与范围挑战

**建议方案 A；主 Agent 已整合下列三项具体约束，本 Agent 已只读复核，计划审查通过，可交用户一次批准。** 根因成立：tick 的 now 先取，next_run_time 默认在稍后以当前时间求严格未来，因此 `nxt <= now` 正常不可成立。保持 next_run_time 的现有 API、新增最近到期专用 helper、复用原执行器，是足以解决原始到点记录要求的最小方案。

不要为一条到点记录引入持久化 scheduler、slot 字段、唯一键、锁、线程池或新 Runtime。原始任务并不要求跨重启精确 slot 或恰好一次；计划已明确现有时间戳恢复只是保守近似，不能宣传为完整恢复/幂等。PRD 的 run_latest 方向支持只选最近一次；PWFL-001 的重试、PWFL-003 的并发幂等与其他工作流卡仍未完成。

## 需要具体化的计划项

| 编号 | 证据与风险 | 建议处理 |
|---|---|---|
| PLAN-01 | 第4节“创建/开始时间”没有确定查询规则。按 success 或 finished_at 会重试失败或吞掉长任务期间下一槽；若 started_at 优先，排队/延迟启动也可把尚未消费槽当作已消费。 | 明确选同 workflow_id、同 tenant_id、triggered_by=cron 的所有状态记录，以创建时刻建立保守水位；若采用其他规则，先列出延迟启动反例。全部时间先转 UTC，manual 不参与，pending/running/failed 均参与；跨槽延迟落库仍保留已说明限制。 |
| PLAN-02 | 含整点不能简单把 now 加一秒后 get_prev；微秒临界可意外选到未来。相同 ZoneInfo 的 fold=0/1 本地 datetime 比较会视为相等。 | 明确包含整点的候选算法，并验证候选 UTC <= now UTC；整点前1微秒、整点、整点后1微秒、回退重复小时均应正式回归。水位比较只用 aware UTC。 |
| PLAN-03 | 实际 croniter 6.2.4 的 DST 返回并非通用人类 cron 直觉，直接继承必须明示。 | 明确继承已安装 croniter 语义并固化有限案例，不宣称通用 DST 正确；若要求不存在的02:30跳过、重复01:30执行两次等额外策略，先由用户裁决，不能偷偷建设新时区调度器。 |

以上为实施前歧义和风险，不是对尚未实现代码的缺陷判定。修订复核：计划第4节已明确 max(created_at) 与状态/租户/来源过滤；包含整点以 now+1微秒求严格前驱、UTC candidate<=now 及极值失败关闭；DST 继承6.2.4有限实测、串行时间快照与无实时 SLA。PLAN-01/02/03 均已在计划层解决，实现仍须正式反例验证。

## 四维审查

**架构：** 保留 `_tick → _trigger → WorkflowEngine → WorkflowBridge → ChatService`。新 Session 按 workflow ID 重读 enabled/suspended、owner 当前状态，避免脱离会话扫描快照重新 add 回写甚至恢复已删除任务。当前计划没有改变权限或持久化状态边界，不需要新 ADR；一旦需模型迁移或精确恢复协议须停下申请。

**代码：** 专用 helper 与下一次 helper 语义分离，复用可选 croniter、ZoneInfo、既有错误类型；SQLite naive 按现有 UTC 约定处理。尝试前推进进程水位，单槽失败不重试；禁止以只有 success 为恢复依据。内存清空测试必须验证数据库行为，不能只看字典内容。异常逐任务隔离，取消继续传播；不将原始 prompt、模型内容或任意异常原文加入新日志。

**测试：** 计划提出实际 `_loop/_tick`、真实 Engine/Bridge/ChatService、隔离库和合成 provider，是适当 E2；只 mock trigger 或只手动执行不足。至少一个真正跨越下一次时刻、落库后新 Session 和历史 API 查询的成功测试；一个失败任务和另一个成功任务同 tick；扫描后删除/停用/暂停与 owner 撤销；不同租户伪记录不抑制定时、manual 不充当水位；同槽重复/下一槽/错过多轮/重置内存后失败记录不重试。原有9个测试没有到点断言，不能代替这些新增测试。

**性能：** 每 tick 每任务一次 cron 解析、首次水位查询的方案是现有规模可接受的增量，不能以此声称压测通过。既有串行 await 意味长任务延迟后续任务和下一 tick；本卡明确无准点 SLA、并行执行或吞吐改善承诺。无需引入池或循环回放历史；恢复查询应只取必要一条/聚合水位，不加载全部历史内容。

## 失败矩阵与复用

| 场景 | 本卡可观察结果 | 既有复用 |
|---|---|---|
| 未到点/创建前的最近槽 | 零执行记录、零模型调用 | cron 解析与 Workflow.created_at |
| 到点/多轮 misfire | 最多最近一次 cron 执行，记录 owner/tenant/input/output/status | 原 Engine、Bridge、ChatService 与历史 API |
| 同槽第二 tick/已记录失败后清内存 | 不重试、不重复执行 | 进程水位与同租户 cron 尝试创建时间 |
| owner 缺失/禁用/迁租、任务已删/停用 | 不执行旧快照；按原规则停用失效 owner | User、Workflow 与现有 Engine 拒绝 |
| 非法 cron/时区、某任务异常 | 本任务失败隔离，同 tick 其他任务继续 | 原逐任务异常隔离 |
| 缺 croniter/开关关闭 | 原启动门控保留，无新自动调用 | _is_scheduler_runnable/start/stop |
| 取消/旧手动/Webhook | 保留原兼容，不声称新增超时或取消能力 | 原生命周期与现有测试 |

## 纯时间探针证据

指定解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Windows，croniter **6.2.4**。实际执行通过 stdin 传入的独立 Python 脚本，核心为 `croniter(expr, now.astimezone(zone) + timedelta(microseconds=1)).get_prev(datetime)`，exit **0**。不导入项目 DB，不启动模型、不运行 pytest。此处 E3 只揭示 helper 风险，不充当交付 E2。

| now UTC / America/New_York 表达式 | 实际 prev UTC |
|---|---|
| 2026-10-20 12:00:00 / `* * * * *` | 12:00:00 |
| 2026-10-20 11:59:59.999999 / `* * * * *` | 11:59:00 |
| 2026-11-01 06:30:00 / `30 1 * * *` | 05:30:00，返回第一次01:30 |
| 2026-03-08 07:30:00 / `30 2 * * *` | 07:00:00，即本地03:00，非字面02:30 |

另实际验证：同 ZoneInfo 下 `2026-11-01 01:30 fold=0 == fold=1` 为 True，转 UTC 后比较为 False。避免将墙钟相等误认为同一绝对时刻。

## 执行分工、非目标及门禁

主 Agent 整合计划/源码与共享文档；独立验证 Agent 拥有新测试文件；本 Agent 只作独立计划审查。后续实现审查需实际入口反例，最终人工审核 Agent 不能由实现者或既有审查者兼任。pytest 共享库串行，Ruff/mypy 及公共路径无筛选全量 pytest 必须实际执行，最终输入指纹与最终 SHA CI 绑定。

不涉及前端/API/配置/依赖/迁移/生产数据；不改运行开关 UX、失败告警、重试、并发池、资源超时或平台分支。用户尚未委托 FLOW-003 业务接受；全部技术门禁通过仍保持 in_review，不能继承上一卡委托自动关闭。

待决事项：集中取得方案 A 及保守恢复/时区边界的用户批准。无未决技术方向问题，无新增供应商、架构或范围审批建议。
