# 证据冲突验证与有限纠错 Evaluation Plan（v0.1）

本计划锁定离线实验的对象、样本、指标和决策规则。运行结果写入单独报告，不写进本计划，也不改写冻结基线。

## 1. 评估对象

- 能力：记忆与检索证据的条件验证，以及有界纠错。
- 变更摘要：新增可显式调用的纯函数模块，默认不接入生产回答链。
- 风险假设：误改本来正确的回答、泄漏淘汰证据、在预算耗尽或取消后继续调用、把合成夹具通过写成生产启用。
- 关联代码：`app/agents/evidence_conflict.py`，`evals/evidence_conflict/`，`tests/eval/evidence_conflict.py`。

## 2. 目标与发布问题

- 要证明：在锁定的虚构夹具上，冲突、拒答、预算耗尽和误改控制组可复现；纠错次数有界；来源授权、租户、版本和取消保持有效。
- 不在本轮证明：真实模型质量、生产回答链收益、官方 35 条门槛变化。
- 发布判断：本实验不构成生产发布。夹具门槛通过只得到 `fixtures_passed`；生产默认必须保持关闭。

## 3. 数据集

| 数据集 | 来源 | 数量/分布 | 脱敏 | 标签方法 | 版本 | 用途 |
|---|---|---|---|---|---|---|
| evidence-conflict | 虚构夹具 | 56；新旧记忆 10、记忆与检索 10、正确回答 20、拒答 6、预算 4、安全 6 | 不含真实敏感数据 | 确定性规则 | 0.1.0 | Smoke 对照 |

划分：development / validation 用于决策，holdout 只记录。训练或调参不得使用 holdout。

## 4. 指标与门槛

见 `docs/plans/plan_evidence_conflict_correction_20261111.md` 第 4 节。对照是基线直出，处理是验证加至多一次纠错。目标错误相对已授权当前事实计算；预算耗尽和取消可以留下未修复的目标错误，但必须停止调用。

## 5. 执行配置

- 环境：本地 mock，不使用真实 Embedding 或对话模型。
- 命令：

```powershell
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/eval/test_evidence_conflict.py -q
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe evals/evidence_conflict/run.py --mode mock --write
```

- `--mode` 不是 `mock` 时必须退出且不写报告。
- 结果：`evals/reports/evidence-conflict-correction-v0.1.json`，`docs/evaluations/evidence-conflict-correction-v0.1.md`。

## 6. 决策记录要求

报告必须同时给出夹具决策和生产决策。不得把 `fixtures_passed` 宣传为生产已启用。
