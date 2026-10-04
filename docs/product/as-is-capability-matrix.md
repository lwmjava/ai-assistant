# ai-assistant As-Is 能力矩阵

> 对账日期：2026-10-02
> 代码基线：交付排期第 1–20 项对应任务卡均为 `done`。任务卡完成不等于该行标成 `Implemented`。发布门禁见 `docs/plans/implementation_rel_003.md`，结论是未达到发布合格。
> 状态术语：`Implemented` / `Partial` / `Planned` / `Deferred` / `Deprecated`  
> 当前事实入口：`AGENTS.md`  
> TARGET 愿景：`docs/product/项目产品需求方案.md` §3.1  
> 历史设计（不可当现状）：`docs/product/项目设计方案.md`

本文只记录**当前代码、配置和测试能证明的能力**。TARGET 愿景不得从本表删除；未证明项不得标 `Implemented`。

## 1. 平台与入口

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| FastAPI 后端 | `Implemented` | `pyproject.toml`；`app/main.py` | — |
| React + TypeScript + Vite 控制台 | `Implemented` | `frontend/package.json`；2026-10-02 `npm run typecheck` 与 `npm run build` 退出码 0，见 `docs/plans/implementation_rel_003.md` | — |
| JWT + RBAC + 多租户 | `Partial` | `app/models/membership.py`；`app/api/routes/invitations.py`；`frontend/src/pages/Invitations.tsx` | 已注册用户可凭邀请码加入其他租户，并在已加入时切换当前租户。成员角色尚未接入整份权限矩阵。资源级 ACL 仍 `Planned` |
| 管理后台用户/租户/系统状态 | `Partial` | `app/api/routes/admin_users.py`；`app/api/routes/admin_tenants.py`；`app/api/routes/admin_system.py`；`frontend/src/pages/Users.tsx`；`frontend/src/pages/Tenants.tsx` | 系统管理员可筛选、分页列出用户和租户，从新增页创建，在行末查看详情、修改、停用或重新启用。可看数据库与向量库是否连通。无在线人数或调用量 |
| Docker Compose 启动 | `Partial` | `docker-compose.yml` | 本轮未重跑容器 Healthy 验收 |
| 默认数据库 SQLite | `Implemented` | `app/core/config.py` | PostgreSQL 为生产目标，`Partial`/`Planned` |
| 命令行迁移与创建系统管理员 | `Implemented` | `app/cli.py`；`pyproject.toml` 的 `ai-assistant` 脚本；`tests/test_cli.py` | 空库可迁移到当前版本，并创建可登录的系统管理员。已有系统管理员、密码短于 8 位或两次重叠创建时拒绝，日志不含密码。没有 start、stop、logs。环境变量引导和 `/setup` 仍可用。管理接口仍可创建多名系统管理员。PostgreSQL 未在本能力中演练 |
| 租户消息条数与源文件配额 | `Implemented` | `app/services/quota.py`；`app/api/routes/admin_tenants.py`；`tests/test_quota.py`；`frontend/src/pages/Tenants.tsx` | 系统管理员可在租户列表的配额弹窗设置可空上限。空值不限制，0 表示不能再新增。超限的对话和源文件写入返回 429，且不留下新的用户消息或新文件。软删除不恢复源文件额度。同一 SQLite 上的并发写入不超过上限。页面不展示已用量。不计费，不按月重置，纯文本摄取不设闸。多副本锁未做。 |
| 对话限流倒计时 | `Partial` | `app/security/rate_limiter.py`；`app/api/routes/chat.py`；`frontend/src/pages/Chat.tsx`；`tests/test_rate_limit_retry.py` | 默认关闭。打开后，补充速率大于 0 且打满桶时，对话页显示倒计时并禁用发送，到 0 后恢复。速率小于或等于 0 时拒绝且不倒计时。计数只在本进程，重启清空。多实例未做。知识库页没有倒计时。 |
| 导出租户对话 | `Implemented` | `app/services/export_service.py`；`app/api/routes/export.py`；`frontend/src/pages/Chat.tsx`；`tests/test_export_conversations.py` | 当前租户成员关系上的租户管理员可在对话页下载本租户对话原文 JSON。系统管理员能看到按钮，点击后页面说明无权导出。其他角色得到 403。审计 `export_requested` 写入成功后才开始返回文件。超过 5000 条或 32 MiB 返回 413，不返回半份文件。不做训练格式、筛选或脱敏。 |

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
| 向量库默认 Local | `Implemented` | `RAG_VECTOR_STORE=local`；`app/rag/vectorstore/local.py`；ADR-0002 | 代码默认仍是 Local。Milvus 五条脚本已通过，产品默认未切换 |
| Milvus 适配 | `Partial` | `app/rag/vectorstore/milvus.py`；ADR-0002；`docs/plans/implementation_evd_004_index_params.md` | 2026-09-29 五条脚本通过：`text-embedding-v3`，1024 维，`pymilvus==2.5.11` 对 `milvusdb/milvus:v2.5.11`。默认仍是 local。不标 `Implemented`。产品上传路径未改成自动写入 Milvus |
| 多格式解析（文本/PDF/Office/OCR） | `Partial` | `app/rag/document_parsers/`；`app/rag/ocr/`；相关 tests | 支持级别以测试为准，禁止写成全格式生产完备 |
| 多策略 Chunking | `Implemented` | `app/rag/chunking/`；`tests/test_chunking.py` | 基线已冻结；无指标不新增策略 |
| Dense + BM25 + RRF | `Implemented` | `app/rag/vectorstore/`；`app/rag/retriever.py` | RRF 是融合，不是独立 Reranker |
| 独立 Reranker（Cross-Encoder/LLM/API） | `Planned` | `docs/plans/implementation_rel_004.md` | 2026-10-02 词面覆盖不采纳，本地检索顺序未改。未留下生产开关。`PRAG-002` 已取消。Cross-encoder 仍未做 |
| 文档版本/去重/重解析 | `Partial` | `app/rag/service.py`；import jobs；`frontend/src/pages/Knowledge.tsx`；`tests/test_rag_import_jobs.py` | 知识库页可重建未删除文档，已删除文档不能被重建救回。生效日期 Flag 默认关；资源 ACL `Planned` |
| 结构化 Citation | `Partial` | 回复 `sources` 含文件名；解析结果已有页码或段落时一并返回；对话页展示这些字段 | 缺 document、chunk、version 的稳定引用标识。生成层引用准确率未测 |
| 版本化 Evaluation | `Partial` | `evals/datasets/rag-v0.1/`；`docs/evaluations/rag-v0.1-baseline-report.md`；`docs/evaluations/rag-v0.1-lexical-coverage-20261002.md` | 未测生成层。语料 13 篇 / 37 块。词面覆盖不采纳，不得写成质量提升 |

