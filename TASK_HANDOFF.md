# 任务交接文档

最后更新：2026-10-07 21:25

> 2026-10-07 21:25 更新：原「已知问题 1」的 `index_id` P0 **已修复并验证**（commit `0c756c3`），
> 并顺带修掉了被它掩盖的第二个缺陷（commit `43d6fa1`，见 §5）。§6 下一步已相应调整。

用途：让接手的 AI 无需重新调查即可继续推进 RAG 模块 15 张任务卡的收尾工作。
本文所有状态均以 `docs/plans/plan_rag_batch_20261007.md`（批次台账）与本机实测为依据；
凡本轮亲自复跑过的，均标注「本轮实测」并给出命令与结果；未复跑的一律标注来源，不做二次推断。

---

## 1. 任务目标

- 用户想完成什么：
  批量交付 RAG 模块 15 张任务卡 —— RAG-024、RAG-026、RAG-037、RAG-034、RAG-032、RAG-015、RAG-036、RAG-028、RAG-029、RAG-027、RAG-038、RAG-030、RAG-033、RAG-035、RAG-042。
  每张卡都要走完「开发 → 独立审查 → 修复 → 验收 → 评估」闭环，完成一张自动继续下一张，全部完成后执行一次**模块整合回归**。
  用户原话授权（2026-10-07）：「按依赖逐卡完成开发、独立审查、修复、验收和评估，完成一张后自动继续，最后执行模块整合回归。」「最终交付全部卡的实际状态和验证证据。」

- 完成标准：
  1. 每卡在台账中标记为可关闭状态，并具备三件套产出：实现说明 `docs/plans/implementation_rag_XXX_*.md`、独立审查报告 `docs/reviews/2026-10-07-RAG-XXX*.md`（审查者须为**独立子 Agent 上下文**）、可复现证据（测试条数 / 变异验证 / 真实退出码）。
  2. 15 卡全部关闭后执行**模块整合回归**：RAG 子集全量 + 全量 pytest + ruff + mypy + 前端 typecheck/build（涉前端卡）。
  3. 最终交付物是「**全部卡的实际状态 + 验证证据**」，而不是「模块看起来完整」。任一必交付项处于未验证 / 失败 / 未做时，该卡不得标记 done。

- 范围与限制：
  - **明确禁止**：git push、merge、deploy、重建真实索引。
  - **仅在当前分支提交**；commit message 只写实现/修复的功能，不得写卡号、ADR 号、文档依据（追溯只放在 tasks.yaml / ADR / 计划文档）。
  - **禁改**：`.env`、生产数据、冻结基线报告 `evals/reports/rag-v0.1-baseline-20260919.json`。
  - **不实施集合外卡**：RAG-031 / 039 / 040 / 041 等维持 Deferred。
  - **不修改已 done 前卡的产品语义**，发现漂移只上报、不顺手改。
  - 逐卡遵守各自 `scope.allowed_paths`，**不合并成全仓许可**。

---

## 2. 当前进度

总计 15 卡：**已完成 8 / 进行中 3 / 未开始 2 / 阻塞 2**。
（状态枚举为本技能台账自建：`pending / running / verified / blocked / deferred / excluded`，**不是** tasks.yaml 的状态模型）

### 已完成（8 张，台账 verified）

