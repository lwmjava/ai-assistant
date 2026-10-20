# 工作流超时与取消：最终关闭对账

日期：2026-10-20。主Agent整合；本文件不是独立人工审核。原始契约：tasks.yaml PWFL-002。批准依据：实施计划及ADR-0017，用户随后明确批准追加app/core/migration.py。本轮只在隔离SQLite/合成合作式执行中验证，没有真实库迁移、收费模型、推送、合并或部署。

本记录覆盖b7fab9d中的旧“缺列阻断”结论：启动修复及独立入口复验已通过。最终全量回归已通过；最终独立技术审核已通过，Ubuntu CI与用户业务接受仍未验证，因此尚不能done。

## 原始要求逐项对账

| 要求及出处 | 输入/操作链 | 可观察结果及E2证据 | 当前结论 |
|---|---|---|---|
| 可配置超时：任务deliverables[0] | 真实HTTP创建/更新，提交后新Session读取 | 默认300，严格整数1–3600；省略保留、显式null及非法值422；6个raw NaN/Infinity/-Infinity创建/更新均422且无写入 | E2通过 |
| 超时中止、标记、释放：acceptance[0] | 真实engine、合作长任务及退避，实际finally闸门 | 总时限含重试等待；failed/WORKFLOW_TIMEOUT、结束时间与耗时持久化；清理前不返回/不移除活动绑定；重复父取消不打断清理 | E2通过，非强杀或硬实时承诺 |
| 手动取消：deliverables[1]/acceptance[1] | 真实HTTP /run→查询历史→cancel→新Session回读 | failed/WORKFLOW_CANCELLED、资源释放、终态幂等；cron _trigger调用者继续；匿名/只读/跨租户/错绑拒绝 | E2通过，交付接口而非页面按钮 |
| 不改历史结构：non_goals[1] | 旧库直接Alembic升级/降级/再升级；启动init_db/auto_migrate | 只新增Workflow.timeout_seconds，旧行默认300，任务/历史内容与行数保留，历史列结构不变 | E2通过 |
| 旧库兼容及启动修复 | 父修订、更早修订/实际新schema、无版本create_all混合库、重复启动 | 缺列先对账再增量升级，不跳过DDL；版本已head却缺列开发False/生产拒绝，不伪报迁移成功 | E2通过；不授权真实库修补 |
| 权限/租户与绑定 | 退避时撤销owner、移出租户、tenant禁用；两个相同URL/ID的独立内存Engine | 再次bridge零调用/失败终态；第二库无法取消第一库，原历史不改 | E2通过 |
| 错误/回滚/幂等 | 失败事务注入、并发取消/删除、终态commit抛OperationalError | rollback后可写终态；活动DELETE409；DB确实不可用时取消503且不泄露内部异常，资源清理但旧running可保留，不伪报已保存 | E2通过相应失败反馈；不承诺自动补偿 |
| 独立入口审查 | execution_review与migration_review独立运行入口反例 | 工程末轮44项；本轮迁移+对账+review16项（其中7项与44重叠），另真实init_db探针两场景 | 无未修复P1/P2，详见两份评审 |
| 本地必要回归 | 冻结728输入后全量pytest、Ruff/mypy、前端四门 | 全量3154 passed/3 skipped/exit0，静态及前端通过 | 本地通过，Windows跳过不替代Linux |
| 最终独立人工审核/业务接受/Ubuntu CI | 另一未参与实现审查Agent、用户业务接受、最终提交CI | 独立final_audit审核53项通过；用户未委托业务接受；未授权推送/未取得最终CI | 本地技术审核通过，外部门禁未验证，禁止done |

E2为真实代码+隔离SQLite及合成bridge/provider的集成证据，不是Gold、真实模型质量或生产性能证据。没有以E3 helper、E4 Mock/历史替代必交付。单纯HTTP200、截图或单测数量均未被用作业务成功依据。

## 环境、基线与输入绑定

基线SHA b7fab9d6db4646ee3a08cc69c1f627b4cc418ee9，分支codex/flow-003-scheduled-execution，开始时工作区干净；候选为该HEAD加最终源码/测试/配置/依赖/评测输入指纹，docs/reviews/2026-10-20-pwfl-002-complete-inputs.json共728项。后续只改说明/评审，不改检查输入；任何相关输入变化使对应证据失效。

Windows11 build22621；解释器D:/DepTooL/anaconda3/envs/ai-assistant/python.exe、Python3.12.0；pytest8.4.2、Ruff0.8.4、mypy1.14.1、SQLModel0.0.22、Pydantic2.10.4、httpx0.28.1、Alembic1.14.1、langgraph1.0.10。CI相关extra已安装，未通过关闭功能规避失败；本地pytest与项目dev声明8.3.4版本有差异，最终Ubuntu CI未验证。Node22.20.0/npm10.9.3。

