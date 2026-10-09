# RAG-031 LLM 辅助切分边界 — 实现说明

日期：2026-10-09。分支/提交：**未提交**（按本卡约束，关闭登记与提交由主 Agent 负责；本 Agent 不改 `tasks.yaml`）。
计划：[plan_rag_031_20261008.md](plan_rag_031_20261008.md)。架构依据：[ADR-0005](../adr/0005-rag-adaptive-chunking-context-budget.md) §3/§9。
前置 RAG-021/022/028 均为 done；本卡基线 HEAD `66b7d9b`。

## 1. 实际完成了什么

在规则结构保护**之后**，为结构原子单元（fenced code / 盒图 / 表格）之间的**正文间隙**提供一个可选、**默认关闭**的 LLM 主题边界建议阶段：

- 模型**只输出句子序号数组**（边界 ID），**绝不输出/改写原文**；代码把序号映射为字符偏移后切取原文。
- 输出严格校验：坏 JSON、非整数、越界、重复、注入式 prose、超时、预算耗尽一律 **规则降级**，回既有字符切分，不阻塞、不改写原文。
- 有限调用：单文档调用次数有上限（费用护栏）；真正发请求走 RAG-028 的 `LLMProvider.chat`（其内已做上下文预算 Guard，未知模型拒绝→降级）。
- 与 RAG-021 结构保护衔接：原子单元仍由规则整体保护；LLM 只碰它们之间的正文间隙；不替代结构保护；不接 `parent_child`。
- 默认关闭（`RAG_LLM_BOUNDARY_ENABLED=false`），关闭时 `protected_split` 行为与 RAG-021 逐块一致。

## 2. 成功与失败行为

| 场景 | 行为 |
|---|---|
| 开关关闭（默认） | 不构造 advisor，`resolve_boundary_table` 返回 None，切分与基线逐块相同，chunk 不含 LLM 元数据 |
| 模型返回合法序号 | 正文间隙按建议边界切分；区间仍过 Embedding 输入护栏（超限细分，保留 oversized 语义）；chunk 记 `llm_boundary_used=True` + 模型/Prompt/协议版本 |
| 模型返回 `[]` | 视为无建议边界，规则切分；chunk 记 `llm_boundary_used=False, degraded_reason=no_boundaries` |
| 坏 JSON / 注入式 prose / 越界序号 | 解析拒绝→规则切分；`degraded_reason=bad_json_or_non_integer` |
| 调用超时 | `degraded_reason=provider_timeout`，规则切分 |
| RAG-028 预算 Guard 拒绝 / 网络错误 | `degraded_reason=provider_error`，请求未外发或失败，规则切分 |
| 单文档调用次数用尽 | 后续间隙 `degraded_reason=call_budget_exhausted`，不再外发 |
| 间隙超过单次发送上限 | 不发请求，`degraded_reason=region_exceeds_input_budget` |
| 重解析（存储 plan 重放） | 不重新发起 LLM（非确定性），走规则 plan 确定性重放 |

所有降级路径都保证：切片并集 == 原文（无丢失）、区间升序（无乱序）、区间在 `[0, len]` 内（无越界）。

## 3. 代码位置

| 文件 | 改动 |
|---|---|
| `app/rag/chunking/llm_boundaries.py` | 新增：`BoundaryAdvisor`、`split_sentence_spans`、`parse_boundary_indices`、`BoundaryOutcome`、版本号与系统 Prompt |
| `app/rag/chunking/structure.py` | 新增 `_GapPlan` 与 `async resolve_boundary_table`；`protected_split` 新增可选 `boundaries` 参数（默认 None，行为不变） |
| `app/rag/chunking/base.py` | guarded wrapper 在结构保护路径上先 `await resolve_boundary_table` 再切分 |
| `app/rag/chunking/base.py` `ChunkParams` | 新增 `llm_boundary_advisor` 字段（服务端注入，不入持久化） |
| `app/rag/chunking/plan.py` | 持久化校验 `required` 排除 `llm_boundary_advisor` |
| `app/rag/service.py` | `_build_chunk_params` 拒绝请求级覆盖并在开启时注入 advisor；`_build_chunk_plan` 持久化排除该字段；新增 `_build_llm_boundary_advisor` |
| `app/core/config.py` | 5 个 `RAG_LLM_BOUNDARY_*` 配置项（默认关闭） |
| `.env.example` / `README.md` | 配置项与切分策略小节同步 |
| `tests/test_rag_031_llm_boundaries.py` | 22 条反例/集成测试（五类失败边界全覆盖） |
| `evals/llm_boundary/` | 评测工具 `run.py` + 合成报告 `report.json`（不发真实调用） |