| 卡 | 主题 | 关键提交 | 证据 |
|---|---|---|---|
| RAG-024 | 发布原子性 | `42bf7e5` + `5cce269`（复审补修） | 复审通过 |
| RAG-026 | uploader 检索隔离 | `85bd20d` | 检索按鉴权主体有效读范围过滤 |
| RAG-027 | 授权引用原文只读核验 | `dbe2686` + `c625925`，tasks.yaml 回写 `6980316` | 专项 30 passed；8 处变异全红；重做审查「建议关闭」（Medium 3、无 High） |
| RAG-028 | 模型能力契约与每次调用预算护栏 | `86acb26` + `b27fde8` + `9a7dfa1` + `4937a5e` | 42 条测试、15 处变异全红，关闭 `fa5c4f2` |
| RAG-033 | 多格式解析与 OCR 质量基线 | `23d8463` + `b0f853e` + `ddd1834` + `60d4293` + `3b5fcb1` | 专项 71 passed；tasks.yaml 回写 `b8b6fa6`；**零遗留，正式关闭** |
| RAG-034 | 审核链与分级定义 | `ffe4dad` | 唯一 High（H1 依据口径）已修并变异验证；Gold 24→21 |
| RAG-037 | 检索状态 | `cfafcdb` + `58da8bf` + `268064b` + `1a0e476` + `eaadade` | 五轮独立复审后关闭，剩余为 Low/Info |
| RAG-042 | 下载往返 | `409dd64` + `e90683e` + `7d4bed3` + `69cdc53` | 审查整改后复审通过，关闭 `fa5c4f2` |

### 正在进行（3 张）

- **RAG-032（索引身份与切换）** —— 最接近收口。
  第三轮独立复审 `docs/reviews/2026-10-07-RAG-032索引身份与切换复审-第三轮.md` 结论：**建议关闭**。
  H-07 / M-06 / M-07 / M-08 / M-09 / L-03 / L-04 / L-05 均判真修，7 处变异全红（每处都带 `assert mutated != original` + md5 双重确认）。
  剩余 3 条 Low 不阻断：
  - **L-02**：`config.py` 与 `.env.example` 注释仍声明 `cosine | ip | l2`，但实现只支持 `l2` / `cosine` —— 运维照注释配 `EMBEDDING_METRIC=ip` 会让摄取与检索直接报错。**收尾已派发且改动已在工作中**（见 §4 未提交修改）。
  - **N-01**：`_revert_switch` 漏还原 `retired_at`。**已修，改动在工作区未提交**（`app/rag/index_registry.py`）。
  - **N-02**：`identity_of_row` 对非 l2/cosine 历史行会抛错，当前不可达 → 按文档化待办处理。
  专项测试数：台账记 71 passed / 1 skipped（改前基线 54 passed / 1 skipped）。**本轮未复跑**（运行超时中断，见 §5）。

- **RAG-029（按块上下文组装与真实来源）** —— 整改已落地，**复审待完成**。
  整改提交 `6aae8c7`（7 文件 / 639 行）：H-01/H-02/H-03 + M-01。
  核心改动：把 Context Builder 接到 Fast RAG（`app/agents/fast_path.py:136`）与 Supervisor 两条真实对话路径，并把「截断补齐围栏」改为**整块丢弃**。
  新增硬不变量：payload 中的 `[资料 N]` 与 `selected` 必须完全一致，**含「都没有」的情况**。
  **本轮实测：`tests/test_rag_029_context_builder.py` → 34 passed**。
  待办：派发独立复审（复审者须为独立子 Agent 上下文），复审通过后方可关闭。

- **RAG-038（检索调用时限、重试与取消）** —— 整改已落地，**复审待完成**。
  High-01 是本批**自己引入的 P0 回归**已修：`RAG_RETRIEVAL_DEADLINE_SECONDS` 默认 8.0 原是检索侧 SLO，却经 `current_or_new_deadline()` 泄漏到写入/导入路径（实测 100 块导入 5.18s 抛 `DeadlineExceededError`、0 向量）。
  修法：新增 `RAG_EMBED_BATCH_DEADLINE_SECONDS=20.0`（`config.py:182`），嵌入改为「绑定则复用共享预算、未绑定则每批各起一个写入侧预算」。对照实测：改前 8.08s / **0 条向量** → 修复后 10.08s / **100 条向量**。
  **本轮实测：`tests/test_rag_038_deadline_retry.py` → 51 passed**。
  遗留（已判越界、另派）：SSE 终态事件 `reason` 字段未修（`app/services/chat_service.py` 不在本卡 allowed_paths）。
  ⚠️ 落盘方式异常：7 个文件不是它自己的 commit，被宽提交 `52769db` 卷入，它只提交了 `9c73082` 记录此事 —— **不要以提交标题判断内容**。