测试conftest隔离本机.env与模型密钥，禁止外发；ENV=development、AUTH_ENABLED=true、AGENT_ORCHESTRATION=self、SECURITY_RATE_LIMIT=false；RAG原默认native/local与开发Mock行为不变；WORKFLOW在相关fixture中显式启用。每个Agent用独立DATABASE_URL/basetemp，没有共享测试库并发污染。未读出或记录凭据。

## 实际命令与结果

所有python命令均使用上面的指定解释器，PYTHONUTF8=1。

```powershell
$env:DATABASE_URL='sqlite:///./data/pwfl002-core-baseline.db'
python.exe -m pytest tests/test_workflow_timeout_migration.py tests/test_migration_reconcile.py -q --basetemp=data/pytest-tmp/pwfl002-core-baseline
```

基线1 failed/5 passed、4.30秒、exit1。新增当前head缺列/更早混合修订反例后4 failed/2 passed、5.11秒、exit1（core-red库及basetemp）；失败均对应缺失的业务行为，非import或环境错误。

```powershell
$env:DATABASE_URL='sqlite:///./data/pwfl002-core-green.db'
python.exe -m pytest tests/test_workflow_timeout_migration.py tests/test_migration_reconcile.py -q --basetemp=data/pytest-tmp/pwfl002-core-green
python.exe -m ruff check . --no-cache
python.exe -m mypy app/
```

9 passed、4.66秒、exit0；Ruff exit0，mypy220文件exit0。日志data/pwfl002-core-green.txt、pwfl002-core-ruff.txt、pwfl002-core-mypy.txt。

```powershell
$env:DATABASE_URL='sqlite:///./data/pwfl002-complete-full.db'
python.exe -m pytest --basetemp=data/pytest-tmp/pwfl002-complete-full
```

最终全量3157项收集，3154 passed、3 skipped、4 warnings、905.94秒、exit0；完整日志data/pwfl002-complete-full.txt。3项跳过为既有Windows符号链接权限、POSIX0600语义、打开文件替换身份保护，不冒充Linux通过。4项警告为既有ast.NameConstant弃用、两个非有限向量故障注入的NumPy警告、本卡失败事务重复ORM身份SAWarning。728项输入在结束后逐项重算，变化0。最终结构化统计/日志SHA256见2026-10-20-pwfl-002-complete-evidence.json。

前端目录实际执行npm run typecheck、npm run test、npm run lint、npm run build，各exit0；Vitest18文件167测试通过、2.76秒。日志data/pwfl002-complete-frontend-*.txt。源码/依赖未变，使用已有node_modules，未重装依赖；无新增页面交互，浏览器验收不适用。

独立migration_review：迁移/reconcile/review16 passed、7.81秒、exit0；原入口/engine/API/重试44 passed、7.92秒、exit0。两组重叠7项，不能相加成独立场景数。两种真实init_db重复启动探针exit0；首个探针fixture遗漏updated_at导致IntegrityError，修正后重跑，未当成迁移结论。完整命令、日志及指纹见docs/reviews/2026-10-20-pwfl-002-migration-review.md。

## 缺陷轨迹、限制与回滚

历史WIP b7fab9d已有engine超时、API缺失行为、模型缺列、非有限错误响应500的有效红测及修复轨迹；当时全量过程3137 passed/1 failed/3 skipped，不能代表当前版本。旧库缺列现已获授权并修复，历史“未批准/blocked”由本记录覆盖。原inputs/evidence.json保留作历史，不覆盖新complete输入/证据。

合作取消不保证强行中断同步阻塞、吞取消的下游或已发生外部副作用。终态数据库不可写时有安全失败但无恢复队列。没有跨进程取消、并发池、前端按钮、生产迁移或收费模型质量评估。

回滚先排空活动执行、回退代码，可保留新增列；获授权时只降级配置列，不删历史/重放任务。当前业务尚不能done；最终本地全绿后保持in_review，等待最终Ubuntu CI和用户业务接受。未授权推送，不自动触发外部收费评测。

## 最终独立审核结论

未参与实现与既有审查的 `/root/final_audit` 完整核对原卡/批准计划/业务diff/关闭对账/调用方，并独立运行53项真实迁移/HTTP/Engine/Bridge/重试入口，53 passed、1条预期SAWarning、14.35秒、exit0；728输入变化0，14份日志指纹一致，无未修复P1/P2。报告 `docs/reviews/2026-10-20-pwfl-002-human-audit-1.md`。这是本地技术审核，不冒充用户业务接受，不代表最终Ubuntu CI或发布合格。PWFL-002保持in_review，用户已授权本地保存，未授权推送。
