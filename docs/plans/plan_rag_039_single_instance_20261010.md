# RAG-039 单实例导入恢复与幂等实施计划

日期：2026-10-10。状态：独立计划审查已通过；报告见 docs/reviews/2026-10-10-RAG-039实施计划审查.md。依据：tasks.yaml RAG-039（当前 HEAD ed438538dd923e3126bbb6fc5e39628566a6e10f），2026-10-09 单实例调整记录。用户本次明确调用 task-card-delivery 并授权多 Agent 实现本卡；不扩展为生产操作、模型费用或发布授权。

## 目标、现状与范围

目标：区分每轮批次与实际并发；同进程异步/线程入口原子抢占、同来源串行发布、running 中断恢复、有限重试幂等和可靠批次聚合。

当前事实：run_import_jobs_once 使用 MAX_CONCURRENCY 作 SQL limit 后逐个 await；pending 状态检查与 running 写入不原子；_process_job 仅捕获 Exception，不处理取消；不存在 orphan running 恢复。已有 Document.import_job_id、attempt_count/max_attempts 可复用。RAGService 摄取/重解析已写向量并 commit，不能沿用 RAG-024 历史说明中“外部写入未接线”的结论。批次无终态时被标 pending，混合失败成功且仍有任务时可过早 partial_success。

只修改 app/rag/import_jobs.py、app/rag/import_scheduler.py、app/rag/service.py、app/core/config.py、.env.example、README.md、tests/、docs/、tasks.yaml（均在卡 allowed_paths）。主 Agent 整合配置、ADR、README、tasks 和交付说明；实现 Agent 独占导入两模块、service.py 最终重解析提交的小范围修改及 tests/test_rag_039_import_recovery.py；独立验证/审查 Agent 使用其自有测试文件。

## 原始契约对账

| 原始要求及出处 | 输入/前置 | 操作链 | 可观察期望 | 证据计划 | 版本 | 结果 |
|---|---|---|---|---|---|---|
| 区分批次/并发；tasks scope.deliverables | RAG-024 已复核 | 配置 batch_size / concurrency → runner | 每轮最多 batch_size，实际同时执行至 concurrency | E2 合成文件+隔离 SQLite 实际摄取；计数 barrier | 当前 HEAD + 最终diff指纹 | 待实施 |
| 原子抢占、幂等；同上 | 单实例已批准 | 多 runner/线程 → 条件 UPDATE → 摄取 | 同 job 一次 attempt，同来源只发布一版 | E2 多任务/真实线程执行入口，重开Session读最终行 | 同上 | 待实施 |
| running 重启恢复；acceptance | orphan running及发布凭据 | runner / start_scheduler → recovery → execute | 未发布重新pending，已发布补终态，次数耗尽failed | E2 重开Engine/Session、实际重建与新进程恢复场景 | 同上 | 待实施 |
| 并发与批次有实测；acceptance/evaluation | 五份合成源文件 | 多任务runner → 聚合 | persisted批次 total/completed/success/failed与子任务一致 | E2 实际摄取，记录最大inflight与轮处理数 | 同上 | 待实施 |
| 不引入队列/OPS/跨实例；non_goals及批准记录 | 既有调度器 | 原调度入口复用 | 无新框架、外部化或跨进程宣称 | 完整diff范围审查 | 同上 | 待审查 |

RAG-024 前置当前 HEAD 基线由主 Agent执行：tests/test_rag_024_publish_atomicity.py + tests/test_rag_import_jobs.py，30 passed，exit 0。关闭前再核对最终变更后的路径与独立审查，不将done本身当证据。

## 方案与选项

推荐最小进程内注册表 + SQL CAS。threading.Lock 仅保护短同步操作，以实际 Engine 对象为绑定维护active job集合、tenant/source占用与全局并发；异步工作不持锁。每个claimed job独立Session，批次limit固定为每轮上限；RAG_IMPORT_BATCH_SIZE 默认2，MAX_CONCURRENCY 默认2。配置和limit必须为有限正整数，禁止bool/NaN/Infinity/负数静默回退。

