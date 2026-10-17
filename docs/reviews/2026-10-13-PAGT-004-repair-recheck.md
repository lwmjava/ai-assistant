# PAGT-004 审查修复独立复验

日期：2026-10-13。身份：`/root/pagt004_repair_recheck`，未参与业务代码或正式测试实现，接替因 usage limit 未能完成复验的原审查 Agent。读取原始任务卡、AGENTS.md、task-card-delivery v1.3.0、批准 ADR-0011、原反例报告及修复计划。只修改本报告，不兼任最终人工审核。

最终结论：**CE-01～07 已修复并通过本角色独立复验；本轮检查无未决 P1/P2**。这是工程审查结论，不替代独立人工审核 Agent 或用户业务验收；不据此自动关闭任务。下文保留 CE-07 发现时事实与后续修复证据。

## 实际命令与证据级别

解释器均为 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。独立运行前取得主 Agent 串行 pytest 时隙，完成后释放。

```powershell
$env:RAG_BACKEND='native'
$env:DATABASE_URL='sqlite:///./data/test_ai_assistant.db'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest -q tests/test_structured_plan_runtime.py tests/test_plan_checkpoint_consistency.py tests/test_plan_entry_acceptance.py tests/test_plan_tool_outcomes.py -p no:cacheprovider --basetemp=data/pytest-tmp-pagt004-independent-recheck
```

实际结果：退出 0，71 passed，1 warning，3.90 秒；警告为 sandbox 的 ast.NameConstant 弃用。这里包含真实 Pipeline/Executor、隔离 SQLite 新 Session、HTTP/ChatService、真实工具函数的 E2 入口证据，以及快照一致性 E3；脚本 provider 不证明真实 LLM 质量。

另外两组独立 PowerShell here-string 经 stdin 运行纯内存脚本，设置 `DATABASE_URL=sqlite://`、`RAG_BACKEND=native`，均退出 0。使用真实 Pipeline、StateManager、Executor、深拷贝 MemoryStore，原子合成 echo 只记录实际调用；审计替换为 QuietRegistry 内存边界，因此内存结果不证明 DB/HTTP 租户绑定。文件反例调用真实 file_ops_tool，根限定 TemporaryDirectory，不读取真实文件、不写业务数据、不访问外网或付费模型。

## 已知反例复验

| 原反例 | 本次实际结果 | 判定 |
|---|---|---|
| CE-01 新计划 password 参数拒绝后落库 | 正式 Pipeline + SQLite 新 Session 测试通过，失败终态无合成值 | 已知反例修复；不覆盖 CE-07 |
| CE-02 恢复 wrong integer | 内存恢复 error=INVALID_ARGUMENTS，调用=[]，终态无合成值 | 通过 |
| CE-02 恢复 sensitive arguments | error=SENSITIVE_PLAN_ARGUMENT，调用=[]，终态无合成值 | 通过 |
| CE-02 恢复未知第二步工具 | error=TOOL_UNAVAILABLE，调用=[]，先整计划验证 | 通过 |
| CE-03 preflight 睡眠 .3 秒 / timeout .01 | 同步/流式均由 runtime 得到 TimeoutError，调用=[]；未用外部 watchdog | 通过 |
| CE-03 quality 睡眠 .3 秒 / timeout .01 | 同步/流式均 TimeoutError，工具恰执行 [1,2] | 通过 |
| CE-04 draft=None | 安全错误为步骤 draft 失败(INVALID_STEP_OUTPUT)，cursor=2 | 通过 |
| CE-05 succeeded/cursor=1、轮次=0、无指纹 | StateManager.valid_snapshot=false，正式 DB 元数据/原子拒绝测试通过 | 通过 |
| CE-06 真实 file_ops 读取临时公开根不存在路径 | 首步 failed，后继 pending，cursor=0，后继调用=[]，安全 TOOL_FAILED | 通过 |

## CE-07：恢复的文本视图绕过完整验证

严重性 P1，置信度 10。构造完整合法 pending v1 快照，仅把 `snapshot['plan']` 改为含 password 合成 marker 的 JSON，structured_plan 保持安全合法。`StateManager.valid_snapshot` 接受快照；真实 Pipeline.run 恢复后实际调用 [1,2]、error=null、cursor=2，三个新 checkpoint 的 state_json 均保留合成值。`_validate_executable_plan` 只检查 structured_plan，文本视图继续进入 `_build_act`/QualityGate/终态快照。

核心复现输入（合成值，不是真实秘密）：

```python
state = AgentState(user_input='multi step', plan=plan_text())
pipeline._prepare_structured_plan(state)
snapshot = copy.deepcopy(StateManager(state).snapshot())
snapshot['plan'] = json.dumps({'password': synthetic_marker})
assert StateManager.valid_snapshot(snapshot)
# MemoryStore.load_active 返回上述深拷贝，真实 Pipeline.run 执行恢复。
```

