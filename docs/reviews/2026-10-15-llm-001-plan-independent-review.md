# 四用途模型配置与熔断独立工程审查

日期：2026-10-15。审查者：Codex 独立子 Agent `/root/inst_validation`，分派者 `/root`。本报告独立于 `2026-10-15-llm-001-engineering.md`，未覆盖该文件。审查者只读计划、原始任务、产品/锁定条款、ADR 和真实调用边界；没有改业务或测试，没有运行 pytest，也不改变主 Agent 正在执行的全量验证输入。

使用 `code-review-quality` 做等价工程审查，按本批交付技能不凑发现数。原始契约为 tasks.yaml LLM-001、产品 P0-8 与 §5.1.7 的四用途决定、1.0 锁定阶段 3；同时核对 ADR-0005 的逐实际 payload 预算、ADR-0008 的索引身份/缓存和已批准“当前独立重排不采纳”的进入条件。

## 结论

计划可在当前授权范围内实施，未弱化本卡原始验收。四用途配置登记与生成客户端执行边界分清；Embedding 保留已生效配置和身份；rerank 登记独立档且不启用检索符合已批准边界。两家真实提供商切换仍是必验，计划没有用两个 stub 取代它。可先实现和验证获授权的确定性部分，缺真实提供商证据时保持 in_review/blocked，不声明整卡完成。

进程内、有界、位于既有 app/llm 调用边界的熔断属于本卡明示“熔断后走兜底链”的实现细化，不改变 AgentState、Harness、RAG 或权限边界，当前不需要仅为内部计数器再开 ADR。若扩为跨实例外部状态、改变 Agent 执行所有权、给 Embedding 增加跨模型/端点多跳、改索引身份或租户 profile 权限，则需要新的范围/架构决定。已有 ADR 不授权这些扩展。

## 原始契约核对

| 要求 | 计划及真实现状 | 审查结论 |
|---|---|---|
| chat/intent/embedding/rerank 与 fallback | 生成 factory 当前只有 chat/intent/fallback；Embedding 有真实独立 factory。新描述映射现有字段而非新增不生效的 LLM_EMBEDDING 配置。 | 合理。四用途解析接口不等于四种对象都支持 chat/completions；未知用途及不兼容执行用途必须明确拒绝。 |
| 规划、执行、质量门用 chat | Pipeline 非 intent 阶段复用已有 self.llm；计划不新增阶段模型档。 | 保留原要求，不引入新的 Agent 边界。 |
| 重排独立配置 | 计划新增 provider/base/model/key 档，默认未启用，不调用未经批准协议，不改变 RAG 候选。 | “配置登记”只能写配置能力，不能写 rerank 已执行/质量提升；实际重排进入仍受已有不采纳决定约束。 |
| 熔断后走兜底 | factory 每次重建客户端，现有路由仅请求内换家；计划用共享有界状态、三次瞬时失败/30 秒冷却、半开单探测，单跳也包装。 | 覆盖真实缺口；不能把临时对象计数作为跨请求熔断。默认数值是实施策略，不是未批准质量指标。 |
| 取消、首 token 后失败 | 当前路由已禁止输出后换家；计划保留并不吞 CancelledError。 | 必须覆盖生成器正常耗尽、取消/关闭和半开名额释放，见下文。 |
| 至少两个提供商实际切换 | 计划保留真实 endpoint/model/凭据和费用边界，离线两个端点仅证明路由。 | 不弱化验收；未经授权不读取工作区 .env、不自行真实外发，证据缺失不关闭。 |
| P0-8 的较宽正文 | P0-8 还含显式 model_profile/租户校验/YAML/default；本卡问题、锁定标准和范围集中在四用途与 fallback。 | 本卡不擅自扩展 API/Schema/租户模型；交付说明要明确未覆盖 P0-8 全文所有产品能力。不能把本卡完成写成整个 ModelRouter 产品完整实现。 |

## 实施前应补足的技术条件

以下是本卡范围内实现细化，不是要求新的用户方向审批，也不能作为降低验收的例外：

1. **半开流式生命周期**：只有完整正常耗尽才能成功关闭熔断。首 token 不是整次成功；CancelledError、消费者 aclose/GeneratorExit、异常或提前停止都要在 finally 释放探测占用，不计瞬时故障。不能让唯一半开名额永久占住。
2. **在途请求与状态代际**：较旧请求成功/失败不能无条件覆盖已打开或更新的半开状态。用探测 lease/epoch 或等价比较保证状态迁移一致，含配置切换、TTL/LRU 淘汰及阈值前并发请求。锁仅围绕本地短状态操作，不能跨 await 持有。
3. **单跳包装与缓存/能力兼容**：现有单跳直接返回 OpenAICompatibleProvider。改成 wrapper 后审查依赖实际 endpoint/model/凭据摘要/能力的缓存与预算调用方，不能失去这些身份后复用首跳响应。每个实际 hop 继续由自身能力执行 payload 预算核验；预算/输入策略拒绝不换家、不计瞬时失败。
4. **失败分类保持分离**：现有 401/403 可换家的配置错误行为可保留，但不积累瞬时熔断；本地权限拒绝、400、预算、安全错误及取消不作为瞬时故障。开路跳过不是新的供应商请求，不增加模型调用预算；冷却时钟用单调时间。成功/非瞬时错误的失败计数重置语义需写清，不靠测试随意调总次数。
5. **配置与身份**：未知 task/profile 不能静默丢弃后落到别家；有界 key 绑定实际配置身份并只存凭据摘要。Embedding 不自动 fallback，不改变 provider/model/deployment/dim/index；rerank 档存在也不产生检索调用。配置对象 repr 与报告不含密钥或原始连接串。

建议确定性验证从原契约独立构造：跨多个 get_llm_provider 实例连续失败、开路跳过、冷却竞争只一探测、半开取消/流关闭后恢复、首 token 后异常不拼接另一家、单跳熔断、未知用途/profile、凭据/端点切换隔离、全链不可用固定脱敏错误、缓存/预算/重试和调用次数按用途保持。嵌入/索引与重排不新增调用亦应有拒绝或零副作用证据。以上未执行，不是已通过测试列表。

## 实际操作与指纹

审查者仅使用 read/diff/rg/Get-FileHash 读取；没有测试、模型请求、费用、索引切换或生产副作用。基线 HEAD `873db9c0fb243b0847b4be57242c8f333f4f0b1e`，未提交分支 `codex/platform-batch-20261015`。本次只读输入 SHA256：

```text
docs/plans/plan_llm_001_20261015.md 258e0e3304f9ff8c89945892793c6eaed9f1a00514a250bb5398e56fe5ef1d8d
app/llm/base.py 59dadefed1a500ffcc7b016defac4f1123ca8479d34820dde471e3fc55f478a5
app/llm/factory.py 09c662fa0ba3a3c30d0722f505fd518ed94538959e8161f3adfa4309c56c2b86
app/llm/routing.py d8a8cca7b079f713d75960b04a28808cadc8ecbf1ae90160e466346c72fad71a
docs/adr/0008-embedding-index-identity-and-switching.md 4c4fed822c18c01a7e6602893a251ba58db6d0bff95f33a9e411802bd322bcba
```

这是实施前工程审查，不是最终源码审查或验收。实现后仍需独立最终 diff/调用方审查、确定性 Evaluation、Ruff/mypy、最终全量 pytest，以及两家真实提供商切换证据；本报告不关闭任务，不授予费用/凭据/外发或范围外架构授权。
