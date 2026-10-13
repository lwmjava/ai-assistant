# PAGT-006 评审修复实现说明

日期：2026-10-13。任务继续 `in_review`，等待用户业务验收；没有提交、推送、合并或部署。计划见 [修复计划](plan_pagt_006_review_fixes_20261013.md)，原始发现见 [代码评审](../reviews/2026-10-13-PAGT-006-code-review.md)，独立复审见 [复审报告](../reviews/2026-10-13-PAGT-006-fix-independent-review.md)。本记录补充并覆盖旧实现说明中相同事项的历史结论。

## 实际行为

已登录用户可在自研编排、状态持久化开启时，携带原 `conversation_id`、`resume_message_id` 和原消息内容恢复自己的活动执行。恢复使用原用户消息，不新增用户消息、不重复扣消息配额；非流式响应和 SSE `message` 事件提供消息 ID。旧请求保持兼容。跨用户或租户（包括管理员代恢复）、错误消息拒绝为 404；内容不一致为 400；缺会话由请求校验拒绝；已完成、过期、损坏、功能关闭或同消息正在执行时拒绝为 409。恢复同样经过当前输入安全检查。

执行恢复保留计划、工具观测、指纹和已用轮次；已完成工具指纹在提交 checkpoint 前更新，剩余预算按同一次持久化执行计算，连续中断不能刷新总预算。非法或被拒工具意图也消耗轮次。未注入持久化 store 的既有 QualityGate 每循环预算语义保留。恢复时舍弃旧检索上下文，重新组装当前记忆，并按当前身份重新检索、组装来源；不复用可能已经失去权限的旧 RAG 内容。

短路径、完整路径及低分拒答成功均保存 done；异常保存 failed；取消保留可恢复 checkpoint。恢复读故障不击穿常规执行，显式恢复遇到不可用状态则拒绝，避免降级为新执行。快照先完整校验再应用；数据库轮次等元信息在注入后统一校验。严格工具参数拒绝 object/array 错类型及 bool 冒充整数或数字。

应用启动清理一次过期 checkpoint，之后每 60 秒最多清理 100 条；关闭持久化时不启动清理。删除再次检查过期条件，保留已刷新行；故障回滚，只记录异常类型。停止时等待正在运行的清理线程结束。清理对象仅运行快照，不删除业务消息。

## 文件与发现映射

| 范围 | 实际修改与对应发现 |
|---|---|
| `app/agents/state.py`、`pipeline.py` | F01/F03/F04/F05：快照验证、累计预算、提交顺序、终态；复审 R1：当前检索重建 |
| `app/agents/tools/executor.py` | F06：严格基础类型校验 |
| `app/services/agent_state_service.py` | F05/F07：绑定查询、读故障、清理；复审 R2：数据库元信息先注入后校验 |
| `app/services/chat_service.py`、`agent_execution_guard.py`、`app/api/routes/chat.py` | F02：显式授权恢复、复用消息与配额、进程内互斥、错误及响应契约 |
| `app/services/agent_state_cleanup.py`、`app/main.py` | F07：启动、周期和停止生命周期 |
| `tests/test_agent_runtime_recovery.py`、`tests/test_chat_resume.py` | 40 条恢复、预算、终态、权限、配额、取消、来源、损坏、清理反例 |
| `tests/test_rag_log_minimization.py` | 既有 Session mock 增加真实字典 info，原日志脱敏断言保留 |
| ADR-0010、README、`.env.example`、`docs/swagger.json` | 已批准边界、配置、API 和回滚同步；Swagger 为当前 app 导出，含旧导出缺失的既有端点，不新增相应业务逻辑 |
| `tasks.yaml`、计划、Evaluation、评审与本说明 | 本卡范围与交付证据同步，不关闭其他卡 |

## 实际验证

