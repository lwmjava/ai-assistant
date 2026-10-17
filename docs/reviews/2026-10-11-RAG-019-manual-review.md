# RAG-019 人工业务验收 · 最终评审结论

- 任务：RAG-019「LLM 响应与检索结果缓存（P2）」
- 评审身份：独立人工审核（人工业务验收）终评 Agent；只读评审，未修改业务实现代码、测试、docs/plans、tasks.yaml 或仓库任何其他文件；未做 git 写入；本次只新增本评审文件。
- 评审日期：2026-10-11（Asia/Shanghai）
- 分支：`codex/rag-019-cache`；基线 HEAD `7709d7b`；工作区改动未提交、未推送（经证据核验 Agent `git status --short` 确认为 22 个文件，与实施说明一致）。

---

## 一、审核范围与方法

### 1.1 本次评审实际读取的文档

1. 实施说明：`docs/plans/implementation_rag_019_cache_20261011.md`（全文 106 行，重点核对第 41–68 行验证证据、第 72–81 行关闭对账记录、第 85–89 行未做/限制/回滚、第 95–105 行 11 个 SHA256 指纹表）。
2. 证据核验报告（Round1，独立第三方实际运行）：`RAG-019-evidence-verification-report.md`（证据核验 Agent 产出，下称「证据报告」）。
3. 契约对账报告（Round1，文档间对账）：`RAG-019-contract-reconciliation-20261011.md`（下称「对账报告」）。
4. 交叉引用：`tasks.yaml` RAG-019 条目（9046–9107 行，经对账报告逐字摘录）、`plan_rag_019_cache_20261011.md`、四份既有审查文档（plan-review / code-review / review-fixes / verification）与评测记录 `docs/evaluations/rag019-cache-safety-v1.md`（经对账报告第 3、5 节转述核对）。

### 1.2 本次评审实际做了什么、没做什么

- 本终评 Agent **未重跑整套验证**（pytest/ruff/mypy），也**未复算 SHA256**。按编排者裁决要点，证据重跑与指纹实算已由证据核验 Agent 完成，本评审基于其实际输出做对账裁决与三分类判定。
- 本评审做的是：完整通读两份 Round1 报告 + 实施说明原文，对两个"一方声称"缺口做闭合裁决，逐项判定契约满足度，并按项目约定落盘最终结论。

### 1.3 实际运行命令与退出码（引自证据报告第三节，项目解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`）

| 命令 | 实际结果 | 退出码 |
|---|---|---|
| `pytest tests/test_rag_019_acceptance.py -q`（独立 E2） | 36 passed, 2 warnings, 49.86s | 0 |
| `pytest tests/test_rag_cache_review.py tests/test_response_cache.py tests/test_rag_cache.py -q` | 29 passed, 13.19s | 0 |
| `pytest -k cache -q`（最终缓存门禁） | 65 passed / 1652 deselected / 3 warnings, 66.66s | 0 |
| `ruff check --no-cache .` | All checks passed! | 0 |
| `mypy app/ --cache-dir=data/mypy-rag019` | Success: no issues found in 194 source files | 0 |
| `pytest <17 文件> -q`（354 项相关回归，与实施说明第 66 行逐字一致） | 354 passed, 1 warning, 49.26s | 0 |

11 个最终源码/测试 SHA256：证据报告第二节逐一实算，**11/11 一致，0 不一致**；对账报告第四节独立实算同样 11/11 一致。两处独立实算与实施说明指纹表三处互证。

### 1.4 隔离机制与测试库路径（引自证据报告 3.0 节）

- 每个 pytest 进程前在 PowerShell 会话显式覆盖：`DATABASE_URL=sqlite:///./data/pytest-tmp/audit019-20261011-db/test_ai_assistant.db`、`RAG_BACKEND=native`、`EMBEDDING_PROVIDER=mock`、`AGENT_ORCHESTRATION=self`；每次独立 `--basetemp`。
- 测试库绝对路径：`E:\culture\SmartCustomerServiceSystem\ai-assistant\data\pytest-tmp\audit019-20261011-db\test_ai_assistant.db`（704,512 字节，本次运行写入）。
- 生产库 `data\ai_assistant.db` mtime = **2026-10-07 17:55:00**，本次运行期间未被触碰；conftest 默认测试库 `data\test_ai_assistant.db` mtime 早于本次运行，因 DATABASE_URL 已被外部覆盖而未被写入。
- 环境失败清单：无。未发生权限/缓存写/basetemp 类阻断。

