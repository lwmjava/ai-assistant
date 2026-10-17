# 检索与 LLM 缓存独立计划评审

日期：2026-10-11。独立评审身份：`/root/cache_plan_review`；未参与业务实现。依据：原始 `tasks.yaml` RAG-019、`docs/plans/plan_rag_019_cache_20261011.md`、ADR-0001/0003/0008、当前调用方及 `task-card-delivery` 1.2.0。

本评审读取并使用 `plan-eng-review` 的架构、代码质量、测试与性能检查维度。单卡交付技能授权常规技术选择连续执行，不采用该通用技能的逐项交互问答、全局配置修改或仓库外日志写入流程。未调用其他模型，独立身份为宿主子 Agent。

## 结论与范围对账

范围保持原卡：检索命中减少向量调用、可安全复用的确定性生成减少 HTTP 调用、租户/有效授权域/数据/索引绑定、真实上下文和模型/Prompt 绑定，以及发布、删除、重解析、改权失效。默认关闭和保守准入不改变原始必交付项；实际入口必须能命中，不能仅以 helper 单测证明完成。

方案符合已批准 ADR-0008 的缓存方向；不需要新的平台、供应商或权限模型。无新增方向性审批阻塞。计划已纳入本评审提出的权限回查失败不得释放缓存、时间越界、流式取消/缺 DONE、响应模型身份、数值合法性、作用域失效和内存字节边界。可进入最小实现，最终关闭仍须独立入口反例、原卡验收证据和人工业务验收。

## 架构与数据流

```text
HTTP search / ChatService
  -> fresh subject + tenant + authorization/data/index snapshot
  -> actual Backend / Provider identity and option validation
  -> scoped key -> TTL/LRU lookup
       miss -> original backend / HTTP provider -> complete-response checks
       hit  -> fresh authorization + index checks
  -> fresh SQL row validation / normal budget and output safety
  -> original retrieval / response path
```

检索装饰器自身必须在返回缓存块前重新授权，而非只依靠父块展开：现有 `HybridRetriever._expand_parents` 遇到回查异常会退回原始子块，该兼容分支不能成为缓存越权通道。Provider 仅消费 Service 绑定的快照回调，不直接操作数据库，维持既有边界。FallbackChain 不存最终结果，防止主模型键复用兜底模型响应。

## 技术细化与实现检查点

1. **授权快照（confidence 9/10）**：主 Agent 已选择绑定原 Session 的同一 Engine、用新只读 Session 查询快照；未提交或脏事务绕过缓存。必须验证查询不触发原 Session 自动 flush、不以身份映射中旧对象证明当前改权；无持久化认证主体的开发模式绕过缓存。系统管理员对停用租户的既有权限例外保持兼容，不新增授权。
2. **时间与空结果（confidence 9/10）**：可见状态指纹须随 effective_at/expiry 和查询日期窗口变化，不能只绑定原始时间字段或 TTL。覆盖生效前空结果在生效后重新查询、scheduled 预告随日期窗口改变，以及过期旧命中。
3. **并发与作用域（confidence 8/10）**：检索和 HTTP 在等待期间快照变化时不得写入；缓存流每次输出重新校验，请求结束失效作用域。异步反思继承 ContextVar 时也不得保留可用请求租约。取消和 aclose 必须清理。
4. **身份与参数（confidence 9/10）**：实际后端实现、数据库、provider/端点/凭据摘要及索引身份均绑定；注入或可变 provider 的旧身份缓存不得掩盖新身份不匹配。max_tokens/timeout/temperature 非法时不能命中；temperature=0 不替代响应身份和授权校验。流式必须正常完成、身份一致、stop 且 DONE，错误/截断不得缓存。
5. **内存与成本（confidence 8/10）**：条数加总字节预算限制大上下文占用；复制缓存值防污染。完整文档/分块快照是保守但可能较重的 SQL 读成本，缓存减少向量/HTTP 次数不自动等于端到端延迟下降。报告调用次数即可，不未经真实环境测量宣称上线性能改善；不新增外部缓存或并发合并要求。

