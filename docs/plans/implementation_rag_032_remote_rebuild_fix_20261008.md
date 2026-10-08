# 新索引远端重建与旧索引保护实施说明

任务映射：RAG-032原卡、ADR-0008；2026-10-08。计划为[整改计划](plan_rag_032_remote_rebuild_fix_20261008.md)。本轮实施Agent `/root/review_context`，因后续承担整改实现，本说明不是独立复审结论；新独立审查由另一Agent执行。

## 实际完成的行为

显式运行重建CLI的运维人员，可把已提交登记且与当前离线/真实provider完整身份匹配的preparing Milvus索引作为写目标。更换模型仍需新索引/collection；旧active在准备、失败及重试期间保留。没有改默认local或生产配置。

Milvus add/delete新增可选keyword-only `target_index_id`：显式目标必须是登记的Milvus active/preparing索引，完整身份匹配；retired、failed、错模型、错维度或块/文档/租户归属错误先拒绝，不在发现后半批非法块之前先写前半批。add连接目标collection，按目标index_id写入。显式目标清理失败会抛出诊断错误；默认active删除仍保留原接口与失败行为，未扩展已完成前卡的产品语义。

Service重建先确认目标身份，再决定是否替换目标同文档块。preparing不清旧active；首次无旧目标块或补偿记录时不做远端清理。重跑同目标同文档清理/替换限定在该目标和文档，其他文档、旧active和retired分块不碰。新分块在SQL提交前写向量库；Local仍复用SQL、add空操作。未知非Local store没有明确目标写能力时保守拒绝preparing，不能默认向active写或删除。

远端部分upsert、目标清理或SQL提交失败时，业务SQL回滚；另一个Session/事务保存目标failed及结构化notes：失败阶段、文档、目标ID、本次新块ID、异常类型、需补偿标志。旧active不改状态。失败目标不能activate。CLI prepare/rebuild保留该补偿记录，显式重试时只清对应目标文档残留，再重建；命令最终失败汇总不会覆盖独立事务留下的new IDs。没有把SQL回滚说成远端事务回滚。

N-02保持保守拒绝：非法历史normalization/metric不自动改写成l2/cosine，需有证据的登记/重建。L-02配置说明与N-01退役时间戳收尾已由5434797/fa49d70完成，本次沿用。

## 文件及契约映射

| 文件 | 交付 |
|---|---|
| app/rag/service.py | 先判目标；preparing限定清理；SQL提交前远端add；业务回滚外补偿记录 |
| app/rag/vectorstore/milvus.py | 显式目标active/preparing校验、整批归属/维度验证、限定目标collection写/删 |
| scripts/rebuild_embedding_index.py | prepare保留补偿notes；rebuild刷新独立失败事务并保留诊断；新增查询按col规约修正类型错误 |
| tests/test_rag_032_remote_rebuild.py | 19条确定性回归/Adversarial案例，真实Service和Milvus路由、假collection边界 |
| scripts/rag_032_test_collection_lifecycle.py | 默认dry-plan；--apply只创建UUID临时库与3个唯一测试collection，真实CLI生命周期，纯合成无费用provider |
| evals/reports/rag-032-real-lifecycle-20261008.json | 最终代码真实Milvus全量重建/旧读/激活/回退/失败/重试机器证据 |
| 本说明及既有说明§7.5 | 旧事实注明历史时点，现状、限制及验证同步 |

## 红绿和命令证据

统一解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。每次pytest前：

```powershell
$run32 = [guid]::NewGuid().ToString('N')
$env:DATABASE_URL = "sqlite:///./data/pytest-tmp/remote32-$run32.db"
$env:PYTHONIOENCODING = 'utf-8'
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest <targets> --basetemp="data/pytest-tmp/remote32-$run32" -q -rs
```

| 实际命令/targets | 退出码 | 结果 |
|---|---:|---|
| test_rag_032_remote_rebuild.py初始正式两项，实施前 | 1 | 两项业务断言失败：旧active远端IDs被删、新target没写入 |
| remote_rebuild + index_identity + rebuild_cli首批 | 0 | 76 passed/1 skipped；新增两项红转绿 |
| remote_rebuild扩展故障11项首跑 | 1 | 9 passed/2 failed，测试漏提交ensure_index注册，回滚后目标不存在；按真实CLI prepare流程先提交修测试设施，不计产品结论 |
| remote_rebuild修正后11项 | 0 | 11 passed |
| remote_rebuild12项 + rebuild_cli | 0 | 24 passed；当时Ruff另报测试import排序，已更正 |
| remote_rebuild（最终15项）+index_identity+rebuild_cli+rag_015_milvus_switch+rag+rag_import_jobs | 0 | 172 passed/1 skipped/8 warnings in 87.34s；warnings是015子进程GBK解码错误，未隐瞒 |
| 显式PYTHONUTF8=1后：rag_015_milvus_switch+rag_clean_compensation+chunking | 0 | 55 passed in 64.29s，无上述子进程警告；确保反例输出实际可读 |
| ruff check --no-cache service.py milvus.py rebuild_embedding_index.py rag_032_test_collection_lifecycle.py test_rag_032_remote_rebuild.py（相应完整路径） | 0 | All checks passed |
| mypy app/rag/service.py app/rag/vectorstore/milvus.py scripts/rebuild_embedding_index.py | 0 | Success: no issues found in 3 source files |
| git diff --check | 0 | 通过 |
| python scripts/rag_032_test_collection_lifecycle.py --apply（两次，每次新UUID） | 0/0 | result=pass；最终报告为ee0df8...运行，首轮af6719...测试集合也保留 |

