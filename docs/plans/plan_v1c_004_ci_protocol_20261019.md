# 工具输出协议偏离修复计划

日期：2026-10-19。关联 V1C-004；候选基线 47dbf69a75ceb8ba0c664066e6afe4fb5bef470b。用户已明确批准追加 app/agents/prompts.py、tests/eval/test_agent_eval_runner.py 并提交。按本计划最小修复，不包含推送、合并或真实模型调用授权。

## 目标和现状

官方 Agent CI 的 agent-003 将标准工具信封输出成 DSML，两个工具指标为 0.5。失败和前次成功的 Prompt 指纹相同。调查证据见 docs/reviews/2026-10-19-v1c-004-ci-investigation.md。

目标是加强运行时与评测共用的工具输出协议说明，保持工具权限、参数校验和现有信封解析行为。提示调整是待真实模型验证的修复候选，不能预先保证消除模型格式偏离。

## 最小方案和影响范围

1. 请求追加允许 app/agents/prompts.py、tests/eval/test_agent_eval_runner.py。前者 SYSTEM_ACT 是所有行动路径与评测共用的系统提示；后者验证官方评测入口，不只验证 helper。
2. 补失败回归：从实际失败协议构造不含业务标准答案的格式反例，确认 DSML 不会执行、不算有效调用；断言公共系统提示明确要求完整起止信封、合法 JSON、禁止供应商私有标签，并在运行时与评测保持一致。格式约束测试只能证明契约，不能证明真实模型服从。
3. 最小强化 SYSTEM_ACT 的通用格式约束，不增加 agent-003 特例、不注入冻结样例或标准答案，不扩大解析器支持协议。必要时同步已允许的版本化 Evaluation 和文档；不改冻结官方数据集、CI、门槛或评测打分逻辑。
4. 运行相关评测/工具/技能/Supervisor 回归、ruff check .、mypy app/、不带筛选的全量 pytest，独立复核新变更。源码输入变化后更新最终指纹，旧结果仅作历史。
5. 真实 official 复验必须绑定最终提交并通过 GitHub Agent official baseline；未获真实调用和 Git 发布授权前不自行触发收费请求或推送。必要 CI 未通过则保持 in_review。

## 非目标和风险

不添加 DSML 执行适配，不放宽参数/权限，不增加重试、best-of 采样或模型调用预算，不更换模型，不处理无关 fitz 警告。共享 SYSTEM_ACT 的变动会影响默认管线和 Supervisor 行动提示，需要公共路径回归。模型输出有非确定性，本地全绿不能代替真实官方复验。

## 验收

失败协议仍被拒绝，标准调用继续通过；公共路径无新增回归；最终版本的 Backend、Frontend、Agent official baseline 必要检查成功，工具选择与合法调用召回继续按 1.0 门槛验收。未通过时继续记录原始失败，不关闭任务。