### 尚未开始（2 张）

- **RAG-030**：依赖 RAG-016（done）/ RAG-029 / RAG-026。RAG-029 复审关闭后即可开工。
- **RAG-035**：真实模型调用，需登记模型/样本用途、调用次数与**费用上限**、Gold 人工确认方式。**不自动开工，需用户授权**（详见 §7）。

### 当前阻塞（2 张）

- **RAG-015（Milvus 切换脚本与真实验收）**：原阻塞原因（Docker 守护进程不可用）**已于 2026-10-07 晚消失** —— `docker version` → 28.5.1，12 核 / 约 12GB，`milvusdb/milvus:v2.5.11` 镜像本地已存在（与锁定的 pymilvus 2.5.11 版本对齐）。
  真实 Milvus 验收已派发，**这是当前最优先的解锁项**。
  现状：脚本与 local 路径已完成 `ad37b3f`（7 passed）；Milvus 路径此前**一次都没连上**（始终 `ConnectionRefusedError`）。
  已用**假 Milvus 反例**堵住「脚本恒真」最坏情况（`c8299b4`，8 个场景全部被脚本捕获，新增 5 条判据用例 + 8 条反例用例，变异 2 处已还原）。**假 Milvus 不能替代真实验收**。
  仍未闭合（需改 `app/`）：`MilvusVectorStore.add()` 无调用点、`milvus.py` similarity=1.0 占位、写入侧 L2 归一化。
- **RAG-036**：依赖 RAG-015，随 RAG-015 真实验收通过而解封。

---

## 3. 关键决定

- 已确定的方案及原因：
  - **RAG-028 未知模型处理 → 运营者显式声明**（用户 2026-10-07 裁决）。护栏默认开启、未知即拒绝；仓库只内置有厂商依据的条目（OpenAI `gpt-4o-mini` 128000/16384；DeepSeek `deepseek-flash`、`deepseek-v4-pro` 1M/384K，标注来源与核对日期）。`deepseek-chat`（代码默认模型）不在内置表内，运营者须显式填写 `LLM_CAPABILITY_DECLARED` + `LLM_CAPABILITY_DECLARED_SOURCE` 才放行。原因：2026-10-07 实取官方文档互相矛盾（`api-docs.deepseek.com` 只列 flash / v4-pro，`deepseek-chat` 已不在在售表内），**不能凭猜测登记窗口**。
  - **RAG-027 管理员豁免判定 → 核验面 = 检索面**。逐项对齐 `read_scope_for` 与 `can_read_document`：`TENANT_ADMIN` 在 uploader 模式下**不豁免**（`app/rag/access.py:80` 显式默认受限），`SYSTEM_ADMIN` **豁免**。（措辞已修正：早前写成「admin 不豁免」不严谨。）
  - **RAG-033 CE-13（删语料即门禁失败）→ 保留现状，不加开关**。强制人工退役 known_gap 是特性不是缺陷；但报错必须可诊断。原因：「削弱预期」与「修好解析器」对门禁必须可区分。
  - **RAG-029 撤销自己当初的一项授权**：原规格允许「首块装不下时截断并补齐围栏」，改为**整块丢弃**。原因：截断既违反 ADR-0005 §9「完整证据块」，又破坏 `selected` 与 payload 一致性（实测 budget=110 时 payload 无 `[资料 N]` 但 `selected=[c1]`、sources 返回 c1，等于声称引用了一块没进模型的证据）。
  - **Milvus 真实验收 → 用户选择「我启动 Docker Desktop，做真实验收」**（而非继续用假 Milvus 或放弃）。
  - **RAG-035 → 不自动开工**，需登记费用上限后再启动。

