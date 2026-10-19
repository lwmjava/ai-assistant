# 实施与审查驱动修复记录

## 初始缺口和红测试

2026-10-19，用户批准计划/allowed_paths/ADR-0015后，先运行真实ChatService、LangGraph、SQLite入口。

- 主线10 failed，6.84秒，退出1：注册工具函数零调用；没有确定性not_called、error、timeout或累计预算披露。日志 data/v1c004-baseline-20261019/red.txt。
- 独立入口33例首轮25 failed /8 passed，11.96秒，退出1。8个零调用安全断言在旧实现没有执行能力时通过，不足以证明新路径安全；必须实现后复跑。日志 data/v1c004-baseline-20261019/independent-red.txt。
- 独立工程审查指出原无store行动循环每次重置预算；无契约工具原注册表放行；原摘要来自模型；无技能时SkillRuntime不检查身份；异步队列可能使tool.call消费者撤权晚于实际执行。

## 最小修复计划

1. 研究节点委派既有行动循环；显式请求累计预算，复用指纹和状态，不伪造store、不调用完整Pipeline.run。
2. 新路径有效契约和风险允许集、技能绑定取交集；Service提供与技能无关的fresh主体回调，Agent不读DB。
3. 真实结果驱动摘要和最终确定性披露；保留ToolOutput现有合法状态，独立记录skipped/not_called等委派类别。自定义工具异常不能把内部错误原文放进新增公开摘要。
4. tool.call流式让出控制采用确认关口；实际启动前再次复核，取消/关闭不启动后续节点，审计与实际副作用同时验证。
5. 编码/文档及测试夹具仅按真实能力变化更新，不能删除隔离/拒绝/状态断言。新增输入后绑定候选，执行适用专项、全量、Ruff/mypy及新独立最终审核。

当前为待验证修复计划；未在此宣布上述缺口已经闭合。

## 首版工程审查发现及第二轮红测试

工程身份 /root/v1c004_engineering 静态发现 E01..E05，主Agent独立补正式入口反例实际复现：

- E01/P2：首工具成功后下一模型失败，异常出口丢成功事实。普通/流式两例红。
- E02/P2：Executor瞬时失败后的重试退避期间取消，没有聚合审计。一例红。
- E03/P2：Service空注册表及旧无delegate构造未确定性披露not_called。主线两例红，独立旧构造两例红。
- E04/P1：新工具结果围栏未进入执行护栏，A返回点名B时模型能启动B。真实入口两例红，B发生未授权副作用。
- E05/P1：首次瞬时失败期间撤销主体，重试第二次仍启动。真实入口两例红。
- E06/P2：ChatService外层GeneratorExit未显式关闭内层事件迭代器，等待ack的Supervisor producer残留；真实入口一例红，并出现未回收任务/协程警告。

review-red2：5 failed /24 deselected，4.21秒，退出1；review-red3：5 failed /29 deselected，4.33秒，退出1。日志位于 data/v1c004-baseline-20261019。初版主线green1为7 passed/3 failed，其中三失败是测试误用AgentEvent.content；修为实际data字段后green2为10 passed，5.49秒。独立green1为35 passed/10 failed，6项非法技能版本及2项旧缓存脚本预期将按真实契约修正并保留说明，剩余2项无delegate披露是业务缺口。

修复方案：结果状态在finally增量采集并在异常出口确定性保留；Executor整个逻辑调用覆盖取消审计，新增仅新委派注入的before_attempt回调检查主体/技能（保留旧默认兼容）；工具结果接入既有UNTRUSTED_SOURCE拒绝规则，保留原始用户授权依据，不能只改Prompt；空/旧无delegate路径明确not_called；Service显式aclose所有嵌套事件迭代器并等待producer清理，不吞取消。修后串行复跑上述红例、独立反例和旧调用方，再独立工程复核。

Second repair round: main 34 tests passed (9.52s, exit 0), including the ten formal regression cases from review-red2/review-red3. Independent 49-case validation is running against the repaired code. No statement of full acceptance is made before independent checks, full pytest and static gates.

## 第二轮独立复验与修复

independent-green2：46 passed /3 failed，21.78秒，退出1，无新增警告。两条部分成功用例复现真实误拦截：格式化观测的受信工具名/参数包装被放入不可信资料检测，导致同一工具不同参数的合法再次调用被拒。修复仅提取已执行ToolOutput中受信` 结果=`分隔之后的外部正文用于防注入检查；模型仍看到完整格式的围栏观测，工具A正文点名工具B的正式反例仍须拒绝。

另一条缓存失败源于独立测试额外要求流式第二次所有模型请求零HTTP，这不是原契约。现有流式后台任务继承的临时cache lease失效后安全绕过缓存，未在本卡优化。改为只读观察真实BoundedCache.get命中：普通要求工具提议命中，流式只证明实际模型命中并减少HTTP，均保留第二请求工具/审计各新增的断言；不能声称流式行动提议命中或100%缓存。旧失败及差异保留在独立报告。

## 继续收口

相关契约随后 286 passed / 1 warning，70.63秒，退出0。Ruff 退出0。mypy 先报 chat_service 流式 events 仍可能为 None，给 Supervisor.run_stream 补上异步迭代注解，并在进入循环前收窄后，216 文件退出0。

全量 pytest：1 failed / 2881 passed / 3 skipped / 3 warnings，752.19秒，退出1。唯一失败是 tests/test_rag_028_model_capability.py::test_supervisor_reports_budget_message_not_retry_hint。直接构造、没有委派器的 Supervisor 在预算失败时仍使用「请缩短输入」文案，且不含「请稍后重试」；回答前面多了未调用工具的确定性说明，因此不再与固定文案逐字相等。该文件不在本卡允许路径，尚未修改。全量证据因此不能关闭本卡。

Approved assertion supplement: user explicitly authorized the single budget test file. Only equality became full-message endswith plus not_called, with the no-retry-hint assertion preserved. Whole file: 44 passed, 3.21s, exit 0. Current ten-file regression: 286 passed/1 warning, 76.99s, exit 0; independent final4: 49 passed,20.86s,exit0,no warnings, refreshed current input manifest. Ruff and mypy app/ exited0 (216 files). New unfiltered full is running with 526 frozen inputs and isolated database. Prior Cursor numbers remain user-provided historical evidence.
