# SSE 运行、回放与显式取消实现说明

日期：2026-10-15。SSE-001，状态 in_review。基线 873db9c0fb243b0847b4be57242c8f333f4f0b1e，分支 codex/platform-batch-20261015，未提交/推送。用户已批准计划及 ADR-0012 的单进程边界和必要文档路径。

## 实际功能

app/services/active_runs.py 拥有独立 asyncio 生产任务与绑定请求数据库引擎的新 Session，HTTP 只是订阅者；断开不取消。当前有效用户可查询、续流或显式停止自己的运行；逐事件及后台心跳复核用户、租户、角色、首改限制、token_version 与会话权限。另一主体无法通过查询或 run_id 冲突探测运行存在。仅保留既有 AUTH_ENABLED=false 的精确合成演示主体，认证重新开启后失效，不创建假用户记录。

所有浏览器事件顶层携带 run_id，SSE id 为 run_id:seq。管线真实边界输出 run/node、intent、plan、tool.call/result、token、citations、error、heartbeat 与 run.finished；规范终态为 done/failed/cancelled。工具调用有配对 call_id，真实执行状态决定 ok，不发送原始参数/观测或异常。错误仅传受控元数据和固定文案，保留已批准 Plan 的受控步骤/错误码诊断；限流映射为单个 rate_limited error、倒计时和不可立即重试语义，拒绝时零新增用户消息。15 秒无输出发送带 ts 的心跳。

同一 UUID run_id 不重复执行；同主体重复提交 409，其他主体统一 404。新增 GET /api/v1/runs/{run_id}、GET .../events、POST .../cancel，RunOut 提供稳定状态/消息绑定/最终回复 schema。Last-Event-ID 非法、未来或过期窗口明确冲突；每运行最多 1000 事件或 8 MiB、进程最多 100 个运行、终态保留 15 分钟。容量仅有活动任务时拒绝新运行，不覆盖旧任务。路由 lifespan 在停机时回收任务；没有改范围外 app/main.py。

取消发生在用户消息已提交、conversation/message 元数据尚未发出时，也从独立 Session 精确恢复绑定；助手回复按精确 assistant_message_id 读取，不能取会话最新一条冒充当前运行。取消保留已经生成的正文并写 stopped 状态，失败写 failed，不伪称完整回复。

前端保存事件 ID、检查连续序号及 run_id、去重增量；首响应或心跳 30 秒超时最多 GET 重连一次，401 只刷新并重开同一订阅，禁止再次 POST 用户消息。显式停止等待取消确认，旧 send/stop/onFinished 晚回调不能重置新轮。界面展示意图、折叠步骤、工具成败及超过 5 秒的耗时；未确认工具结果不伪称成功。断流可查询运行终态并加载绑定回复；错误按 retryable 展示重试，限流仍由倒计时约束发送。

主要代码：app/services/active_runs.py、app/api/routes/chat.py 与 api/router.py；agents/pipeline.py、plan_runtime.py、fast_path.py、supervisor.py、tools/base.py；services/chat_service.py；frontend 的 lib/sse.ts、hooks/useChatStream.ts、StageTracker/MessageList/Chat.tsx 与 API 类型。README、Swagger 和 ADR 已同步或由最终候选导出重新绑定。

## 实际验证与发现修复

指定 Windows Python 3.12.0，PYTHONUTF8=1/PYTHONIOENCODING=utf-8；每项使用全新 basetemp 和测试库。后端 pytest 由主 Agent 串行执行，未让多个 Agent 同时污染共享库。

- 初始流式协议红：tests/test_sse_active_runs.py 2 failed，退出 1；管线边界红 tests/test_sse_pipeline_events.py 3 failed，退出 1。首组隔离遗漏引起真实 Embedding 意外调用，已向用户说明并纠正，完整事实见 docs/reviews/2026-10-15-platform-test-network-isolation.md，不将其当真实模型验收。
- 前端重连红：sse-replay.test.ts 2 failed，退出 1；修后通过。独立传输反例初跑 3 failed/3 passed，证明缺号、缺 ID 与错误 run 终态三项缺口，修后通过。
- 独立运行取消绑定/跨主体 ID 侧信道：2 failed/14 passed，退出 1；精确绑定与授权修后 24 passed，退出 0。关闭认证演示兼容新增业务红 1 failed/3 passed，修后真实/撤销/演示/离线组合 20 passed，退出 0。
- 首轮存量管线组 74 passed/1 failed，失败是旧 wire 信封断言；正确迁移协议后扩展组 80 passed，退出 0。随后存量对话/计划/限流组 49 passed/2 failed，实际暴露受控步骤诊断和过频提示丢失；通过源头 typed metadata 与限流规范映射修复，未删除业务断言。
- 当前联合命令：python -m pytest tests/test_offline_network.py tests/test_sse_active_runs.py tests/test_sse_active_runs_review.py tests/test_sse_pipeline_events.py tests/test_chat.py tests/test_chat_resume.py tests/test_plan_entry_acceptance.py tests/test_stream_session_detach.py tests/test_rate_limit_retry.py tests/test_supervisor.py tests/test_structured_plan_runtime.py tests/test_plan_tool_outcomes.py tests/test_chat_controls.py --basetemp data/pytest-tmp/sse-final-contract-20261015 -q --tb=short：156 passed/1 warning，23.21 秒，退出 0。
- 前端独立传输 7 项与 Hook 4 项实际 11 passed，退出 0；状态机另有规范内容/工具关联、短路/异 run 拒绝、取消未确认工具用例。阶段性前端四门中 typecheck、Vitest 70 项、lint 通过；build 首次暴露局部状态推断过窄，显式标注 StreamSnapshot 后实际 build 退出 0。后续新增回调失败测试仍要最终重跑全部四门，不把旧统计冒充最终输入。
- 实现 Agent 实际局部 Ruff 与 mypy，全 app 207 文件通过；主 Agent 最终全库重跑与候选指纹仍待整合。

独立评审与来源：docs/reviews/2026-10-15-sse-001-review.md；真实页面成功/停止/刷新持久化证据：docs/reviews/2026-10-15-sse-001-ui.md。确定性 Agent Evaluation 版本为本次 SSE protocol Smoke v1 的阶段/工具/错误场景（tests/test_sse_pipeline_events.py 及独立运行/传输用例），仅证明协议与生命周期，不证明模型或检索质量。

## 未完成门禁与回滚

尚缺批次最终源码下不带筛选全量 pytest、最终前后端检查/指纹与独立复核，以及适用 Linux/同最终提交 Ubuntu CI、人工业务判断；页面断网本身未人工实演，已有独立传输/生命周期自动测试。后续 LLM/附件变动必须更新前卡相关证据。保留 in_review，不宣称可发布或整批完成。

单进程内存回放不跨重启/实例，不是新任务队列；长期问答仍按既有消息持久化。未改引用侧栏、RAG 算法/索引或真实供应商。回滚前停止接收新 run、等待活动运行完成并一起回退前后端协议，不删除消息/快照或重要数据。
