# 默认管线上下文组装补修计划

对应 tasks.yaml 的 RAG-029；原实现说明保留历史，本计划补足2026-10-08独立复审发现的实际缺口。业务实施在当前 RAG-032 修复收口后开始。

## 目标与现状

默认 AgentPipeline 接受 tuple 等结构化命中，完整证据块超预算时确定性披露未采用资料，字符串降级时清空上轮 selected。独立复审的三条失败探针见 `.workbuddy/tmp/rev029-20261008/test_independent_review.py` 和 `docs/reviews/2026-10-08-RAG-029按块上下文复审.md`。当前管线维护第二份 list-only 组装逻辑，与已存在的共享入口漂移。

## 方案与影响范围

- `assemble_retrieval` 增加默认空值的 memory 参数，复用既有 Memory/RAG 独立预算；默认管线调用共享入口。保留无 Retriever 时既有合并行为。
- 结构化命中非空但 selected 为空时，管线把既有 `CONTEXT_BUDGET_EXHAUSTED` 加入模型指令和最终确定性披露；同步与流式回复均可见，不伪装为检索无命中。
- 降级路径清空 selected；修正 Context Builder 模块注释中首块截断的过时描述。
- 预计仅修改 `app/rag/retriever.py`、`app/rag/context_builder.py`、`app/agents/pipeline.py`、相关 tests/docs/tasks，均在原卡 allowed_paths。

## 非目标与风险

不修改检索排名、模型配置、字符预算值、父块策略、数据库与前端；selected 仍只代表实际选入，不代表引用正确。不扩大字符串降级来源声明。风险为同步/流式披露重复或记忆丢失，回归覆盖两种入口及已有 Fast Path/Supervisor 行为；可回退本次补修提交。

## 验收

先将三条独立业务反例纳入正式测试并复现失败。新增实际模型消息与最终同步/流式答案断言，证明 tuple 完整块、预算说明、selected 清空及记忆保留。运行相关 Context/Pipeline/Fast Path/Supervisor 回归、ruff 与 mypy；独立子 Agent 复核后再记录 done，另写实现说明及更新 tasks.yaml。
