# 模型任务配置与路由工程审查

审查者：Codex 子 Agent `/root/inst_review`。只读代码与契约审查；未实施业务代码、未运行测试、未访问外网或读取工作区 `.env`。结论针对本批工作区当前实现，不作为最终 diff 审查或关闭证据。

## 原始契约与事实

对照 `tasks.yaml` 的 LLM-001、需求 P0-8 / §5.1.7、1.0 锁定计划及 ADR-0008：必须配置 chat、intent、embedding、rerank 与兜底链，拒绝未知任务，熔断后降级；规划、执行、质量门复用 chat；至少两家提供商切换后实际走所选一家。四用途已批准，不重开五阶段模型决策；PRAG-002 仍不可启动，不能借配置工作启用未采纳重排。

| 代码事实 | 影响 |
|---|---|
| `app/llm/factory.py` 仅接受 chat/intent；配置 profile 只有 chat/intent/fallback | 需要明确四任务登记；fallback 是配置档，不是第五种任务 |
| `routing.py` 每次请求重新尝试主节点；无跨调用失败计数、冷却或半开探测 | 当前是逐请求 failover，不能宣称已有熔断 |
| `ChatService.llm` property 每次构建 provider；factory 单跳直接返回兼容客户端 | 熔断状态不能仅放在临时 chain 实例；单跳也应覆盖保护 |
| `is_failover_error` 仅网络/超时、401/403/408/429/5xx 降级；400、预算错误不降级 | 保留此分类；不能将能力/预算拒绝和取消当熔断信号 |
| 流式路径首 token 前可换家，已输出后异常固定失败 | 必须保留，不能在同一回答拼接两家输出 |
| `app/rag/embeddings/factory.py` 已按独立 EMBEDDING_* 返回 EmbeddingProvider | embedding 已有真实类型与调用路径，不能塞进 chat/completions |
| 索引身份包含 provider/model/deployment/dim/version/normalization/metric | 同维仍可能不兼容；变更 base_url 也可能改变身份 |
| Embedding HTTP 层已有 deadline、有限 retry、输入限制和维度校验 | 不再叠无界 retry；不得用兜底换模型绕过身份/预算 |
| 无重排执行接口或 rerank provider，当前检索路径没有独立重排 | 配置档可以登记，不能伪造已接入或已启用检索重排 |
| pipeline 已接收 chat 与 intent 两客户端，其余阶段调用 self.llm | 无需为规划/执行/质量门新增模型档或改 Agent 边界 |

## 最小实施建议

1. 在 `app/llm/` 增加任务配置描述/解析器，明确枚举四用途，未知用途失败。描述至少含接口类型、provider、model、endpoint、timeout、profile/fallback 名称；密钥不进入日志、公开结果或配置报告。chat/intent 保持现有环境键兼容；embedding 描述直接映射现有 EMBEDDING_*，不复制另一套 LLM_EMBEDDING_*。rerank 使用独立空默认配置并标识未启用，不默认回退 chat。
2. 对外明确区分“解析四任务配置”与“获取可执行生成客户端”。保留 `get_llm_provider()` 的生成接口，不把 EmbeddingProvider 强转成 LLMProvider，也不提供将 embedding 文本发到 chat/completions 的兼容捷径。嵌入仍由既有 RAG factory 执行；rerank 档只供未来批准的重排入口使用。若将“配齐”解释为必须提供可调用 rerank 协议适配，需先明确协议/供应商和本卡边界，不可仅以空配置声称执行能力完成。
3. chat/intent 兜底链继续只接受兼容生成 profile；链顺序去重并显式拒绝非法 profile，禁止静默丢弃拼写错误后落到别家。配置校验不要放宽模型能力护栏。若支持 default profile，应明确定义它为既有 chat 主档的兼容别名，不能让 embedding/rerank 落到生成 default。
4. 在 `routing.py` 或小型同层模块实现有界进程熔断登记，键绑定实际 task/profile、端点、模型、凭据摘要与相关配置；失败阈值、冷却期采用有校验的配置。状态不能因 factory 重建消失；成功复位、开路跳过、冷却后仅一条并发探测、取消不计故障。不要持锁跨 await；测试使用可控单调时钟，不 sleep。阈值与冷却不是检索质量门槛，可作为有说明的实现选择，不需要重开方向裁决。
5. 单跳也要受熔断保护，但可保持现有客户端类型兼容，或明确调整原 `test_llm_route.py` 的类型断言，保留模型/endpoint/调用结果的有效业务断言。多链流式不改变首 token 后禁止换家的现有约定。缓存仍按实际 endpoint/model/凭据摘要/请求及能力绑定，不跨任务共享响应或弱化权限重核。
6. embedding 默认只有当前已登记索引的唯一 provider，没有自动跨模型/端点兜底。已知身份不兼容必须在发请求/查询前失败，仍由已有索引治理实现显式新建/重建/切换。若本卡要为 embedding 真正实现多跳，只能对完整身份相同的候选，且还需处理 wrapper 暴露身份、输入策略、deadline 和缓存；这会涉及 RAG 范围补充，建议本卡不做。