- 用户明确提出的偏好或要求：
  - **沿用已批准决定，不做阶段性确认**；只有**新冲突或必须授权**的操作才找用户。
  - **不推送、不合并、不部署、不重建真实索引**。
  - 可复现的**常规人工操作验收由 Agent 执行**。
  - 最终交付「**全部卡的实际状态和验证证据**」—— 状态必须诚实，未验证就写未验证。
  - 证据政策：事实引用代码、配置、测试、Trace、数据或已批准文档；**假设必须标记并给出验证动作**。
  - 严禁把「我记得有」当成「我看见过」（见 §7 事故）。

- 已尝试但放弃的方案及原因：
  - **「用 `git diff --stat` 复核改动行数是否合理」这条启发式已废弃** —— 对纯行尾（CRLF）污染完全失效：blob 哈希不同但 `git diff` / `git status` 均为空。改用字节级判据 `assert Path(f).read_bytes().count(b"\r\n") == 0`。
  - **RAG-015 的假 Milvus 反例方案是过渡手段，不能替代真实验收**（已明确记录，不得据此标记 done）。
  - **撤销「截断补齐围栏」**（见上）。

---

## 4. 文件与工作状态

- 项目路径：`E:\culture\SmartCustomerServiceSystem\ai-assistant`
- 当前分支：`rag-batch-20261007`（基线 `92da115`，前分支 `nightly/rag-20261007` 未推送）
- 最近提交：`43d6fa1`（fix(agents): 编排失败提示保留多 Agent 协作上下文）
  前一条 `0c756c3`（fix(rag): 为历史库补齐分块索引身份列并建索引）；再前 `a63088a`（docs: 重建 §13 收尾清理项并记录宽提交事故）
  近期相关提交：`5f05507`（config.py 归一化回 LF）、`3b5fcb1`（RAG-033 C.4.1/C.5）、`6aae8c7`（RAG-029 审查整改）、`60d4293`（RAG-033 N-03）、`21416f1`（RAG-032 复审整改）、`c8299b4`（RAG-015 假 Milvus 反例）

- 尚未提交的修改（`git status` 实测）：

  | 文件 | 性质 |
  |---|---|
  | `app/core/config.py` | **真实改动**（7 行）— RAG-032 L-02：`EMBEDDING_NORMALIZATION` / `EMBEDDING_METRIC` 注释改为只接受 `l2` / `cosine`，其余取值在索引身份构造时直接抛 `IndexIdentityError` |
  | `app/rag/index_registry.py` | **真实改动**（14 行）— RAG-032 N-01：`_apply_switch` / `_revert_switch` 的 `previous` 元组增补 `retired_at`，撤销时连退役时间戳一起还原 |
  | `tests/test_rag_032_index_identity.py` | **真实改动**（+85 行）— RAG-032 新增测试 |
  | `.env.example` | **真实改动**（4 行） |
  | `docs/reviews/2026-10-07-RAG-033…复审-第二轮.md` | 审查报告（+68） |
  | `docs/reviews/2026-10-07-RAG-038…独立审查.md` | 审查报告（+116） |
  | `docs/reviews/2026-10-07-RAG-032…复审-第三轮.md` | **未跟踪**（新增审查报告） |
  | `.worktrees/` | **未跟踪**（遗留 worktree：`r3-21416f1`、`r3-verify`）。⛔ **绝不可提交**，详见 §7 说明 |
  | `app/rag/backend/native.py`、`app/rag/vectorstore/milvus.py` | ⚠️ **内容实际与 HEAD 完全一致**，仅 stat 脏（mtime 被改动）。经 `git diff --quiet` 逐文件确认，字节级 CRLF 计数均为 0。**不要误判为 CRLF 污染，也不要觉得这里有未保存的工作** |