备选纯asyncio.Lock无法覆盖多个线程事件循环；跨进程租约/新队列违背已批准non_goals，均不采用。

抢占：在同registry锁中查询pending候选、校验身份绑定及attempt上限、排除active来源、UPDATE WHERE status=pending AND attempt_count=原值，rowcount为1才获执行权，attempt_count原子+1，commit后加入active。候选源键由tenant+file来源/URL构成；reparse从文档读取同源键，和新版本上传共用。没有来源时安全串行同tenant未命名来源。遍历pending避免同源占位导致其它来源饥饿。

恢复：每次runner入口和scheduler启动执行短锁内恢复，排除registry active job；新进程registry为空。running若同租户Document.import_job_id有凭据则补success/document_id；否则不足max_attempts重置pending并持久trace，次数耗尽failed。不自动重试已有failed。身份绑定错误failed且不访问源文件/远端。普通retry使用条件UPDATE避免两个线程重置已running状态；次数耗尽明确拒绝，重算batch。

重解析：不加schema，为RAGService.reindex_document_in_place增加可选import_job_id参数，最终commit之前才设置对应job.document_id=target.id、job.status=success，并校验job租户、目标、running绑定；无参数的既有调用保持兼容。job终态与文档/块一同提交，避免提前autoflush持有SQL写锁跨Embedding await；失败/取消rollback后读取真实持久receipt，避免重建成功到任务终态之间的重复副作用窗口。普通摄取用document.import_job_id凭据。在取消、异常和恢复时先判已发布，不能删已发布源文件或把成功翻成失败；未提交URL暂存只清理本attempt未引用副本。CancelledError必须向上传播，未发布任务保留可恢复状态及trace，释放active注册。

batch重算先expire/read持久子任务；未全部终态时任何running或已有完成项标running；全终态后success/partial_success/failed。修改与job终态尽量同事务，避免旧Session identity map聚合失真。

身份与权限：job.tenant/user、batch.tenant/user一致；如果用户存在，活动状态与租户应匹配（系统管理员沿既有跨租户控制权兼容）（历史测试/内部任务无User表行保持既有兼容，API创建仍由鉴权入口保证）；reparse文档tenant必须一致，creator存在时沿用can_write_document。来源storage在现有root安全校验之上按现存tenant目录sanitize规则核对真实resolved目录归属，拒绝其他租户路径；不新增资源ACL。

## 失败边界清单与反例

| 类别 | 边界 | 正式反例测试 |
|---|---|---|
| 数值 | limit/concurrency/batch_size的0/负数/bool/NaN/Infinity/极大值 | 拒绝并保证job仍pending、attempt未变化；有限配置约束 |
| 失败 | 文件缺失、Embedding异常、URL超时、混合成功失败 | 真实摄取或故障注入；旧版本仍可检索，failed trace和batch持久一致 |
| 中断 | await时取消、running重启、发布后终态窗口中断 | 取消传播；新Session/recovery不重复文档/块，source不误删 |
| 权限租户 | job/batch跨租户、reparse目标跨租户、源文件路径跨租户、错误creator绑定 | runner入口拒绝且不读文件/不调用embedding，受害tenant数据不变 |
| 身份绑定 | 切换Engine、active进程任务与orphan区分、reparse来源与上传统一 | 并行两库独立；recovery不重置active；同来源并发一次发布 |
| 回滚幂等 | 失败重试、已发布receipt、重复runner、线程抢占 | 新Session读attempt与文档/块唯一性，实际retrieve旧版，有限重试拒绝 |

Evaluation为版本化Smoke/Adversarial生命周期场景，由真实代码+隔离SQLite+合成embedding提供E2。Mock/合成向量不证明检索质量/真实Milvus生产能力。向量数值NaN/零等由既有Embedding门禁保护，本卡不改算法；本卡数值边界为调度预算。

