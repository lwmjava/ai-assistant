# RAG-040 章节摘要真实保真评价 — 护栏内预算与模型方案（待批准）

> 日期：2026-10-09
> 状态：**提案，待主 Agent 转用户批准；批准前不发起任何真实调用**。
> 费用上限：用户批复总费用 ≤ ¥5。
> 适用：RAG-040「真实保真评价」必交付项当前为「未验证（无调用授权）」；本方案获批后才执行真实调用。
> 红线：不发起真实调用即不算产生费用；不输出任何密钥值；AI 判断不升级人工结论。

## 1. 模型候选与 key 前提

## 1. 模型约束（2026-10-09 用户追加）

**硬约束：一律使用 `.env` 已配置密钥的国内模型；不使用任何国外模型（gpt-4o-mini 等一律排除）；不修改 `.env`；`.env` 值不得进入任何代码 / Prompt / 日志 / 报告 / 提交内容，报告只记录 provider 与模型名。**

- **对话/生成（本评价唯一使用的模型）**：DeepSeek，经 `LLM_API_KEY` + `LLM_BASE_URL` + OpenAI 兼容接口（host=`api.deepseek.com`）。
  - **唯一可用候选 = `deepseek-flash`**：已登记 RAG-028 能力（`deepseek-flash-1m-384k-20261007`），`resolve_effective_capability` 命中，RAG-035 已验证该能力链路。
  - `deepseek-chat`（当前 `LLM_DEFAULT_MODEL`）：**未在 `LLM_CAPABILITY_DECLARED` 登记**（`resolve_effective_capability` 返回 NONE），Guard 会拒，**本评价不使用**；未来若登记才可用，本卡不改 `.env`、不为它登记能力。
- **嵌入（本评价不需要；如后续需要）**：DashScope 千问 `text-embedding-v3`（`EMBEDDING_API_KEY`=set、`EMBEDDING_MODEL=text-embedding-v3`）。本真实保真评价**不做向量检索/Embedding 调用**（摘要保真 vs 原文回查是文本比对），故嵌入侧本次 0 调用。
- **明确排除**：gpt-4o-mini 及一切国外模型；无 `OPENAI_API_KEY` 独立密钥。

## 1.1 候选可行性（布尔检查 `.env`，不输出值）

| 候选 | 部署 | 能力登记（RAG-028） | key 前提（布尔） | 结论 |
|---|---|---|---|---|
| **deepseek-flash**（唯一采用） | `api.deepseek.com` | **是**（`deepseek-flash-1m-384k-20261007`，窗口 1M / 输出 384k） | `LLM_API_KEY`=set | **采用**：复用现有 key，仅把 model 覆盖为已登记的 `deepseek-flash` |
| deepseek-chat | `api.deepseek.com` | **否**（NONE） | `LLM_API_KEY`=set | **不用**：未登记能力，Guard 拒；不改 `.env` |
| gpt-4o-mini（国外） | `api.openai.com` | 是 | `OPENAI_API_KEY`=absent | **排除**：国外模型 + 无 key |
| 千问 text-embedding-v3（嵌入） | DashScope | — | `EMBEDDING_API_KEY`=set | 本次 0 调用（评价不需 Embedding） |

**选择依据**：
- 当前主 key 已指向 `api.deepseek.com`，默认 `deepseek-chat` 未登记能力；同 key 覆盖为 `deepseek-flash` 即满足「复用 RAG-028 已登记能力 + 国内模型 + 不修改 `.env`」。
- 不选 `deepseek-v4-pro`：更贵且章节摘要不需要旗舰质量。
- 国外模型一律不用。

## 2. 样本设计

- **来源**：仅既有合成语料 / 本仓库评测数据集，**不外发真实客户文档**。候选：
  - `evals/section_summary/run.py` 现有合成章节样本；
  - `evals/chunk_structure_integrity/`、`tests/eval/` 中既有结构化正文样本（带父块/章节）。
- **数量与口径**：**8 个章节样本**（含 2 个超长章节触发分批合并，1 个空/极短章节验证不发请求）。每样本为「章节正文 + 该章关键事实点（expected_answer_points）+ 应回查的父/子块 ID」三元组。
- **每例调用构造**：
  - 单批输入 ≤ 4000 字符（≈ ≤ 4000 token 预占）；超长样本切批，批间合并再发 1 次。
  - 单条摘要输出预留 256 token。
  - 温度 0.0；不启用 SDK 隐式重试。
