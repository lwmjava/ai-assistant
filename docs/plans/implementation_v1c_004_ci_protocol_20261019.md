# 工具调用文本协议修复说明

日期：2026-10-19。基线：47dbf69a75ceb8ba0c664066e6afe4fb5bef470b。用户批准新增两个路径并保存本地提交；不包含推送或合并授权。

## 实际行为

默认管线和 Supervisor 复用的公共行动系统提示现在明确要求完整的 `<tool_call>{"name":"工具名","arguments":{}}</tool_call>` 信封，参数必须为合法 JSON 对象，并禁止 DSML 等供应商私有标签、Markdown 围栏及附加文字。最终普通回答仍按用户语言输出。这里的禁止是模型输出契约，未给解析器新增检测功能。

没有更改 inspect_tool_call、工具权限、参数验证、预算、重试、官方数据集或门槛。不能直接执行 DSML，也不会将失败回复重新采样到通过。

## 修改位置与证据

- app/agents/prompts.py：公共 SYSTEM_ACT 明确完整信封及格式约束，运行时与官方评测使用同一提示。
- tests/eval/test_agent_eval_runner.py：运行时/官方提示一致性，以及 DSML 和标准信封两类完整 official 入口离线回放。前者保持工具召回 0.5 和失败退出，后者为 1.0；两者均为 12 次请求，不添加重试。
- docs/ai-prompts/evals/supervisor-tool-contract-v1.yaml：1.0.1 登记格式偏离反例，标记合成离线性质。
- tasks.yaml：登记用户批准的新增路径、本地提交授权与失败 CI。

红阶段：1 failed、2 passed、34 deselected，2.97 秒，失败对应缺少完整公共系统协议说明；这只能证明提示契约缺失，不能证明修改前后真实模型质量差异。

指定 conda Python 与隔离 SQLite：相关回归 309 passed、188.63 秒，退出 0；mypy app/ 216 个文件通过；Ruff 默认缓存因权限失败（退出 2），`ruff check . --no-cache` 全库通过（退出 0）。不带筛选参数的全量 pytest：2885 passed、3 skipped、3 条既有警告，860.30 秒，退出 0；全量后 526 项输入指纹一致。最终结论与日志摘要记录在 docs/reviews/2026-10-19-v1c-004-ci-protocol-evidence.json。

独立只读审查：/root/ci_protocol_review，无 P1/P2；见 docs/reviews/2026-10-19-v1c-004-ci-protocol-review.md。

## 当前限制、发布与回滚

这是一份加强模型输出协议的修复候选，未声称真实模型已恢复。最终提交仍需通过 GitHub Backend、Frontend 和 Agent official baseline；工具两项召回继续要求 1.0。没有真实模型复验或推送授权，本轮不执行这些操作；任务保持 in_review。

纯后端提示/离线测试变化，没有前后端 API 契约或前端源码变更，本地前端检查不适用。没有修改进程、信号或平台分支；最终 Ubuntu CI 仍待复验。没有生产配置或数据迁移；回滚时撤销本次提示/测试/Evaluation 变更即可恢复此前文本协议说明，不涉及数据库回滚。

全量的 3 条跳过来自 CLI 文件系统平台验证：当前 Windows 未授予符号链接能力、POSIX 0600 权限语义、替换打开文件的身份保护；不是本轮新增跳过。Linux 相关结果以最终提交的 Ubuntu Backend CI 为准，本轮未将这些项目声明为通过。


## 最终关闭补记（2026-10-19）

用户已明确要求关闭V1C-004。最终业务提交9cbe94fffe269cde9d93c00fb6d1f6d25fd01264的CI run39268207253，Backend/Frontend/Agent official baseline全部success，官方四项指标均1.0，Prompt指纹与本地一致；本轮新独立审核120 passed/37.64秒/exit0，526项当前输入匹配。任务现为done；前文未提交/未推送/待CI语句保留为历史记录，不代表当前关闭状态。完整对账见docs/reviews/2026-10-19-v1c-004-closure.md及ci-protocol-evidence.json。本轮仅修改文档与任务状态，没有改变已验证执行输入，没有合并或部署。