## 4. 验证命令与结果

解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`（Python 3.12.0）。隔离配方：全新 UUID SQLite + 独立 basetemp + 清空 `LLM_API_KEY/EMBEDDING_API_KEY/DASHSCOPE_API_KEY/OPENAI_API_KEY` + `PYTHONUTF8=1`。

| 命令 | 结果（退出码） |
|---|---|
| 新测试先红（collection 时模块不存在） | exit 2（ImportError，预期失败复现） |
| `pytest tests/test_rag_031_llm_boundaries.py -q` | **22 passed，exit 0** |
| `python -m evals.llm_boundary.run --output evals/llm_boundary/report.json` | exit 0；clean/empty/bad_json/injection/out_of_range/timeout 六场景均 char_coverage_equal=True、ordered=True、within_bounds=True，失败切片正确降级 |
| `pytest tests/ -k "rag or chunk or context or embedding" -q` | **838 passed, 3 skipped, 2 failed, exit 1**（2 失败为既有基线，见下） |
| `python -m ruff check app/` | **All checks passed，exit 0** |
| `python -m mypy app/` | **Success: no issues in 186 source files，exit 0** |
| 全量 `pytest -q` | **1220 passed, 3 skipped, 2 failed, exit 1**（同样仅两条既有基线） |

### 既有失败取证（不在本卡 diff 内，与本卡无关）

1. `tests/test_rag_033_parse_quality.py::test_report_file_name_carries_gate_version` — 报告文件名带 gate 版本/日期漂移；本卡未改 `document_parsers/` 与报告生成。
2. `tests/test_rag_log_minimization.py::test_milvus_delete_failure_retains_diagnostics_without_exception_body` — 失败点 `app/rag/vectorstore/milvus.py:264`：测试内 `<lambda>() got an unexpected keyword argument 'index'`，是测试 mock 签名与 `_connect(..., index=...)` 的既有漂移；本卡未改 `milvus.py`。

本卡 tracked 改动仅：`.env.example`、`README.md`、`app/core/config.py`、`app/rag/chunking/{base,plan,structure}.py`、`app/rag/service.py`，新增 `app/rag/chunking/llm_boundaries.py`、`tests/test_rag_031_llm_boundaries.py`、`evals/llm_boundary/`、本计划与本说明。`service.py` 改动仅限切分参数构建三函数，不涉及 milvus 删除或解析质量报告。

## 5. 明确没做的事（残余边界）

- **未发起任何真实/付费模型调用**；评测 provider 为脚本化合成实现。
- **真实单变量质量与成本报告未产出**——无调用/费用授权（见关闭对账）。
- 不改写原文、不替代 RAG-021 结构保护、不默认启用；不接 `parent_child`；不改纯正文（无结构单元）策略路径。
- 不把 LLM 边界写入版本化 `chunk_plan`（非确定性）。
- 未新增依赖、未改数据库 schema、未改检索/RRF/Embedding。
- 未提交 Git、未改 `tasks.yaml`；未触碰 RAG-015/032/036。

## 6. 关闭对账记录

证据类别：E1=真实入口+真实后端/模型；E2=真实代码+隔离后端/合成 provider；E3=单元/内存自检；E4=推导/历史/Mock。

| 原始契约项（tasks.yaml RAG-031） | 证据类别 | 结果 | 证据位置 / sha256 |
|---|---|---|---|
| 模型仅输出边界 ID | E2 | 通过 | `llm_boundaries.py`（`304a4ab3…e55c4`）；测试 `test_accepts_sorted_unique`、`test_valid_indices_map_to_offsets` |
| 原文由代码切取 | E2 | 通过 | `structure.py` `protected_split`（`919ccef3…5b7d6`）；测试 `test_offsets_partition_text_without_loss_or_reorder`（拼接==原文） |
| 校验（ID 整数/范围/去重/可排序） | E2 | 通过 | `parse_boundary_indices` 9 条解析测试；`tests/test_rag_031…`（`52cd1486…2cec0`） |
| 失败规则降级 | E2 | 通过 | 超时/坏JSON/注入/越界/预算 5 条测试 + 评测六场景 report.json |
| 有限调用（次数/费用有界） | E2 | 通过 | `test_budget_exhausted_stops_calling`（provider 实调=2=max_calls）；RAG-028 Guard 复用 `LLMProvider.chat` |
| 注入/坏 JSON/超时回退 | E2 | 通过 | `test_injection_prose_rejected`、`test_bad_json_degrades`、`test_timeout_degrades` |
| 无丢失/乱序/越界 | E2 | 通过 | `test_offsets_partition_text_without_loss_or_reorder`（拼接==原文、升序、界内） |
| 与 RAG-021 结构保护衔接、不替代 | E2 | 通过 | guarded wrapper 仅在 `structure_units(text)` 命中时接入；原子单元保护路径不变；`base.py`（`1b4a806c…5386e`） |
| 默认关闭 | E2 | 通过 | `RAG_LLM_BOUNDARY_ENABLED=False`；`test_default_off_is_identical_to_baseline`；`config.py`（`7fc377c9…3d1a74`） |
| 配置开关+.env.example+README 同步 | E2 | 通过 | `.env.example`（`3a99db98…e180c2`）、`README.md`（`a60555e4…dd900`） |
| 切分计划版本绑定（advisor 不入持久化） | E2 | 通过 | `plan.py`（`a8a8e2f1…143cd1`）排除字段；`service.py` `_build_chunk_plan`（`678f19bc…5aee9e`） |
| 模型/Prompt 版本记录 | E2 | 通过 | chunk metadata 记 `llm_boundary_model/prompt_version/protocol_version`；测试断言 |
| **真实单变量质量与成本报告** | **E1 阻塞** | **未验证** | 真实模式工具 `evals/llm_boundary/run_real.py` 已建（$0.1 停止线/最坏价预占/usage 采集/HTTP 计数/无凭据不发请求）；预检报告 `evals/llm_boundary/report-real-20261009.json`（sha256 `0640972ab01f5bf83793f9065370ccd3404823b916c22300027232e4beeceaaf`，status=blocked_no_credentials，real_calls=0，http_attempts=0）。**原因：无 OPENAI_API_KEY**（.env 与进程环境均空；现有 LLM_API_KEY 指向 api.deepseek.com，无法访问 gpt-4o-mini）。用户批复 gpt-4o-mini/$0.1 已记录，但凭据缺失导致 0 次真实调用；质量/成本数值待补凭据后由该脚本产出 |
| 评测工具/报告骨架（记录版本与失败切片） | E2 | 通过 | 合成 `evals/llm_boundary/run.py`（`590bee86…be221`）+ 真实 `run_real.py`；report.json 含 prompt/protocol/code sha256 与六失败切片；真实报告记录模型/Prompt/数据版本、逐例、usage、请求 ID、计数器、费用、失败切片、holdout 不适用说明 |

### 未验证项（如实列出）

- **真实单变量质量与成本报告**：未验证（E1 阻塞）。用户 2026-10-09 批复 gpt-4o-mini、约 30 次调用、$0.1 停止线（覆盖原计划 <$0.01 提案）。但预检确认**无 OPENAI_API_KEY**（.env 与进程环境均空；现有 LLM_API_KEY 为 DeepSeek，无法访问 gpt-4o-mini），按付费红线与预检要求**未发起任何调用**，real_calls=0。质量/成本/边界准确率数值待补 OpenAI 凭据后由 `run_real.py` 产出；报告诚实标注无凭据，不冒充 E1。
- 独立入口级反例审查：由主 Agent 另行派发未参与实现的 Agent 执行（本 Agent 为实现者，串行自审不构成独立审查）。

### 既有基线失败（与本卡无关，未修复）

`test_rag_033_parse_quality::test_report_file_name_carries_gate_version`（日期漂移）、`test_rag_log_minimization::test_milvus_delete_failure...`（mock 签名）——均不在本卡 diff 内，取证见 §4。
