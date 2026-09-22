# ai-assistant As-Is 能力矩阵

> 对账日期：2026-09-22
> 代码基线：`main`（`GOV-001`～`RAG-006` 已在验收范围内落地）
> 状态术语：`Implemented` / `Partial` / `Planned` / `Deferred` / `Deprecated`  
> 当前事实入口：`AGENTS.md`  
> TARGET 愿景：`docs/product/项目产品需求方案.md` §3.1  
> 历史设计（不可当现状）：`docs/product/项目设计方案.md`

本文只记录**当前代码、配置和测试能证明的能力**。TARGET 愿景不得从本表删除；未证明项不得标 `Implemented`。

## 1. 平台与入口

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| FastAPI 后端 | `Implemented` | `pyproject.toml`；`app/main.py` | — |
| React + TypeScript + Vite 控制台 | `Implemented` | `frontend/package.json`；2026-09-13 `npm run typecheck` / `npm run build` 通过 | — |
| JWT + RBAC + 多租户 | `Partial` | `app/core/security.py`；API dependencies；`app/rag/access.py` | 租户读路径已对齐（ADR-0001）；资源级 ACL 仍 `Planned` |
| Docker Compose 启动 | `Partial` | `docker-compose.yml` | 本轮未重跑容器 Healthy 验收 |
| 默认数据库 SQLite | `Implemented` | `app/core/config.py` | PostgreSQL 为生产目标，`Partial`/`Planned` |

## 2. Agent 编排

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| 自研五阶段 `AgentPipeline`（默认） | `Implemented` | `AGENT_ORCHESTRATION=self`；`app/agents/pipeline.py`；`app/services/chat_service.py` | 不是 LangGraph 五阶段主路径 |
| LangGraph Supervisor | `Partial` | `app/agents/supervisor.py` | 可选编排，不是默认主路径 |
| ChatService 组装 Memory/RAG/Tools/Skills/安全 | `Partial` | `app/services/chat_service.py` | 同时承担部分 Harness 职责，未独立提取 |
| 独立 Agent Harness / Context Builder / State Manager | `Planned` | `docs/governance/agent-harness-engineering.md` | RAG-005 基线已冻结；仍禁止无指标大爆炸重构 |
| Tool Registry（名称/描述/Schema/函数） | `Partial` | `app/agents/tools/` | 权限、风险、超时、审计、版本未齐 |
| 独立 Tool Executor | `Planned` | 治理规范 §3 | — |
| YAML Skill 加载 | `Partial` | `app/agents/skills/` | Preconditions/Escalation/Evaluation 未齐 |
| Workflow 调度 | `Partial` | `app/workflow/`；`pyproject.toml` extra `workflow` | 未安装 `croniter` 时调度器不可运行；`tests/test_workflow.py::test_scheduler_runnable_and_start_stop` 在本环境失败 |

## 3. RAG

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| 对话默认不注入 RAG | `Implemented` | `RAG_ENABLED=false`；`tests/test_chat.py`；`tests/eval/test_rag006_pipeline.py` | 开启后有脚本化 LLM 集成；真实 LLM / Citation 未测 |
| 后端默认 `native` | `Implemented` | `RAG_BACKEND=native` | LangChain/LlamaIndex 为可选适配 |
| 向量库默认 Local | `Implemented` | `RAG_VECTOR_STORE=local`；`app/rag/vectorstore/local.py`；ADR-0002 | 本阶段正式 Local；升格 Milvus 须另开 ADR |
| Milvus 适配 | `Partial` | `app/rag/vectorstore/milvus.py`；ADR-0002 | 实验后端；缺摄取/检索/重解析/删除闭环证据 |
| 多格式解析（文本/PDF/Office/OCR） | `Partial` | `app/rag/document_parsers/`；`app/rag/ocr/`；相关 tests | 支持级别以测试为准，禁止写成全格式生产完备 |
| 多策略 Chunking | `Implemented` | `app/rag/chunking/`；`tests/test_chunking.py` | 基线已冻结；无指标不新增策略 |
| Dense + BM25 + RRF | `Implemented` | `app/rag/vectorstore/`；`app/rag/retriever.py` | RRF 是融合，不是独立 Reranker |
| 独立 Reranker（Cross-Encoder/LLM/API） | `Planned` | 迭代指导 §4.4 | 必须由同一 Evaluation 证明价值 |
| 文档版本/去重/重解析 | `Partial` | `app/rag/service.py`；import jobs；`tests/eval/test_rag006_pipeline.py` | 当前版本读路径已与检索对齐；生效日期 Flag 默认关；资源 ACL `Planned` |
| 结构化 Citation | `Partial` | 回答目前主要是 source 文本 | 缺 document/chunk/version/page/section |
| 版本化 Evaluation | `Partial` | `evals/datasets/rag-v0.1/`；`docs/evaluations/rag-v0.1-baseline-report.md` | 未测生成层；语料规模小，不得当生产质量 |