## 范围与需裁决事项

现有 allowed_paths 足以完成四任务配置描述、chat/intent 熔断与路由，以及配置/文档/测试。为了“所有任务登记在一个解析入口”本身，**不必修改 `app/rag/`**：解析器使用现有 EMBEDDING_* 描述即可，现有嵌入执行已消费相同设置。

若要求真实 RAG factory 也调用新描述解析器，最小额外文件仅 `app/rag/embeddings/factory.py`；这是范围授权事项，不应借此允许整个 rag。当前未获此路径授权，不能实施。`app/rag/index_identity.py`、`index_registry.py`、检索器、服务和 chunking 均无本卡必要改动；不能为了降级成功修改身份规则。

下列缺口需要用户提供具体决定或外部证据，不能用测试数量替代：

- P0-8 原文还包括请求显式 model_profile、租户可用性及 YAML/default profile；本卡问题与锁定计划聚焦四任务配置和兜底。若要求 P0-8 全文所有规则一并落地，将涉及 API/Schema/租户授权等范围外文件；应明确本卡边界，不默默遗漏或扩范围。已有四任务和五阶段不拆档决定无需重问。
- rerank 的配置登记与未启用状态必须如实写明；如果验收要求实际 rerank 调用，须确定已批准协议/模型能力以及是否仍不接检索主链。不能以 LLM chat 当原生 rerank，不启用被否决候选。
- 至少两家真实提供商可切换需要可用且授权的 endpoint/model/凭据和外发/费用边界。本次不从工作区 `.env` 发现凭据，也不擅自外发。两个隔离 HTTP 模拟端点可以证明请求路由、流式、熔断和凭据隔离，不能冒充两家真实提供商上线证据。

## 独立验证建议

红测应证明四任务解析缺失与真实熔断缺失；不得把导入不存在当行为红。覆盖未知 task/profile、chat/intent 独立配置、规划/执行/质量门仍用 chat、两 endpoint 实际请求命中、开路跳过与冷却半开并发、请求取消、400/预算/输入策略不重试、流式中途失败不拼接、全链不可用固定脱敏错误、凭据/配置变更清理或隔离熔断状态、缓存键与调用预算保持。

embedding 需以既有真实类型断言证明不调用 chat/completions、索引同维异模型/部署拒绝规则仍有效；不要修改冻结 holdout。rerank 配置不导致检索路径新增调用、不改变已否决实验结论。保存 Evaluation Smoke/Observed Regression 与模型路由配置版本，AI 数据不标 Gold。

改动触发后端全量 pytest、Ruff、mypy；相关最终输入变化后重新绑定证据。两家真实提供商及必要外部验证未满足时保持 in_review/blocked，不以 Mock 全绿关闭任务。
