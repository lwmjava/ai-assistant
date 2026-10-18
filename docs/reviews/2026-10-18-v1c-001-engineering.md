# V1C-001 独立工程准入与计划审查

日期：2026-10-18。审查身份：`/root/v1c001_engineering`，未参与业务实现，不兼任最终人工审核。基线 HEAD `f350e94b3829a0d48f9a0cfce467c01bae5dd662`，分支 `codex/v1c-001-lazy-retrieval`。

## 依据与范围

实际读取 AGENTS.md、基础必读、适用 Cursor global/architecture/runtime/RAG/testing/brownfield 规则、tasks.yaml 原卡、收尾拆分计划、ADR README、RAG-044 计划/实现说明/10-18 页面报告、相关真实代码，以及 `task-card-delivery`、`plan-eng-review` 技能。按工程审查技能的范围、架构、质量、测试、性能和失败模式维度执行；因本次角色仅授权仓库内独立审查，未执行其全局配置写入、遥测、外部同步、Git发布及反复交互环节，未宣称这些操作完成。

审查计划：`docs/plans/plan_v1c_001_20261018.md`，初审 SHA256 `0048b00bbd0a8fca23cacb9017a8e67e2907ca01d2c883fa4fb952af0cb1c747`。提议 ADR：`docs/adr/0013-chat-retrieval-dependency-lifecycle.md`，初审 SHA256 `44c7920db37c3f1769e5c7e2bc8c9ef3c4c5f8a6967aecad93406aa04474140d`。

## 前置卡证据复核

独立执行 `Get-FileHash app/rag/service.py,tests/test_rag_index_init.py -Algorithm SHA256`（exit 0），与 `data/index-init-inputs.json` 相符：service `df1bf2f720eec05613cc604a09c0022c5a054fbecd2bd222e02948a37c1628e6`；test `d20af9928c7be76d09d67d5ecf6c2f3920bc8eda955e2697e92c1b5cb6bb62fa`。

RAG-044 旧实现说明仍记录旧候选全量失败/in_review；原卡与新页面报告记录后续验收/CI。此为历史证据时点差异，不把旧失败写成当前失败，也不以 done 字段替代复核。主 Agent 报告当前指定解释器专项 60 passed / 1 warning、exit 0，包含 RAG-044/状态/父块回归；本审查者未重复运行 pytest，避免共享测试库竞争。旧页面与 CI 属于历史证据，未独立重新查询远程 CI，不能充作本卡最终代码验收。

## 范围与方案判断

接受原范围：仅把对话路径 RAG 依赖构造延至真实 retrieve，不调整默认检索策略。已有 self preflight direct 分支本来跳过检索，langgraph simple/tools 同样不使用检索器；Service 内请求级钩子足以覆盖这些路径。保留 RAGService 的摄取/管理 eager 生命周期，比全面惰性化更小、更易回滚。无需新增供应商、表、依赖或配置开关。ADR-0013 和 allowed_paths 尚待批准；AGENTS §4/§12 与原卡/拆分计划明确要求，实施准入尚未放行。

```text
Service 身份/输入校验 → 轻量 Retriever 钩子
  → self preflight direct / fast simple-tools → 无依赖构造
  → full / fast RAG / Supervisor retrieve
      → 首次创建原 RAGService → 原 HybridRetriever
      → 原索引/读域/父块/阈值治理 → 按预算选块 → last_selected 来源
      → 普通依赖故障 → unavailable，清空本轮证据
```

## 问题与推荐

1. **[P2，待澄清] 权限异常重抛不等于请求拒绝。** 计划要求失效主体/跨租户/权限绑定错误不转普通不可用，但 `AgentPipeline._fill_retrieval`、fast RAG 与 `SupervisorGraph._retrieve` 的外层 `except Exception` 已将 Retriever 异常收敛为 unavailable。仅在惰性钩子内重新抛出普通 Exception 子类仍会被它们吞掉。建议明确把必要身份/租户快照校验留在 ChatService 已有受控边界，在进入检索消费边界前拒绝；不把安全检查一起惰性化。若选择检索内部新权限异常传播，则必须说明三调用方的显式重新抛出方案及扩展 allowed_paths，不能让实现者暗改范围。
2. **元数据兼容风险已覆盖。** `assemble_retrieval` 通过 `setattr(last_selected)` 写回；只做 `__getattr__` 转发会产生两个不同状态。计划现已明确读写不得触发构造、选块输出为权威；推荐显式字段与同步，不引入通用魔法代理。实际测试需证明 selected 与来源一致，不能只测构造计数。
3. **初始化幂等/取消风险已覆盖。** 计划明确同请求只尝试一次，取消传播，新请求可恢复。实现时取消继承 BaseException 的事实不等于所有生命周期正确，仍需真实入口取消反例与持久化终态检查。

