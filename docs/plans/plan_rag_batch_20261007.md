# RAG 模块批次交付计划（2026-10-07）

技能：`docs/ai-prompts/skills/module-batch-delivery/SKILL.md` v1.0.0 + `references/acceptance-gates.md` v1.0
分支：`rag-batch-20261007`（基线 `92da115`，前分支 `nightly/rag-20261007` 未推送）
执行 Owner：当前批次主 Agent（WorkBuddy，单 Agent 承载；独立审查用独立子 Agent 上下文）

## 1. 授权集合与终点

用户 2026-10-07 原话授权：

> 授权自动完成任务清单【RAG-024、RAG-026、RAG-037、RAG-034、RAG-032、RAG-015、RAG-036、RAG-028、RAG-029、RAG-027、RAG-038、RAG-030、RAG-033、RAG-035、RAG-042】。按依赖逐卡完成开发、独立审查、修复、验收和评估，完成一张后自动继续，最后执行模块整合回归。可复现的常规人工操作验收由 Agent 执行。沿用已批准决定，不做阶段性确认。只有新冲突或必须授权的操作才找我。不推送、合并、部署或重建真实索引。

集合外但清单外的卡（RAG-031/039/040/041 等）**不在本批次**，不实施。

**授权边界（用户明确禁止）**：git push / merge / deploy / 重建真实索引。
**授权边界（用户明确授予）**：可复现的常规人工操作验收由 Agent 执行。

## 2. 契约出处与版本

- 卡契约：`tasks.yaml`（当前分支基线），15 卡原文 deliverables / acceptance / non_goals / allowed_paths / risk。
- 已批准决定（沿用，不重复裁决）：
  - ADR-0001 §10：uploader 检索隔离方向已批准。
  - ADR-0005 §9：预算、来源、评测与摘要裁决（能力配置/计数校准/未知模型拒绝/selected 语义/费用登记/门槛制定方法/摘要生命周期）。
  - ADR-0007（Accepted）：RAG-027 引用原文只读核验权限范围。
  - ADR-0008（Accepted）：RAG-032 索引身份与新旧索引切换治理。
  - ADR-0002：Milvus 目标（开发 Lite / 生产 2.4+），当前默认 local。
  - `docs/plans/plan_rag_remaining_order_20261006.md` §8：排期与逐卡依据。
- 前置卡状态核对（本批次开工时）：RAG-013/014/016/020/021/022/023/004 全部 `done`。

## 3. 依赖拓扑与执行顺序

按 `depends_on` 拓扑排序，同级按 priority（P0 优先）与推进清单顺序：

| # | 卡 | P | 依赖 | 集合内前置 | 顺序理由 |
|---|---|---|---|---|---|
| 1 | RAG-026 | P0 | — | — | 无依赖；027/030 的前置 |
| 2 | RAG-024 | P0 | 013(done) | — | 无集合内前置 |
| 3 | RAG-037 | P0 | 023(done) | — | 038 的前置 |
| 4 | RAG-034 | P1 | 004(done) | — | L0 文档/数据核对；035 的前置 |
| 5 | RAG-032 | P1 | — | — | 015 的强制前置 |
| 6 | RAG-028 | P1 | — | — | 029/030 的前置 |
| 7 | RAG-033 | P1 | — | — | 无依赖 |
| 8 | RAG-042 | P1 | 020(done) | — | 无集合内前置 |
| 9 | RAG-015 | P0 | 014/023/032 | 032 | 036 的前置 |
| 10 | RAG-027 | P1 | 026 | 026 | ADR-0007 已批准 |
| 11 | RAG-029 | P1 | 028 | 028 | — |
| 12 | RAG-036 | P1 | 015 | 015 | — |
| 13 | RAG-038 | P1 | 037 | 037 | — |
| 14 | RAG-030 | P1 | 016/029/026 | 029, 026 | — |
| 15 | RAG-035 | P1 | 034/023 | 034 | 依赖 034 审核链 |

## 4. 环境事实（开工实测，影响证据解释）

| 项 | 实测 | 影响 |
|---|---|---|
| 解释器 | `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe` = Python 3.12.0（AGENTS.md §10 指定） | 全部命令以此为准 |
| 基线 pytest（RAG 子集） | **352 passed / 2 skipped / 0 failed** | 见 §5 复现方式 |
| 沙箱 pytest 幽灵 error | 固定 `--basetemp` 被复用时，shim 的 safe-delete 拦截 `rmtree` → `SystemExit:1`，表现为 ERROR at setup。**非产品缺陷** | 每次运行必须传**全新不存在的** `--basetemp` |
| 共享测试库污染 | `data/test_ai_assistant.db` 跨运行持久化；残留 Document 会触发上传去重分支 `app/rag/service.py:549` 删除新源文件，导致 3 个 upload/download 用例假失败 | 验证统一用独立 `DATABASE_URL` |
| Milvus 真实服务 | 无 docker；19530 端口关闭；`milvus-lite` 3.x 与锁定版 `pymilvus==2.5.11` 协议不兼容（实测 `ShowCollectionsResponse has no "shards_num"`），升级 pymilvus 属依赖变更需授权 | RAG-015/036 的「真实 Milvus 命中」为**已知未验证项** |
| 缺失依赖 | `tiktoken` / `pytesseract` / `pdfplumber` / `rank_bm25` 未安装 | RAG-028 走校准保守估算；RAG-033 的中文 OCR 无法以真实依赖证明 |
| 真实 API 凭据 | `.env` 内有 LLM(DeepSeek) 与 Embedding(DashScope text-embedding-v3) 键 | RAG-035 可跑真实模型，但需登记调用/费用上限 |