过程设施失败如实记录：两个扩展pytest命令分别误指定不存在的tests/test_rag_022.py、tests/test_rag_021_structure.py，退出1/no tests ran，不当成业务失败或通过；后按实际文件列表执行上述有效回归。初次mypy脚本提示既有Document.is_current.is_类型错误，改为col后退出0。一次Python归一化脚本写文件权限拒绝（退出1），正规apply_patch落盘成功，最终diff为小范围改动。

唯一skip是llamaindex依赖缺失、fallback native，仍无显式llamaindex runtime身份传递证明；不把skip写成通过。未重跑全量pytest/前端，留给批次整合与最终独立复核。

## 真实测试collection证据

使用本机Milvus v2.5.11；真实pymilvus查询、upsert和CLI，Embedding为显式合成mock provider、模型synthetic-model-a/b/c-failure、8维、offline-unlimited，费用0。测试证明生命周期和副作用隔离，不证明生产语义检索质量。

最终run_id `ee0df8fba55348eaa914cea394ededc7`，全新SQLite位于报告database字段。collection前缀 `rag032_synthetic_ee0df8fba55348eaa914cea394ededc7`。脚本事前核对三集合均不存在，不读取/修改既有业务索引，不执行drop_collection；两轮总计6个新测试集合均保留。

1. A真实摄取3份合成文档，SQL/远端 IDs逐条一致、维度8。
2. B prepare→全量rebuild，B的SQL/远端3条一致，A仍active且原A IDs实际命中。
3. B重复完整rebuild，数量仍3、旧A向量未变；其他目标文档保护另有确定性用例。
4. B activate经过真实抽样检索校验，查询命中B；切回provider A执行rollback，原A IDs仍命中，B集合保留。
5. C真实upsert后注入异常：CLI退出1，目标failed、new IDs补偿记录持久化、activate退出3；A仍真实命中。
6. C重新prepare保留补偿notes→rebuild，限定C文档清理残留，C恢复为3条SQL/远端一致，A仍active可读。

report.commands保存各真实CLI argv/退出码/输出；snapshots保存各阶段SQL/远端ID、维度与active归属；failure_evidence保存独立事务诊断。真实重要数据重建、收费Embedding或生产切换均未执行。

## 上一轮代码SHA256（最终复审整改前历史快照）

| 文件 | SHA256 |
|---|---|
| app/rag/service.py | 36556357ABD439E8F525119238A6A6009FD53305ACF66700A9BB46DA1F374FCE |
| app/rag/vectorstore/milvus.py | 337F05533ECA4477A7F6F646D01FCDD1D995EC7A935A4509B4F29046E72D200A |
| scripts/rebuild_embedding_index.py | 205D5C783EC450DB3D0637E65D757167A633F840C3F00D2D8355E87DFACF4725 |
| scripts/rag_032_test_collection_lifecycle.py | CE7F2DC72B09424DB7452486072C2ECF9CC9F9D8B1F3E978481C2C7CC2A87997 |
| tests/test_rag_032_remote_rebuild.py | A8B23ED60A6EBDBBF4935E53BEE757CC3F8A83239F90FDACB35AAFEE98665121 |
| evals/reports/rag-032-real-lifecycle-20261008.json | 561C04C672A17FF1E3C4FAD3143C4ED84A6FECDC13DA065B2FA674D4ACF2E2E5 |

## 未做、风险及回滚

没有改变resilience/BM25公式、默认local、真实模型、资源ACL/缓存或非法历史身份；没有任务状态更新、提交、推送、部署。llamaindex runtime未验证，新独立复核及批次回归未完成，不依据本说明自动关闭。

SQL与远端仍无共同事务；失败target可能保留孤儿向量，明确标记补偿并阻止激活，重试只清本target同doc。当前active就地重解析的原失败契约未扩展为新事务设计，未宣称跨库原子替换已完成。并发重建控制属于OPS-002，本轮只验证顺序重试幂等效果，不宣称并发幂等。

代码回滚仅撤销本轮目标接口/调用与补偿改动；不删除测试集合或业务数据。生产重要索引重建和清理继续要求明确授权。原索引及其匹配provider配置保留，可用显式rollback恢复旧读路径。

## 最终独立反例整改与本轮最终证据

