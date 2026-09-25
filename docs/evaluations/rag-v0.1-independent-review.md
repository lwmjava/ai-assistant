# RAG v0.1 独立校验报告

> 候选集：`evals/datasets/rag-v0.1/cases.json`  
> 原始资料：`evals/fixtures/corpus/`（`corpus-0.1.0`）  
> 校验日期：2026-09-13  
> 方法：确定性程序校验 + 同会话二次通读  
> 限制：**生成与复核处于同一会话，不能代替另一模型或人工 Gold。** 按 `docs/ai-prompts/README.md`，最终裁决不得由同一生成会话完成。

## 1. 程序校验

命令：`python -c "from tests.eval.validation import validate_dataset; print(validate_dataset())"`

| 检查 | 结果 |
|---|---|
| Schema / case_id 唯一 | PASS（44 条） |
| exact_quote 可在声明 source 逐字定位 | PASS（0 missing） |
| version / effective_at / source 与 manifest 一致 | PASS |
| development / validation / holdout 齐全且无同问泄漏 | PASS（26/9/9） |
| 近重复（同身份同类别 Jaccard≥0.85） | PASS（0） |
| 敏感模式（私钥、云厂商 token、password=） | PASS |
| Gold 未在无审核人时标注 | PASS（`pending_human_review`） |

## 2. 逐类复核

每项仅给 PASS / REVISE / REJECT / HUMAN_REVIEW。

| 范围 | 判定 | 证据与理由 |
|---|---|---|
| rag-001–012 fact | PASS | 报价均可在现行文档定位；禁止点指向旧版/未生效价 |
| rag-013–016 multi_hop | PASS | 多证据均在原文；015 升级条件与退款审批同时成立 |
| rag-017–020 no_answer | PASS | 语料无港澳台发票、学生折扣、API QPS、俄语界面 |
| rag-021–023 conflict | PASS | 对客 24h/4h 与内部 4h/1h 原文冲突；期望升级而非静默选择 |
| rag-024–026 ambiguous | PASS | 缺套餐类型/用量/工单类型；026 同时禁止用内部备忘冒充对客承诺 |
| rag-027–029 stale_version | PASS | 现行 v2 报价存在；v1 的 99/599/2000 列为 forbidden |
| rag-030–032 effective_date | PASS | 249 元仅出现在 2027 预告；030 明确评测日 2026-09-13 |
| rag-033–035 cross_tenant | PASS | tenant-a 不得命中青禾文档；tenant-b 不得命中北风价格 |
| rag-036–038 private_resource | HUMAN_REVIEW | 资源 ACL 为 Planned（ADR-0001）。期望已写清，但实现尚未对齐 |
| rag-039–042 prompt_injection | PASS | 可引用打印机/耗材事实；禁止执行角色切换与 refund_tool |
| rag-043–044 citation_mismatch | PASS | 计费价不支持一律全额退；24h 是首次响应不是寄修完成 |

## 3. 统计

- 程序通过率：44/44
- 二次通读 PASS：41
- HUMAN_REVIEW：3（ACL Planned）
- REVISE / REJECT：0
- 重复项：0
- Gold：24（阿明，2026-09-13，`ACCEPT_GOLD`；rag-036 不得检索员工编号）

## 4. 文档覆盖缺口

语料是合成客服域，覆盖计费、退款、SLA、工时、升级、质保、跨租户、注入。  
未覆盖：真实工单、合同原文、OCR/扫描件、多页 PDF 页码。这些不是本版本 Gold 来源。

## 5. 推荐人工抽查

见 `docs/evaluations/rag-v0.1-gold-review-batch.md` 的 24 条。优先冲突、跨租户、注入、无答案和 ACL。

Gold 已由产品负责人在对话中批准，记录见 `rag-v0.1-gold-review-batch.md`。
