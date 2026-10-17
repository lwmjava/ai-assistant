# PAGT-006 评审修复独立复审

日期：2026-10-13。对象：当前工作区对 `2026-10-13-PAGT-006-code-review.md` 中 F01–F07 的修复，以及本轮新增显式恢复入口与过期清理生命周期。审查者独立只读检查业务代码，没有参与修复；仅在复审结束后新增本报告。

**结论：当前修复中没有发现未解决的 P1/P2 阻断项。** 本轮额外发现两个 P2，通知实施 Agent 修复后，重新执行独占内存反例确认关闭。此结论不替代用户业务验收，任务不得仅凭本报告自动关闭。

## 依据与范围

读取 AGENTS.md、迭代指导与 Harness 治理入口、tasks.yaml 的 PAGT-006 任务契约、适用 Cursor Rules（全局、架构、Agent Runtime、Tools、Testing、安全）、ADR-0010、评审修复计划和原评审报告；使用 `code-review-quality` 技能，按执行层、状态层、服务/API 入口和生命周期分块审查当前 diff。授权范围包含用户明确批准的 resume_message_id、API/main/README/.env.example/OpenAPI 扩展。

重点核对：工具结果/指纹/轮次的 checkpoint 一致性；同次执行总预算；成功、错误与取消出口；恢复读取与快照校验；严格参数类型；HTTP 身份/租户/会话/原消息归属、配额复用、进程内同消息互斥；清理的批量限制、刷新竞争和关闭等待。

## 原发现的修复检查

| 原发现 | 复审事实 |
|---|---|
| F01 重复执行 | 观测、代码结果、指纹与轮次更新完成后再提交 checkpoint，已消除原正常提交快照不一致。工具执行与提交之间断电窗口仍是明确限制。 |
| F02 入口不可达 | 可选 resume_message_id 经过归属、内容和活动快照检查后复用原用户消息；不经过新消息与配额扣减路径。互斥 claim 在异常与生成器关闭的 finally 释放。 |
| F03 恢复刷新预算 | 持久化路径使用真实已用轮次；非法、重复和拒绝调用同样消耗预算；耗尽时无工具收尾。未注入 store 的既有 QualityGate 每循环语义保留。 |
| F04 短路无终态 | 非流式与流式的意图短路、检索终态分支补写 done；取消没有误写 done。 |
| F05 读异常与损坏快照 | 常规读取故障回滚并降级；显式恢复快照不可用时拒绝。类型校验先于内存修改。本轮另补数据库轮次合并后的校验，见下文。 |
| F06 严格类型遗漏 | object/array 类型校验已补；integer/number 排除 bool；范围仍是已承诺的基础类型，而非完整 JSON Schema。 |
| F07 无实际 TTL 清理 | 启动与每 60 秒周期清理接入生命周期，每批至多 100 行；删除再次检查 expires_at，关闭等待在途线程完成。只清理运行快照，不操作历史业务消息。 |

表中结论由代码走查支持，不把所有分支描述为本审查者已独立执行的测试。正式 pytest 与静态检查结果由主 Agent 另行记录。

## 本轮新增发现与实测闭环

### R1 / P2：恢复跳过当前检索，沿用旧 RAG 正文且返回空来源

修复前位置：`app/agents/pipeline.py` 恢复后的完整路径在 `if not resumed` 内调用 `_fill_retrieval`，非流式约第 691–692 行、流式约第 957–958 行；`app/services/chat_service.py` 的 `_reply_sources` 只消费新 retriever 的 `last_selected`。

快照恢复旧 `state.context`，但新请求 retriever 不恢复来源身份；故旧证据继续进入模型，来源列表为空，文档删除/当前版本/有效状态不再通过本次受控检索校验。独占内存反例使用合成快照、脚本管线和新 retriever，输出为：

```text
{'retrieval_revalidated': 0, 'model_context': 'OLD_RAG_DOCUMENT_TEXT', 'returned_sources': []}
```

修复后：`_resume_state` 在应用快照前保存当前请求 Memory；成功恢复后恢复当前 Memory 并清空旧检索状态/披露，完整路径再次执行既有 `_fill_retrieval`，重建当前证据与 `last_selected`；保留计划、工具结果、指纹与预算。不能简单把旧 context 再交给 `_fill_retrieval`，因为该方法会把 context 作为 Memory 合并，仍可能带入旧证据。

重新执行非流式与流式纯内存反例，用当前合成 ChunkResult 走实际上下文组装与 `_reply_sources`，断言旧证据不进入、新 Memory 与当前证据进入、来源 chunk_id 为 fresh。实测输出：

```text
{'stream': False, 'retrieval_calls': 1, 'old_evidence_removed': True, 'sources': 1}
{'stream': True, 'retrieval_calls': 1, 'old_evidence_removed': True, 'sources': 1}
```

这是确定性恢复与来源接线证据，不是检索质量或生产权限变化的真实在线评测。

### R2 / P2：数据库轮次在校验后注入，损坏快照会在显式恢复后降级成新执行

修复前：`AgentStateService._load_active` 先校验 state_json，再注入数据库 `row.round_no` 为 `_round_no`。合法 JSON 加负轮次数据库行因此通过 Service；Pipeline 拒绝该快照后按全新执行运行，显式恢复的安全拒绝边界失效，可能重复工具。

独占 SQLite 内存库实建 Tenant/User/Conversation/Message/AgentRunState，合法 state_json 配 `round_no=-1`，输出：

```text
{'service_accepts_snapshot': True, 'runtime_accepts_snapshot': False, 'round_no': -1}
```

修复后：注入 `_round_no` 及耗尽状态后再统一调用 `StateManager.valid_snapshot`，损坏值返回 None；显式入口按不可恢复拒绝。再次执行同一内存数据反例，断言 load_active 为 None：

```text
{'negative_round_rejected': True}
```

## 实际命令与限制

全部反例使用 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。命令形式为 PowerShell here-string 将一次性 Python 脚本送入标准输入：

```powershell
@'
# 独占内存恢复反例；合成数据，无网络、无业务数据库。
'@ | & 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -
```

以上仅展示实际调用形式，不是完整反例源码；脚本在本次审查工具执行记录中保留，未新增脚本文件。修复前 R1、修复前 R2、修复后两项合并复测共三次实际命令，退出码均为 0；前两次的 0 表示缺陷复现，最后一次的 0 表示修复断言通过。执行出现既有 fitz 弃用警告，没有断言失败。

本审查者未运行共享数据库 pytest，避免与主 Agent 的测试争用。正式专项、回归、全量 pytest、ruff、mypy 与 OpenAPI 结果由主 Agent 另行记录；本报告不沿用历史通过数字作为本轮执行证据。未修改业务代码、测试、任务卡或其他报告。

未验证生产 PostgreSQL、多实例租约、真实付费模型、真实生产文档撤销或工具执行与 checkpoint 之间断电的 exactly-once；没有访问业务数据库、凭据或执行生产清理。进程内互斥不等于跨实例一致性。建议持续补充连续两次中断预算、完整/检索终态错误落库、清理故障回滚与租户过滤反例；这些覆盖建议不构成已复现的阻断缺陷。

本轮最终只读结论为无未解决 P1/P2，仍需主 Agent 完成正式验证与交付对账，并由用户进行业务验收。