---

## 二、对账裁决：两个"一方声称"缺口的闭合

对账报告第 6 节曾列出两处证据薄弱点。本终评按编排者裁决要点逐一闭合：

### 缺口 (a)：354 项相关回归仅实施说明自述

- 对账报告判定：仅 implementation 第 54/66 行自述，code-review 明言"未并行重跑数据库测试"。
- **裁决：缺口已关闭。** 证据报告第 117–126 节按实施说明第 66 行逐字命令独立重跑该 17 文件，实际结果 **354 passed / 1 warning / exit 0（49.26s）**，与说明声称一致；F3 受影响回归 4 文件（test_llm/llm_route/chat/chat_controls）36 passed 已包含在本次 17 文件运行内随 354 一同通过。该项从"一方声称"升级为**已查证**。

### 缺口 (b)：F3 后 65 门禁日志缺第二方逐行读

- 对账报告判定：code-review 只实读了 F3 修复前的 63 门禁日志；F3 后 65 门禁日志仅 review-fixes 一句话转述。
- **裁决：当前状态已关闭，历史过程仍属一方声称。** 证据报告第 92–99 节独立运行 `pytest -k cache`，实际结果 **65 passed / 1652 deselected / 3 warnings / exit 0**，与实施说明第 60 行数字完全一致——即"当前工作区代码在 F3 修复后确实通过 65 项门禁"这一**状态**已由实际运行验证。但证据核验 Agent 是**重新运行**而非逐行阅读历史日志 `data/rag019-cache-gate-final3.log`，故"该历史日志当时逐行记录为 65"这一**过程性证据**仍为一方声称（不影响当前代码状态判定）。

两份报告间**未发现实质冲突**：唯一数量差异（三缓存测试文件中间阶段 25 passed vs 本次 29 passed）证据报告已解释为 F3 修复后新增用例的阶段演进，与"最终门禁见追加"一致，非矛盾。

---

## 三、契约对账结论（逐项：原始要求 → 实际证据 → 满足/缺口）

| # | 原始要求（出处） | 实际证据 | 判定 |
|---|---|---|---|
| R1 | 检索缓存：相同查询+参数不重复向量调用（tasks.yaml 9075 交付物 + 9083 验收；plan 第 17 行） | E2 `real_local_query_avoids_second_vector_call` 实际向量调用 1 次通过；证据报告独立重跑 36 E2 exit0 | **满足**（诚实标注：非生产延迟指标） |
| R2 | 确定性 LLM 缓存省调用/不重复计费（9076 + 9084；plan 第 18 行） | E2 `default_chatservice_actual_http_count` 首会话 6、同消息新会话仍 6；65 门禁通过 | **满足（口径受限）**：实现只证明 HTTP 调用次数不增，不证明真实外部账单下降；实施说明已诚实披露"无真实外部账单证据，不量化费用"，与 plan 第 18 行"不代表外部计费凭证"一致 |
| R3 | 缓存键含租户/有效授权域/数据/索引版本、实际 context/model/Prompt 版本，命中再授权（9077 + 9085；plan 第 19 行） | E2 + 7 入口反例 + 独立验证报告（context/model/凭据/endpoint 各变化新增请求；跨租户/DB 隔离；索引身份/RRF 变化）；65 门禁通过 | **满足**；资源级 ACL 仍 Planned，与 AGENTS.md §6 / plan 第 24 行一致，未把 Planned 写成 Implemented |
| R4 | 发布/删除/重解析/改权使缓存失效；跨租户/不同权限不复用（9086；plan 第 20 行） | E2 `lifecycle_publish_reparse_delete`、`permissions_mutate_in_new_session`、独立事务反例（读当前内容/空结果） | **满足** |
| R5 | 非目标：无 Redis、不对非确定性请求强缓存、不单凭 temperature=0（9079–9081；plan 第 7/24 行） | E3 有界原语反例（NaN/Inf/极大/负/0.7 不缓存）；diff 无依赖变化；默认两开关 false、LLM 默认 temperature=0.7 未改 | **满足** |
| R6 | 失败/回滚/幂等边界（plan 第 26–34 节反例清单；tasks.yaml 9090 rollback） | E2（503 重试、模型错、无 DONE、取消再入、fallback、TTL/关闭/副本）+ E3 内存/数值/租约；未提交 flush/DML/rawDriver 不缓存 | **满足**（注意：此行直接出处是 plan 既定反例，非 tasks.yaml 字面验收，属既定范围非扩张） |
| R7 | 依赖当前 HEAD 复核与独立审查（plan 第 38/40 行） | 前置 110 passed 日志经 plan-review 读尾确认；F1/F2/F3 三方文档 RED→修复→GREEN 互证；无未修复 P1/P2 | **满足** |
| R8 | 项目人工业务验收（AGENTS.md §9/§13；tasks.yaml 9048 状态 in_review） | **本次评审即该人工验收**。见第五节结论 | 见第五节 |

