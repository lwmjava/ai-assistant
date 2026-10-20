# 工具调用格式兼容修复说明

日期：2026-10-20。关联 PWFL-001 当前 CI 阻断。状态：本地修复、全量验证和独立代码审查完成；未提交、未推送，未完成最终 CI 验收。

## 实际行为

模型在行动阶段可以输出既有 tool_call JSON，也可输出整个回复仅含一次 calls/invoke/parameter 的受支持 DSML。共享 inspect_tool_call 将其解析为同一种 ToolCall；调用继续经过原有注册工具、参数、身份、租户、技能、预算、审计和执行链。解析本身不执行工具或授予权限。

兼容两种明确标记（单/双全角竖线），参数 string=true 原样保留字符串，string=false 严格解析 JSON。最多 128 KiB、64 参数，名称有界。重复参数/JSON 键、非有限数字、嵌套或多个调用、混合格式、解释文本/代码围栏、未知结构/属性、坏闭合和超限内容被拒绝。不猜测别名，不执行参数中的表达式或代码。

官方评测入口移除对 DSML 的标准标签二次拆解，保留标准 JSON 原有严格检查；随后仍执行原 schema、代码和记录适配器校验。评测器版本 agent-chain-eval-0.4.1，冻结数据、指标阈值、请求预算、打分和副作用限制均未改变。SYSTEM_ACT 继续要求优先输出标准信封。

## 文件和任务映射

- app/agents/tools/dsml.py：有界单调用格式兼容。
- app/agents/tools/base.py：共享工具意图入口接线，畸形协议返回 invalid。
- scripts/run_agent_chain_eval.py：合法兼容请求复用共享解析结果，保留标准 JSON 检查。
- tests/test_tool_dsml_protocol.py：合法字符串/JSON、两种标记及失败边界反例。
- tests/eval/test_agent_eval_runner.py：官方完整入口合法 DSML 一次记录、非法多调用失败，无额外请求。
- docs/plans/plan_agent_ci_protocol_20261020.md：批准方案与发现的 P1 修复范围。
- docs/reviews/2026-10-20-agent-ci-protocol-review.md：独立入口反例及复核。
- docs/reviews/2026-10-20-agent-ci-protocol-inputs.json：最终检查输入快照，以基线 HEAD 加文件 SHA256 绑定候选。

## 验证环境及中间证据

基线 c1b09010cc135e235b4673f06bcb7fba7f56547f，分支 codex/flow-003-scheduled-execution。基线 GitHub run39377425900 Backend/Frontend 成功，Agent official 失败：tool_selection_recall 与 tool_call_validity_recall 均0.5，agent-003 为未识别 DSML。fitz 警告不是直接失败原因。

Windows 11 build22621，指定 conda Python3.12.0；pytest8.4.2、ruff0.8.4、mypy1.14.1、httpx0.28.1、pydantic2.10.4、sqlmodel0.0.22、langgraph1.0.10。命令使用 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，PYTHONUTF8=1。测试使用本轮 data/ 下独立 SQLite 与 basetemp；不读取或改写业务库、不发真实模型请求。

- 红测：新增协议反例16失败，证明旧解析器缺失行为（data/protocol-red.txt）。
- 过程失败：默认 pytest 临时目录与 Ruff 缓存目录权限错误分别保留日志；换本轮独立 basetemp 和 Ruff --no-cache 后重跑，不将环境错误算作业务失败。
- 官方入口等相关测试98通过，7.84秒，退出码0（data/protocol-related-03.txt）；随后仅递增评测版本和同步注释，最终全量仍须重验。
- python -m ruff check . --no-cache：退出码0（data/protocol-final-ruff.txt）。python -m mypy app/：219文件通过、退出码0（data/protocol-final-mypy.txt）。
- 独立 Agent /root/protocol_review：既有真实行动入口注入同义 DSML，保留权限、参数、预算、取消、审计等业务断言，95通过、7.30秒、退出码0（data/protocol-review-02.txt）；另构造技能限制反例，工具实际零执行（data/protocol-review-skill-02.txt）。这是隔离合成输入证据，不证明真实模型质量。
- 最终 `python -m pytest --basetemp=data/pytest-tmp/protocol-full-01`：3109 passed、3 skipped、3 warnings，713.11秒，退出码0（data/protocol-full-01.txt）。DATABASE_URL=sqlite:///./data/protocol-full-test.db。3项跳过为 Windows 缺符号链接创建能力、POSIX 0600权限语义、Linux打开文件替换身份保护；不声称这些平台检查已通过。3项警告为既有 ast.NameConstant 弃用和两个非有限向量故障注入的 NumPy 警告。
- 验证结束后4020项输入SHA256全部匹配。无前端/API契约变更，前端本地检查不适用。未增加平台相关分支；最终 Ubuntu Backend 与 Agent official 仍需绑定最终提交复验。

## 验收对账

| 要求 | 证据 | 结果 |
|---|---|---|
| 单调用兼容且标准信封继续可用 | E2：官方完整入口隔离测试、原标准协议回归，最终全量通过 | 本地通过 |
| 畸形、多调用、重复参数及非有限值拒绝 | E3：协议合成反例；E2：官方记录入口多调用不计成功 | 通过 |
| 原执行权限、预算、取消、技能、审计仍生效 | E2：独立行动入口95pass与技能零执行反例 | 通过 |
| 不改变指标门槛与模型请求预算 | 源码diff独立审查及E2官方入口12请求断言 | 通过 |
| 最终输入绑定、必要本地回归 | 4020指纹一致；全量3109pass、Ruff/mypy退出码0 | 通过，3项Windows跳过明确保留 |
| 最终提交真实模型与Ubuntu CI | 尚未提交、未触发复验 | 未验证 |
| 本轮用户业务接受 | 当前仅获修复授权 | 未验证，不关闭任务 |

独立审查由 /root/protocol_review 执行，首轮发现官方入口P1，修复后复验无未修复P1/P2；详见评审文档。实际使用 task-card-delivery、investigate、brainstorming 技能；未调用浏览器或前端QA。日志文件摘要见 docs/reviews/2026-10-20-agent-ci-protocol-evidence.json。

## 未做、风险与回滚

没有实现通用 DSML/XML、原生 provider tool calling、多工具并行、格式纠错重试、模型切换、Prompt 修改或新配置。没有修改工作流重试、冻结评测数据和 CI。兼容仅覆盖明确语法，其他输出仍拒绝，不承诺模型以后永远服从格式。

最终提交的 Backend、Frontend、Agent official baseline 尚未取得，PWFL-001 保持 in_review。未经本轮 Git 发布和真实模型调用授权，不自动提交、推送或触发收费复验。回滚仅撤销本轮兼容代码和测试，不操作业务数据。
