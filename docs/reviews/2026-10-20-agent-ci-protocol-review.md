# Agent 工具协议兼容独立审查（第一轮）

日期：2026-10-20。审查身份：独立子 Agent `/root/protocol_review`；未参与业务实现。依据：用户批准的 `docs/plans/plan_agent_ci_protocol_20261020.md`、AGENTS.md、task-card-delivery 与 code-review-quality 技能。审查不凑发现数量。

## 结论

**暂不通过。存在未修复 P1：官方评测入口在共享解析成功后仍硬拆标准信封，合法 DSML 导致 IndexError。** 主 Agent 已确认该点，并停止涉及未授权 scripts 的实施。需批准调整计划/范围后修复，再复核。本文不构成业务验收、任务关闭或真实模型恢复证明。

## 确认发现

- P1：`scripts/run_agent_chain_eval.py:282` 的 `_validate_call` 调用共享 `inspect_tool_call` 后仍执行 `raw.split("<tool_call>", 1)[1]`。新增共享解析使合法 DSML 为 ok，但该拆分没有匹配项，`IndexError` 不在捕获范围。`record_tool_call` 和完整 official run 均可复现；会使本次目标 CI 仍失败。建议保留标准 JSON 的重复键/非有限值复核，仅对已由严格共享解析器校验的 DSML 跳过标准信封拆分，保留 schema、注册工具、代码合法性、记录回调和原门槛。
- 测试断言：多调用的既有 evaluator 错误码是 `invalid_envelope`，新增测试期望 `invalid_tool_call` 导致另一失败。这是测试适配错误，不应为迎合断言改变原错误语义。

## 调用方与安全边界

共享解析器用于默认行动循环、结构化计划的未规划调用拒绝、official evaluator/gate/probe。解析仅形成 ToolCall，不直接执行工具。现有 `_reject_tool_call`、ToolExecutor/Registry 权限、技能、预算与审计路径须继续保留；评测特有二次解码不能从“使用相同 helper”推导为完整入口兼容。

## 实际执行与反例