- 关键文件路径及用途：

  | 路径 | 用途 |
  |---|---|
  | `docs/plans/plan_rag_batch_20261007.md` | **批次台账，唯一权威进度源**（192 行，§9 流程台账 / §10 裁决 / §11 质量事件 / §12 回归 / §13 收尾清理） |
  | `docs/ai-prompts/skills/module-batch-delivery/SKILL.md` + `references/acceptance-gates.md` | 交付流程与验收门禁定义 |
  | `tasks.yaml` | 卡契约出处（**注意：本批 15 卡不在其中**，tasks.yaml 只有 RAG-001…013；本批仅 RAG-027/033 做了回写） |
  | `app/rag/context_builder.py` | RAG-029 按块预算组装（`assemble_retrieval()` 入口） |
  | `app/rag/index_registry.py` | RAG-032 索引身份与切换治理（`:194` 激活前检索校验、`:202` `_revert_switch`） |
  | `app/rag/resilience.py` | RAG-038 `Deadline` / `retry_async` / `bind_deadline` / `ensure_budget` |
  | `app/core/migration.py` | **待修**：`_ensure_rag_schema_columns`（`:267-286`）缺 `index_id`（见 §5 已知问题） |
  | `app/core/config.py` | `:182` `RAG_EMBED_BATCH_DEADLINE_SECONDS`；`:28` `retrieval_retry_effectively_disabled()` |
  | `scripts/milvus_switch_check.py` | RAG-015 切换校验脚本（20 条测试） |
  | `scripts/rebuild_embedding_index.py` | RAG-032 重建脚本（`:68-93` 身份来源已与运行时对齐） |

- 已生成的成果文件：
  - 各卡实现说明：`docs/plans/implementation_rag_XXX_*.md`
  - 各卡独立审查报告：`docs/reviews/2026-10-07-RAG-XXX*.md`（已归档 9+ 份）
  - RAG-015 反例产物：`evals/reports/rag-015-fake-milvus-counterexamples-20261007.json`
  - ADR：`docs/adr/0005`（预算/来源/评测/摘要）、`0007`（只读核验权限）、`0008`（索引身份与切换）

---

## 5. 验证情况

- 已执行的测试或检查及结果：

  **本轮亲测**（解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，全新 basetemp + 独立 `DATABASE_URL`）：

  | 检查项 | 命令 | 结果 |
  |---|---|---|
  | RAG-029 专项 | `pytest tests/test_rag_029_context_builder.py -q --basetemp <new>` | **34 passed** |
  | RAG-038 专项 | `pytest tests/test_rag_038_deadline_retry.py -q --basetemp <new>` | **51 passed** |
  | RAG-032 未提交改动内容核对 | `git diff app/rag/index_registry.py` / `app/core/config.py` | 确认 N-01（`retired_at` 三元组还原）与 L-02（注释收紧）均已落地 |
  | `native.py` / `milvus.py` 是否真有改动 | `git diff --quiet` 逐文件 | **内容同 HEAD**，仅 stat 脏；CRLF=0 |
  | `index_id` schema bug 根因 | 读 `migration.py:355-410` + `:267-286` + `alembic/versions/b2c3d4e5f607` | 根因确认，见下 |

  **台账记录（本轮未复跑，引用需注明来源）**：RAG-032 专项 71 passed / 1 skipped；RAG-033 专项 71 passed；全量基线 783 passed / 2 skipped / 0 failed（截至 `58da8bf`，799s）。

  ⚠️ 本轮尝试复跑 RAG-032 / RAG-015 专项时**两次因超时被 SIGTERM 中断**，未取得结果，**不要把这些数字当作本轮实测**。

- 尚未验证的部分：
  - **RAG-015 真实 Milvus 验收**（Docker 刚可用，尚未跑通；此前 Milvus 路径一次都没连上）。
  - RAG-036（依赖 RAG-015）。
  - RAG-030、RAG-035（未开工）。
  - RAG-029 / RAG-038 的**独立复审**（整改已落地但复审未完成，这是关闭它们的前置）。
  - **模块整合回归**（15 卡全关后才执行）。
  - mypy：本机未安装（仅 requirements 可选依赖里有），`mypy app/` 门禁不可执行。
  - 全量 pytest：沙箱内会得到约 76 个幽灵 error（`shutil.rmtree` 被 safe-delete 守卫拦截），**非产品缺陷**，权威数字须在用户本机终端重采。

