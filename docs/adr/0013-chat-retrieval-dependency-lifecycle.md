# ADR-0013 对话检索依赖按使用时机创建

日期：2026-10-18。状态：**Accepted**。任务 V1C-001。用户在本会话对计划/范围及本 ADR 的集中确认回复“按实施计划开始实现”，批准本卡方案。接受不代表实现或验收完成，也不授权生产启用、真实数据操作或付费模型调用。

## 背景与事实

ChatService 两入口在路由及管线 preflight 决策前构造 RAGService；该服务构造函数会调用 Embedding、VectorStore 和 backend 工厂。RAG-044 只将索引核验移至真正检索，不能防止其余工厂故障阻断不需要检索的请求。默认 self full、fast RAG、Supervisor 的当前检索策略和 tenant/uploader 读域不得被本次性能/可用性调整改写。

AGENTS.md §4/§12 要求改变 Harness/RAG 边界先有批准决定，原卡也要求实施前明确 allowed_paths 与必要 ADR。ADR-0010/0012 分别批准工具/状态与SSE执行所有权，没有决定本次检索依赖生命周期。

## 选项

1. 仅提前 fast 路由：不能覆盖默认 self preflight 短路，不充分。
2. **推荐：Service 层请求内惰性 Retriever 钩子**，直到实际 retrieve 才创建原 RAGService/HybridRetriever；不变已有路由或RAG摄取/管理路径。
3. RAGService 全面惰性化：会改变摄取、重建和控制面失败时机，范围过大，不采用。

## 提议决定

- 轻量钩子归 Service，绑定本请求已有 Session、主体与 tenant；不得全局缓存这些对象。Agent 仍只调用 Retriever 协议，不能直接访问 SQL。
- RAG关闭返回 None。启用时创建钩子不创建任何 Embedding/VectorStore/backend，也不执行索引操作；真正 retrieve 时至多尝试一次构造。成功后请求内复用，失败本轮不自动重试，新请求可独立再尝试。
- last_hits/last_selected/last_status/last_outcome 的读取与选块写回不能触发构造；真实检索沿用原结构化结果和选块来源，初始化失败清空结果并明确 unavailable，不能伪装 no_hit 或复用前请求来源。
- 取消继续传播。ChatService 在创建轻量钩子前同步验证 reader/read_scope 的 tenant 绑定并固定本轮主体/读域快照，不创建任何检索依赖；身份与读域校验不延迟到 retrieve。原API、会话权限、ActiveRun逐事件和regenerate前后身份检查保持原位置；guarded_chat仅负责互斥。本卡不新增普通非流式对话全程撤权策略。
- 三条 retrieve 消费者已有 broad except Exception；不能以“proxy抛权限异常”声称必然请求级拒绝。新的必要身份拒绝只能在上述Service受控边界、进入这些catch之前完成。若实现发现必须在retrieve内部引入新身份拒绝，则先修订方案及范围，显式解决三消费者错误传播，不静默按unavailable继续。
- 真正检索继续走原索引身份、受控父块、过滤、预算及来源逻辑，不改既有检索后权限过滤及错误分类。
- self full 仍按现有策略检索，self preflight 短路与 fast simple/tools 不检索；prepared/resume/regenerate 沿现有路径。暂不改 Supervisor 工具能力、工具优先路由、技能消费或组合任务策略。
- 不新增配置、供应商、数据库表、迁移、分布式缓存、后台任务或生产索引操作。API/SSE schema和前端行为约定不变，错误转入原受控不可用语义。

## 后果、兼容与风险

无关依赖故障不再阻断已确定不检索的请求；实际检索构造失败的可观察时机从入口移至 retrieval，故必须补异常分类/来源清空/持久化回查证据。last_selected 写回和调用后 metadata 是兼容风险，须用完整入口反例验证。请求内缓存不能跨租户、会话或运行复用；不改变默认 RAG_ENABLED/知识库读域。

普通依赖错误仅记录安全类型，不新增原文或凭据日志。取消不作为初始化故障；授权与主体/读域绑定验证保持同步Service边界；按具体既有异常类型审查，不用宽泛 except BaseException。成功Fixture建立真实有效User/Tenant，不删除拒绝断言。

## 实施范围、验证和回滚

具体拟申请路径及逐条验收见 [实施计划](../plans/plan_v1c_001_20261018.md)。批准后再登记原卡 allowed_paths 并补有效红测试，最小实现。真实 Service/API+隔离SQLite+合成provider覆盖 chat/stream、self preflight/full、fast三路、Supervisor、恢复/附件/重新生成、缓存/预算和调用用途。真实检索覆盖租户、父块、索引身份、来源、四种终态；不以Mock证明质量。

最终执行后端全量pytest、Ruff/mypy、独立入口级反例审查和最终人工审核；必要CI/人工接受缺失不done。回滚同时恢复ChatService与钩子，回到原eager构造；无数据迁移或删除，不操作重要索引。

## 批准记录

2026-10-18：主 Agent 提交完整实施计划、本 ADR 和具体 allowed_paths，并明确请求批准后继续；用户回复“按实施计划开始实现”。据此批准上述决定和范围，已登记 tasks.yaml。Git提交、推送、生产启用及真实数据/费用仍须相应授权。