## 测试、质量和性能

计划矩阵覆盖两个入口、self direct/full、fast 三路、Supervisor/fallback、resume/附件/regenerate、缓存/调用用途；这与原卡要求相符，不把新增复杂组合任务纳入本卡。失败边界五类均已列出，仍需补有效业务红测试与 E2 真实 Service/API 隔离集成证据。NaN/Inf 等只验证既有治理兼容，不借机改检索算法。实际检索保持父块、索引身份、tenant/uploader、no_hit/below_threshold/unavailable 和来源预算。

元数据读取不得调用工厂；每请求最多构造一次，不添加额外模型决策，作为可检验的性能约束足够，本卡没有新增真实检索质量或延迟达标门槛。公共 ChatService 变更触发最终全量 pytest、Ruff、mypy；最终输入变化后旧证据失效。前端/API契约不变时前端门禁可说明不适用，不能以历史 CI 关闭最终候选。

顺序实施，无独立可并行的业务改动流；验证/独立审查可在候选稳定后串行安排隔离库。不存在需要现在添加的范围外 TODO。

## 审查结论

范围保持；架构/代码质量/性能方案可接受，测试矩阵齐备。初审 1 项 P2 经下列修订复核已清零，工程方案审查通过；ADR/allowed_paths 用户批准门禁仍待决。此报告不是业务验收、最终独立入口反例审查或人工审核结论；未执行 pytest、未改业务代码、未读取 .env、未提交/推送。

## 修订复核

主 Agent 已将主体/读域与租户绑定验证留在 ChatService 创建轻量钩子之前，并明确固定本轮快照、零检索工厂调用，处于三消费者的异常捕获边界之外。延迟的是 provider/store/backend，不能借 proxy 内重抛普通权限异常声称请求级拒绝。原 API、ActiveRun、重新生成保障保持原位置；guarded_chat 只负责互斥，普通非流式请求不新增全程撤权策略。失败矩阵同步匹配这些边界。若实现中必须新增 retrieve 内身份拒绝，需先修订方案/范围，不能以 unavailable 静默替代。

上述修订明确了可实现的安全边界，清除问题 1；问题 2/3 是已纳入计划的实现风险，并非未决缺陷。复核命令 `rg -n` 检查两文档身份相关条款、`Get-FileHash`、`git diff --check` 均 exit 0。最终复核指纹：计划 `b9e31fce79798954d4217e71995282a598732ba7c6351d9d2e322855b02800da`；ADR `54ac3f4164ddd50317927cfa892d192669ac3ada1579bf89ed46470cb5e48ee3`。

## 实施后只读工程复核

用户后续批准按计划实施后，主 Agent 请求本审查者只读复核业务 diff；不承担独立入口实跑或最终人工审核角色。读取完整 ChatService 业务 diff、新增 ChatRetriever、正式与独立测试文件、RAGService 构造/缓存/父块接线、CachedRagBackend 和 AuthorizedCacheScope；本轮未运行 pytest，验证 Agent 正独占运行。

候选源码指纹：`app/services/chat_service.py` 为 `e4ea4e55291152169a38beaeb49f9e971ec52ef424ff1499212585844cc708a9`；`app/services/chat_retrieval.py` 为 `91946079fff339933f841927a5a8eb0aeacaa70fba16b07808d59b2672865e9d`。

静态检查未发现新的 P1/P2：

- 主体复制只包含必要身份字段，密码置空，无 ORM 关系对象；read_scope 在钩子建立前校验，延迟工厂使用固定 tenant/reader，随后以原快照覆盖服务重新推导的 read_scope。缓存授权仍持有该 reader 副本，继续经独立会话复核真实主体，不将原 ORM 对象后续变化带入检索。
- 元数据读取/写回不调用工厂；proxy 自持 last_selected，兼容共享选块入口的 setattr 与 ChatService 来源输出。每次 retrieve 先清旧状态，初始化失败清空命中与选块，保持 unavailable；首次构造失败缓存类名，重复调用不触发构造风暴，新请求不共享实例。
- CancelledError 不属于 Exception，依赖构造和实际 retrieve 两处均保持取消传播；普通失败日志仅记录异常类型。实际 HybridRetriever、父块、索引和阈值逻辑未改，摄取与管理路径未改。

上述仅为源码/计划一致性判断，不替代 E2 业务证据。最终关闭仍须将数值/低分、初始化后新请求恢复、取消前零工厂调用、resume/regenerate、缓存及各入口逐项映射到当前候选实际验证，不能以历史全量数量推导覆盖。候选继续变化时本段指纹需重新核对。工程源码审查通过，业务验收与最终门禁由验证/独立审核角色另行给出。