实际打印摘要：`mismatch_accepted_before_run=True; calls=[1,2]; error=None; cursor=2; sensitive_in_checkpoint=True; checkpoint_count=3`。不输出 marker 正文；脚本退出 0 只是完成测量，该业务反例失败。

修复要求：在 v1 恢复之后、任何 RAG/行动/模型消费之前，对完整权威结构计划进行注册/参数/敏感验证，并从已验证结构重新生成唯一安全文本视图，或拒绝不一致文本。验证异常不得被恢复 catch 处理成全新执行。保留旧文本 checkpoint 兼容。增加真实 SQLite + 新 Session 同步/流式回归，断言不泄漏至 checkpoint 或模型消息，非法权威结构仍零工具调用。主 Agent 已收到即时通知，待正式红测试、修复及本角色独立复验。

## 指纹与限制

首轮复验核心 SHA256：

| 文件 | SHA256 |
|---|---|
| app/agents/pipeline.py | 4C3F5883DA8F14612AD8F4B154B4F6108F2102A1BE04853B3E2100C884FF6E6A |
| app/agents/state.py | 57C78269832FBC02CDADF4968ABB61C74D199EED09432582F9C4F4E678A5737F |
| app/agents/plan_runtime.py | 888EE8EAFF4821BA2DFEE8BE6C8C30A6B0D4DE91306A936DB31608E96BDE15FA |
| app/agents/tools/file_ops.py | C6C2E1A4FA802A1B94F515317B9115360B3DA3900F47988676CCF5470C3A3C0D |
| tests/test_plan_checkpoint_consistency.py | DE2DDFE05C32306F6CFB0624C4D6FCDBCF6DAA727527D5650EAB53D2EC2E54EA |

首次文件指纹命令误猜 file_ops_write.py 不存在；该写工具实际位于 file_ops.py，不将不存在文件计为审查证据。未运行全量 pytest、生产/付费 LLM、多实例、PostgreSQL、真实生成质量或重要数据恢复。本报告不是最终人工业务验收，不授权 done、推送、部署。既有 checkpoint 写失败 fail-open/断电窗口不能声称 exactly-once。

## CE-07 修复后独立复验与最终指纹

主 Agent 落盘 CE-07 修复计划、执行真实 SQLite + 新 Session 红测试后，在 `_resume_state` 成功应用 v1 快照后、RAG/行动等消费之前完整校验权威结构，并由已验证结构生成唯一文本视图。PlanExecutionError 显式重新抛出，不能被 broad catch 降级为全新执行。旧文本快照未被套用 v1 canonical 规则。

本角色重新执行上述四文件相同 pytest 命令，仅 basetemp 改为 `data/pytest-tmp-pagt004-independent-recheck-final`：退出 0，72 passed，1 warning，4.37 秒。随后新正式 CE-07 测试已参数化同步/流式，独立追加：

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest -q tests/test_structured_plan_runtime.py -k canonical_text -p no:cacheprovider --basetemp=data/pytest-tmp-pagt004-independent-canonical
```

退出 0，2 passed / 27 deselected，1 warning，1.82 秒。测试真实 SQLite 旧活动快照、恢复、模型消息采集及新 Session 终态读回，不以仅内存 marker 检查替代落库证据。

另一次独立 stdin 内存脚本实际走同步/流式 Pipeline 两个入口，ProbeLLM 采集所有模型消息，MemoryStore 深拷贝保存所有新快照。两个入口都实际调用 [1,2]、error=null，并满足 `checkpoint_leak=false`、`model_message_leak=false`、`json.loads(state.plan)==state.structured_plan`。该脚本同时再次验证 CE-02 类型错误、敏感参数、未知后继，三者调用均=[]、明确安全错误、终态无合成值；不是静态阅读推断。脚本退出 0。

最终复验 SHA256：

| 文件 | SHA256 |
|---|---|
| app/agents/pipeline.py | B0FC989E742526D23F5089192581D45D1DD8116F50D5F8A230CEFB8D84626E95 |
| app/agents/state.py | 57C78269832FBC02CDADF4968ABB61C74D199EED09432582F9C4F4E678A5737F |
| app/agents/plan_runtime.py | 888EE8EAFF4821BA2DFEE8BE6C8C30A6B0D4DE91306A936DB31608E96BDE15FA |
| tests/test_structured_plan_runtime.py | DAB9BC24796C4127EE11DA9BEED8CD62E02950C2F5D585A039E3EFD38FAD44B3 |

CE-07 当前按实际入口证据关闭。CE-01～06 已知反例与原有兼容验证保持通过；不声称穷尽所有未见反例。主 Agent 仍需完成最终全量回归、关闭对账与独立人工审核，用户未委托业务判定时任务保留 in_review。
