# 工作流告警验收缺陷修复交付对账

日期2026-10-21。基线3acd233d370ad50339c0cce616bf0243fd25c58d；候选HEAD加[737项冻结输入](2026-10-21-workflow-alert-fixes-inputs.json)。用户明确要求修复并批准README最小扩围；不降低原验收、不推送。任务维持in_review，最终Ubuntu CI与用户业务接受未验证。

| 缺陷/原始要求 | 真实操作与结果 | 证据 | 结论 |
|---|---|---|---|
| PWFL-004异常原文泄露 | 相同原验收脚本经Engine/newSession复跑，告警与日志合成私密标记各0 | [原反例复验](2026-10-21-workflow-alert-fixes-probe.json)，E2 | 已修复 |
| 同一失败一条告警、同trace_id日志 | 安全测试与独立payload中2次attempt仍单告警/两条log，trace一致 | privacy正式测试、[独立审查](2026-10-21-workflow-alert-fixes-independent.md)，E2 | 通过 |
| 超时/取消/重试/租户隔离 | 最终回归49项+额外五探针通过 | 独立审查，E2 | 通过相应隔离入口 |
| FLOW-005 API文档缺失 | runtime与Swagger operation/schema一致，README路径存在 | 新契约测试及原诊断脚本，确定性契约 | 已修复 |
| FLOW-005页面未验收 | 真实登录A→仅A告警→刷新仍在→退出→登录B仅B | [A截图](2026-10-21-workflow-alert-ui-tenant-a.png)、[B截图](2026-10-21-workflow-alert-ui-tenant-b.png)、[B完整AX](2026-10-21-workflow-alert-ui-tenant-b-ax.txt)，E2 | 本轮基本页面验收通过 |
| 完整执行轨迹表述 | 模型与说明改为终态后开始/结束两条摘要 | audit/models及实现说明diff | 已纠正漂移，不声称逐次attempt日志 |
| 本地必要门禁 | 737输入冻结，完整pytest3213pass/3skip/4warnings/exit0；Ruff/mypy/前端四门exit0 | data/workflow-alert-fixes-* | 本地必要门禁通过 |
| 最终同SHA Ubuntu CI与业务接受 | 未推送，无法取得最终提交对应新CI | 未验证 | 保持in_review，不done |

## 实际检查

指定解释器D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，Windows/Python3.12.0。PYTHONUTF8=1；测试隔离数据库，不读取本机.env或使用外部模型密钥。

- DATABASE_URL=sqlite:///./data/alert-review-fixes-red-20261021.db；`python -m pytest tests/test_workflow_alert_privacy.py -q --basetemp=data/pytest-tmp/alert-review-fixes-red-20261021`：7 failed，2.95s；失败对应真实错误复制与缺失路由，非导入错误。
- DATABASE_URL=sqlite:///./data/alert-review-export-20261021.db；`python -m pytest tests/test_workflow_alert_privacy.py tests/test_workflow_execution_alert.py tests/test_workflow_owner_alert.py -q --basetemp=data/pytest-tmp/alert-review-fixes-green-20261021`：18 passed，2.90s，exit0；最终源码由全量及独立审查再次覆盖。
- `python -m ruff check . --no-cache`：exit0；`python -m mypy app/`：exit0。
- frontend：`npm run typecheck`、`npm run test`、`npm run lint`、`npm run build`分别exit0；Vitest18files/167tests。沿用现有node_modules，依赖未变，未重跑npm ci。
- DATABASE_URL=sqlite:///./data/workflow-alert-fixes-full-20261021.db；`python -m pytest --basetemp=data/pytest-tmp/workflow-alert-fixes-full-20261021`：运行中，不将专项代作全量。
- DATABASE_URL=sqlite:///./data/workflow-alert-fixes-original-probe-bootstrap.db；`python docs/reviews/2026-10-21-flow005-pwfl004-user-acceptance-probe.py.txt`：执行exit0且privacy.pass=true；脚本JSON的candidate_sha为原验收基线硬编码，实际修复候选绑定本轮737输入，不把旧SHA冒称当前源码。

