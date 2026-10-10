# RAG-017 实现说明 — 召回率持续测评门禁(基线挂 CI 作 1.0 发布门禁)

> 日期：2026-10-10
> 计划：`docs/plans/plan_rag_017_gate_20261010.md`（含门槛批准记录）
> 任务卡：`tasks.yaml` `RAG-017`（P1 / 实施中，云端实跑通过后置 done）
> 分支：`rag-batch-20261007`

## 1. 功能完成情况

谁可以做什么：

- **任何人推送/发起 PR**：CI backend job 在 pytest 之后追加「RAG baseline gate」步骤——用冻结基线配置（`RAG_BACKEND=native`、`RAG_CHUNK_STRATEGY=structured`、`RAG_VECTOR_STORE=local`、生效日期过滤关闭、`EMBEDDING_API_KEY` 取自 GitHub Secrets）跑 `scripts/run_rag_baseline.py --mode official`，再用 `scripts/rag_gate_check.py` 判定。任一步失败 → job 失败 → 工作流失败，作为 1.0 发布门禁之一。
- **改动切分/Embedding/检索参数**：CI 自动重跑基线；只要任一指标跌破门槛、出现越权、或配置与冻结基线不一致，即被拦截。
- **维护者**：本地也可手动跑 `python scripts/rag_gate_check.py --report <报告> --baseline evals/reports/rag-v0.1-baseline-20260919.json` 复现判定。

门禁规则（2026-10-10 用户批准，建议档）：

| 项 | 门槛 |
|---|---|
| Recall@1 / @5 / @10 | ≥ 0.78 / 0.94 / 0.94 |
| MRR | ≥ 0.92 |
| nDCG@10 | ≥ 0.92 |
| 安全切片越权案例 | == 0（零容忍） |
| run_config | 必须与冻结基线一致（provider 非 Mock、model=text-embedding-v3、dim=1024、native、structured/500/64、k=60、local） |
| 外部模型失败 | 报告缺失/损坏/字段不全直接失败，不得记质量通过 |

## 2. 代码位置与行为

| 文件 | 内容 |
|---|---|
| `scripts/rag_gate_check.py` | 新增门禁判定脚本：`check_config`（配置一致 + 拒 Mock）、`check_metrics`（指标 + 安全切片）；通过退出 0，任一不满足退出 1 |
| `tests/eval/test_rag_gate.py` | 9 个用例：真实报告通过；Recall/MRR/nDCG 退化、越权、Mock 嵌入、配置不一致、报告缺失、指标缺失均拒绝 |
| `.github/workflows/ci.yml` | backend job 新增 `RAG baseline gate` 步骤（env 冻结配置 + Secret 密钥；跑官方基线 → 跑门禁判定） |
| `evals/reports/rag-v0.1-baseline-20261010.json` | 本次官方基线报告（真实嵌入，作为门禁对照实测证据） |
| `docs/plans/plan_rag_017_gate_20261010.md` | 本卡实现计划（含门槛批准记录） |

## 3. 验证（2026-10-10 实测）

| 命令 | 结果 |
|---|---|
| `python scripts/run_rag_baseline.py --mode official`（冻结配置） | 退出 0；Recall@1=0.8095 @5=0.9619 @10=0.9619 MRR=0.9429 nDCG@10=0.9401；引用覆盖 0.9184；越权 0/13；embedding=OpenAICompatibleEmbeddingProvider/text-embedding-v3/1024（非 Mock） |
| `pytest tests/eval/ -v` | 98 passed（19.61s），含新增 9 个门禁用例 |
| `ruff check scripts/rag_gate_check.py tests/eval/test_rag_gate.py` | 通过 |
| 退化注入 | 测试内构造 Recall@1=0.5、MRR=0.8、nDCG@10=0.8、越权=1、Mock 提供商、chunk_strategy=auto 均被拒绝（退出 1） |

配置一致性核对：本次报告 run_config 与冻结基线逐项一致（native/structured/500/64/k60/local），门禁对照可比。

## 4. 未做的事与说明

- **云端实跑**：用户已授权推送当前分支，Actions 实跑结果待回填本节（CI 通过后本卡置 done）。
- 未做重排实验（REL-004 裁定）、未新增数据集、未把 Mock 当证据。
- 未修 QA-005/工作区 pytest 其他失败项。
- **环境问题（上线前技术债关联）**：本机 `data/pytest-tmp/run` 目录 ACL 损坏（连管理员修复都被拒），导致默认 basetemp 下 pytest 全 error——与先前「336 errors」同源；本卡验证改用 `--basetemp=data/pytest-tmp/run2` 绕过（与 conftest 设计一致，不改代码）。CI 为 ubuntu 不受影响。修复需管理员权限，已向用户说明。

## 5. 回滚

- 删除 CI 门禁步骤与 `scripts/rag_gate_check.py`、`tests/eval/test_rag_gate.py` 即恢复仅手动跑基线。
- 无数据库、配置或权限变更。
