# 对话按需创建检索依赖实现说明

2026-10-18。任务 V1C-001，分支 codex/v1c-001-lazy-retrieval，基线 f350e94b3829a0d48f9a0cfce467c01bae5dd662，候选为 HEAD 加 [514 项输入摘要](../reviews/2026-10-18-v1c-001-evidence.json)，未提交或推送。用户已明确批准 [实施计划](plan_v1c_001_20261018.md) 及其允许路径，ADR-0013 已 Accepted。自动验证与独立最终审核已通过，任务in_review，用户业务接受已通过，等待最终提交Ubuntu CI，不声明任务done。

## 实际完成的行为

现有对话入口允许写会话的主体，仍经原身份依赖、输入安全、会话/配额及工具规则执行对话。启用 RAG 时，ChatService 先固定主体和检索读域，在同步 Service 边界校验 tenant 绑定；只创建轻量请求内钩子，不创建 Embedding、VectorStore 或检索后端。

默认 self 的 preflight 短路、langgraph 的普通问答与工具短路径无需检索时，上述三个依赖工厂即使会报错也不会调用。两种 Service 入口的合成问答/真实计算器执行能完成，并通过新 Session 读取最终助手消息和空来源。没有新增模型分流或模型请求；既有路由及 preflight 仍决定是否检索。

默认 self 完整管线、知识库短路径和真实 Supervisor 在调用 retrieve 时才构造原 RAGService/HybridRetriever；每轮请求成功构造一次后复用，初始化失败本轮记为 unavailable 且不自动重建。后续新请求可在依赖恢复后重试。真正检索仍经过原 local/backend、索引身份、tenant/uploader、父块回查、预算选块及来源输出逻辑。

依赖初始化失败时返回空检索文本、清空命中/来源并带 unavailable 终态；原编排逻辑向用户披露“检索服务当前不可用”，不会写成 no_hit。日志只记录新构造异常的类型，不输出异常原文或堆栈。空库仍是 no_hit，历史块缺身份索引仍是 unavailable，候选全低分仍终态拒答；实际保留源文件和旧分块，不补建索引或写入索引身份。

钩子的 last_hits、last_selected、last_status、last_outcome 读取及 last_selected 写回不会触发工厂；选块后的 last_selected 是来源依据。每次 retrieve 先清理前轮元数据，取消原样传播，不因清理失败变成 unavailable。流式取消沿用 stopped 持久化语义，不承诺不留助手记录或撤销已经开始的外部动作。

Reader 副本只包含本轮所需身份字段，不复制密码或 ORM 关系，不入库；Session/User/检索器不做全局缓存。既有 API、ActiveRun 逐事件复核、重新生成前后复核位置保持，不新增普通非流式全程撤权策略。RAG_ENABLED=false 仍直接返回 None，不创建钩子。

## 文件映射

- app/services/chat_retrieval.py：请求内惰性钩子、一次构造、失败状态/来源清理及安全类型日志。
- app/services/chat_service.py：同步读域绑定和 Reader 快照、原服务工厂闭包；只有这一个既有业务文件被修改。
- tests/test_chat_retrieval_lifecycle.py：由主 Agent 构造的真实 Service + 临时 SQLite 入口、工厂故障、四状态、恢复/重新生成、缓存、取消及数值兼容案例。
- tests/test_chat_retrieval_lifecycle_review.py：独立验证 Agent 构造的真实 local 摄取/检索、父块来源、tenant/uploader、选块预算、取消、Supervisor/准备附件及身份快照反例。
- docs/ai-prompts/evals/chat-retrieval-lifecycle-v1.yaml：版本 1.0.0，AI-authored Smoke/Adversarial，将上述行为映射为可执行确定性案例，不是 Gold。
- README.md、ADR-0013 及目录、计划/审查/证据和 tasks.yaml：功能与批准范围同步。

## 验证过程与证据性质

前置 RAG-044 的关键源码/测试指纹与既有修复清单相符；当前基线专项四文件 60 passed / 1 warning，退出 0，Ruff/mypy 均 0。原回归仅用于开工现状核对，不冒充新版本验收。

红测试先针对既有 ChatService 构造行为：32 failed / 1 passed，17.07秒，退出1；失败是依赖构造故障提前中断真实 Service 操作、元数据读触发工厂，并非 import/收集失败。最小实现后首轮 33 passed，退出0。独立首轮8例由主Agent串行执行通过，验证Agent再扩至14例自行运行通过（独立身份及指纹见入口报告）。

扩展专项曾出现 6 failed / 61 passed：5例引用不存在的 parent expansion 配置开关，另1例错误假定流式取消不保存 stopped 记录。检查真实配置和既有 ChatService finally 后，删除不存在配置引用，并明确断言不得保存成功助手、保留记录必须 stopped/空来源；未改变业务逻辑或减弱取消要求。验证Agent首次运行也遇到默认临时目录 WinError5 的 setup 失败，改用新的独立 basetemp 后通过；不将设施失败计作业务通过。初轮Ruff有一处新增import顺序错误，已修复，同一规则最终通过。