**矩阵小结：关闭对账表 8 行「原始要求」全部可溯源到 tasks.yaml RAG-019 或 plan 对应章节；R1–R7 证据链在独立 E2、缓存门禁、ruff/mypy、354 回归与四份审查文档间闭合，未发现把 Planned/Mock/合成结果写成生产质量或成本收益的表述。**

---

## 四、三分类清单

### 4.1 已查证（本次审核实际核验，或经证据核验 Agent 实际运行背书）

1. 分支 codex/rag-019-cache、HEAD 7709d7b、22 文件未提交未推送（证据报告第一节 git 只读确认）。
2. 11 个最终文件 SHA256 与实施说明指纹表一致（证据报告第二节 + 对账报告第四节两处独立实算，11/11）。
3. 独立 E2 验收 36 passed exit0（证据报告 3.1）。
4. 三缓存测试文件 29 passed exit0（证据报告 3.2）。
5. 最终缓存门禁 `pytest -k cache` 65 passed / 1652 deselected exit0（证据报告 3.3）——F3 后当前状态。
6. `ruff check --no-cache .` exit0（证据报告 3.4）；`mypy app/` 194 源文件 exit0（证据报告 3.5）。
7. **354 项相关回归 354 passed exit0（证据报告 3.6 独立重跑）——原"一方声称"缺口 (a) 已关闭。**
8. 隔离机制有效：生产库未被触碰，全部 pytest 落在临时隔离库（证据报告 3.0）。
9. F1/F2/F3 均有 RED→修复→独立 GREEN 记录，且三方一致把 F3 定为 P2（未夸大为外部越权/数据泄漏）——经对账报告第 3 节三方互证。
10. 默认两开关关闭、进程内、有界 TTL/LRU/条数/字节预算、不引入 Redis/新依赖（tasks.yaml 非目标 + plan + code-review 互证）。
11. 命中再授权、跨租户/DB 不串、发布/删除/重解析/改权失效、失败/回滚边界（E2 + 审查文档 + 评测记录三方表行）。
12. 评测记录 `rag019-cache-safety-v1` 存在，且自述为 Smoke/Adversarial/Observed Regression，非 Gold（符合 AGENTS.md §10「AI 生成数据默认不是 Gold」）。

### 4.2 一方声称（仅实施说明/既有审查文档自述，本次未独立执行）

1. **前置 HEAD 依赖 110 passed 中两个文件**：`test_rag_032_rebuild_cli.py`、`test_rag_032_remote_rebuild.py` 本次未运行（证据报告第 155 行；同批 test_rag_026_uploader_scope / test_rag_032_index_identity 已含在 354 回归内通过）。
2. **RED 阶段过程数值**：相同查询向量调用 2 而非 1、默认五阶段 HTTP 6→12、flush 反例返回旧空结果——本次未独立重现 RED，仅采信修复后 GREEN 证据（证据报告第 156 行）。
3. **F1/F2/F3 的"先 RED 后修复"反例过程**——本次仅复现修复后 GREEN 门禁，未独立重现各缺陷反例（证据报告第 157 行；过程见 review-fixes 文档）。
4. **`git diff --check` 空白检查 exit0**——本次未执行该 git 命令（证据报告第 158 行）。
5. **YAML 解析、184 卡 ID 唯一性与状态合法性**——本次未执行（证据报告第 159 行）。
6. **F3 后历史门禁日志 `data/rag019-cache-gate-final3.log` 的逐行读**——当前 65 状态已由证据核验 Agent 重跑验证（见缺口 b 裁决），但该历史日志当时逐行记录这一过程性证据仍属一方声称。
7. 微观计数原始日志（具体秒数/调用次数的原始 stdout）——以测试名为载体被印证，原始日志未由本评审复看。

