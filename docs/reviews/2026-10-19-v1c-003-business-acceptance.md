# V1C-003 独立业务接受

日期：2026-10-19。独立执行身份：`/root/v1c003_business_acceptance`，未参与本卡实现、此前技术审查或最终技术审核。授权依据：用户明确要求“请启用一个独立agent帮我做业务验收，验收通过后比较代码并推送到github”。本报告只执行代用户业务接受；Git 比较、提交、推送和最终 CI 由主 Agent 负责。

**结论：批准范围内的业务接受通过。** 不是只认可先前报告：我重新读取原卡交付、批准计划和 ADR-0014、README 支持矩阵、Service/运行时实际接线及正式入口测试断言，并自行运行普通/流式业务路径和额外恢复变版反例。未发现本卡剩余 P1/P2。整卡仍须最终候选 CI，不能由本结论推导已可发布。

## 版本与环境

基线及当前未提交 HEAD：`d886560ca211930f7c35d886788496599c6b6d9e`，分支 `codex/v1c-001-lazy-retrieval`。候选为 HEAD 加 `2026-10-19-v1c-003-evidence.json` 的 **521 项 input_sha256**；独立计算前后均 521 项一致，0 项漂移。只新增本报告及两份日志，未改业务、测试、配置、依赖和评测输入。

Windows 11 10.0.22621；解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，Python 3.12.0 conda-forge；pytest 8.4.2、SQLModel 0.0.22、LangGraph 1.0.10。真实 ChatService、SQLModel/独立 SQLite、真实受控执行链及已安装 Supervisor；模型为合成脚本，工具为真实本地 calculator 或隔离探测函数。conftest 清除真实凭据并阻断外网；未访问生产数据、付费模型或远程业务系统。

证据级别 E2：证明授权内容实际消费、路径约束、真实本地执行和持久化；不代表模型回答质量、检索质量、生产性能。93 项中数值校验等 helper 断言只是 E3 补充，必交付结论来自真实 Service 入口及新 Session 读取。

## 业务判断与观察

| 用户可观察要求 | 我选择和检查的实际链路 | 结果及接受理由 |
|---|---|---|
| 支持路径确实使用技能，不能只显示名称 | `test_supported_prompt_paths_consume_actual_version`、`test_supervisor_workers_consume_prompt_but_control_does_not`、私有 worker fence 与 structured reason 测试 | 普通/流式真实模型 payload 含技能正文；research/draft 均收到私有围栏，调度节点不含正文；安全元数据记录准确版本而不泄露正文，新 Session 的助手消息保存技能名称。符合明确支持矩阵。 |
| 绑定工具真的执行且不扩大权限 | `test_bound_calculator_really_executes_once_with_exact_version`、`test_bound_tools_do_not_allow_model_to_use_another_registered_tool`、structured unbound 反例 | 本地 calculator 经执行链成功一次，记录工具、实际路径和有效版本；模型试图调用其他已注册工具时探测函数零调用。绑定不能授予名单外工具。 |
| 未注册/空绑定/不支持不能假成功 | missing binding、unsupported tool mode、early RAG refusal 测试 | 持久化回复有确定性技能提示；Supervisor 与普通/RAG 短路径明确不支持工具模式；低分拒答仍保留提示，没有虚构工具执行成功。符合获批非目标，不将不支持误判缺陷。 |
| 失败和取消不能报执行成功 | `test_tool_terminal_metadata_does_not_claim_execution_success` | 注入真实探测函数失败或取消，安全元数据分别 error/cancelled，未记录 executed，未泄露异常正文；流式取消后保存 stopped。用户不会把匹配名称误认成工具成功。 |
| 撤权立即阻止下一个新动作 | match 后停用/变版、tool one 撤权、stream tool.call yield 后撤权 | 旧正文不进入新模型请求；第一个已完成工具保留，第二个零副作用；即使先收到流式调用事件后才撤权，实际函数仍零调用。离散关口生效，未承诺撤回已完成操作。 |
| 私有资料不跨人/跨租户 | `test_real_entry_cannot_consume_other_members_private_skill` 的 self/fast/Supervisor ×普通/流式×同租户他人/跨租户 | 模型 payload 无私有标记，新 Session 助手消息技能名单为空、正常完成。隔离不是从 SQL 行存在推导。 |
| 恢复不能重放旧副作用或借新技能继续旧计划 | 同版本恢复、旧/畸形绑定、切换编排、绑定篡改及独立额外恢复变版探测 | 同版本只执行第二工具，第一成功前缀、预算、指纹保留；不重新选择技能。旧或篡改绑定明确拒绝，历史状态不覆盖。额外4例版本改变/停用均零新模型/工具调用且旧 checkpoint 原文保留。 |