## 验收、验证与交付

先写上述有效失败测试，记录失败对应行为；最小实现后运行新测试、既有Import/RAG024及适用RAG子集，ruff app/rag、mypy app/rag，项目规定解释器固定D:/DepTooL/anaconda3/envs/ai-assistant/python.exe。每Agent使用独立DATABASE_URL=data/test_rag039_<role>_<uuid>.db与短basetemp=data/t/r39<role><uuid8>，屏蔽真实模型凭据；不读.env、不付费调用。普通sandbox终端创建失败已由主Agent记录，执行采用auto-review认可的require_escalated。

独立Agent从真实导入runner/重试/调度启动入口实际构造反例，记录身份、完整diff/指纹、结果于docs/reviews；P1/P2未修复不得关闭。关闭另写实现说明及原始契约逐项E1/E2证据对账。人工判断仍按skill v1.2保留，不把多Agent全绿替代业务批准。

非目标：不做多进程/多实例、不新队列/分布式锁、不自动failed重试、不重建生产数据、不改默认向量库/检索算法/模型/ACL、不调用付费模型、不Git提交/推送/合并/部署、不扩展OPS任务。

风险与回滚：仅单进程claim保证；外部向量副作用仍不具跨库原子性，沿现有补偿，不声称exactly-once跨系统。取消/失败保留旧读路径与source。停止scheduler后回退本卡代码与配置，无新增schema，无数据迁移；保留导入行/trace，不删除重要数据。方向冲突、allowed_paths越界、无法隔离的基线失败、连续三次无新证据或生产/敏感副作用按AGENTS停止条件上报。



## 独立计划审查补充（实施前）

URL配额：fetch后begin_source_quota、保存源快照、job.storage_path/content_type持久commit，源路径已由used_source_bytes计入配额，短事务立即释放写锁；再解析和Embedding。runner的工作Session使用autoflush=False，不让job parser/hash变更在Embedding await前自动flush。running恢复有同租户快照时复用，不重复抓取/预留；失败清理未发布且未引用的本次快照。增加真实Tenant(storage_limit_bytes非空)+两条不同URL并发入口反例，验证同时inflight与均成功，超限仍拒绝。

until_idle遇容量/同源占用导致单轮0且仍有pending/active时短异步等待后扫描，不能提前宣称idle；取消向上传播，所有claimed但未启动任务也需释放registry并恢复。身份及跨库反例覆盖实际Engine绑定。


## 独立审查 F-01 修复计划

出处：docs/reviews/2026-10-10-RAG-039入口独立审查.md，P2。第二条claim的commit失败可能使第一条已持久running任务留在active，尚未启动执行，until_idle永久等待。正式反例由独立审查Agent建立。修复将整轮claim+launch包进统一异常清理，只释放本轮claimed IDs，持久running保留供下一轮恢复；活跃其它runner不受影响。之后重跑正式反例、导入生命周期与独立入口验证。

## 独立审查 F-02 修复计划

出处：docs/reviews/2026-10-10-RAG-039入口独立审查.md，P2。已存在 import_job_id 文档凭据但来源/租户/用户不一致或已软删时，不能当作无凭据重摄取。正式wrong_source与真实delete_document反例由审查Agent执行为2failed。修复在claim/recovery入口绑定校验查询任务凭据，对已有不一致/软删凭据明确失败并持久trace，禁止新增文档或增加attempt；合法历史非current凭据仍能补终态，不将旧版本重新发布。

## 既有回归日志修复

扩大RAG回归中旧test_rag_log_minimization保留了失败日志脱敏要求。合并异常分支时原generic错误日志被遗漏；恢复仅任务ID+exception_type的rag_import_failed日志，不包含异常正文或堆栈。主Agent将旧虚拟源路径helper测试迁到真实runner入口，保持脱敏和trace断言；不以删断言制造通过。