最终两文件专项 **85 passed / 1 warning，38.31秒，退出0**。包含：三类工厂在非检索路径零调用；三种真实检索路径的构造失败/空库/无身份索引/低分拒答；原始 ORM 身份变更与缓存开关；请求独立、失败后新请求恢复；有效 checkpoint 恢复与重新生成；NaN/Inf/极端/零值与原 eager 语义相同。数值案例只证明兼容，不声明新增有限数值过滤或算法质量提升。

Ruff全库 `python -m ruff check . --no-cache` 退出0；mypy `python -m mypy app/` 退出0（213源文件）。最终无筛选全量 pytest：2664 passed / 3 skipped / 3 warnings，642.05秒，退出0。三项跳过为既有Windows的符号链接能力、POSIX 0600及打开文件替换身份保护；不证明Linux行为。三项警告为既有ast.NameConstant弃用与数值对抗测试的除法/乘法警告，未屏蔽。

环境：Windows，D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，Python3.12.0、pytest8.4.2、Ruff0.8.4、mypy1.14.1、SQLModel0.0.22、Pydantic2.10.4、langgraph1.0.10。设置 PYTHONUTF8=1、PYTHONIOENCODING=utf-8；conftest屏蔽真实.env/凭据和非loopback网络，正式回归使用测试库与独立basetemp。真实 Service/SQL/local 后端及合成 LLM/MockEmbedding64 为 E2 链路证据，不证明模型、Embedding或生产检索质量。元数据/数值对照等局部断言为E3，不单独支撑必交付。

无前端、API/SSE schema、依赖或运行配置变更，前端四门及新增页面交互验收不适用；不把“没有运行页面”说成页面成功。必交付通过真实 Service 入口及新 Session 最终业务状态验证。没有新增进程、信号、文件系统或平台分支；尚无本次候选最终提交的 Ubuntu CI，不引用旧673787f的成功关闭新业务版本。

实际命令和输出日志：

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_chat_retrieval_lifecycle.py tests/test_chat_retrieval_lifecycle_review.py --basetemp data/pytest-tmp/v1c001-contract-final3 -q --tb=short
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m ruff check . --no-cache
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m mypy app/
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest --basetemp data/pytest-tmp/v1c001-full -q --tb=short
```

原始日志在 data/v1c001-baseline/，摘要与最终输入见机器证据。所有pytest串行运行，不与子Agent并行污染共享库。

## 独立分工、未做和回滚

主Agent负责实现、主线测试、基线/最终门禁和共享文档；/root/v1c001_engineering 负责独立工程准入及只读业务diff复核，未参与实现；/root/v1c001_validation 负责另一正式测试文件及自行执行的入口级反例审查。初始计划P2（不能只靠proxy重抛权限异常穿透三消费者）已通过明确eager Service身份校验边界修订关闭；实现审查未发现待修复P1/P2。最终另由未参与实现及既有审查的Agent出具人工审核结论。

不重构 RAGService 摄取/管理、不变默认self/native/local/RAG开关、不重建真实索引、不改变知识库读域或工具/技能/Supervisor接线、不加表/迁移/供应商/全局缓存。未运行真实模型或质量评测，未读取真实业务数据，未提交、推送、合并或部署。

回滚同时恢复 ChatService._build_retriever 原构造方式并移除惰性钩子，配套恢复README和契约文档；不删除消息、分块或索引。回滚将恢复已知eager初始化隔离缺口，不声称无行为影响。必要CI或用户人工业务接受未齐时保持in_review，不写done/可发布。

最终全量后重算514项输入摘要全部相符；仅补验收文档，未改变验证输入。[关闭对账](../reviews/2026-10-18-v1c-001-acceptance.md)逐项记录E2证据及待验收项；规范化原始日志已随机器证据保存于docs/reviews/，带SHA256。

实际读取并使用的技能：`docs/ai-prompts/skills/task-card-delivery/SKILL.md`（单卡流程、独立审查和证据门禁）、`C:/Users/123/.agents/skills/brainstorming/SKILL.md`（实现前需求与方案澄清）、`C:/Users/123/.agents/skills/gstack/plan-eng-review/SKILL.md`（工程准入审查）。实际分工与对应产物见上文及最终审核报告。

最终独立审核由此前未参与实现或审查的 `/root/v1c001_final_audit` 完成：[审核报告](../reviews/2026-10-18-v1c-001-final-audit.md)。审核Agent独立实跑入口14项与前置索引3项，共17 passed/1 warning，8.88秒，退出0；再次验证514输入和6日志指纹全匹配，无待修复P1/P2。此后只同步审核结论及任务备注，未改变检查输入。任务保持in_review，未验证项原样保留为用户人工业务接受、最终提交Ubuntu CI。

用户业务验收补记：用户明确回复“我已检查普通问答、计算工具和知识库问答，并认可隔离测试证明的故障处理行为。V1C-001 用户业务验收通过”。该项已通过；前文待用户接受为当时历史状态，当前仅最终提交Ubuntu CI待验证。任务保持in_review。本次仅补验收说明，514项检查输入及6份日志摘要全部匹配，沿用原验证结果；未提交或推送。

Git保存授权补记：用户明确要求“提交本轮新增和修改的文件”，授权保存当前改动。本次以待验收交付保存，业务接受已通过，最终提交Ubuntu CI仍未验证，保持in_review；不推送、合并或部署。提交前514项检查输入及6份归档日志摘要全部匹配，沿用原验证结果。
