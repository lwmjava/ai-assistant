# VDB三卡验收修复实现说明

2026-10-21。承接用户“修复三个问题，完成三卡验收”授权；具体方案见[修复计划](plan_vdb_acceptance_fixes_20261021.md)，原始问题见[首次验收](../reviews/2026-10-21-vdb-001-003-acceptance.md)。本说明只陈述已实现行为，任务最终状态以重验报告为准。

## VDB-001

默认仍是Milvus，local只供显式隔离/离线。Milvus启动选择失败时普通API仍启动；健康检查503并显示`runtime=unavailable`及固定脱敏原因。向量读、登记和连接拒绝，不逐请求重新选择、不回退local；恢复需核对配置并重启。JWT和应用数据库等门禁不被吞掉。

真实Standalone验收还发现空库正式搜索旧问题：统一resolver已确认空库，但Milvus包装层仍抛不可用。现在仅该明确空库用专用内部异常返回[]；未知历史分块、错实例、模型不符和运行时不可用仍失败，不转换成no_hit。

位置：`app/main.py`、`app/api/routes/health.py`、`app/rag/vectorstore/runtime.py`、`app/rag/index_registry.py`、`app/rag/vectorstore/milvus.py`。

## VDB-002

已登记Lite也复核开发环境、开关、本机地址、包和文件路径；限制变化拒绝，不自动改用Standalone。已登记Standalone端点改变或不可达也不自动换库。新Lite文件先创建父目录、执行真实SDK只读探针，成功后才保存运行选择；既有Lite文件丢失拒绝，不能创建空替身。SDK导入、连接和清理失败都收敛到脱敏选择错误，失败不保存登记。

索引归属使用已有字符串字段保存模式+规范化地址/绝对文件路径SHA256。缓存键使用同一实例摘要；读、已有索引复用、写索引解析、显式目标写删、连接、激活和历史认领统一核验，差异先于外部副作用或状态变更拒绝。历史无完整实例记录的索引不自动认领。URI内嵌凭据/查询串被拒绝；Token独立配置，不写入实例摘要。

Lite2.5.1不支持GetVersion，健康检查改用该模式支持的list_collections；Standalone保留服务版本探针。

位置：`runtime.py`、`index_registry.py`、`milvus.py`、`cache.py`、`models/rag.py`、`health.py`。不新增列，不升级SDK，不更换向量供应商。

## VDB-003及回归

`tests/test_vdb_acceptance_fixes.py`新增正式反例；独立审查又从激活/已有写索引/历史认领和真实SDK异常入口发现绕过，全部先复现再修复。日志目录`docs/reviews/vdb_fix_20261021/`：首次red 10失败/1通过，review-red 2失败，SDK/空库/健康各1失败，初始化import/cleanup 2失败；最终三卡专项63通过。父目录helper未存在时的AttributeError不单独作为业务失败证据，真实SDK缺目录失败记录才是该根因证据。

独立第三轮63通过，实际反例均拒绝且原索引/notes保持，最终11输入文件指纹无漂移，见[独立审查](../reviews/2026-10-21-vdb-fixes-independent-review.md)。周边回归有162、164及160通过的阶段证据，后续源码变化后不拿阶段数字关闭最终版本。

真实探针`tests/test_vdb_real_modes.py`使用真实Milvus/Lite、独立SQLite/集合、合成provider/model/dim=synthetic/synthetic-vdb-lifecycle-only/8，不调用付费模型。prepare/verify独立进程核对摄取/租户/重解析/删除和持久化，Standalone另重启专用容器。脚本不参与自动普通pytest，不把未执行模式记通过。最终真实、Linux、全量和CI结果集中在重验报告，不以本实现说明替代。

解释器与命令：指定Windows conda Python执行`-m pytest`、`-m ruff check . --no-cache`、`-m mypy app/ --cache-dir data/t/vdb_fix_mypy`，独立测试库；Windows子进程设置`PYTHONUTF8=1`和`PYTHONIOENCODING=utf-8`避免GBK误解码。未改变产品安全或RAG开关掩盖失败。Linux使用专用容器，不冒称Windows原生Lite通过。

## 非目标、兼容和回滚

没有自动数据迁移/清库/双写/重建、没有部署生产、没有Git提交或推送。端点摘要不能证明同地址数据卷从未被替换，卷恢复仍需受控核验。Milvus既有Bounded一致性可能令刚写入向量短暂不可见；真实验收明确记录有限只读等待，不声称强一致或检索质量提升。Milvus保持Partial。

README、ADR-0002/0019和生产手册已同步。关闭兜底不会自动迁移Lite数据；回滚保留文件、集合和登记。旧代码不识别完整实例绑定时需先受控核验，禁止删除归属表消除报错。最终门禁缺失时保持未完成状态。
