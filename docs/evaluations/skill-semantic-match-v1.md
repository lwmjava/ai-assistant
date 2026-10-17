# Skill semantic match v1

数据类型：Observed Regression / Adversarial / Smoke，AI辅助合成，不是Gold。SKILL-003 原契约：语义能够命中关键词未命中的技能，且不改变技能执行/权限护栏。选择协议 skill-semantic-select-v1：严格JSON name/confidence或null，仅允许已授权启用候选，有限阈值，最多1项语义命中。

对照：相同隔离DB技能及用户，真实同步关键词入口未命中；真实异步ChatService入口用合成provider提议相应候选后激活，private/global沿既有围栏。E2仅证明集成，不能证明真实模型语义质量。

用例：tests/test_skill_semantic.py、test_skill_semantic_entry.py及独立入口反例审查；涵盖确定性零额外调用、未授权候选/disabled/模型不适配、超长请求/输出/候选、重复键/未知名/非法JSON、NaN/Infinity/bool/越界/低分、私有正文不外发、故障不记录敏感详情、取消和两对话入口execution claim释放。输入JSON32768字节、请求4096、候选32项/描述512、输出1024、5秒/max_tokens128/无本层重试；成本和延迟上限为协议，不是实测上线性能。

2026-10-14用户已明确批准6条真实评测及独立业务最终验收：现有deepseek-flash实际6次调用，4正例命中、2无关未激活，Action结果6/6；第6条name=null对象违反严格协议，被安全拒绝，schema服从5/6。真实Smoke证据docs/reviews/skill003_real_smoke_20261014.json，不代表通用质量或Gold，不重复调用筛选通过。版本登记与证据在实现说明、关闭对账和tasks.yaml记录。
