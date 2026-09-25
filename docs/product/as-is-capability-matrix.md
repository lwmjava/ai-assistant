# ai-assistant As-Is 能力矩阵

> 对账日期：2026-09-13  
> 代码基线：`main`（工作分支 `docs/gov-001-close-and-rag-001`）  
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
| JWT + RBAC + 多租户 | `Partial` | `app/core/security.py`；API dependencies | 资源级 ACL 未定，见待决策 ADR-KB |
| Docker Compose 启动 | `Partial` | `docker-compose.yml` | 本轮未重跑容器 Healthy 验收 |
| 默认数据库 SQLite | `Implemented` | `app/core/config.py` | PostgreSQL 为生产目标，`Partial`/`Planned` |

## 2. Agent 编排

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| 自研五阶段 `AgentPipeline`（默认） | `Implemented` | `AGENT_ORCHESTRATION=self`；`app/agents/pipeline.py`；`app/services/chat_service.py` | 不是 LangGraph 五阶段主路径 |
| LangGraph Supervisor | `Partial` | `app/agents/supervisor.py` | 可选编排，不是默认主路径 |
| ChatService 组装 Memory/RAG/Tools/Skills/安全 | `Partial` | `app/services/chat_service.py` | 同时承担部分 Harness 职责，未独立提取 |
| 独立 Agent Harness / Context Builder / State Manager | `Planned` | `docs/governance/agent-harness-engineering.md` | 禁止在 RAG baseline 前大爆炸重构 |
| Tool Registry（名称/描述/Schema/函数） | `Partial` | `app/agents/tools/` | 权限、风险、超时、审计、版本未齐 |
| 独立 Tool Executor | `Planned` | 治理规范 §3 | — |
| YAML Skill 加载 | `Partial` | `app/agents/skills/` | Preconditions/Escalation/Evaluation 未齐 |
| Workflow 调度 | `Partial` | `app/workflow/`；`pyproject.toml` extra `workflow` | 未安装 `croniter` 时调度器不可运行；`tests/test_workflow.py::test_scheduler_runnable_and_start_stop` 在本环境失败 |

## 3. RAG

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| 对话默认不注入 RAG | `Implemented` | `RAG_ENABLED=false` | 开启后缺 Chat+RAG 集成验收 |
| 后端默认 `native` | `Implemented` | `RAG_BACKEND=native` | LangChain/LlamaIndex 为可选适配 |
| 向量库默认 Local | `Implemented` | `RAG_VECTOR_STORE=local`；`app/rag/vectorstore/local.py` | Milvus 正式级别待 ADR-VS |
| Milvus 适配 | `Partial` | `app/rag/vectorstore/milvus.py` | 缺摄取/检索/重解析/删除闭环证据 |
| 多格式解析（文本/PDF/Office/OCR） | `Partial` | `app/rag/document_parsers/`；`app/rag/ocr/`；相关 tests | 支持级别以测试为准，禁止写成全格式生产完备 |
| 多策略 Chunking | `Implemented` | `app/rag/chunking/`；`tests/test_chunking.py` | 无评测基线前不新增策略 |
| Dense + BM25 + RRF | `Implemented` | `app/rag/vectorstore/`；`app/rag/retriever.py` | RRF 是融合，不是独立 Reranker |
| 独立 Reranker（Cross-Encoder/LLM/API） | `Planned` | 迭代指导 §4.7 | 必须由同一 Evaluation 证明价值 |
| 文档版本/去重/重解析 | `Partial` | `app/rag/service.py`；import jobs | 与检索权限语义未统一 |
| 结构化 Citation | `Partial` | 回答目前主要是 source 文本 | 缺 document/chunk/version/page/section |
| 版本化 Evaluation | `Planned` | `evals/`、`docs/evaluations/` 仅有目录说明 | `RAG-004`/`RAG-005` |

## 4. 安全、记忆、观测

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| 输入/输出安全与注入检测 | `Partial` | `app/security/`；`SECURITY_BLOCK_ON_INJECTION=false` | 默认检测不阻断；RAG 资料信任标记不足 |
| 对话 Memory 裁剪/压缩 | `Partial` | `app/memory/` | 与 RAG Context 合并需回归 |
| Trace | `Partial` | `app/debug/` | 内存环形缓冲，非持久化生产 Trace |
| 审计 | `Partial` | `app/audit/` | 企业合规报表为 TARGET/EE |
| HITL 统一审批闭环 | `Deferred` | 治理规范 §3 | 高风险能力引入前完成 |

## 5. 仍需 ADR 的问题（本轮不决定）

| ID | 问题 | 为什么现在不能改代码 | 任务 |
|---|---|---|---|
| ADR-KB | 知识库是租户共享，还是用户/资源 ACL 私有 | 列表按上传者过滤，检索按租户过滤，语义冲突 | `RAG-002` |
| ADR-VS | 正式 VectorStore 是 Local，还是把 Milvus 定为生产目标 | 默认 Local；Milvus 缺闭环证据 | `RAG-003` |

未批准前禁止修改过滤条件或切换默认向量库。

## 6. 文档漂移（已确认）

| 文档声明 | 代码事实 | 处理 |
|---|---|---|
| 竞品表写「LangGraph 五阶段」 | 默认 `AgentPipeline` | PRD 竞品表改为当前/目标分列 |
| 竞品表写「混合检索 + 4 重排」 | 仅 RRF，无独立 Reranker | 标 `Planned` |
| OS 矩阵写「RAG = Milvus 单机」 | 默认 Local | 改为 Local 默认、Milvus 可选 |
| 设计方案写 `app/graphs/`、`reranker.py`、Chroma | 真实目录 `app/agents/`、`app/rag/vectorstore/` | 设计方案标为历史稿 |
| 设计方案写 `src/ai_assistant/` | 真实目录 `app/` | 同上 |
| 宣称 Recall/MRR/NDCG 已有 | 尚无数据集和报告 | 标 `Planned` |

## 7. 本轮验证命令

| 命令 | 结果 | 说明 |
|---|---|---|
| `pytest -q --tb=no` | 240 passed，2 skipped，1 failed | 失败项：`tests/test_workflow.py::test_scheduler_runnable_and_start_stop`；日志为未安装 `croniter`（optional extra `workflow`） |
| `cd frontend && npm run typecheck` | 通过 | 2026-09-13 |
| `cd frontend && npm run build` | 通过 | 2026-09-13 |
| `git diff --check` | 以当次工作区为准 | 文档任务 |

该 workflow 失败是环境缺少可选依赖，不是本次文档改动引入。不在本任务安装依赖或降低门禁。
