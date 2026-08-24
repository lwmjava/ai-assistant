# ai-assistant 接入 LangGraph 与下一步开发规划

## 一、核心结论

- **LangGraph 是设计文档指定的技术栈，不是新方向。** 上游《开源项目改造重构完整设计方案》Phase 1 明确："LangGraph 五阶段管线 + Supervisor 多 Agent 编排"，技术栈表为 `FastAPI · LangGraph · LangChain`。此前"README 声称 LangGraph、代码却自研"的矛盾，原因是代码尚未对齐设计蓝图——接入 LangGraph 实为补齐设计意图，而非引入新框架。
- **采用"局部借用"而非双线并行**：保留现有自研五阶段管线作为主干（已 31 测试、稳定），仅把 LangGraph 作为**子编排层**用于 Supervisor 多 Agent / 长流程，避免双写的重复造轮子与收敛风险；通过 `AGENT_ORCHESTRATION=self|langgraph` 配置切换。

## 二、当前进度对照（ai-assistant 实际状态）

| 设计阶段 | 关键内容 | 状态 |
|---|---|---|
| Phase 0 基座 | ,配置/DB/JWT/RBAC/Channel/Docker | ✅ initial-foundation，已合 main |
| Phase 1 Agent | 五阶段 + 工具 + Skill + Supervisor | ⚠️ 自研五阶段 + 工具调用已合 main；Supervisor / Skill / QualityGate 未做 |
| Phase 2 RAG | Milvus + 混合检索 + 重排 + 引用 | ⚠️ 自研本地版已合；Milvus/重排4策略/父文档/RAG评估未做 |
| Phase 3 工作流 | cron 引擎 + 执行历史 | 🔶 workflow 分支，未合 main |
| Phase 4 记忆与进化 | 记忆压缩/Reflect/Skill进化/蒸馏 | ❌ 全部未做 |
| Phase 5 管理后台 | Admin API + 前端 + SFT 导出 | ❌ 未做 |

## 三、推荐的开发顺序（优先级）

1. **Supervisor 多 Agent 编排（P1-1）** — LangGraph 最直接落点，上游 `supervisor.py` 可迁移为多 Agent 子编排器，复用现有 `Tool`/`Retriever`。
2. **Agent 质量补齐：短路路径 + QualityGate 自纠错** — 补齐 PRD 核心差异化（五阶段含质量门与意图短路）。
3. **Phase 4 记忆与进化** — 对话窗口/压缩、Reflect 反思、Skill 自动改进、夜间蒸馏。
4. **Phase 5 管理后台 API**（物理分离 `/api/admin/*`）— 用户/租户/审计/配额/Feature Flag/模型管理。
5. **RAG 增强** — Milvus 替换 LocalVectorStore、重排 4 策略、父文档检索、查询变换、RAG 评估。
6. **SFT 导出（P1-9）** — 租户对话导出 Alpaca/ShareGPT。

## 四、遗留项与风险
- git 推送无凭据：本地分支仍需 GitHub PAT 才能推远程，或在本机终端手动推。
- 本地分支落后远程 9–19 个提交，动手前建议先 `git fetch` 同步。
- 本次仅为分析，未改动任何文件。下一步建议切到 Plan 模式，将第 1、2 项拆成可执行步骤确认后再动手。