平台 Windows；解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`；HEAD `c1b09010cc135e235b4673f06bcb7fba7f56547f` 加工作区。

命令：`python.exe -m pytest tests/test_tool_dsml_protocol.py tests/eval/test_agent_eval_runner.py -q --basetemp=data/pytest-tmp/protocol-review-01`（使用上述解释器，PYTHONUTF8=1、PYTHONIOENCODING=utf-8、DATABASE_URL=sqlite:///./data/test_ai_assistant.db）。退出码 1，52 passed / 2 failed，5.89s。失败分别为合法 DSML 官方入口 IndexError、畸形信封错误码断言。

独立使用 Python stdin 直接构造并实际运行：两个完整调用拼接、DSML 与标准格式混合、解释文字前缀、嵌套调用、空参数名、原始未配对代理字符均返回 invalid；普通 `DSML is a notation` 返回 none；未知工具产生 ok 意图（解析无授权职责）；合法 calculator DSML 直接交 `runner.record_tool_call` 抛 IndexError。未运行任何真实工具、外部模型或付费请求。上述是隔离/单元反例，不证明真实模型恢复。

初读时担心 UTF-8 编码异常未捕获，实际确认 UnicodeEncodeError 是 ValueError 子类，原始代理字符被正确拒绝，因此不列为缺陷。

第一轮文件 SHA256（并发实现后须重新绑定最终版本）：

| 文件 | SHA256 |
|---|---|
| app/agents/tools/base.py | 4C59955C1E892664DA687A0E8D2C96CBCB68CBE46B388BED7058D876713ADEBA |
| app/agents/tools/dsml.py | 38B1F3FCB6B7EEC6919DCBA8509180E1990318E49FB94BD8B8C85966286D5D2B |
| tests/test_tool_dsml_protocol.py | 51D3EF9D67C880644B02ECB92D28A895F3620B4C2AE8D5DDE7FA473E7E26BDB6 |
| tests/eval/test_agent_eval_runner.py | B78A2B4FE45421062ADE20FF2B75238941DD98B4D53DD956F8168FC0DF70D586 |
| scripts/run_agent_chain_eval.py | EEC235EC883447BF5292C94EF2DDCEE144A8C1F5BB4355148582488F57A0DD86 |

## 尚待复核

修复最终 diff、default/Supervisor 权限/技能/预算/取消入口、全量 pytest、Ruff、mypy，以及最终提交的真实 CI 均需相应证据。未授权的 scripts 文件未修改；本 Agent 只写此评审文档。

# 第二轮：修复后独立复核

复核时间：2026-10-20 17:11（Asia/Shanghai）。本节覆盖第一轮暂不通过结论；第一轮保留作缺陷与修复轨迹。当前结论：**本轮代码与隔离入口审查通过，没有未修复 P1/P2；真实模型 CI 与业务接受仍未验证，不构成任务关闭。**

用户已批准追加 `scripts/run_agent_chain_eval.py`。复核完整工作区 diff：标准 JSON 仍独立检查重复键及非有限值；合法 DSML 使用共享严格解码结果，继续验证注册工具、参数 schema、代码及 recording callback。evaluator 版本更新为 0.4.1，不改冻结案例或指标门槛。第一轮 P1 和错误码断言均修复。

## 独立入口复验

没有新增或修改源码/测试文件；避免改变主 Agent 已冻结的检查输入。通过 Python stdin 临时 pytest plugin，在 collection 后仅把现有 `tests/test_tool_path_contract.py` 的 `_call(expression)` 请求生成器替换为等义 DSML：对 expression 做 json.loads，每个键值以 `parameter name="key" string="false">json.dumps(value)</parameter>` 表示，封装唯一 `path_probe` invoke；原测试的成功、拒绝、调用次数、结果、审计断言全部保留。临时转换不加入提交，也不改变正式测试文件。

实际命令：指定 conda `python.exe -` 执行上述 plugin，并调用 `pytest.main(['tests/test_tool_path_contract.py', 'tests/test_tool_dsml_protocol.py', 'tests/eval/test_agent_eval_runner.py', '-q', '--basetemp=data/pytest-tmp/protocol-review-02'], plugins=[ReplayDSML()])`。环境为 PYTHONUTF8=1、PYTHONIOENCODING=utf-8、测试配置；默认测试库 `data/test_ai_assistant.db`，与主 Agent 全量所用 `data/protocol-full-test.db` 不同。结果 **95 passed，7.30s，退出码 0**，日志 `data/protocol-review-02.txt`。

覆盖真实 `_iter_action_loop` wired/unwired 与 fast 入口、ToolExecutor 成功/异常/超时/有限重试、严格参数拒绝、身份/租户/角色撤销拒绝、伪造身份参数、重复指纹、轮次预算、取消后禁止成功审计。新增官方入口案例验证单一 DSML 记录一次、不增加模型请求、多调用仍失败；不是仅运行解析 helper。

另外以 Python stdin 直接构造真实 `SkillRuntime(SkillContext(mode=TOOL, tool_names=['different_tool'], versions=[合成有效身份]))`，给默认行动链回复 `review_probe` 的合法 DSML。真实 `_run_action_loop` 拒绝请求，探针函数零调用，观察中有拒绝说明，退出码 0。日志 `data/protocol-review-skill-02.txt`。合成 provider/技能身份只证明隔离控制，不代表生产授权或真实模型恢复。

## 最终输入绑定及限制

HEAD 仍为 `c1b09010cc135e235b4673f06bcb7fba7f56547f` 加工作区；文件与 `docs/reviews/2026-10-20-agent-ci-protocol-inputs.json` 对照：

| 文件 | SHA256 |
|---|---|
| app/agents/tools/base.py | fcd528bc823903bdf85dcb42fa72206d652838e5e04e820e591a994060ffdd73 |
| app/agents/tools/dsml.py | 38b1f3fcb6b7eec6919dcba8509180e1990318e49fb94bd8b8c85966286d5d2b |
| tests/test_tool_dsml_protocol.py | 51d3ef9d67c880644b02ecb92d28a895f3620b4c2ae8d5dde7fa473e7e26bdb6 |
| tests/eval/test_agent_eval_runner.py | 514e457c0bfc8ddadfa6bfd587628268c8d828ac2cb01b121798e36240653716 |
| scripts/run_agent_chain_eval.py | 7fdf69337f5df26d16e2153c06597500000c290f6111a3874a9059d99410d189 |

本轮对源码、测试、配置、依赖和评测输入的修改数量为零，没有冻结后新增测试。仅追加评审说明，不使既有对应检查失效。全量 pytest、Ruff/mypy 的最终结果由主 Agent 记录；本审查未自行重跑全量，未运行外部收费模型，未提交、推送、合并或变更任务完成状态。仍须最终 SHA 的三项 CI 与适用业务接受。
