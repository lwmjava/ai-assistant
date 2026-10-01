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
| JWT + RBAC + 多租户 | `Partial` | `app/models/membership.py`；`app/api/routes/invitations.py`；`frontend/src/pages/Invitations.tsx` | 已注册用户可凭邀请码加入其他租户，并在已加入时切换当前租户。成员角色尚未接入整份权限矩阵。资源级 ACL 仍 `Planned` |
| 管理后台用户/租户/系统状态 | `Partial` | `app/api/routes/admin_users.py`；`app/api/routes/admin_tenants.py`；`app/api/routes/admin_system.py`；`frontend/src/pages/Users.tsx` | 系统管理员可列出用户并改角色、停用，可改租户名并停用，可看数据库与向量库是否连通。不重新启用。无在线人数或调用量 |
| Docker Compose 启动 | `Partial` | `docker-compose.yml` | 本轮未重跑容器 Healthy 验收 |
| 默认数据库 SQLite | `Implemented` | `app/core/config.py` | PostgreSQL 为生产目标，`Partial`/`Planned` |

## 2. Agent 编排

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| 自研五阶段 `AgentPipeline`（默认） | `Implemented` | `AGENT_ORCHESTRATION=self`；`app/agents/pipeline.py`；`app/services/chat_service.py` | 不是 LangGraph 五阶段主路径 |
| LangGraph Supervisor | `Partial` | `app/agents/supervisor.py`；`app/agents/route.py` | 显式打开且已安装 `langgraph>=1.0.0,<1.1` 时，只有明确要求多轮调研才进入 Supervisor；不需要工具和知识库的问题只生成一次。默认编排仍是五阶段管线 |
| ChatService 组装 Memory/RAG/Tools/Skills/安全 | `Partial` | `app/services/chat_service.py` | 同时承担部分 Harness 职责，未独立提取 |
| 对话停止、断线加载、重命名与只读禁发 | `Partial` | `app/services/chat_service.py`；`frontend/src/pages/Chat.tsx`；`tests/test_chat_controls.py` | 流式可停止并留下已停止标记。断线后可加载已写完的回复，未写完不会显示成完成。可重命名和删除自己的会话。viewer 不能发送。不做自动重连，也不把生成放到请求之外继续跑 |
| 独立 Agent Harness / Context Builder / State Manager | `Planned` | `docs/governance/agent-harness-engineering.md` | RAG-005 基线已冻结；仍禁止无指标大爆炸重构 |
| Tool Registry（名称/描述/Schema/函数） | `Partial` | `app/agents/tools/` | 权限、风险、超时、审计、版本未齐 |
| 独立 Tool Executor | `Planned` | 治理规范 §3 | — |
| YAML Skill 加载 | `Partial` | `app/agents/skills/`、`app/services/skill_service.py` | 成员可创建私有关键词技能并在对话中选用；系统管理员可跨租户管理并新增系统全局技能；工具、检索和升级由服务端固定；版本史和技能市场仍未做 |
| Workflow 调度 | `Partial` | `app/workflow/`；`requirements.txt` 的 `croniter`；`pyproject.toml` extra `workflow` | 开关打开且已安装 `croniter` 时，调度任务能创建和取消。到点触发仍没有执行记录 |

## 3. RAG

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| 对话默认注入本租户当前版 | `Partial` | `RAG_ENABLED` 代码默认 `true`；`ChatService._build_retriever`；`tests/test_rag.py`；`frontend/src/components/chat/MessageList.tsx` | 无真实 Embedding 时开发环境用 Mock，只能证明链路。回复可带来源文件名及已有页码或段落。对话页在助手正文下展示这些字段，来源为空时不显示来源区。本机 `.env` 仍可将检索关掉。真实 LLM / Citation 未测 |
| 后端默认 `native` | `Implemented` | `RAG_BACKEND=native` | LangChain/LlamaIndex 为可选适配 |
| 向量库默认 Local | `Implemented` | `RAG_VECTOR_STORE=local`；`app/rag/vectorstore/local.py`；ADR-0002 | 代码默认仍是 Local；正式目标已改为 Milvus，实现未完成 |
| Milvus 适配 | `Partial` | `app/rag/vectorstore/milvus.py`；ADR-0002 | 正式目标；缺摄取/检索/重解析/删除闭环证据；决定已改在 ADR-0002，实现未完成 |
| 多格式解析（文本/PDF/Office/OCR） | `Partial` | `app/rag/document_parsers/`；`app/rag/ocr/`；相关 tests | 支持级别以测试为准，禁止写成全格式生产完备 |
| 多策略 Chunking | `Implemented` | `app/rag/chunking/`；`tests/test_chunking.py` | 基线已冻结；无指标不新增策略 |
| Dense + BM25 + RRF | `Implemented` | `app/rag/vectorstore/`；`app/rag/retriever.py` | RRF 是融合，不是独立 Reranker |
| 独立 Reranker（Cross-Encoder/LLM/API） | `Planned` | 迭代指导 §4.4 | 必须由同一 Evaluation 证明价值 |
| 文档版本/去重/重解析 | `Partial` | `app/rag/service.py`；import jobs；`frontend/src/pages/Knowledge.tsx`；`tests/test_rag_import_jobs.py` | 知识库页可重建未删除文档，已删除文档不能被重建救回。生效日期 Flag 默认关；资源 ACL `Planned` |
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
| ADR-0002 | 正式目标 Milvus（开发 Lite / 生产 2.4+）；Local 为评测与回退；默认在门槛通过前仍为 local | `Partial`：默认仍是 local；Milvus 闭环未证明 | 决定已改在 ADR-0002；实现未完成，门槛见 ADR §5 |
| ADR-0003 | 生效日期 / scheduled 预告检索 | `Partial`：`RAG_EFFECTIVE_DATE_FILTER` 默认关闭；打开后 Local 按 as-of / 查询日期窗口过滤 | 默认现网仍只检索 `is_current`；未做真实嵌入复跑 |

资源级 ACL 仍为 `Planned`。2026-09-19 A1：评测不把同租户当前文档命中记为越权。

## 6. 文档漂移（已确认）

| 文档声明 | 代码事实 | 处理 |
|---|---|---|
| 竞品表写「LangGraph 五阶段」 | 默认 `AgentPipeline` | PRD 竞品表改为当前/目标分列 |
| 竞品表写「混合检索 + 4 重排」 | 仅 RRF，无独立 Reranker | 标 `Planned` |
| OS 矩阵写「RAG = Milvus 单机」 | 默认 Local；正式目标已改为 Milvus（ADR-0002） | As-Is 保持默认 Local；实现闭环前不得写生产已用 Milvus |
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

该行是 2026-09-13 的跑次：当时失败是环境缺少 `croniter`。此后 `requirements.txt` 已列入该依赖，启停用例在装有该包的环境通过。到点触发仍没有执行记录。工作流调度保持 `Partial`。