- **不做 LLM-as-judge**：保真判定用确定性骨架（`tests/eval/generation_judge.py` 的事实覆盖/禁止点/引用口径）+ 人工复核 pending，不再额外调用模型做评判，控制费用与避免 AI 判断冒充结论。

## 3. 调用与费用上限（总 ≤ ¥5）

按最坏价格预占（peak，CNY；依据 DeepSeek 公开定价页，2026-09 快照：flash 输入缓存未命中 ≈¥2~3/1M、输出 peak ≈¥8/1M）：

- 最坏输入单价预占：**¥3 / 1M token**；最坏输出单价预占：**¥10 / 1M token**（均高于公开 peak，留余量）。
- 单次请求最坏预占：
  - 输入 4000 token ÷ 1e6 × ¥3 = ¥0.012
  - 输出 256 token ÷ 1e6 × ¥10 = ¥0.00256
  - **单请求最坏 ≈ ¥0.015**。
- **硬调用上限：10 次**（8 章 × 1 次 + 至多 2 次合并冗余）。
- 最坏累计：10 × ¥0.015 ≈ **¥0.15**。
- **预算信封**：`BudgetEnvelope(ceiling=¥1.0)`（约 6.6 倍最坏累计余量）；**绝对天花板 ¥5**（用户批复）。
- **停止线逻辑**：
  1. 每次发请求前先问「余额是否够覆盖下一次最坏请求」，不够即停；
  2. 预估累计成本 ≥ ¥1.0 立即停止；
  3. 实际账单或预估触达 ¥5 绝对天花板立即停止；
  4. 服务错误 / 超时 / 未知 usage 不重试、不计成功，只切片记录。

## 4. 护栏

- 关闭 SDK 隐式重试（`OpenAICompatibleProvider` 一次性 chat，不自动重试）；每次 HTTP 尝试显式计数进报告。
- 未知 usage / 超时 / 5xx / 网络错误：该例标 `failed`，**不得算评测通过**；断点续跑按绑定键跳过已 ready 摘要，**不重复计费**。
- 不记录任何密钥 / 正文进日志与报告；报告只含模型/Prompt/协议版本、数据指纹、调用计数、预估成本、延迟、人工复核状态。
- AI 判断（确定性骨架）结果 `human_review_status="pending"`，**不升级人工结论**。
- 失败切片与断点续跑：`SectionSummaryJob`/`SectionSummary` 已按 (document, chunk, hash, plan, model, prompt) 幂等，重跑不重复副作用。

## 5. 验收口径（真实保真评价判定维度）

复用 `tests/eval/generation_judge.py` 既有确定性口径，对每条真实摘要：

- **事实覆盖**：章节关键事实点是否被摘要覆盖（`points_covered`）。
- **幻觉/禁止点**：摘要不得出现原文没有的事实（`forbidden_violations`，零容忍）。
- **引用准确性**：摘要对应的 `source_chunk_ids` 能否回查到真实父/子块（原文回查）。
- **报告形态**：`evals/section_summary/` 产出一份版本化 JSON，记录 model/prompt_version/protocol_version/data_fingerprint/`calls_used`/`estimated_cost_cny`/`latency_ms`/每例覆盖与禁止点命中/`human_review_status="pending"`。真实保真数值在人工复核前不得写成达标。

## 6. 执行前置（批准后才做）

1. 用 `deepseek-flash` 覆盖模型构造真实 provider（复用现有 `LLM_API_KEY`，**不修改 `.env`、不新增国外模型/密钥**），确认 `resolve_effective_capability` 命中 `deepseek-flash-1m-384k-20261007`。
2. 跑 `evals/section_summary/run.py --provider real --ceiling 1.0`（本卡当前 `main()` 仍默认 synthetic；real 路径已预留扩展点，见实现说明）。
3. 跑完落报告；报告只记录 provider 与模型名（DeepSeek / deepseek-flash），不含任何密钥值；`真实保真评价` 证据级别从「未验证」按实跑结果改写。

## 7. 当前未验证项（本提案不改变）

- 本提案**未执行任何真实调用**；真实保真评价仍为「待方案批准后执行」。
- RAG-035 未 done（P1 + 人工 Gold/发布阈值待用户），与本评价正交但仍前置登记。
