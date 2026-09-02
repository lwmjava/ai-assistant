# 计划：LangGraph 局部接入 + QualityGate 与短路路径

## 目标
在 ai-assistant 当前 `workflow` 分支（最新代码）上，按「局部借用」思路补齐两项能力：
1. **LangGraph Supervisor 多 Agent 编排**（仅在 Supervisor 层引入 LangGraph，不重写五阶段管线）
2. **QualityGate 质量门 + Preflight 意图短路**（在现有五阶段上增量，默认关闭，开启后为现状的严格超集）

## 分支策略（遵循扁平命名约定）
- 基于当前 `workflow` 新建扁平分支 **`agent-supervisor`**（沙箱 git 不持久化 `feature/` 嵌套名，已多次验证）。
- 自研五阶段管线保持为默认主干；LangGraph 仅作为叠加的「子编排层」，由配置切换，保证离线 Mock 测试不受影响。

## 配置项（app/core/config.py 新增）
- `AGENT_ORCHESTRATION: str = "self"` —— `self`（默认）| `langgraph`
- `AGENT_QUALITY_GATE_ENABLED: bool = False` —— 质量门默认关闭，避免改变现有行为
- `AGENT_QUALITY_THRESHOLD: float = 0.6`
- `AGENT_MAX_REVISIONS: int = 2` —— 质量不合格时的最大自纠错轮数

## 1. LangGraph Supervisor（局部借用）
- 新增 **`app/agents/supervisor.py`**：
  - **懒加载** `langgraph`（在 pyproject 中以 `[langgraph]` 可选 extra 声明），仅当 `AGENT_ORCHESTRATION=langgraph` 且真正构造时 import；导入失败给出明确报错（不污染自研路径）。
  - 定义 `SupervisorGraph`：基于 `StateGraph` 编排 `supervisor` 路由节点 + 若干 worker 节点（`researcher` 复用现有 `ToolRegistry`/`Retriever` 执行检索+工具循环；`reviewer` 复用现有 reflect 提示）。worker 用轻量封装，复用 `AgentPipeline` 已有的工具循环，**不重复造轮子**。
  - 适配层：`Tool`→LangChain `Tool`（基于 `ToolRegistry.describe()`/`run()`），`Retriever` 直接适配。
  - 对外暴露与 `AgentPipeline` 一致的 `run(state)->state` / `run_stream(state)` 契约（`chat_service.py` 的 `get_pipeline()` 按配置返回实现）。
- 当前 `AgentPipeline` 不动（仍是默认路径）。

## ,2. QualityGate 与短路路径（app/agents/pipeline.py 增量）
- **Preflight 短路**：在 `run`/`run_stream` 最前端加一次轻量判断（LLM 或规则），输出 `needs_plan` 标记。若为简单问候/闲聊/单轮事实问答 → 跳过「规划」「检索」「反思」，直接走「响应」（基于模型知识）。新增 `_build_preflight`，`AgentState` 加 `needs_full_pipeline: bool`。
- **QualityGate 质量门**：「行动」产出 draft 后、进入「反思」前，新增 `_build_quality_gate` 评估（覆盖度 / 是否拒答 / 与计划一致性）。低于阈值则甲 critique 追加进上下文并重跑「行动」循环，最多 `AGENT_MAX_REVISIONS` 轮（自纠错）。
- Stream 模式：广播 `stage: 质量门` / `stage: 自纠错` 事件。

## 关键决策
- **不重写整个管线**：LangGraph 仅覆盖「多 Agent 协作」这一小块；其余阶段保持自研，离线 Mock 测试不受影响。
- **QualityGate 默认关闭**：开启后对现有行为为严格超集（不开启则与现状完全一致）。
- 不主动删除任何文件；README「LangGraph 驱动」表述等文档校正留待后续确认。

## 测试（复用现有 phase0 venv；新增 tests/test_supervisor.py）
- Preflight：简单问题走短路、复杂问题走全链路（用 Mock LLM 驱动）。
- QualityGate：注入低质量 draft → 触发修订 → 最终通过（断言修订次数）。
- Supervisor：安装 langgraph extra 后验证路由与 worker 调用；import 缺失时给出清晰错误（测试可 skip）。

## 风险与注意
- LangGraph 版本/API 漂移：用懒加载 + 可选 extra 隔离，自研路径零耦合。
- 测试环境需为 phase0 venv 安装 `langgraph`（或单独 env），否则 supervisor 用例 skip。
- 本地分支落后远程 9–19 提交的是其它分支（非当前 workflow），动手前建议先 `git fetch` 同步。

## 执行顺序（待你确认后，切 Craft 执行）
1. 基于 `workflow` 创建分支 `agent-supervisor`
2. `app/core/config.py` 新增配置项
3. 新增 `app/agents/supervisor.py`（懒加载 + 适配 + SupervisorGraph）
4. 改造 `app/agents/p ipeline.py`：preflight 短路 + QualityGate 自纠错
5. `app/services/chat_service.py`：`get_pipeline()` 按 `AGENT_ORCHESTRATION` 选择实现
6. 新增 `tests/test_supervisor.py` + 扩展 `tests/test_agent.py`
7. 用 phase0 venv 跑 pytest；提交（不自动合 main，待你审核）

## 待跟进
- 是否把 LangGraph 设为默认？默认建议保持 `self`。
- 是否现在就校正 README 中 LangGraph 表述？建议一并处理，但需你确认。
- 后续 Phase 4 记忆与进化、Phase 5 管理后台 API 不在本轮范围。