原始卡 non-goals 保持：不做技能市场、自动淘汰、额外工具授权；Supervisor 工具执行和 RAG/工具组合不在此卡。README 支持矩阵没有把这些路径写成支持。

## 独立实际命令和结果

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_skill_path_contract.py tests/test_skill_path_contract_review.py --basetemp data/pytest-tmp/v1c003-business-acceptance -v --tb=short
```

退出码 **0**，**93 passed / 1 warning / 0 skipped，46.53 秒**。warning 为已有 ast.NameConstant 弃用提示。原始日志：`docs/reviews/2026-10-19-v1c-003-business-acceptance.log`，SHA256 `f50caf6f0944367857a529cfe1d200c4051cc8e12943c83f4066f2c044f649a3`。

另执行 Python stdin 独立恢复探测（退出码 **0**，4/4通过）：先导入 tests.conftest，importlib 加载 `tests/test_skill_path_contract_review.py`；对 `streaming in (False, True)` 与 `change in (version, disable)`，使用全新 `data/pytest-tmp/v1c003-business-resume-probe/<streaming>-<change>`；通过 `entry_db.__wrapped__` 建立真实用户/租户及 SQLite；`private_skill` 加 `manifests_for_chat` 取当前授权 manifest，`seed_bound_checkpoint` 保存已完成第一步骤的 checkpoint。然后独立 Session 提交 Skill.version=2.0 或 enabled=False，再由 `service_for` 构造真实 ChatService，经 `invoke(...conversation_id=cid,resume_message_id=mid)` 普通/流式恢复。独立断言：捕获 ResumeRejectedError；effects 与 model.inputs 都空；新 Session 中 row.state_json 与原始 raw 字节文本相同，status=suspended、round_no=1、stage=action，消息总数仍1。每个 case 完成释放 engine 并恢复 monkeypatch。探测输出日志 `docs/reviews/2026-10-19-v1c-003-business-resume-probe.log`，SHA256 `4714f87c272eb6e41c07809795f28335ecde49d0c405b56b6c20851ce43a661d`。stdin 不形成额外正式测试或变更冻结输入；上述步骤和正式辅助构造器可复现全部断言。

主线程此前全量 2797 passed / 3 skipped、Ruff/mypy 通过属于同一521指纹候选的其他门禁，本人不冒称重新执行全量或静态检查。本轮重新实际执行的是93项与4个独立恢复反例。

## 边界、风险与交接

业务接受通过不等于最终 Git 提交 CI 已通过。需由主 Agent 比较当前完整 diff 与远程基线，核对最终输入后按用户授权提交、推送，并记录同一候选 SHA 的 Ubuntu Backend 等必要 CI 结果；待 CI 前 tasks 保持 in_review。没有合并、部署或生产启用授权。

旧等时间戳加随机 ID 导致重新生成顺序误判的基线缺陷仍存在，已在独立缺陷记录中保留；用户批准仅明确旧回归测试的先问后答夹具，本卡没有修复或隐藏该业务缺陷。它不改变本卡技能接线的业务接受结论，也不能据此宣布整体产品无缺陷。

前端/API未变化，本卡行为由真实后端普通/流式入口可验证，未执行浏览器。真实 LLM 输出质量、真实 Embedding 检索质量、生产权限配置/性能、持续授权轮询及撤回已启动工具不在此次可自动复现业务接受证明范围。

本次实际读取并使用技能：`docs/ai-prompts/skills/task-card-delivery/SKILL.md`。我只承担全新独立业务接受，不兼任实现者或此前审核者；不启动下一任务。