上述是实现检查点，已由计划及主 Agent 方案承接；不是已证实业务缺陷。最终代码评审需要检查实现是否实际满足。

## 失败路径与测试矩阵

| 路径 | 反例及期望 | 证据要求 |
|---|---|---|
| 检索命中 | 重复 search / make_retriever，减少实际 LocalVectorStore 调用且内容相同 | 独立库+合成 Embedding E2 |
| 授权/绑定 | tenant/uploader、角色或禁用变化、新 DB、新索引、实际后端变化 | 真实提交后新会话 E2 |
| 生命周期 | 发布/软删/重解析/内容变化、空结果到生效、expiry、未来查询窗口变化 | 实际 Service 入口 E2 |
| 缓存回查 | SQL 回查失败不得退回未授权缓存；dirty Session 不缓存 | 隔离故障注入 E2 |
| LLM 命中 | 重复实际 Provider/ChatService、隔离 HTTP 服务计数、temperature>0 不命中 | E2，非实际供应商计费证明 |
| LLM 故障 | 超时、模型漂移、截断、取消、无 DONE、变化发生于等待/输出间 | E2，重试重新调用 |
| 数值/回滚 | NaN/Infinity/极端参数、开关关闭、独立副本、TTL/LRU/字节容量 | 入口 E2 + 原语 E3 |

计划列明的五类失败边界均有对应测试要求；实现前无未处理的关键失败边界。未执行最终代码反例，不能将此计划结论作为最终关闭证据。

## 前置卡当前 HEAD 复核

基线 HEAD：`7709d7bdb67290726f3e66200fab642577f23704`。

- RAG-026 历史独立报告的 HTTP 检索 F-1 后续已扩范围修复；当前入口显式传 `reader=current_user`，无主体默认语义已明确。历史静态/运行报告不代替当前验证。
- RAG-032 的 H-R1/M-R1/M-R2/M-R4 后续修复见 `implementation_rag_032_review_fix_20261008.md`，当前 probe 使用独立只读 Session，非共享主事务；未发现仍未修复的前置 P1。Milvus probe 的历史限制由后续 RAG-015 承接，缓存不声称 Milvus 为 Implemented。
- 主 Agent 当前 HEAD 依赖选择实跑：`data/rag019-deps-20261011.log`，尾部 `110 passed in 21.92s`，退出码 0 由执行者记录。本 Agent 阅读日志和当前源码，未再次运行共享 DB 测试。

关键源码 SHA256（实施前）：

| 文件 | SHA256 |
|---|---|
| app/rag/access.py | 9CC445B106EEA52BA24FD0306E3C9CC53EAAEA93BC705B9A1F2F4545C6F0091F |
| app/rag/index_registry.py | B47A623AE34A8DD0486C0C8B9DB468FCC9C7A6AA6F991BE3AA4245B6910033AC |
| app/rag/index_identity.py | 67A3079F24BE6E2F92BD43BD36E935CC4832FD47859D0F340036D967861E449D |
| app/rag/vectorstore/local.py | 60D92C9559CB44717A4CDE68DE8567690945848B2E84AF03A2FF78B4DF4779DC |
| app/rag/vectorstore/milvus.py | 66BEF32FECA14F8E1B249E171DDB0285C80BF5DE11FAE20F0B4C932D2C2A9C48 |
| app/rag/service.py | BD5DD7197A9C59CDBDE41A6C900713DD9D8A0C4FA6DF5573EB47CFE7C5B1CD59 |
| app/api/routes/rag.py | 6B458292F22AAD80B28FA4073634E4C237920DF530D7B299BED26396701BDA59 |

## 分工与完成门禁

检索实现与 Provider 实现可并行，但授权快照、配置、文档由主 Agent 单独整合；共享 DB 验证串行。计划评审身份独立于实现者。代码完成后再由独立 Agent 执行入口反例并记录结果。无仓库外日志写入、无新模型外发、无收费真实模型调用、无生产数据或默认库改动。

Step 0：保持原卡范围。架构、代码质量、测试、性能均完成计划维度检查；5 项技术细化已纳入方案，无未决方向性决定。未自动修改任务状态，未将计划通过等同业务验收通过。