## 5. 统一验证命令（本批次证据基线）

```bash
# 每次运行换新的 RUN 名，避免 basetemp 复用与测试库污染
RUN=<name>; rm -rf data/pytest-tmp/$RUN; mkdir -p data/pytest-tmp/$RUN/db
DATABASE_URL="sqlite:///./data/pytest-tmp/$RUN/db/test.db" \
  D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/ \
  -k "rag or chunk or context or embedding" -q --basetemp data/pytest-tmp/$RUN/tmp
```

Ruff / mypy：
```bash
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m ruff check app/rag/ app/api/ app/services/
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m mypy app/rag/
```

前端（涉前端卡）：
```bash
cd frontend && npm run typecheck && npm run build
```

## 6. 允许范围与 Git 授权

- 逐卡遵守各自 `scope.allowed_paths`；**不合并成全仓许可**。
- 禁改：`.env`、生产数据、`evals/reports/rag-v0.1-baseline-20260919.json`（冻结报告）。
- Git：仅在当前分支提交，commit message 只写实现/修复的功能（不写卡号、ADR 号、文档依据）；**不 push、不 merge、不 deploy、不重建真实索引**。
- 每卡产出：实现说明 `docs/plans/implementation_rag_XXX_*.md` + 独立审查 `docs/reviews/2026-10-07-RAG-XXX*.md`（审查者须为独立子 Agent 上下文）。

## 7. 非目标（批次级）

- 不实施集合外卡；不解决 OPS-001/002、RAG-039/041（Deferred 维持）。
- 不改冻结基线报告；不把 Mock 结果写成生产质量；不用 holdout 调参。
- 不新增供应商、队列、渲染依赖；不引入新的顶层架构。
- 不修改已 done 前卡的产品语义（发现漂移只上报，不顺手改）。

## 8. 模块验收与终点

- 15 卡逐卡关闭后，执行**模块整合回归**：RAG 子集全量 + 全量 pytest + ruff + mypy + 前端 typecheck/build（涉前端卡）。
- 前卡证据在后续卡改动后**重新验证受影响范围**。
- 任一必交付项处于「未验证 / 失败 / 未做」时，该卡不标记 done，模块只声明「可执行部分已交付」。

## 9. 流程台账

状态枚举（本技能台账，非 tasks.yaml 状态模型）：`pending / running / verified / blocked / deferred / excluded`

| 卡 | 状态 | 备注 |
|---|---|---|
| RAG-026 | verified | commit `85bd20d`；检索按鉴权主体有效读范围过滤 |
| RAG-024 | verified | commit `42bf7e5` + `5cce269`（复审补修：外部向量库探测移入 try、补偿登记尽力而为） |
| RAG-037 | running | commit `cfafcdb`（第一轮）+ `58da8bf`（第二轮闭合高危项）；独立复审进行中 |
| RAG-034 | running | 交付主体 `docs/evaluations/rag-v0.1-审核链与分级定义.md` + `evals/VERSIONS.md` + Gold 24→21；独立审查进行中 |
| RAG-032 | pending | 只读调研完成：ADR-0008 已 Accepted，属实现卡；缺统一索引身份抽象、向量库无身份登记、Milvus `add()` 在 app/ 下无调用点 |
| RAG-028 | pending | |
| RAG-033 | pending | 依赖 `pytesseract` / `pdfplumber` 未安装，中文 OCR 无真实依赖可证 |
| RAG-042 | pending | |
| RAG-015 | pending | Milvus 真实验收预计未验证（无 docker、milvus-lite 与锁定 pymilvus 不兼容） |
| RAG-027 | pending | ADR-0007 已批准，属实现卡 |
| RAG-029 | pending | |
| RAG-036 | pending | Milvus 真实验收预计未验证 |
| RAG-038 | pending | |
| RAG-030 | pending | |
| RAG-035 | pending | 真实模型调用需登记费用上限，预计需升级授权 |

### 批次内新增环境事实

| 项 | 实测 | 影响 |
|---|---|---|
| 全量 pytest 基线 | 截至 `58da8bf`：**783 passed / 2 skipped / 0 failed**（799s） | 后续卡以此为对照；数字只在用户本机终端重采才可信 |
| 沙箱 heredoc 怪癖 | Bash `<<'EOF'` 传给 `python -` 时，**正则里的 `\s` 会被吃掉**（`r'\s'` 实际得到 `s`），导致匹配静默失败 | 在 heredoc 里写 Python 正则要避免反斜杠，改用字符串切分或写临时脚本文件 |
| 日志事件名映射 | `app/security/log_redaction.py` 的 `_minimize_content_record` 对 `app.agents.pipeline` 且带异常的日志，未命中 `_PIPELINE_FAILURE_EVENTS` 时统一落 `agent_pipeline_failed` | 新增管线日志要同步登记事件名，否则不同故障混成同一个事件 |
