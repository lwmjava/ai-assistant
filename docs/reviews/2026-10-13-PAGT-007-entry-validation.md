# PAGT-007 独立入口验证

日期：2026-10-13。实际身份：`/root/pagt007_entry_validation_v2`。角色为独立验证，未参与 runner、gate、probe、CI 实现，也不是最终人工审核 Agent。

读取 AGENTS.md、task-card-delivery 1.3.0、原 tasks.yaml 卡、批准的实施计划、独立计划审查和 review_fixes；读取迭代指导、Harness 规范以及 00/01/02/03/07/08/09/10/11 规则。仅修改独立正式测试与本报告；未读取 .env、执行真实模型、修改数据集、提交或发布。

## 验证入口及证据界限

正式测试 `tests/eval/test_agent_gate_entry.py` 独立构造报告，不使用 runner.build_report、score 或 probe.recompute 构造通过结果；仅读取冻结数据及实际身份指纹接口。通过独立 Python 子进程执行 runner、gate、probe 真 CLI。子进程环境清空模型凭证、固定 Mock provider 和不可连接的本地 URL，测试数据库为 data/test_ai_assistant.db。

这些是 **E2 合成入口/门禁机制证据**，不是真实模型质量、真实 GitHub CI 或发布合格证据。历史报告与 Mock 不用于关闭原卡。副作用字段是设计声明，不当作观测计数；本报告不额外声称 Tool/MCP/RAG/DB 的全部运行路径已被哨兵验证。

| 原卡要求/失败边界 | 独立入口反例与观测 |
|---|---|
| 报告身份与完整性 | smoke、历史 schema、错误模型/端点、raw route/tool 与预测不符、缺案例、重复案例，均 gate exit2；不输出敏感 marker 或 Traceback |
| 数值边界 | NaN、bool、1e308 退出2；route0.9 合法通过；route0.8、followup0.5、tool0.5 必须 exit1 且精确目标 metric reason，不能用任意非零冒充拦截 |
| 不可信输入 | 非UTF8、重复JSON键、NaN、深层数组拒绝且安全分类错误 |
| official 准入 | 缺真实 intent provider 无模型调用、exit2、无报告 |
| 回滚/幂等 | smoke 生成后 gate拒绝；runner 重跑同一路径拒绝并保留原字节；probe 重入保留原manifest |
| 三类退化机制 | 从 route0.9 的自洽合成报告执行 probe CLI，三类结果必须 exit1，manifest 标明 not-model-quality；smoke 源不创建探针目录 |
| CI 权限与持续接线 | strict YAML 拒绝任何重复键；独立 job 无 needs/skip/continue-on-error，contents read；仅official step注入secret，安装与其它步骤无该secret；只上传指定 agent-ci 目录 |

## F03 红绿闭环

首次严格 YAML 正式反例命令：

```powershell
$env:RAG_BACKEND='native'
$env:DATABASE_URL='sqlite:///./data/test_ai_assistant.db'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/eval/test_agent_gate_entry.py::test_ci_workflow_has_unique_keys_and_one_agent_job -q -p no:cacheprovider --basetemp=data/pagt007_entry_f03_red
```

实际结果：exit1，1 failed，0 skipped，2.14s。真实 ci.yml 中 duplicate LLM_CAPABILITY_GUARD_ENABLED 触发 ValueError；当时还存在两份 agent-baseline。没有以测试导入失败作为业务红。主 Agent 去重后，该正式测试进入完整绿批。

## 完整验证过程

完整命令使用相同 RAG/DB 环境：

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/eval/test_agent_gate_entry.py -q -p no:cacheprovider --basetemp=data/pagt007_ev2
```

第一轮 exit1，9 passed /16 failed，55.26s；16 项的实际业务 exit 与 Agent reason 正确，但既有 fitz 导入会向 stdout 输出 deprecation notice，导致测试对全部stdout的严格相等失败。修正测试为匹配所有 AGENT_ 协议行并仍要求精确唯一 reason 及 exit，未改业务代码、阈值或删除反例。

第二轮相同命令改 basetemp=data/pagt007_ev2b：exit0，25 passed /0 skipped，57.84s；ruff check tests/eval/test_agent_gate_entry.py --no-cache exit0。共享pytest独占时隙已释放。

主 Agent 此后发现 F06：Git LF 与 Windows CRLF 冻结指纹不兼容。**本轮绿是修正前候选证据；最终源码修正后必须重跑并记录指纹，不用本轮结果关闭最终代码。**

F06 修正后最终复验：相同完整命令改 `--basetemp=data/pagt007_ev2f`，**exit0，25 passed /0 skipped，58.91s**。原始报告身份接口已包含 canonical newline 算法及扩展源码指纹；本角色未修改源码。本次25例涵盖 F03 严格 YAML 绿、全部 CLI 完整性/数值反例、runner 准入/幂等、probe 三类目标机制。F06 LF blob 正式红及修复专项回归由主 Agent 单独记录，本角色未把主 Agent 结果冒充独立执行。

换行收尾复验：主 Agent 只把 runner CRLF 归为 LF，本角色核对当前字节没有 CRLF，将当前 LF 还原 CRLF 后 SHA256 精确等于上一轮 `90c2362d9c0ed52bdb7688d9fb29875a892284f88364cb96293b061d771c5050`，确认文本等价，没有逻辑改动。完整25例再次实际执行（basetemp=data/pagt007_ev2lf），**25 passed /0 skipped，exit0，59.50s**。独占时隙释放。报告下列指纹以这一次 LF 复验为准。

最终工作区 HEAD：`a0d02eb411a53c3191e81f0fc99528ea60d0d3c4`；含其它卡未提交改动，以下为本次验证候选的原始文件 SHA256（不依赖单一Git HEAD判断最终源码）：

| 文件 | SHA256 |
|---|---|
| scripts/run_agent_chain_eval.py | 0cce4c41abef7887067d4f30d9d2ac02ef95fbf9674c5c92b677c4196dab319d |
| scripts/agent_gate_check.py | e157a355c34e7854989bdb5f1384832f5ed0bc28f46de74d0baa992fdb0288f7 |
| scripts/agent_gate_probe.py | 2f867d4845861bd2619358838c1f638f91afab359dd482c93ba390ba5377961d |
| .github/workflows/ci.yml | f5a0c1b22b2e50148b1bc3b529410089c276ebe0be07321fc3f31ca143535d93 |
| tests/eval/test_agent_gate_entry.py | de1b29cc229096028af6095fe7e58ac6ded835e719a3d1a486777e306fb29e80 |

## 未验证与结论

真实 GitHub Actions 执行、Secrets 配置、分支保护与用户业务验收未由本角色执行，不得从 workflow 静态接线推导通过。真实模型 official 由主 Agent 在用户授权范围另行执行，本角色不代替其外部证据。

当前结论：F06 修正后最终候选独立入口25例通过、F03正式红绿闭合，无本角色发现的未修复 P1/P2。真实 GitHub CI 和业务验收仍未验证，不能据此标记 PAGT-007 done。
