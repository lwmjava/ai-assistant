# ADR-0003 知识库生效日期与预告版本

> 状态：**Accepted**  
> 任务：`RAG-006`（生效日期过滤切片）  
> 起草日期：2026-09-19  
> 批准日期：2026-09-19  
> 批准人：产品负责人（本仓库个人开发者）  
> 风险：L2  
> 决定：选项 A。默认 `RAG_EFFECTIVE_DATE_FILTER=false` 保持现网；打开后才启用 as-of / scheduled。

## 1. 背景

`RAG-005` 基线中 `rag-031`（「2027 年标准套餐打算卖多少钱？」）完全无法命中
`doc-billing-future`。该文档在语料中 `is_current=false`、`effective_at=2027-01-01`。

当前检索硬过滤 `Document.is_current == True`，因此未生效预告**永远进不了候选集**。
这与 `stale_version` 切片（必须挡住已废止旧版）共用同一个二值开关，无法同时表达
「挡旧版」和「按需打开未来预告」。

`Document` 模型没有 `effective_at` / `expires_at` 字段。语料 manifest 里的日期只存在于评测夹具。

## 2. 事实

| 声明 | 证据 |
|---|---|
| 检索只保留 `is_current=true` | `app/rag/vectorstore/local.py` `hybrid_search` |
| 文档模型无生效日期字段 | `app/models/rag.py` `Document` |
| 旧版本过滤本轮基线 0 违规 | `docs/evaluations/rag-v0.1-baseline-report.md` 第 4 节 `stale_version` |
| 未来预告完全不可检索 | rag-031；同报告第 5.3 节 |
| 预告正文要求不得当现行价答复 | `evals/fixtures/corpus/kb-future-pricing.md` |
| ADR-0001 规定旧版本不进入检索 | `docs/adr/0001-knowledge-base-permission.md` |

## 3. 选项

| 选项 | 语义 | 对 rag-031 | 对 stale_version |
|---|---|---|---|
| **A（本 ADR 建议）** | 默认只检索「已发布且对 as-of 时刻已生效」的当前版本；仅当查询带明确未来日期或预告意图时，额外打开 `scheduled/preview` 文档，并必须带「未生效、不得当现行价」约束 | 可命中预告 | 旧版仍排除 |
| B | 用 `effective_at` / `expires_at` 推导是否当前，弱化 `is_current` | 评测日 2026-09-19 仍检不到 2027 预告 | 需另定义废止 |
| C | 改案例为「不应检索未生效预告」 | 评测变绿，产品能力不增加 | 不变 |

未选 B：无法回答「将来卖多少」这类带日期的合法问题。  
未选 C：会把能力缺口改写成数据集期望，属于降级门禁。

## 4. 决定

选择 **选项 A**。

1. **默认检索（as-of = 现在，除非调用方显式传入）**  
   文档必须同时满足：
   - 同租户（ADR-0001）；
   - `is_current=true`（该版本组的已发布指针）；
   - 若存在 `effective_at`，则 `effective_at <= as_of`；
   - 若存在 `expires_at`，则 `as_of < expires_at`。

2. **预告 / 已排期版本**  
   `is_current=false` 且 `effective_at > as_of` 的文档默认不进入检索。  
   仅当查询带可解析的未来日期，或产品定义的 preview 意图时，才把匹配该日期窗口的
   scheduled 文档加入候选，并在分块元数据与上下文中标记
   `version_status=scheduled`，禁止当作现行收费/政策。

3. **已废止版本**  
   被新版本取代的旧文档保持 `is_current=false` 且无未来 `effective_at`，
   任何默认或预告路径都不得命中（与 ADR-0001、stale_version 一致）。

4. **`is_current` 不删除**  
   它继续表示「该版本组当前发布指针」，不单独承担生效日期语义。

## 5. 后果

- 普通「现在多少钱」仍只命中现行文档，199 不会被 249 预告污染。
- 「2027 年打算卖多少」可以命中预告，但必须带未生效约束。
- 需要文档字段、导入写入、检索过滤和评测 as-of 约定一并设计。
- 查询日期解析失败时，保守回退为默认检索（不打开 scheduled）。

## 6. 迁移

1. `Document` 增加可空 `effective_at` / `expires_at`；历史行视为立即生效、无过期。
2. 检索增加可选 `as_of`；默认 `datetime.now(UTC)`。
3. Feature Flag 例如 `RAG_EFFECTIVE_DATE_FILTER=off|on`，默认 `off` 保持现网。
4. 评测夹具把 manifest 日期写入文档行；复跑 rag-031 / stale_version / effective_date。
5. 不重建生产业务数据语义，只填空字段。

## 7. 验证（Accepted 且实现后）

| 编号 | 场景 | 期望 |
|---|---|---|
| ED-01 | as-of=现在，问现行价格 | 命中 current，不命中 future，不命中 stale |
| ED-02 | 查询含 2027-01-01 的预告价 | 可命中 scheduled，上下文带未生效标记 |
| ED-03 | 无日期的「现在是否 249」 | 不命中 future，回答现行 199 |
| ED-04 | 跨租户 + 预告 | 仍不命中 |
| ED-05 | Flag=`off` | 行为与本 ADR 之前一致 |

## 8. 回滚

将 Flag 设回 `off`。不删除 `is_current`。不要求立刻回填日期字段。

## 9. 本阶段明确不做什么

- 不删除 `is_current`，不用独立 Reranker 猜测是否返回预告。
- 查询日期解析失败时不打开 scheduled。
- 资源级 ACL 仍为 Planned，与本 ADR 无关。
