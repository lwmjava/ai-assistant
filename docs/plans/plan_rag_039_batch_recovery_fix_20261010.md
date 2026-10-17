# RAG-039 批次汇总提交失败恢复修复计划

日期：2026-10-10。状态：独立计划审查通过，报告见 docs/reviews/2026-10-10-RAG-039批次恢复修复计划审查.md。本轮用户明确授权修复最终审核F03、补回归并复审；沿既有单实例范围，不重复审批方向，不授权生产、模型费用或Git。

## 目标与当前事实

依据 tasks.yaml RAG-039 的“重启恢复与幂等、并发与批次有实测”，以及 docs/reviews/2026-10-10-RAG-039任务卡最终审核.md F03/P2。当前子任务success可先提交，随后finally提交batch；最后batch提交失败时两success任务仍已持久，而batch留running/1/1。恢复入口仅扫描running任务，不能修复无running子任务的汇总漂移。GET批次直接返回持久汇总，用户可永久看到处理中。

当前工作区保留全部前次RAG039与后续QA005改动；本次不回退、不改写其归属。当前import_jobs指纹 DC37295C3F253D02D8FAFB427B0EB74F17687A1E7551AF765ED6D75BEC5A7DBF；既有全仓1364通过为历史证据，不能替代新增F03反例。

## 方案与非目标

推荐在现有_registry短锁内的_recover_locked中，恢复running任务后flush，再对批次按已持久化子任务重新对账。使用既有_recompute_batch规则（pending全未开始；运行/部分完成保持running；全终态success/partial_success/failed），只写实际漂移的聚合字段，与恢复事务commit一起提交。recover返回值继续仅代表恢复running子任务数，不把batch修复算作执行/claim；已success任务不回放、不增加attempt、不触发Embedding/源文件或发布。runner与scheduler沿已有恢复入口自然执行修复，不新增对外API或队列。

安全条件：关联jobs的tenant/user必须与batch一致；异常绑定只留下脱敏诊断，不用其它租户/用户的job重算批次，不更改job。空批次维持原样，不把0/0判success。现存子任务数小于batch声明total_jobs时属于缺任务/构建未完，保留原汇总并诊断，禁止把残缺批次缩为完整success；更多子任务可按实际持久集合修正total。持锁期间对活动job只读取持久running状态并计算batch，不重置job，不回放取消/失败，不释放他人registry。复用原聚合规则可通过抽取纯汇总值helper或提供现有jobs参数降低重复查询；不重复创造状态规则。

非目标：不改变任务状态机/次数/claim/来源串行/receipt；不做原子跨系统事务或新schema；不重试已failed/success任务；不修复删除的子任务或猜测外部批次任务数；不变更权限模型/生产数据/模型/配置/API；不新队列、不跨进程竞争、不Git。

## allowed_paths 与归属

本轮实现者仅写 app/rag/import_jobs.py、tests/test_rag_039_import_recovery.py（或新增 tests/test_rag_039_batch_recovery.py）、本计划、docs/adr/0009-single-instance-import-recovery.md 的技术补记、docs/evaluations/rag039-import-lifecycle-v1.md 的Observed Regression场景。均在原卡allowed_paths。主Agent负责 tasks.yaml 与实际实现说明/最终关闭对账；contract Agent独占复审报告。旧两个测试文件及QA005改动不属于本轮写入。

## 失败边界与验收

| 边界 | 领域入口和反例 | 可观察结果 |
|---|---|---|
| 最后batch commit失败 | create batch2→同源上传2→run_once，故障仅最后汇总commit，恢复入口与until_idle | 红测证明success子任务/唯一文档但batch running1；修复后新Session success2/2，attempt全1，无再次Embedding |
| 全失败/混合 | batch final计数故障或持久漂移，已有success/failed | 恢复failed/partial_success与准确计数，不重放failed |
| active/pending/取消 | 正式runner await期间恢复，或取消后恢复 | active不重置，未终态保持pending/running，attempt/registry不变 |
| 权限与身份绑定 | batch和job tenant/user不一致 | 不聚合异租户/异主体，受害批次保持原样，诊断不含内容 |
| 缺子任务/空批次 | 声明total2仅1success、零子任务 | 不伪造complete/success，不删除/补造任务 |
| 幂等/重启 | 二次恢复及新Python进程恢复terminal jobs的stale batch | 状态/计数持久一致，零重复文档/块/Embedding/attempt，已有job终态保持 |
| 失败回滚 | 恢复阶段batch commit仍失败后重开Session | 恢复异常可观察且事务回滚；后续恢复成功，不能吞错宣称完成 |
| 数值 | batch计数漂移负数/极大值 | 绑定完整且数量不少于声明时按实际校正；不改原调度预算正整数边界 |

