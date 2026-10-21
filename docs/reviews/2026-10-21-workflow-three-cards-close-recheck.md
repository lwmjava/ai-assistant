# 三张工作流任务卡关闭前独立复核

日期：2026-10-21。复核角色：`/root/workflow_close_recheck`，未参与本次修复实现；本轮仅新增此报告，未修改业务、测试或任务状态，未提交或推送。

## 结论与版本

当前候选提交 `11f36aa76e6eebaa6efe0313dfc86fc9d29fc1eb`，分支 `codex/flow-003-scheduled-execution`。对 PWFL-003、FLOW-005、PWFL-004 未发现新增关闭阻断项或未解决的 P1/P2。用户最新明确要求关闭三卡，补齐人工业务验收；最终同 SHA Ubuntu CI 成功，补齐原报告中待 CI 的门禁。主代理可据此更新三卡为 done。

本轮核对 task-card-delivery 技能、项目规则、任务契约、原 PWFL-003 最终审核及本轮告警修复独立/最终审核。关闭基线采用最终源码清单而非原 PWFL-003 的旧清单：原 733 项随后因批准的告警实现产生预期漂移，不能继续声称原 733 项无变化。

独立运行 SHA256 断言，`2026-10-21-workflow-alert-fixes-inputs.json` 的 **737 项输入全部匹配**，`2026-10-21-workflow-alert-fixes-evidence.json` 的 **23 项日志/截图全部匹配**，退出码 0。后续仅追加关闭说明与 tasks.yaml 状态不改变这些业务检查输入；如再修改源码、测试、配置或依赖，相关证据应重新绑定。

## 契约与前置

| 卡 | 复核范围 | 前置状态 |
| --- | --- | --- |
| PWFL-003 | 同进程并发容量控制、同一 cron 到期意图去重、满载拒绝、租约与取消清理 | PWFL-001 done |
| FLOW-005 | owner 停用/迁移后挂起，一次告警，当前租户管理员可查询，跨租户拒绝 | FLOW-002 done |
| PWFL-004 | 最终失败一次告警，按 trace 关联的持久化运行摘要，重试/超时/取消边界 | PWFL-001、NFR-002 done |

关闭前机器读取三卡均为 in_review；上述前置均为 done。当前专项包括 retry、schedule、timeout/cancel、权限/租户、并发和失败隐私回归，不仅依据前置状态字段。

原 P1（异常原文进入新增告警/日志）已用内部错误码与受控摘要修复，包含凭据、私有输入、伪造取消/超时及成功摘要回归。原 P2（owner 告警 API 的 README/Swagger 缺口）已补齐。参见 `2026-10-21-workflow-alert-fixes-independent.md` 与 `2026-10-21-workflow-alert-fixes-human-audit.md`；本轮未把其当时“待 CI”追溯改写为已通过。

## 本轮独立运行

Windows 11 build 22621；解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0、pytest 8.4.2、SQLModel 0.0.22、SQLAlchemy 2.0.36、Pydantic 2.10.4、httpx 0.28.1、Alembic 1.14.1、langgraph 1.0.10。与最终证据环境一致；本地 pytest 与 CI 固定的 8.3.4 存在版本差异，平台门禁由同 SHA CI 补足。

实际 PowerShell 命令：

```powershell
$env:DATABASE_URL='sqlite:///./data/workflow-three-close-independent-20261021.db'
$env:PYTHONUTF8='1'
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest -k workflow -q --basetemp=data/pytest-tmp/workflow-three-close-independent-20261021 *> data/workflow-three-close-independent-20261021.txt
$rc=$LASTEXITCODE
exit $rc
```

退出码 **0**：**163 passed、3053 deselected、2 warnings，48.49 秒**。日志为 `data/workflow-three-close-independent-20261021.txt`。独立 SQLite 和 basetemp 隔离，无生产库操作、外发通知或付费模型调用。两项警告为既有 ast.NameConstant 弃用和故意触发事务回滚测试的 SQLAlchemy identity 冲突；均非失败。

本轮未重跑全量；复用哈希仍一致的最终全量日志 `2026-10-21-workflow-alert-fixes-full-final.txt`：**3213 passed、3 skipped、4 warnings，724.71 秒，退出码 0**。其前曾中断的运行不作为通过证据。最终证据另含 Ruff 全仓、mypy app/ 222 文件，以及前端 typecheck、167 项 Vitest、ESLint、build 均退出码 0。Windows 的三个平台跳过不声称本地通过。

## 同提交远程 CI

读取主代理通过 GitHub API 独立取得并保存的 `2026-10-21-workflow-three-cards-ci.json`，核对 run 与各 job 的 head SHA，均等于上述候选 SHA；不是仅凭用户口述判定。运行 [39544372233](https://github.com/lwmjava/ai-assistant/actions/runs/39544372233) 为 completed/success，三个 ubuntu-latest job 均 completed/success：

- [Frontend](https://github.com/lwmjava/ai-assistant/actions/runs/39544372233/job/119039092827)：类型、Vitest、ESLint、构建。
- [Agent official baseline](https://github.com/lwmjava/ai-assistant/actions/runs/39544372233/job/119039093014)。
- [Backend](https://github.com/lwmjava/ai-assistant/actions/runs/39544372233/job/119039093024)：Ruff、mypy、pytest 与 RAG 门禁步骤成功。

这补齐 Linux/同版本 CI 门禁；未核验远程分支保护或 bypass 策略，不声称自动强制保护已安装，也不表示已合并或部署。

## 明确保留的边界

并发/去重仅限批准的单进程范围，cron 仍串行，无跨节点锁或重启后的 exactly-once 保证。终态数据库写入失败仍可能留下 RUNNING，未新增自动补偿。新增告警和运行摘要脱敏不等于清理历史 `WorkflowExecution.error`；既有该字段和历史数据未改写。持久化日志为完成时保存的起止摘要，不是实时逐 attempt 全量 Trace。无外部邮件/Webhook 或告警规则 UI。

复用的真实前后端 UI 证据验证租户 A/B 管理员隔离与刷新持久化；未声称全面跨浏览器或浏览器控制台审计。以上均为已批准非目标或已披露残余风险，不将本次关闭解释为整个平台生产就绪。
