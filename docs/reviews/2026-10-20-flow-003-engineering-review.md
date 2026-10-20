# FLOW-003 独立工程审查

日期：2026-10-20。独立身份：`/root/flow003_engineering`，未参与产品代码或测试编写，仅维护本报告。实际使用 task-card-delivery 与 code-review-quality 技能。后者要求凑发现数与本项目技能“不要凑够发现数量”冲突时，遵循项目原始证据，不制造缺陷。基线 `150afa8a8df771f60beb0fede8fe7038c40c500a`，分支 `codex/flow-003-scheduled-execution`。

已读 AGENTS、迭代指导、Harness 规范、适用 global/architecture/testing/security/reliability/Brownfield 规则、原卡、批准计划、独立计划审查、基线记录、完整两源 diff、原工作流执行链与两新增测试。原始要求为到点进入执行并持久化 cron 记录，不能只手动执行证明。不要求本卡建设新 slot 表、exactly-once、重试、并发池或新告警能力。

## 首轮结论：发现两个实际入口缺陷，修复前不通过

| 编号 | 严重性 | 实际反例与可观察结果 | 最小修复建议 |
|---|---|---|---|
| ENG-01 | P2 | 真实 `_tick` 扫描 UTC12:00 日计划后，在 `_most_recent_execution_time` 调用内通过新 Session 把 cron 从 `0 12 * * *` 改成 `0 13 * * *`，或时区 UTC 改 Asia/Shanghai；原 `_trigger` 只核 enabled/suspended，仍调用 Bridge 一次并落 success 一条。当前未来计划被旧到期快照执行。 | 新 Session 复核计划身份；计划改变则本次不执行，保留下次 tick 正常判定；正式回归两个变体。 |
| ENG-02 | P1 | 保留 active User，把同租户 Tenant 禁用或通过隔离 SQL 删除；真实 `_tick → _trigger → WorkflowEngine` 仍调用 Bridge 一次且新 Session 可读 success cron 一条。cron 不经过 API 的 Tenant 校验。 | 在当前 cron 激活入口新 Session 校验 Tenant 存在且 active，拒绝时不进入 Engine；补禁用、缺失两正式反例。不改变其他 API/Engine 权限语义。 |

发现后均即时发送主 Agent；未修改实现与测试，等待主 Agent先记录修复计划及正式红测试，再修复后复验。不得转交后卡来解除本卡关闭义务。

## 实际探针与证据类别

Windows，指定 Python 3.12/croniter6.2.4。独占验证窗口，串行执行，无付费模型、外网、生产数据库或 Git 写。通过 stdin 执行独立 Python 脚本，`importlib` 加载 `tests/test_workflow_schedule_contract.py` 中隔离库夹具的 `scheduled.__wrapped__`，`pytest.MonkeyPatch` 和独立临时 SQLite；真实 scheduler/Engine，合成 Bridge 记录调用，结束显式 dispose。E2 证明激活、权限判定和持久化边界，不能代表模型答案质量。

1. ENG-01 两变体探针 exit0，输出各 `calls=[('owner','tenant','scheduled input')] rows=1`。这是观测性探针退出码，不是业务通过；零执行期望被违反，需正式断言红。
2. ENG-02 第一探针禁用变体同样 calls1、success1。缺失变体最初用 ORM `session.delete(Tenant)` 触发 relationship 将 User.tenant_id 置空导致 NOT NULL 错误，脚本 exit1、清理时 Windows 文件占用；此夹具错误不作为业务红或缺失租户结论。
3. ENG-02 第二探针采用 `session.execute(delete(Tenant))` 只删除隔离库租户行，保留错误 User 绑定；exit0，真实 calls1、success cron1，独立证实缺失租户边界。正式测试须保留严谨 finally 清理。

探针核心步骤可复算：fixture 建 active Tenant/User 与历史一分钟 Workflow；ENG-01 包装原 `_most_recent_execution_time`，在新 Session 修改指定 Workflow 字段后仍返回原查询；ENG-02 在 tick 前新 Session 修改租户状态或 SQL 删除；`await scheduler._tick()` 后用 fixture 新 Session `rows()` 读取，并核对合成 Bridge calls。

