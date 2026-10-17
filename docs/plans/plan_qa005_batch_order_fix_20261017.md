# 批次终态测试排序稳定性修复计划

任务 QA-005；用户已批准按明确时间顺序修复。只改 tests/test_rag_039_acceptance.py 的批次终态测试及本任务文档/登记，不改调度器、断言、安全、配置或用户已有 ENV 改动。

现状：调度按 created_at、id 排序，测试却假定 upload 调用顺序等于执行顺序。原失败隔离数据库证实 missing/last 时间相同，last 的随机ID更小，导致首批执行两个正常文件。原始证据见 docs/reviews/2026-10-17-QA-005失败与跳过原因调查.md。

方案：保存三个上传ID，在原设置缺失路径的事务中为三个任务分别赋固定UTC时间及0/1/2秒偏移。保留原批次 running/partial_success、成功失败计数、运行中观察及最终状态断言；不用 sleep。

验收：确定性故障注入在旧测试失败、修复后通过；整份导入验收通过；全量pytest、Ruff、mypy。测试只涉及输入排序，不改变平台行为；Linux最终新提交CI仍需推送后获得，旧CI不得作为本修复的新证据。前端不适用。

基线：HEAD 7cdfb29ae7aa42b103af7856211be89984e658c7；该版本Ubuntu CI全量2510通过/4跳过，但测试排序假设未修正。当前已有ENV文档、验收脚本与tasks改动，全部保留。指定解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe；测试用新隔离库和basetemp。实现后记录命令、退出码、输入指纹与未执行项；QA-005保持in_review直到最终版本门禁满足。