独立/migration_review负责安全入口反例，/final_audit负责未参与本轮实现/既有审查的[最终技术审核](2026-10-21-workflow-alert-fixes-human-audit.md)已通过。实际沿用task-card-delivery修复回路及code-review-quality的安全/逻辑/测试维度。业务实现主Agent为/root。没有声称调用未运行的外部评审流程。

原始WorkflowExecution.error与业务名称快照、历史表数据不在本次新表异常扩散修复范围，保留残余边界；无需迁移，不擅自删除历史。任务状态与Git保存不代表最终CI、发布或业务接受。本轮回滚为排空后撤回代码文档，未推送/合并/部署。


## 首轮全量中断记录

首轮data/workflow-alert-fixes-full-20261021.txt只输出到49%，没有最终汇总或退出码文件。工具session85335后续返回Unknown process id，Get-Process未发现存活python。具体终止原因无充分证据，不归因于RAG代码或断言失败；也不能记通过。相关源码/737输入未改，已重新执行：DATABASE_URL=sqlite:///./data/workflow-alert-fixes-full-retry-20261021.db；`python -m pytest --basetemp=data/pytest-tmp/workflow-alert-fixes-full-retry-20261021 -o faulthandler_timeout=120`，日志data/workflow-alert-fixes-full-retry-20261021.txt，退出码写data/workflow-alert-fixes-full-retry-exit-20261021.txt。不筛选、不跳过，120秒堆栈诊断仅增加可观测性，不更改测试断言或门禁。


## 终态摘要分类复核补记

主Agent最终diff复核发现本轮新摘要对success也调用失败分类，会产生status=success reason=bridge_error的错误文案。先增加独立成功日志反例，再最小修正：只有failed生成failure reason，success使用none。对应源码/测试输入变化使正在运行的全量重跑无效，停止该轮、重新冻结后运行最终全量；不能沿用修改前绿证据。独立审查须复核此补丁。


成功摘要修正后：相关19项通过（3.06s），最终审核Agent独立37项通过（6.66s，exit0），737最终输入变化0。最终有效全量命令改为DATABASE_URL=sqlite:///./data/workflow-alert-fixes-final-20261021.db；`python -m pytest --basetemp=data/pytest-tmp/workflow-alert-fixes-final-20261021 -o faulthandler_timeout=120`，日志data/workflow-alert-fixes-final-20261021.txt。旧full-retry已主动停止且输入过期，不作为通过证据；前端/API输入未再变化，前端四门可沿用原结果。


## 最终有效全量与关闭对账

最终冻结候选的完整pytest为3213 passed、3 skipped、4 warnings、724.71秒（12分04秒）、退出0，3216项全部收集；完整日志[full-final](2026-10-21-workflow-alert-fixes-full-final.txt)。3项skip为既有Windows符号链接、POSIX0600和打开文件替换语义，不记通过，需Ubuntu覆盖；4warning为既有AST弃用、RAG数值边界invalid divide/overflow与工作流故障注入SAWarning，未屏蔽。没有新增skip或禁用相关功能。

Ruff全仓exit0；mypy app/222files exit0；前端typecheck、18files/167tests Vitest、lint、build分别exit0。独立入口最终49项+5探针通过；fresh最终审核独立37项通过。737项SHA256逐项复算变化0，验证后仅更新验收文档。详细版本、非敏感配置、完整命令及原始/归档日志SHA256见[evidence.json](2026-10-21-workflow-alert-fixes-evidence.json)。前端依赖与源码未变化，沿用本轮实际四门，不重复npm ci。

两项原P1/P2以及成功摘要误报均已修复并有正式反例；页面基本E2E已补齐。最终同提交Ubuntu Backend/Frontend/Agent official CI及用户业务接受仍未验证，本报告不关闭任务、不得宣称可发布；不独立声称远程分支保护已强制生效。任务维持in_review。


最终独立技术审核已通过：/root/final_audit独立37项通过，737输入0变化，23份日志/截图指纹全部匹配；无未修复P1/P2。用户业务接受与最终同提交Ubuntu CI仍未验证，in_review不变。暂存范围、YAML、引用和git diff --cached --check已核对，只有批准范围及本轮验收证据；三个无关未跟踪文件未加入提交。