## 其余边界只读检查

当前 max(created_at) 查询按 workflow_id、tenant_id、cron 来源过滤，所有状态参与；manual、started_at、finished_at 不替代水位。UTC 转换避免 fold 本地相等误判，未来创建时间与等于到期时间分别处理。失败尝试先消费进程水位，下一 tick 不因只查 success 而重试。非法 cron/时区单任务隔离，保留既有取消传播。串行扫描时间快照、croniter DST 既有候选及跨重启保守近似已获批准，不据此要求新增架构。

主 Agent 首轮报告42 passed/11.11s、Ruff/mypy exit0，仅为收到的历史运行摘要，不冒称本 Agent实际运行或修复后证据。全量回归、最终输入绑定、最终独立技术审核与用户业务接受仍须各自完成。

## 修复后复验：工程审查通过

主 Agent/独立作者补正式回归，实际业务红记录为 `2026-10-20-flow-003-contract-eng01-red.txt`、`2026-10-20-flow-003-contract-eng02-red.txt`，修复计划与闭环见同目录 `2026-10-20-flow-003-review-fixes.md`。本 Agent重新读取最终两源完整 diff，确认 `_trigger` 对 cron_expr/timezone/created_at 比较扫描计划身份；Tenant 存在且 active 才进入 Engine。失效租户仅拒绝本次，不误当 owner 失效永久停用。

独占 pytest 窗口实际执行（非主 Agent转述）：

```powershell
$env:DATABASE_URL='sqlite:///./data/flow003-engineering-test.db'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_workflow.py tests/test_workflow_schedule_contract.py tests/test_workflow_schedule_integration.py -q --basetemp=data/pytest-tmp/flow003-engineering *> data/flow003-engineering-pytest.txt
```

结果：**48 passed、1条既有 sandbox ast.NameConstant 弃用警告、12.84秒、exit0**。包含 ENG-01两变体和ENG-02两变体正式反例，以及真实 Bridge/ChatService/历史 API/已启动 loop、self/langgraph 两路径、owner与Tenant拒绝的最终数据断言。合成模型/隔离DB为E2；不是生产模型质量或真实墙钟等待时延证据。日志 SHA256：`81abc0944bfd64729e9502813d626879b1586a8d4d1695293512644762423e0c`。

另用上述相同独立 fixture 探针实际执行两条新的反例/恢复路径，均 exit0：

- 扫描后新 Session 将 created_at 改成扫描时间后10秒：本槽零调用/零记录，移除变更钩子并推进下一分钟后恰好一条 cron 记录。
- Tenant 禁用时本槽零调用/零记录；新 Session重新启用后推进下一分钟，仍可正常调用并落一条 cron 记录，不被误永久暂停。

两条探针均真实 `_tick → _trigger → Engine`、合成Bridge、提交后新Session读取。探针脚本不写产品或测试，正式身份与计划变化回归仍以上述48项为准。

冻结输入（未提交，HEAD加工作区文件SHA256）：

| 输入 | SHA256 |
|---|---|
| app/workflow/engine.py | `0b11a570d62755541c8044ab1bd333c4d25d7dfdfc800b20c810ecee27697b1c` |
| app/workflow/scheduler.py | `3cbfd480dc090139fac83c2d233a7a15421d460bc718a4520de78801e3133f41` |
| tests/test_workflow.py | `cb08ceb68615a52de27bf3c7dfe38aea2071fd16a040c34b3f39a46f77f472c2` |
| tests/test_workflow_schedule_contract.py | `2ada07778ddb875b61a20e42636607d746989861f1c392a32de11405f4812f05` |
| tests/test_workflow_schedule_integration.py | `9d38804854a7c0a669a0a527678b4d177bb1e71840171c8b1b02899764c6357d` |

**ENG-01、ENG-02均已修复并由本独立审查者实际重验，无未修复P1/P2，工程审查通过。** 不据此标卡 done。主 Agent仍须最终全量回归、Ruff/mypy、最终版本绑定、独立最终技术审核；用户业务接受和最终SHA CI仍待证据。本 Agent不是实现者，也不兼任最终独立人工审核或本卡业务接受。