### 4.3 未验证（实施说明自己列出的未执行项，如实保留，不得写成完成）

1. **生产启用**——未做（两开关默认 false，未上生产）。
2. **真实重要数据重建**——未做。
3. **付费/真实外部模型调用与真实账单**——未做（用 localhost 合成 HTTP 代替；E2 请求计数只证明省调用，不证明成本下降）。
4. **真实 Milvus 缓存验证**——未做（Milvus 仍 `Partial`，默认向量库 `local`）。
5. **多实例 / 外部缓存 / 并发请求合并协调**——未做（明确为 non-goal）。
6. **端到端延迟/性能达标声明**——未做（仅有 SQL/哈希开销披露，无整体性能指标）。
7. **PostgreSQL 生产缓存、检索/生成质量指标**——未做。
8. **资源级 ACL**——仍 `Planned`（非本卡范围，ADR-0001）。
9. 前端页面验收——无前端变更，不适用。

---

## 五、残余风险与阻塞项

1. **未重跑的前置依赖文件（低风险）**：`test_rag_032_rebuild_cli.py`、`test_rag_032_remote_rebuild.py` 未在本次证据核验中运行；但同批另两个前置文件已随 354 回归通过，且该两文件与缓存实现无直接 diff 关联，残余风险低。
2. **过程性证据未独立重现（低风险）**：RED 阶段数值、F1/F2/F3 反例过程、历史门禁日志逐行读、`git diff --check`、YAML/184 卡唯一性均未由本次独立复跑。修复后 GREEN 状态已被实际运行验证，过程性证据缺失不改变"当前代码通过门禁"的结论，但属诚实标注的审计薄弱点。
3. **口径限制（非遗漏）**：验收原文 9084「不重复计费」在实现侧只能证明「HTTP 调用次数不增」，不能证明真实账单下降；实施说明已诚实披露，未夸大为成本节约。
4. **生产侧全部未验证项（中风险，但均为明确 non-goal/阶段外）**：生产启用、真实 Milvus、PostgreSQL 生产缓存、多实例并发合并、端到端性能、付费模型账单、资源级 ACL——这些在 plan 第 24 行与实施说明第 87 行均明确列为非本卡目标，不构成对 RAG-019 契约的违反，但在生产启用前必须另行验证。
5. **未提交/未推送**：工作区 22 个文件改动尚未提交。这是实施说明刻意保持的状态（等待用户业务验收后再提交），非缺陷；转入 done 前需用户决定提交时机。

---

## 六、明确结论

**RAG-019 是否满足其契约：是（在本卡契约字面范围内）。**

依据：
- R1–R7 七项契约要求（检索缓存、LLM 响应缓存、键绑定与命中再授权、生命周期失效、非目标守住、失败/回滚边界、依赖复核与独立审查）均有可溯源证据，且经本次独立证据核验重跑复现：36 E2、65 缓存门禁、ruff、mypy(194)、354 回归全部 exit0，11 个 SHA256 三处互证一致。
- 两个原"一方声称"缺口（354 回归、65 门禁当前状态）已由证据核验 Agent 实际运行关闭；未执行项全部为 plan 明确的 non-goal 或阶段外生产项，实施说明未把任何 Planned/Mock/合成结果写成生产可用。
- 无未修复 P1/P2；F3 被三方一致定级 P2（进程内上下文快照错配，未证明跨租户外部泄漏），定级诚实。

**状态变更建议：建议从 `in_review` 转入 `done`（对自动契约验收而言），但最终决定权在用户。**

理由：本卡契约中唯一未闭环的 R8「人工业务验收」即由本次评审承担，评审结论为"自动证据链完整、范围诚实、无业务 RED、无阻塞性缺陷"；残余风险均为过程性审计薄弱点或明确 non-goal，不构成本卡契约的未满足项。

**同时必须向用户明示**：转入 done 只代表"本卡契约范围内的自动验收与本次人工审核通过"，不代表生产就绪。生产启用、真实 Milvus/PostgreSQL 缓存、多实例并发、付费模型账单与端到端性能等均属未验证项，须在后续对应任务卡（NFR/OPS/PRAG 族）中另行验证后才可上生产。

**本评审不做任何状态变更**：未修改 `tasks.yaml`、未改 git、未提交。是否将 RAG-019 从 `in_review` 改为 `done`，由用户最终决定。