证据使用真实入口+隔离SQLite/源文件+合成Embedding（E2生命周期，不证明检索质量）。新增正式反例先运行确认有效红；不能以import/环境错误作业务缺失证据。计划通过后才改业务。

## 验证与交付

解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，独立DATABASE_URL=data/test_rag039_batchfix_<uuid>.db，短basetemp=data/t/r39bf<uuid>；key空、native/local/mock，不读.env、不调用付费模型。实际命令：pytest新增F03与全部039/024/导入/重解析失败/日志相关入口；ruff相关模块和测试；mypy app/rag；git diff --check。报告实际退出码、红绿与文件指纹。独立Agent直接读契约与完整diff运行自构反例，P1/P2残余不得关闭。新进程case使用指定解释器+显式隔离URL/root与空key。

同步ADR0009批次汇总恢复语义及lifecycle-v1 F03 observed regression，不以旧技术“通过”覆盖本次审核失败。最终实现说明与tasks保留人工业务验收未验证，除非用户另有明确授权。

回滚：停止导入调度后仅回退本轮batch修复函数/调用与新增测试/docs补记，保留前次RAG039/QA005和所有任务文档源文件。无schema降级/数据删除；回退后汇总恢复缺口仍在，需要人工核对，不能称业务恢复。

## 查询成本细化

恢复汇总使用一次SQL outer join + group by，按批次取得子任务总数、completed/success/failed/running以及tenant/user的min/max；不逐批次查询或载入完整历史job。绑定校验和缺子任务判定在汇总行上完成。抽取现有纯状态判定helper供原_recompute_batch与修复共用。只修改实际漂移批次；恢复仍需一次历史聚合，复杂度为数据库批次/任务聚合而非N次数据库往返，不添加缓存/新索引/schema。

## 独立计划审查补充：恢复路径缺子任务保护

恢复running的逐job循环不再调用旧_finish_batch（其_recompute会先将声明total缩为现存len）。先完成running job的恢复/trace并flush，之后统一执行带绑定/空/缺子任务guard的批次对账。正常_process_job和retry的既有聚合语义保持本轮非目标。新增反例：batch声明total2，仅1running子任务且有合法发布receipt；恢复job可补success，但batch不得缩total1或变终态success，避免guard失去原声明证据。


## 全仓复验 SQLite 争用调查补记（2026-10-10）

原首次全仓命令结果为 1 failed / 1389 passed / 300.74s，日志 data/rag039_batchfix_full_20261010.log；失败为 test_thread_event_loops_share_actual_process_concurrency_cap，恢复入口 SELECT running 遇到 SQLite database locked。本次不能将此记录写成全仓通过，也不能据单次重跑通过否定它。

事实：失败用例没有 ImportBatch；新增汇总 query 返回空集，不执行 batch UPDATE。单用例复跑 1 passed / 2.18s（退出码0）；独立审查方 current 15次和仅关闭 _reconcile_batches 的15次均通过。未发现 registry 与 SQL 的确定性锁反转；这些观察不能证明不存在其它争用窗口。

进一步以原 fixture 的 SQLite timeout=0.05s、真实3线程/各自eventloop、6个不同源文件和cap2进行可控事务对照。after_cursor_execute 在第二个真实 INSERT INTO RAG_DOCUMENTS 返回后持实际写锁150ms，当前实现及仅 _reconcile_batches no-op 两种参数均在另一线程的 claim CAS UPDATE 抛 database locked，2 failed / 4.79s，退出码1。首次 INSERT 持锁的早期探针两种参数均通过（2 passed / 3.03s）：该时刻执行容量已满且同eventloop同步持锁，其它runner只有读，尚未产生第二写者。独立审查方每次 INSERT 延迟150ms也在两对照均复现争用。该反例证明长于50ms等待预算的真实写事务可以使旧恢复/claim路径失败，F03汇总不是此故障的独有因素；反例失败点 UPDATE 与原全仓 SELECT 不同，不能把二者当成完全相同的已证明堆栈。

按主Agent确认，本轮保持F03范围，不引入busy retry机制、不改SQLite timeout、不删除/降级原并发回归。仅本轮新建的故意超时诊断探针保留在 data/rag039_sqlite_lock_probe_20261010.py，不进入默认pytest收集；它是失败诊断证据，不是F03正式验收通过项。调查后停止并行测试，主Agent串行独占执行全仓复验。最终交付须同时保留首次失败、串行复验结果与SQLite短等待预算在较长事务争用时会失败的残余边界；F03批次对账测试及其既有独立复审结果仍有效，但不替代全仓结论。