- 已知问题及复现方法：

  1. **`rag_document_chunks.index_id` 缺失（P0）—— ✅ 已修复（`0c756c3`，2026-10-07 21:25）**
     - 现象：`tests/test_supervisor.py::test_supervisor_error_skips_subtask_and_hides_exception` 报 `no such column: rag_document_chunks.index_id`。
     - 根因（已确认）：`_stamp_if_schema_already_at_head()`（`migration.py:355-381`）在「后建 RAG 表已存在 + 配额列已存在」时直接 `stamp(head)` 并 `return True`，**跳过其间所有迁移**，包括添加 `index_id` 的 `b2c3d4e5f607`。随后 `auto_migrate()` 见无 pending 迁移即调用 `_ensure_rag_schema_columns()`，而该函数只补 `rag_documents` 各列与 `rag_document_chunks` 的 `parent_id`/`strategy`/`chunk_metadata`，**从不补 `index_id`**。
     - 后果：早于 `b2c3d4e5f607` 的库被静默标记为「已迁移」，且永远修不好。
     - **已实施的修法**：`migration.py` 的 `_ensure_rag_schema_columns()` 中 `chunk_columns` 增补 `"index_id": "ALTER TABLE rag_document_chunks ADD COLUMN index_id VARCHAR"`，并补 `CREATE INDEX IF NOT EXISTS ix_rag_document_chunks_index_id`（与 `b2c3d4e5f607:62-63` 的 DDL 对齐）。
     - **验证证据**：坏库 `data/test_ai_assistant.db` 实测 `alembic_version=b2c3d4e5f607` 但无 `index_id`（而已有 ensure 补的 `parent_id`/`strategy`/`chunk_metadata`，三件事一次坐实）；复制该库跑 `init_db()` → BEFORE `index_id=False` → AFTER `index_id=True` 且索引已建。回归 `test_supervisor.py` + `test_rag_schema_repair.py` + `test_rag.py` = **63 passed**。
     - ⚠️ **修法上的坑**：`migration.py` 工作区是 100% CRLF 而 blob 是纯 LF，`git status` **完全不报**；直接编辑提交会造成 507 行整文件重写。必须先 `read_bytes().replace(b"\r\n", b"\n")` 归一化再改，改完 `assert` 输出无 CRLF（实测 diff 收敛到 8 insertions / 1 deletion）。
  1-bis. **Supervisor 编排失败文案丢失上下文 —— ✅ 已修复（`43d6fa1`）**
     - 由上面 `index_id` 修好后**被掩盖的失败暴露出来**：同一条用例不再报 SQL 错，转而断言失败——期望「抱歉，多 Agent 协作处理时出现问题，请稍后重试。」，实际是笼统文案。
     - 根因：`b2432e4` 只改了 `pipeline.py`（给 `_failure_text` 加 `generic` 参数）与 test，**从未改 `supervisor.py`**，它仍调 `_failure_text(exc)`。
     - 修法：`supervisor.py:316` 传入 `generic="抱歉，多 Agent 协作处理时出现问题，请稍后重试。"`。**变异验证**：去掉该参数 → 用例立刻在 `test_supervisor.py:326` 变红（1 failed / 16 passed），已还原。`tests/test_supervisor.py` → **17 passed**。
  2. **SSE 终态事件 `reason` 未修**（RAG-038 溢出项，已判越界另派卡）。
  3. **CRLF 行尾污染（全树级）**：`app/**` + `tests/**` 约 250 个工作区文件是 CRLF，而仓库 blob 应为纯 LF；任何人提交都会造成整文件伪差异。已列入 §13 收尾清理，**现在不动**。
  4. **台账自身有重复行**：§9 的 RAG-032 行与 §11 的「M-05」行各被重复写入一次（内容近似）。接手者以最新一段为准，不必当成两条独立事件。

