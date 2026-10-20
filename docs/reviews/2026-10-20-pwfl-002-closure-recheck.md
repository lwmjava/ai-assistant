# PWFL-002 关闭前当前版本复核

日期：2026-10-20（Asia/Shanghai）。独立复核身份：`/root/pwfl002_close`。本 Agent 未参与本卡实现或既有审查；本轮只新增此报告，未改业务、测试、任务卡，未提交或操作真实库。

结论：当前 HEAD `8c56a23a97e80b4f24862642107af02d8df9df8d` 未发现新的技术关闭阻断，未发现未修复 P1/P2。本轮用户明确说明“PWFL-002 可以关闭 github CI成功”，作为用户对 CI 成功及业务关闭接受的直接证据登记。未独立取得 GitHub run/job URL、Linux 日志或核验远程分支保护；不得写成独立查验的 CI 证据，也不代表发布、合并或部署授权。

## 复核依据

已读取 AGENTS.md、task-card-delivery 1.3.0、推进指导、Harness 治理、适用规则00/01/07/08/09/10/11、tasks.yaml 原始交付/验收/非目标，最终实现说明、acceptance、complete-inputs/complete-evidence、工程/迁移审查及 final_audit 人工审核。原始交付为可配置超时及手动取消；非目标为跨进程强杀和改变执行历史结构。最终独立审核已由先前 `/root/final_audit` 出具，本报告是依赖准入及关闭当前版本复核，不冒充首次最终审核。

逐项使用指定 conda Python 对 complete-inputs 的 `files` 计算 SHA256：728 项，变化0；对 complete-evidence 的日志 SHA256 重算：14 项，变化0，退出码0。历史全量3154 passed/3 skipped及Ruff/mypy、前端四门证据因此与当前检查输入一致；本轮没有重新执行全量、静态检查或前端四门，不把历史执行称为本轮执行。3个Windows平台skip仍如原报告保留；Linux CI成功来源为本轮用户确认。

## 独立实际复验

采用独立SQLite及basetemp，未共享其他 Agent 的测试库；conftest隔离环境与模型密钥，无收费模型或生产访问。

```powershell
$env:DATABASE_URL='sqlite:///./data/pwfl002-closure-check.db'
$env:PYTHONUTF8='1'
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest tests/test_workflow_timeout_migration.py tests/test_migration_reconcile.py tests/test_workflow_timeout_review.py tests/test_workflow_timeout_cancel.py tests/test_workflow_cancel_api.py tests/test_workflow_retry.py -q --basetemp=data/pytest-tmp/pwfl002-closure-check
```

实际退出码0：53 passed、1 warning、13.30秒，没有跳过。警告为预期失败事务注入的重复ORM身份 SAWarning。原始输出保留在本轮工具会话1524。

| 契约/边界 | 当前真实路径 | 结果 |
|---|---|---|
| 配置及数值范围 | HTTP创建/更新后新Session读取；严格整数1–3600、默认300；NaN/Infinity等拒绝 | E2通过 |
| 超时与释放 | Engine总预算包含重试退避、finally收尾、终态持久化 | E2通过 |
| 手动取消 | HTTP run/cancel及历史回读、重复取消、父取消、cron调用者继续 | E2通过 |
| 身份租户/绑定 | 跨租户/只读/错绑定、owner撤销及双Engine身份 | E2通过 |
| 失败与幂等 | rollback、终态DB失败安全响应、活动删除及取消清理 | E2通过相应失败反馈 |
| 旧库与历史兼容 | 直接升级降级再升级、auto_migrate旧修订/混合库/head漂移 | E2通过 |
| 前置重试 | 原test_workflow_retry.py成功/失败/重试上限 | E2通过 |

E2指真实代码及隔离SQLite、合成合作长任务/故障注入，不证明真实模型质量、性能或生产数据迁移。无新增页面，浏览器验收不适用。合作取消、同步阻塞/吞取消下游、已发生副作用不可撤回及终态DB失败无恢复队列的限制与原始批准契约、实现说明一致。

工程/迁移报告与最终审核均无未决P1/P2；历史非有限JSON错误和旧库缺列已有正式反例并在当前53项组合复验通过。本轮未改变冻结输入，原证据未失效。主 Agent 可依据用户直接关闭接受登记更新任务状态；若还需独立远程审计，应明确区分用户确认与实际run URL核验。回滚仍按原说明先排空活动执行、回退代码，真实数据库降级须单独授权。