## 4. 安全、记忆、观测

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| 输入/输出安全与注入检测 | `Partial` | `app/security/`；`app/rag/retrieval_guard.py`；`format_context` 不可信围栏；`tests/eval/test_rag006_pipeline.py` | 默认仍不阻断用户输入注入；真实 LLM 拒答/答案点未测 |
| 对话 Memory 裁剪/压缩 | `Partial` | `app/memory/`；`app/rag/context_merge.py`；`tests/test_context_merge.py`；`docs/plans/implementation_mem_001.md` | 默认窗口下 25 条只滑窗，31 条压缩并保留最后 5 条。对话入口不读取记忆配置。长期记忆写入与授权未齐 |
| Trace | `Partial` | `app/debug/` | 内存环形缓冲，非持久化生产 Trace |
| 审计 | `Partial` | `app/audit/` | 企业合规报表为 TARGET/EE |
| HITL 统一审批闭环 | `Deferred` | 治理规范 §3 | 高风险能力引入前完成 |

## 5. 已批准 ADR 与实现缺口

| ID | 决定 | 代码是否已对齐 | 实现任务 |
|---|---|---|---|
| ADR-0001 | 读路径：同租户共享当前版本；写路径：成员仅自己的文档 | `Partial`：`RAG_KB_SCOPE=tenant` 已对齐列表/详情/检索/导入读路径；资源 ACL 仍 Planned | 资源 ACL 另开任务 |
| ADR-0002 | 正式目标 Milvus（开发 Lite / 生产 2.4+）；Local 为评测与回退；五条通过后才改默认 | `Partial`：2026-09-29 五条脚本通过；默认仍是 local | 不标 `Implemented`。把默认改成 `milvus` 尚未做 |
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
| 交付计划与拆分文首曾写 80% 未达到、五条未执行、B3–D4 尚未实现 | 2026-09-29 起 16/16；五条脚本通过，默认仍 local；2026-10-02 起第 17–20 项任务卡 done | 2026-10-02 已改仍被当作当前事实的段落。点状实现说明保留当时结果 |

## 7. 验证命令

最近一次发布门禁是 2026-10-02，见 `docs/plans/implementation_rel_003.md`。本表这次只改文档，没有重跑这些命令。

| 命令 | 结果 | 说明 |
|---|---|---|
| `python -m pytest -q --tb=no` | 2026-10-02：退出码 4294967295 | 收集 521 条，打出 164 个标记（17 error、10 failed）后卡住，没有汇总行。发布结论是未达到发布合格 |
| `ruff check .` | 2026-10-02：退出码 1 | 同上 |
| `python -m mypy app/` | 2026-10-02：退出码 1 | 同上 |
| `npm run typecheck` / `npm run build` | 2026-10-02：退出码 0 | 工作目录 `frontend/` |
| `scripts/milvus_five_gates.py` | 2026-09-29：退出码 0，`all_passed true` | 嵌入 `text-embedding-v3`，1024 维。默认向量库仍是 local |

更早的全量 pytest（2026-09-13：240 passed，2 skipped，1 failed）失败项是当时环境缺少 `croniter`。此后 `requirements.txt` 已列入该依赖，启停用例在装有该包的环境通过。到点触发仍没有执行记录。工作流调度保持 `Partial`。
