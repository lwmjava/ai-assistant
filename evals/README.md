# evals

> 状态：`RAG-004` 已建立 Schema、合成数据集和 **Gold v0.1（24 条）**；
> `RAG-005` 已产出 **首份真实嵌入检索基线（2026-09-19）**
> 更新日期：2026-09-20

## 当前资产

| 路径 | 说明 |
|---|---|
| `evals/schemas/rag_case.schema.json` | Case JSON Schema |
| `evals/fixtures/corpus/` | 授权合成语料 `corpus-0.1.0`（13 篇 / 37 分块） |
| `evals/datasets/rag-v0.1/cases.json` | 44 条；其中 24 条 Gold v0.1 |
| `evals/datasets/rag-v0.1/index.json` | 版本、划分和 Gold 状态 |
| `evals/reports/` | 基线运行的机器可读报告 |
| `tests/eval/validation.py` | Schema/引用/重复/敏感扫描 |
| `tests/eval/metrics.py` | Recall/MRR/nDCG/分位数/引用命中纯函数 |
| `tests/eval/harness.py` | 语料摄取进隔离索引并跑真实检索链路 |
| `scripts/run_rag_baseline.py` | 基线运行器 |

## 划分

- development：26
- validation：9
- holdout：9（`RAG-005` 基线已运行一次，此后不得用于调参）

## 运行

```powershell
python scripts/run_rag_baseline.py --mode official   # 真实嵌入，正式基线
python scripts/run_rag_baseline.py --mode smoke      # Mock 嵌入，仅验链路
pytest tests/eval/ -v                                # 数据集校验 + 指标 + 夹具冒烟
```

正式模式在解析到 Mock 嵌入或 `RAG_VECTOR_STORE != local` 时直接退出，不产出报告。
评测索引写在 `data/eval_rag_v01.db`（每次重建，已 gitignore），不污染开发库与测试库。

## 约束

- AI 生成数据默认是 Silver。Gold v0.1 已由阿明于 2026-09-13 批准 24 条。
- 未授权生产数据和密钥不得进入本目录。当前语料全部合成。
- Mock 嵌入只能证明链路，不得用于报告检索质量。
- 当前基线的有效性限制（语料规模、未测生成层）见
  `docs/evaluations/rag-v0.1-baseline-report.md` 第 6、7 节，引用分数时必须一并说明。
- 后续优化遵守单变量原则：一次只改 Chunking、Embedding 或 Rerank 中的一项，并与本基线对比。
- 2026-09-19 A1：同租户当前文档被检索不记越权；资源 ACL（`acl_planned`）只约束生成层答案点。
- ADR-0003 已 Accepted（选项 A）。`RAG_EFFECTIVE_DATE_FILTER` 默认关闭；打开后带未来日期的查询才检索 scheduled 预告。
- 不得改写 `evals/reports/rag-v0.1-baseline-20260919.json`。
- `RAG-006` 读路径默认 `RAG_KB_SCOPE=tenant`。脚本化 LLM 已覆盖注入文档拒工具；真实 LLM 拒答/Citation/`forbidden_answer_points` 未测。

## Agent 链路

| 路径 | 说明 |
|---|---|
| `evals/schemas/agent_chain_case.schema.json` | 多轮分流用例契约 |
| `evals/datasets/agent-chain-v0.1/` | 冻结历史 0.1.0，保留原始用例和失败证据 |
| `evals/datasets/agent-chain-v0.1.1/` | 当前 10 条 Silver 用例；补齐 agent-004 的前序代码，不是 Gold |
| `scripts/run_agent_chain_eval.py` | 冒烟模式验证链路；正式模式调用真实模型做分流和工具选择 |

```powershell
python scripts/run_agent_chain_eval.py --mode smoke
python scripts/run_agent_chain_eval.py --mode official
```

正式模式不创建会话，不连接检索、真实沙箱、MCP 或业务数据库。行动请求复用运行时 Prompt 和白名单工具 Schema，模型提议通过参数校验后只执行内存 recording fake。报告写入新文件，拒绝覆盖已有报告。冒烟结果不能当作分流或工具调用质量；official 的模型 HTTP 请求本身有网络活动，兼容 side_effects 字段不是动态观测计数。

### Observed Regression：agt003-empty-action-v1（2026-10-13）

`tests/test_empty_action_reply.py` 保存 17 项确定性空行动失败用例：同步/流式、短路/文本兼容、空串/空白、Skill 注入、预算收尾、工具结果保留、正常无工具及结构化错误兼容。包括隔离 SQLite checkpoint 的新会话回读，失败必须保存为 error。使用脚本化 LLM 和记录工具，无真实模型、网络或沙箱执行。对应最终专项组合 240 passed / exit 0，命令与红绿记录见 `docs/plans/implementation_agt_003.md`。该版本仅证明失败处理链路，不改变冻结的 agent-chain-v0.1 数据、指标或阈值，不代表真实模型质量提升。

