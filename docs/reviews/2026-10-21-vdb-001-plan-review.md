# VDB-001 准入与范围独立审查

日期：2026-10-21。审查 Agent：`/root/vdb_plan_review`，未参与业务实现。基线 HEAD：`ac494b386bccd00e5e0f66f1ec214664cdb3216d`。本报告是准入审查，不是实现验收；具体实施计划尚未落盘，不能宣称该计划已通过。

读取原任务卡、AGENTS.md、单卡交付技能与工程计划审查维度后，按架构、权限、兼容、失败路径、测试与性能执行等价工程审查；未运行 gstack 配置、遥测或安装。只读核对当前代码，未运行共享数据库测试。依赖审计 Agent 的领域反例由该 Agent 实跑，本报告不冒领其执行证据。

## 结论

**暂不通过实施准入。** Milvus 方向已批准，无需重选。当前前置路径仍有安全和索引激活缺口；部署检查与模型身份整改建议涉及三个未列入 `allowed_paths` 的文件，其中真实 provider 为静态风险、评测脚本为已复现缺陷，证据不得混用。应先明确扩围与前置缺口处理方案，再继续业务实现。任务不能按已完成依赖卡的状态直接准入，也不能以未来真实 Milvus 验收替代当前缺口修复。

## 当前代码发现

| 级别 / 置信度 | 当前证据 | 影响与要求 |
|---|---|---|
| P1 / 高 | `app/rag/vectorstore/milvus.py:370` 起，候选 ID 的 SQL 回查限制 index_id、文档可见性和可选上传者，未限制 `DocumentChunk.tenant_id` 与 `Document.tenant_id` | 远端候选成为唯一租户防线；错误候选可回查其他租户正文。切默认前必须二次校验租户以及文档与分块归属，补领域入口反例。该源文件已在范围内。 |
| P1 / 高 | `app/rag/index_registry.py:269` 起，激活探针直接从全局 engine 建 Session，用 SQL 中的向量做 NumPy 自匹配；不访问 Milvus | SQL 有向量不能证明远端写入完整、连接可用或目标索引可读；且忽略调用方 Session 绑定。激活成功不能作为真实 Milvus 闭环证据。修复设计须保持事务回退和失败拒绝。该源文件已在范围内。 |
| P1 / 高 | `app/rag/embeddings/openai_compatible.py:120` 起解析响应 data/usage，维度校验只比较长度；未核验响应 model | 同维错误模型响应可以按配置身份登记，削弱任务要求的索引身份校验。修复至少涉及此源文件，当前不在允许范围。响应模型缺省、别名和不匹配须先约定兼容行为，不能自行把未声明响应等同已验证身份。 |
| P1 / 高 | `scripts/rag_hybrid_embedding_check.py:83` 的 `embed` 核对请求配置，但 `:108` 只记录响应 model，随后验证向量形状与有限值后返回 | 依赖审计的模型不符反例实际调用此评测脚本，不是真实 provider。响应模型不符仍可进入评测，前置身份评测证据不可靠。若本卡闭合该前置反例，必须扩围此脚本；仅修改 provider 不会使此反例通过。 |
| P2 / 高 | `scripts/check_production_compose.py:31` 起要求默认只有 app/db、默认 local，`:98` 要求 Dockerfile 默认不安装 Milvus extra | 与默认 Milvus 的生产部署契约冲突。此检查脚本必须与新默认共同更新，不能删检查或保留虚假 local 断言；当前不在允许范围。 |

依赖审计向主 Agent 报告已运行五条失败反例：远端不可用仍激活、部分重建仍激活、忽略 Session 绑定、错误远端候选跨租户回查、评测脚本响应模型不符仍接受。第五条只证明 `scripts/rag_hybrid_embedding_check.py:embed` 的缺陷，不能当作 `OpenAICompatibleEmbedding` 的入口复现；provider 尚只有独立静态风险证据，需另补反例。其命令、退出码与原始输出应引用该审计报告；本报告的独立结论来自上述当前代码核查，不仅依据历史 Review。

## 必须明确的范围调整