所有 Python 命令使用 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，数据库为 `data/test_ai_assistant.db` 或专用内存 SQLite。证据级别 E2；脚本模型与合成工具不是 Gold 或真实 LLM 质量评测。Evaluation 版本为 [agent-runtime-recovery-v1](../evaluations/agent-runtime-recovery-v1.md)。

| 实际命令/阶段 | 退出码 | 结果 |
|---|---|---|
| `python -m pytest tests/test_agent_runtime_recovery.py -q`（首批先红） | 1 | 12 failed / 1 passed |
| `python -m pytest tests/test_chat_resume.py -q`（首批先红） | 1 | 10 failed / 1 passed |
| `python -m pytest tests/test_agent_runtime_recovery.py -k current_retrieval -q`（先红） | 1 | 非流式与流式 2 failed |
| 初轮专项与既有预算回归 | 0 | 76 passed |
| 领域回归首次 | 1 | 326 passed / 1 failed，Session mock 的 info 不是字典；已适配且不改脱敏断言 |
| `python -m pytest -q --basetemp=data/pytest-tmp-recovery-full-20261013` | 1 | 1812 passed / 1 failed，默认 native 断言受本机 `.env` 的 langchain 配置影响 |
| `$env:RAG_BACKEND='native'; python -m pytest tests/test_agent_runtime_recovery.py tests/test_chat_resume.py tests/test_rag_backend.py::test_factory_defaults_native -q --basetemp=data/pytest-tmp-recovery-final-target` | 0 | 41 passed（新增 40 条 + 配置隔离失败项） |
| `python -m ruff check . --no-cache` | 0 | All checks passed；首次未关闭缓存因缓存目录访问失败，未当作检查结论 |
| `python -m mypy app/` | 0 | 201 source files，无错误 |
| `npm run typecheck`、`npm run build`（frontend） | 0 / 0 | 类型检查通过，Vite 1957 modules，构建完成 |
| `python docs/export_swagger.py`（开发环境、测试库） | 0 | 请求/响应恢复字段和错误契约已导出 |

最终串行全量命令：`$env:RAG_BACKEND='native'; python -m pytest -q --basetemp=data/pytest-tmp-recovery-final-full`，退出码 0，**1824 passed / 0 failed**，372.28 秒；3 条警告为既有 ast 弃用与极端数值向量测试的 NumPy 警告。YAML 解析、184 卡数量不变、26 个修改/新增文件允许范围、OpenAPI 导出恢复字段对账均退出 0。`git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check` 退出 0；普通 diff --check 将原有 CRLF 根文档的 CR 识别为行尾空白，按原换行格式核对后通过，未忽略真实空格错误。一次早期反例 pytest 曾与首轮全量测试并发，后续最终全量重新串行执行，避免共享测试库互相干扰；首轮失败已定位为本机后端配置，不归因于并发。

## 安全、回滚与未做事项

权限和失败证据包含跨租户、跨用户、管理员代恢复、错误内容、终态/过期/损坏、同消息并发、取消后恢复、清理故障回滚与租户过滤。独立复审先复现 R1/R2 再验证修复，当前没有未解决 P1/P2。README 和 `.env.example` 的三项配置已同步，关闭旧记录的这一偏差。

互斥只适用于同一进程，不证明多实例租约。工具副作用与快照写入之间仍有断电窗口，不承诺 exactly-once。未做 PostgreSQL 生产验证、NULL 兜底多进程压测、web_fetch golden 沉淀、真实付费模型或生产权限撤销评测、前端自动恢复 UI、生产清理与部署。没有改 `.env` 或重要业务数据。

回滚可关闭 `AGENT_STATE_PERSIST_ENABLED` 停止新快照、恢复入口和清理，或回退本轮代码；保留原表和业务消息，无新增 schema 迁移。已经过期且清理掉的快照不恢复，不影响消息记录。新增 SSE message 事件属于已批准的兼容扩展，AgentPipeline 的既有 AgentEvent 主事件契约保持。