### 输入修复：agent-chain 0.1.1（2026-10-13）

用户批准新增版本并将正式 evaluator/独立 gate 同步切换到 0.1.1。仅 agent-004 增加生成请求与完整冒泡排序代码两轮历史，其余案例语义、期待值、10 例分布和 0.9/1.0/1.0 阈值不变。assistant 代码消息 240 字，适配已有较早历史 300 字上限。新 cases/index 的规范化 SHA-256 分别为 `77ff27c8c67c68afb55eed51384e20dd07171b1d047de6cde346c976f38f5ad6`、`929e4114ef2f548de0e2d89e3d28f8ebb174f57caee39273c911fd3518c58251`。

0.1.0 文件与历史报告保留原状；旧输入绑定不能被新门禁当作当前质量证据。两个版本因输入变化不能直接声称质量提升。F3 正式回归位于 `tests/eval/test_agent_chain_f3.py`，证明输入完整、示例可运行、冻结文件不变与版本身份拒绝；不证明真实模型能正确调用工具。未重新执行真实 official；recording fake/参数执行验证的 F2 仍待处理。

### 调用校验协议：evaluator 0.3.0 / report 与 gate 2.0.0（2026-10-13）

用户批准 F2 修复：保留数据 0.1.1 及原三项指标阈值，新增 `tool_call_validity_recall >= 1.0`。index 的 evaluator_version 记录数据发布时初始评测器；新报告 evaluator_version、source_hashes 与覆盖真实行动 Prompt/工具 Schema 的 prompt_hash 绑定当前代码。旧协议报告继续保留，由新版 gate 拒绝，不能冒充当前质量证据。

每个工具例仍只请求一次模型，不新增重试或规划请求；一次评测共 12 次模型请求。仅三种白名单工具，参数依生产 Schema 的已支持子集校验（未知校验关键字失败关闭、不新增依赖）；code/表达式不允许空白，timeout 必须是有限正数且不是 bool，Python 仅 AST 解析。agent-004 的 AST 必须匹配已给前序代码；agent-003 无固定前序代码，只证明调用参数与语法可用，不证明算法正确。

逐例 `tool_record` 包含意图、工具名、参数有效性、固定错误码、参数 SHA-256、recording_attempts、recorded_calls 和 recording_result。参数无效时两个计数均为 0；合法调用才实际进入一次内存 Tool callback，结果为 `recorded`。不执行代码，不产生假 stdout/code_result；`recorded` 不代表真实沙箱成功。原 `tool_selection_recall` 仍只测名字，因此同名空参数可得 1.0，但有效调用率为 0.0，official/gate 失败。gate 从 raw 回复重新验证并按严格字段类型比对记录，独立计数评分，拒绝记录和汇总篡改；这种回放不是报告真实性的密码学证明。

`agent_gate_probe.py` 现在验证 route、followup、tool、arguments 四类受控退化；arguments 探针保留正确工具名并使用空参数，只触发有效调用率失败。无工具提议同时降低工具名召回和有效调用率，保留两个失败原因。测试与实际命令见实现说明；确定性专项通过不替代真实 official 或人工业务验收。

### 原生工具调用：evaluator 0.5.0（2026-10-22）

行动阶段与官方评测发送相同的原生函数定义。报告中的 `tool_reply` 是客户端归一后的内部信封，不能当作原始 HTTP `tool_calls`。冻结数据 0.1.1 与四项阈值不变；评测器版本更新。畸形、多调用、文本协议或未注册工具在客户端拒绝，不新增格式重试。


## 已确认代码保持回归 v1

2026-10-14 的真实official Observed Regression：agent-004正确选择code_sandbox，但给先前代码的print增加“排序结果:”前缀；函数AST相同，模块AST变化，tool_call_validity_recall为0.5。冻结agent-chain-v0.1.1仍为Silver，未扩容或改变阈值/评分。修复版本confirmed-code-preservation-v1通过生产/隔离评测共用行动规则及默认规划的最近原历史约束执行参数，只有用户明确要求修改时才允许修改，工具权限与预算不变。

确定性回归覆盖完整/短路共享提示、默认结构化完整run/stream实际记录工具实参、无sandbox/预算耗尽、历史未改与输出前缀拒绝。代码保持提示传播不等于模型质量通过，模型质量仅由新鲜official评测。原四阈值0.9/1.0/1.0/1.0不变，旧失败报告保留。修后第1轮报告agent-chain-v0.1-official-confirmed-code-r1-20261014.json四指标1.0，独立gate0；四类新鲜派生故障探针仅证明拦截机制，无额外模型请求。用户最多三轮授权，第1轮已通过，不执行第2/3轮。

实际命令及红绿/Windows编码问题、输入/Prompt指纹、范围外生产限制见docs/plans/implementation_agt_003.md与docs/reviews/confirmed_code_rounds_20261014.json。模型登记prompt-model-v4仅登记源码/默认配置，非生产启用。
