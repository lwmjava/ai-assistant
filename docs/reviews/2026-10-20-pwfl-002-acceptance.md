# 工作流超时与取消验收对账（阻断，未关闭）

日期：2026-10-20。主Agent整合，不冒充独立人工审核。原始契约为tasks.yaml PWFL-002；已批准方案及ADR-0017有效。本地提交授权有效，无推送、合并、部署、真实数据库迁移或收费模型授权。

## 关闭对账

| 原始要求/约束 | 实际证据与级别 | 结果与缺口 |
|---|---|---|
| 任务可配置超时 | E2：真实HTTP创建/更新、严格1–3600整数、默认300、新Session回读；非有限raw JSON6项422且无写入 | 新建/更新配置通过；旧库自动升级失败，整项不能全通过 |
| 超时中止、标记、释放占用 | E2：真实engine总时限、退避超时、资源finally、超时清理期父重复取消；真实WorkflowBridge/ChatService传播取消 | 合作式路径通过，不声称强杀或清理硬时限 |
| 手动取消接口 | E2：HTTP运行并发取消、同租户写权限/系统管理员、跨租户/错绑/匿名/只读拒绝、终态幂等及新Session回读 | 通过，无UI按钮交付 |
| 失败/幂等/身份绑定 | E2：退避撤权、cron取消保留调用者、双独立内存数据库同URL/同ID不交叉取消、事务rollback、实际终态commit失败503 | 通过相应失败反馈；DB持久化不可用会留下running历史，无自动补偿承诺 |
| 不改执行历史数据结构 | E2：迁移旧行升级/降级及历史表schema对比；仅Workflow新增列 | 直接Alembic通过，启动auto_migrate缺列失败 |
| 独立入口审查 | 独立Agent `/root/execution_review` 新增7反例，最终44 passed/exit0，含原重试与6非有限输入；最终指纹见工程审查 | 已有已知迁移阻断，不能据此done |
| 必要回归与最终版本 | 最终专项102 passed/1 failed/exit1；Ruff/mypy通过；前端四门通过。输入清单另存 | 全量过程版本不能替代最终版本全量；Ubuntu CI未验证 |
| 最终独立人工审核、用户业务接受 | 未执行最终审核：自动验收门禁尚未通过；用户未委托本卡业务接受 | 未验证，禁止关闭 |

E2均为真实代码与隔离SQLite/合成合作式provider/bridge，不证明真实模型质量或生产可用性。无E1外部收费模型/真实库证据。历史证据及Mock不冒充最终验收。

## 最终适用检查

平台Windows11 build22621，指定conda `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，Python3.12.0，pytest8.4.2、Ruff0.8.4、mypy1.14.1、SQLModel0.0.22、Pydantic2.10.4、httpx0.28.1、Alembic1.14.1；langgraph1.0.10已安装，未禁用CI相关extra。Node22.20.0/npm10.9.3。依赖版本与CI锁定版本可能不同（pytest本地8.4.2，项目dev声明8.3.4），最终Ubuntu CI仍需核验。

`PYTHONUTF8=1`，专项 `DATABASE_URL=sqlite:///./data/pwfl002-related-final.db`。完整专项命令：

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_workflow.py tests/test_workflow_retry.py tests/test_workflow_schedule_contract.py tests/test_workflow_schedule_integration.py tests/test_workflow_timeout_cancel.py tests/test_workflow_cancel_api.py tests/test_workflow_timeout_migration.py tests/test_workflow_timeout_review.py tests/test_openapi_coverage.py -q --basetemp=data/pytest-tmp/pwfl002-related-final
```

结果：102 passed、1 failed、2 warnings、35.51秒、exit1。唯一失败 `test_startup_migration_adds_timeout_to_previous_schema`：旧修订a4b7c2d9e160被auto_migrate直接stamp为b5c8d3e0f271，timeout_seconds仍缺失。未跳过失败。警告为既有ast.NameConstant弃用和故意注入重复主键失败事务的SAWarning。日志 `data/pwfl002-related-final.txt`。

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m ruff check . --no-cache
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m mypy app/
```

各exit0，mypy220文件。日志data/pwfl002-ruff-final.txt、data/pwfl002-mypy-final.txt。

中间版本还实际执行了无筛选 `python.exe -m pytest --basetemp=data/pytest-tmp/pwfl002-full-01`，DATABASE_URL=sqlite:///./data/pwfl002-full-01.db：3137 passed、1 failed、3 skipped、4 warnings、679.11秒、exit1。唯一失败仍为上述启动迁移。3项跳过沿既有Windows符号链接/POSIX权限/打开文件替换限制，未声称通过；警告含既有弃用、两个向量非有限故障注入及本卡重复主键反例。执行期间后续修复了Engine绑定与非有限字段校验并追加反例，因此这是过程版本，不能冒充最终候选全量通过。修复范围未批准，最终全量尚待阻断解除后运行。曾试图定位该旧进程以停止过期检查，Get-CimInstance权限拒绝，未实际停止；该命令失败不计入业务断言。pytest最终自行正常汇总并exit1，未留下运行任务。日志data/pwfl002-full-01.txt。

前端目录分别实际执行 `npm run typecheck`、`npm run test`、`npm run lint`、`npm run build`，各exit0；Vitest18文件167测试通过。日志 `data/pwfl002-frontend-final-*.txt`。前端源码与依赖未改，使用已有安装；未运行npm ci。仅API交付，不伪造浏览器页面验收。

## 缺陷修复与过程失败

有效红测为engine1秒仍运行、API13项缺失行为、模型/迁移2项缺列，以及非有限JSON创建/更新6项真实HTTP500。非有限输入已在局部字段before校验归一化为安全字符串，再由strict int拒绝，6项422且DB不变；未改全局handler。数据库绑定从URL改为实际Engine实例，双内存库隔离反例已通过。专项Ruff发现局部import顺序，已修；Ruff缓存权限错误后按既有规范使用--no-cache成功。初次API更新默认None的mypy赋值错误已显式表达省略sentinel，最终类型检查通过。

旧库自动升级缺陷仍待 `app/core/migration.py` 最小扩围授权；该文件未改，失败保留。不得把直接Alembic通过当成启动升级通过。没有最终独立人工审核结论或业务关闭接受。

## 输入与回滚

当前候选以HEAD8cc3b06加工作区记录；源码/测试/配置/依赖/评测输入SHA256见 `2026-10-20-pwfl-002-inputs.json`。报告和任务状态文档不改变检查输入；相关输入再改使对应验证失效。独立工程审查另有关键8文件指纹。

先排空活动执行，再回退本卡代码。新配置列可保留；如撤迁移只撤超时配置，不删历史。真实重要数据迁移未获授权。任务保持blocked，可按已授权提交为WIP；不得发布或声称可完成关闭。

最终格式复核：移除了独立反例测试末尾多余空行，业务代码未变。为绑定最终测试输入，专项按同一命令改用 `DATABASE_URL=sqlite:///./data/pwfl002-related-final2.db` 与 `--basetemp=data/pytest-tmp/pwfl002-related-final2` 重跑：102 passed、1 failed、2 warnings、27.42秒、exit1；仍唯一旧库启动迁移失败。日志data/pwfl002-related-final2.txt。Ruff再次exit0，mypy/前端检查输入未变沿用。输入清单已更新该测试SHA256；独立审查正文指纹反映修剪前版本，仅末尾空行变化，无业务差异。