## 4. 安全、记忆、观测

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| 输入/输出安全与注入检测 | `Partial` | `app/security/`；`app/rag/retrieval_guard.py`；`format_context` 不可信围栏；`tests/eval/test_rag006_pipeline.py` | 默认仍不阻断用户输入注入；真实 LLM 拒答/答案点未测 |
| 对话 Memory 裁剪/压缩 | `Partial` | `app/memory/`；`app/rag/context_merge.py`；`tests/test_context_merge.py` | 长期记忆写入与授权未齐 |
| Trace | `Partial` | `app/debug/` | 内存环形缓冲，非持久化生产 Trace |
| 审计 | `Partial` | `app/audit/` | 企业合规报表为 TARGET/EE |
| HITL 统一审批闭环 | `Deferred` | 治理规范 §3 | 高风险能力引入前完成 |

## 5. 已批准 ADR 与实现缺口

| ID | 决定 | 代码是否已对齐 | 实现任务 |
|---|---|---|---|
| ADR-0001 | 读路径：同租户共享当前版本；写路径：成员仅自己的文档 | `Partial`：`RAG_KB_SCOPE=tenant` 已对齐列表/详情/检索/导入读路径；资源 ACL 仍 Planned | 资源 ACL 另开任务 |
| ADR-0002 | 正式 Local；Milvus 实验/Partial；评测固定 Local | 是（默认已是 local，本阶段不切换） | 升格 Milvus 须另开 ADR |
| ADR-0003 | 生效日期 / scheduled 预告检索 | `Partial`：`RAG_EFFECTIVE_DATE_FILTER` 默认关闭；打开后 Local 按 as-of / 查询日期窗口过滤 | 默认现网仍只检索 `is_current`；未做真实嵌入复跑 |

资源级 ACL 仍为 `Planned`。2026-09-19 A1：评测不把同租户当前文档命中记为越权。

## 6. 文档漂移（已确认）

| 文档声明 | 代码事实 | 处理 |
|---|---|---|
| 竞品表写「LangGraph 五阶段」 | 默认 `AgentPipeline` | PRD 竞品表改为当前/目标分列 |
| 竞品表写「混合检索 + 4 重排」 | 仅 RRF，无独立 Reranker | 标 `Planned` |
| OS 矩阵写「RAG = Milvus 单机」 | 默认 Local | 改为 Local 默认、Milvus 可选 |
| 设计方案写 `app/graphs/`、`reranker.py`、Chroma | 真实目录 `app/agents/`、`app/rag/vectorstore/` | 设计方案标为历史稿 |
| 设计方案写 `src/ai_assistant/` | 真实目录 `app/` | 同上 |
| 宣称 Recall/MRR/NDCG 已有 | 已有 v0.1 检索基线，语料很小 | 可引用分数但必须带有效性限制 |

## 7. 本轮验证命令

| 命令 | 结果 | 说明 |
|---|---|---|
| `conda run -n ai-assistant pytest`（RAG-004/005/006 相关 10 个文件） | 2026-09-22：56 passed | 权限、注入围栏、生效日期、数据集、基线 harness |
| `conda run -n ai-assistant pytest tests/test_rag.py tests/test_rag_import_jobs.py tests/test_rag_backend.py tests/test_chunking.py tests/test_chat.py tests/test_security_smoke.py tests/eval/` | 2026-09-22：113 passed，1 skipped | 文档对账当次扫描 |
| `pytest -q --tb=no`（全量） | 2026-09-13：240 passed，2 skipped，1 failed | 失败项：`tests/test_workflow.py::test_scheduler_runnable_and_start_stop`（缺 `croniter`）；本轮文档修复未重跑全量 |
| `cd frontend && npm run typecheck` / `npm run build` | 通过 | 2026-09-13；本轮未重跑 |
| `git diff --check` | 以当次工作区为准 | 文档任务 |

该 workflow 失败是环境缺少可选依赖，不是本次文档改动引入。不在本任务安装依赖或降低门禁。