1. 增加 `scripts/check_production_compose.py`：保留必填配置、内部端口、持久化、健康检查、资源限制等保护，同时使默认服务及依赖断言匹配批准的 Milvus 部署方案。
2. 增加 `app/rag/embeddings/openai_compatible.py`：响应身份核验位于该真实入口，不在 `index_identity.py` 单独增加配置字段即可解决。若需要修改公共异常契约，再依据具体设计列出真实文件；本轮不猜测扩展整个 embeddings 目录。
3. 增加 `scripts/rag_hybrid_embedding_check.py`：建议一并闭合已复现的前置评测身份缺陷；该脚本有独立 HTTP/预算/响应解析链路，不能通过修复 provider 间接宣称修复。复测需分别调用脚本与 provider 入口，保留模型缺省、别名、不符的独立结果。

`app/core/bootstrap_config.py` 未发现 local 默认硬编码；`app/cli_lifecycle.py` 默认加载单一生产 Compose 文件，但是否修改取决于最终 Compose 设计，**不列为目前已证明的必扩围文件**。若将 Milvus 纳入默认生产文件，现有 CLI 可以沿用；如仍依赖额外覆盖文件，则必须重新审查默认 CLI 的可运行链路。

## 原始任务逐项与失败边界

| 原始交付 / 验收 | 当前结论 | 恢复后必须提供的证据 |
|---|---|---|
| 默认实际 Milvus，未知值和故障不退 local | 未实施；factory 目前非 milvus 一律 local | 未设置、合法 Milvus、显式 local、未知值、SDK 缺失和连接失败逐项断言；配置/镜像/Compose 一致 |
| 空库首次摄取、查询、删除、重解析 | 未验收 | 独立测试库与真实 Standalone，逐入口读写结果；失败写入、部分完成、重试及清理边界，不只 Mock 绿灯 |
| 租户与索引身份 | 当前准入不通过 | 错误租户远端候选、文档/分块租户不一致、模型同维不符、索引身份不符、远端不可用及部分重建反例 |
| 非检索请求不受无关故障影响；检索 unavailable | 未验收 | 真实请求入口隔离；不能依靠远端空候选伪造 no_hit，不能全局启动强连阻断普通请求 |
| 混合候选窗口限制与回滚记录 | 尚待具体方案 | 记录远端候选窗口后再作 SQL 可见性过滤所致召回不足；不承诺 local/Milvus 排名或数据自动接管一致 |
| 最终版本检查 | 尚未通过本卡验收 | 任务相关回归、全量 pytest、ruff、mypy、适用 Linux/同 SHA CI；本报告不把未知或未跑结果记通过 |

非目标保持：不实现 Lite，不迁移或清库，不引入 Chroma、重排或查询改写，不改冻结 local 评测，不把 Mock 当检索质量证据。公共配置引发的测试默认变化应显式隔离 local 场景，禁止全局强制 local 掩盖新默认验收。

本轮业务代码未改。解除阻断后须审查落盘实施计划、逐条失败测试和最终输入指纹；当前无生产就绪或任务完成结论。

## 具体计划落盘后的只读复核补记

已读取 `docs/plans/plan_vdb_001_20261021.md`，并核对 VDB-001 当前仅标记 `blocked`。本补记不批准扩围，不解除准入阻断。

- 合同与非目标一致：默认 Milvus、空库生命周期、租户/身份、普通请求故障隔离、unavailable、回滚与最终门禁均保留；Lite、清库迁移、重排和冻结评测不在本卡实现。
- 五条反例映射准确：远端失联、部分重建、Session 绑定三项归于 index_registry 激活；跨租户候选归于 Milvus SQL 回查；模型响应不符归于独立评测脚本。provider 为静态风险、另待入口反例，没有混称已复现。
- 三文件扩围映射准确：生产默认契约检查对应 `scripts/check_production_compose.py`；已复现模型评测缺陷对应 `scripts/rag_hybrid_embedding_check.py`；provider 静态风险及待补反例对应 `app/rag/embeddings/openai_compatible.py`。计划明确路径尚未获准，未扩整个目录。
- 证据分级整体准确：真实 Milvus 运行证据才可作为 E1；真实业务入口配独立库和合成边界属于 E2；SQL/NumPy 自匹配不能替代 Milvus 验收，专项全绿不能覆盖五条失败。细分时 Settings/factory 的单元断言仍属 E3，必须与同一行要求的真实服务入口 E1/E2 配套，不能单独算整项通过；计划未声称其已通过。

该计划已足以清楚呈现阻断与待决策范围，尚不能作为可立即执行的批准方案。具体恢复实现仍须取得精确扩围授权，补正式失败测试，并按最终业务版本重新取证。
