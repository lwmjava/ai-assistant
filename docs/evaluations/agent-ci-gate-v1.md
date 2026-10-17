# Agent CI 门禁 Evaluation v1

类别：Observed Regression / Adversarial / Smoke（工程机制）；非 Gold。原模型基线仍是 agent-chain-v0.1 Silver 十例，不把下列工程测试加入或扩大模型语料。

| 类别 | 实际入口与用例 | 期望 |
|---|---|---|
| 数值 | gate CLI：NaN/Infinity/bool、负数、>1、错误汇总、空/重复/额外案例 | exit2，安全分类，无凭据回显 |
| 阈值 | 合法 route0.9；route0.8、followup0.5、tool0.5 | 0.9可通过，其余exit1且对应 metric reason |
| 身份 | official intent为Mock/fallback；错误期待模型/端点；错误Git/Prompt/数据/源码 | 模型请求前拒绝或gate exit2 |
| 接线 | raw与预测不一致、工具回复与名称不一致、评分器伪全1 | gate独立解析/计数拒绝，不能两边共同自证 |
| 冻结/跨平台 | Git LF blob 与 Windows CRLF原文件；普通内容变化 | LF/CRLF一致，内容变化拒绝；算法显式记录 |
| 失败 | 模型 unavailable/部分失败/单次及总超时、非字符串/超大回复、非法/重复键/深层JSON | 无完整成功报告，有限时间、安全错误 |
| 权限/信息 | 无Secret、缓存开启、能力配置未批准、越报告路径、模型回显实际密钥 | 不降护栏，不进行未经授权业务写入，拒不安全输出 |
| 副作用 | 数据集只发送合成输入，模型消息无expected字段；评测工具选择不执行沙箱 | 选择评测不执行工具；仅证明覆盖的代码入口 |
| 幂等/回滚 | 既有报告、原子发布失败、probe重复目录 | 原证据保留，本次临时文件清理；不能覆盖 |
| CI | 严格YAML无重复键、独立job、Secrets仅official、禁止skip/fail-open、安全artifact路径 | 固定触发及接线可核对；实际GitHub运行另取E1 |
| 探针 | 完美与route0.9基线分别造3类故障；损坏输入/错误CLI | 目标metric exit1才成功，不接受任意非零 |

正式回归位于 tests/eval/test_agent_eval_runner.py、test_agent_gate.py、test_agent_gate_entry.py；独立入口探针 docs/reviews/pagt007_review_probe_20261013.py。合成报告/隔离transport只是 E2 工程机制，不作真实模型质量验收。新鲜真实 official 报告为 E1 本地模型证据；实际 CI run 仍需单独记录。

结果、命令、退出码与最终指纹见 implementation_pagt_007.md、closure_pagt_007_20261013.md 及独立审查报告；未执行项保持“未验证”，不能引用历史指标关闭卡。CLI reason codes / 版本 /报告构成门禁观测证据，不新增运行时 Agent Trace 或业务审计路径。
