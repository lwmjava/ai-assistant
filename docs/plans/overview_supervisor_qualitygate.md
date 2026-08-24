# 概览：Agent Supervisor（LangGraph 局部借用）+ Pref, light 短路 + QualityGate

## 完成内容
在分支 `agent-supervisor`（基于 `workflow`，提交 `cdcdf78`）实现了规划的两项能力（第 1、2 项）：

1. **LangGraph Supervisor（可选子编排）**
   - 新增 `app/agents/supervisor.py`：`SupervisorGraph` 用 LangGraph `StateGraph` 编排 supervisor 路由 + research/draft worker；worker 复用自研 `AgentPipeline` 已有的「行动」工具循环，**不重复造轮子**。
   - `langgraph` 为**可选依赖**，仅在构造时懒加载；缺失时抛出带安装指引的 `ImportError`，`chat_service` 捕获后回退自研管线。
   - `app/services/chat_service.py` 新增 `_build_pipeline`，按 `AGENT_ORCHESTRATION`（self|langgraph）选择编排器。

2. **Preflight 意图短路 + QualityGate 质量门**
   - `app/agents/pipeline.py`：理解后先经 Preflight（`SYSTEM_PREFLOW`）分流，简单问候/闲聊/单轮事实问答跳过「规划/检索/反思」直接作答；复杂问题走完整流程后，QualityGate 对草稿打分，低于 `AGENT_QUALITY_THRESHOLD` 则把评审意见回灌「行动」重跑，至多 `AGENT_MAX_REVISIONS` 轮自纠错。默认关闭质量门（现状的严格超集）。

3. **配置**：`app/core/config.py`、`.env.example` 新增 `AGENT_ORCHESTRATION`、`AGENT_QUALITY_GATE_ENABLED`、`AGENT_QUALITY_THRESHOLD`、`AGENT_MAX_REVISIONS`。

4., **测试**：新增 `tests/test_supervisor.py`，覆盖 Preflight 短路、QualityGate 开关两种阈值路径、Supervisor 边界（缺失依赖报错 / 已装则构造）。

5. **文档**：`docs/plans/后端实现说明.md` 增补模块表条目与刷新测试状态。

## 关键决策
- 采用「局部借用」而非「双线重写」：自研管线为默认主干，LangGraph 仅覆盖多 Agent 协作层，由配置切换 → 避免重复造轮子与不可控。
- README 复核结论：当前分支 README **已无**「LangGraph 驱动 / LangChain」等不实表述（仅「Agent 智能编排 / 五阶段推理管线」），故无需校正；之前担心的文档矛盾在此分支已不存在。

## 验证
- `pytest`：**75 passed / 2 skipped**；新增用例全部通过（Supervisor 构造用例在无 `langgraph` 环境 Skip）。
- **既有失败（与本任务无关）**：`tests/test_workflow.py::test_scheduler_runnable_and_start_stop` 因测试环境未安装可选依赖 `croniter` 而失败，未触碰 workflow 代码，待补装 `croniter` 后可恢复。

## 下一步 / 备注
- 分支已本地提交，**尚未推远程、未合 main**（按约定待你审核）。推送仍需 GitHub PAT（本地凭据不可用）。
- 若希望「接入 LangGraph」真正可用：在运行环境 `pip install "ai-assistant[langgraph]"` 即可启用 `AGENT_ORCHESTRATION=langgraph`。