---

## 6. 下一步

1. ~~**修 `index_id` schema bug**~~ ✅ **已完成**（`0c756c3`）+ 顺带修掉 Supervisor 文案缺陷（`43d6fa1`），见 §5。
   **当前首先执行的应是：RAG-015 真实 Milvus 验收**（Docker 已可用：28.5.1 / 12 核 / 约 12GB，本地已有 `milvusdb/milvus:v2.5.11` 镜像）—— 这是最高解锁项，通过后 **RAG-036** 随之解封。

2. **随后执行（按此顺序）**：
   a. **收口 RAG-032**：把工作区已有的 L-02 / N-01 改动单独提交（**务必带路径限制，不要用宽提交**），确认 N-02 文档化，标记 closed。
   c. **RAG-029 / RAG-038 各派一次独立复审**（审查者须为独立子 Agent 上下文），复审通过后关闭。
   d. **RAG-030** 开工（RAG-029 关闭后）。
   e. **RAG-035**：向用户申请授权（模型/样本用途、调用次数、费用上限、Gold 人工确认方式）后再开工。
   f. **模块整合回归**：RAG 子集全量 + 全量 pytest + ruff + mypy + 前端 typecheck/build。
   g. **§13 收尾清理**：全树 CRLF 归一化（单独提交）、删除 `data/rag015-python-deps`（137M）、`git worktree remove` + `prune` 遗留 worktree、清理 `uutest5.py`。

3. **完成后如何验收**：
   - 每卡三件套齐备（实现说明 + 独立审查报告 + 可复现证据）。
   - 15 卡台账状态无 `running` / `blocked` 残留（除用户已授权的 deferred）。
   - 模块整合回归全绿，且与开工基线（全量 783 passed / 2 skipped / 0 failed；RAG 子集 352 passed / 2 skipped / 0 failed）对比无新增失败。
   - 最终向用户交付「**全部 15 卡的实际状态 + 验证证据**」清单，未验证项必须如实标注为未验证。

---

## 7. 接手注意事项

- 不要覆盖或修改的内容：
  - `.env`、生产数据、**冻结基线报告** `evals/reports/rag-v0.1-baseline-20260919.json`。
  - **已 done 前卡的产品语义**（发现漂移只上报，不顺手改）。
  - **不要进入第二个 worktree** `C:/Users/123/.codex/worktrees/92a2/ai-assistant`（分支 `codex/rag-021`，commit `16308e7`）—— 不是本批的。
  - ⛔ **`.worktrees/` 下那 524 个「未跟踪文件」绝对不能提交**（专项说明见本节末尾）。
  - 不要以提交标题判断内容：RAG-038 有 7 个文件被宽提交 `52769db` 卷入，它自己只提交了 `9c73082` 记录此事。
  - 未提交的工作区改动（§4 表格）是 RAG-032 的收尾成果，**提交时务必带路径限制**，避免重演宽提交事故。

- 必须先阅读的文件：
  1. `AGENTS.md`（AI 开发规则的权威来源：模块边界、分层依赖、命名、安全红线、文档同步）
  2. **`docs/plans/plan_rag_batch_20261007.md`**（本批唯一权威进度源，尤其 §4 环境事实、§5 验证命令、§10 裁决、§11 质量事件、§13 收尾清理）
  3. `docs/ai-prompts/skills/module-batch-delivery/SKILL.md` + `references/acceptance-gates.md`
  4. ADR-0005 §9、ADR-0007、ADR-0008

- 需要用户确认的事项：
  1. **RAG-035 真实模型调用**：需登记模型/样本用途、调用次数与**费用上限**、Gold 人工确认方式后才可开工（涉及真实费用）。
  2. **删除类操作**：`data/rag015-python-deps`（137M）、仓库根 `uutest5.py`（1117 行 untracked）、遗留 worktree 清理 —— 均属删除，执行前需明确。
  3. **合并 / 推送 / 部署**：用户明确禁止，需另行授权。
  4. §13 全树 CRLF 归一化会产生一次全树级提交，建议在全部卡片收口、worker 停手后执行。