复审发现三条实际漏洞，已按计划补充逐项整改：

- Service先构造全批候选，再调用Milvus.validate_add无远端I/O的预检；完整身份、块/文档/租户、所有向量维度及有限数值在任何目标清理之前校验。错误候选保留原目标远端与SQL块。add自身重复校验仍保留。
- CLI rebuild --apply拒绝已经active的身份，退出3，active状态不改。原active就地reparse仍支持。
- 显式捕获CancelledError，与普通异常一样回滚SQL并用独立Session登记failed/补偿new IDs；随后重新抛出原取消，不吞取消。

独立实际生命周期还发现默认一致性query可能漏刚upsert的残留，导致失败重试SQL3/远端4。显式目标清理查询增加consistency_level=Strong，按同目标/文档/租户查询并删除。默认active清理保持既有一致性/容错约定。新增确定性边界模拟默认query不可见而Strong可见，重复后SQL与远端ID完全相等；不是以多跑碰运气代替修复。

正式红测初次三失败中CLI是factory monkeypatch位置错误，修正后单项业务红灯：CLI返回0且把active转preparing。其余两个首次即业务红灯：已存在目标向量被清空、取消后target仍preparing/notes空。正式修复后18通过，再增加Strong边界成为19项。

实际命令使用上文统一解释器/全新UUID basetemp与DATABASE_URL，最终明确设置PYTHONUTF8=1、PYTHONIOENCODING=utf-8：

| 命令 | 退出码 | 实际结果 |
|---|---:|---|
| pytest tests/test_rag_032_remote_rebuild.py tests/test_rag_032_rebuild_cli.py tests/test_rag_032_index_identity.py -q -rs | 0 | 93 passed/1 skipped，13.97s |
| pytest 上述3文件 + tests/test_rag_015_milvus_switch.py tests/test_rag.py tests/test_rag_import_jobs.py -q -rs | 0 | 176 passed/1 skipped，95.64s，无GBK子进程warnings |
| mypy app/rag/service.py app/rag/vectorstore/milvus.py scripts/rebuild_embedding_index.py --no-incremental --cache-dir=data/pytest-tmp/mypy32-<新UUID> | 0 | Success，3文件 |
| ruff check --no-cache app/rag/service.py app/rag/vectorstore/milvus.py scripts/rebuild_embedding_index.py scripts/rag_032_test_collection_lifecycle.py tests/test_rag_032_remote_rebuild.py | 0 | All checks passed |
| python scripts/rag_032_test_collection_lifecycle.py --apply | 0/0 | Strong修复后两次不同全新UUID生命周期pass，C失败重试无残留累积 |
| git diff --check -- 上述032代码/测试/计划文件 | 0 | 本卡通过；全仓另卡ADR0002 CRLF曾报错，未越界改动 |

设施失败另记：误指定tests/test_rag_032_cli.py、tests/test_rag_015_milvus.py分别exit1/no tests ran；按rg实际文件修正后执行上述有效命令。mypy默认缓存missing_stubs写权限拒绝exit1，改独立UUID目录后exit0；脚本写CLI文件权限拒绝后用apply_patch落盘。测试新增import排序曾Ruff1，已修为0。上述设施错误不能替代业务结论。

最终实际生命周期run_id `4ce13ffa860745b2ab56f9bb66c7abcb`，报告database与retained_collections记录隔离库和3个新集合。上一轮0c823776...及既有af6719...、ee0df8...均保留；独立复审失败轮efcbbf0...也不得删除。没有读取、重建或删除业务真实索引。费用为0的显式mock/synthetic provider只证明生命周期；不证明生产检索质量。

本轮唯一skip仍为缺llamaindex依赖/fallback native，因此本实现Agent不宣称显式llamaindex后端验收通过；父Agent正在处理后端依赖与独立最终审查。未改任务状态、未提交。

### 本轮最终文件SHA256

| 文件 | SHA256 |
|---|---|
| app/rag/service.py | 7F375C7B9F9BAA298CB2DD1D61C5554652FF728FC4E791377A42F7B43530C16C |
| app/rag/vectorstore/milvus.py | EECB5333AA8B102E50A67ED37FB60807D9A4D7FD6022906A7F853C87A63A2E96 |
| scripts/rebuild_embedding_index.py | 71694C24721BF48B2F2BC8DF34BBD50D89BC00F2B1E36D7EBFD9C189A0597613 |
| scripts/rag_032_test_collection_lifecycle.py | CE7F2DC72B09424DB7452486072C2ECF9CC9F9D8B1F3E978481C2C7CC2A87997 |
| tests/test_rag_032_remote_rebuild.py | AF81A8B6F85D74BD6277349A23D1D927A63EF087A317A92D4A6B684D0FF5CA27 |
| evals/reports/rag-032-real-lifecycle-20261008.json | 70D447909FC51E4E5D7BEDEF612D102E3BE2994B8BE373ADC2F73BC03F72DE75 |
