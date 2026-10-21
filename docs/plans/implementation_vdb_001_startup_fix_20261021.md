# VDB-001 启动隔离修复说明

用户2026-10-21授权修复验收问题。按 plan_vdb_acceptance_fixes_20261021.md 顺序先完成启动隔离，再推进VDB-002。

Milvus选择失败现在记为进程内向量不可用，普通API继续服务，健康检查报告503、runtime=unavailable和固定脱敏原因。向量读索引、登记和连接拒绝，不逐请求换库或回到local。JWT和数据库等原有启动门禁不被吞掉。修改位于runtime.py、main.py、health.py、index_registry.py；ADR-0019已对齐。

失败测试先执行，全部验收反例10失败/1通过（red.log）；修复本项后，指定解释器以独立DATABASE_URL执行 `-m pytest tests/test_vdb_acceptance_fixes.py::test_startup_failure_keeps_non_retrieval_api_available tests/test_health.py -q --basetemp data/t/vdb_fix_startup`，8通过，退出0（docs/reviews/vdb_fix_20261021/startup.log）。该测试走真实TestClient lifespan，调度器边界被隔离，不代替真实服务验收。

未关闭VDB-001：真实服务、全量回归、最终Linux/CI及最终指纹仍待组合验证。未修改生产库、未付费调用、未提交。