- ⚠️ 本批质量事件（接手者务必遵守，否则会重演）：
  本批出现过 **3 次**「结论与磁盘事实不符」，其中 **2 次出自 lead 自己**（凭印象写下「Milvus 已跑通并抓到 2 个 P0」「RAG-032 M-05 变异存活」，而 worker 从未如此报过）。由此固化六条校验动作，最关键的三条：
  1. 报了行号 → 必须 `sed -n` / `wc -l` 能复现，否则视为未发生。
  2. 报了产物 → 必须 `ls -la` 能看见，否则视为未交付。
  3. **凡是要据此指责某个 worker 的条目，动笔前必须先 `grep` / `sed` 拿到一手输出** —— 指责错人比漏掉问题代价更大。
  另：报「变异存活」前必须先证明变异真的生效（`assert mutated != original`），否则可能是在修一个不存在的缺陷；且要区分「未命中」（没匹配上，测试没跑）与「未变红」（真缺陷）。

- ⛔ **专项：`.worktrees/` 是什么、为什么冒出 524 个待提交文件**

  **是什么**：`.worktrees/` 是子 Agent 自建的**临时 git worktree** 目录，用途是在干净检出上做独立基线复跑
  （例如 `r3-21416f1` 用于在 `21416f1` 上复核 RAG-032，`r3-verify` 用于验证）。它们是**复查工具，不是产品代码**。

  **为什么冒出 524 个待提交文件**：正常情况下 git 遇到带 `.git` 的子目录会把它当独立仓库、只显示成一个目录条目。
  实测：
  - `.worktrees/r3-verify` → **有 `.git`**（且 `git worktree list` 里是正式注册的 worktree）→ git 只显示 1 个条目。
  - `.worktrees/r3-21416f1` → **`.git` 没了（孤儿）** → git 不再认它是独立仓库，转而把里面 **524 个文件逐个列为未跟踪**。

  也就是说，这 524 条**不是新工作，而是一整份仓库源码副本被误当成了新文件**。

  **风险**：此时若执行 `git add -A` / `git add .` 或任何不带路径限制的提交，会把整份源码副本灌进仓库 ——
  正是本批 `52769db` 宽提交事故的放大版。

  **正确处置**（属删除操作，执行前需用户确认）：
  1. 先确认里面没有未提交的唯一成果（正常情况没有，它们只是检出副本）。
  2. `r3-verify` 用 `git worktree remove .worktrees/r3-verify` + `git worktree prune`（它是正式注册的）。
  3. `r3-21416f1` 是孤儿，用 `git worktree prune` 清注册后直接删目录。
  4. 同理处理 `.workbuddy/tmp/wt029r`、`.workbuddy/tmp/wt038v`（这两个也是注册的 worktree）。
  5. **不要动** `C:/Users/123/.codex/worktrees/92a2/ai-assistant`。
  6. 建议事后把 `.worktrees/` 加进 `.gitignore`，避免复发。

- 环境事实（开工实测，影响证据解释）：
  - 解释器必须用 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`（Python 3.12.0）。
  - 每次 pytest **必须传全新不存在的 `--basetemp`**，并用独立 `DATABASE_URL`（共享库 `data/test_ai_assistant.db` 跨运行持久化会污染）。
  - 统一验证命令见台账 §5。
  - 新建 git worktree 里 `core.autocrlf=true` 会让语料检出成 CRLF 造成假红 —— **不是缺陷，是 worktree 伪影**。
  - `.env` 是 untracked，`git worktree add` 不携带 → 在 worktree 跑测试可能因缺配置假红。
  - Bash heredoc 传给 `python -` 时正则里的 `\s` 会被吃掉。
