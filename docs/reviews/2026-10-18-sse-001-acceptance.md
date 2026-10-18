# SSE-001 当前版本验收结论

日期：2026-10-18（Asia/Shanghai）。验收对象：`673787f9b792b10542d004449cad99166415b7d9`，分支 `codex/cli-lifecycle-compose-hardening`。本次只补验收脚本、计划、证据和说明，没有修改业务源码、测试、配置、依赖或 CI。开始时 CHAT-006/CHAT-007 的文档及 tasks.yaml 已有修改，均保留。期间出现的 RAG-044 页面报告属于其他工作，不纳入本卡。

结论：**SSE-001 在 ADR-0012 已批准的单进程范围内验收通过，可关闭为 done。** 用户本轮要求代为验证，且原任务 execution 已记录用户委托 Agent 代行验收。本报告为当前版本复核，不冒称人类用户亲自操作，也不授予合并或生产发布权限。

## 原卡对账

| 要求 | 本次核验 | 结论 |
|---|---|---|
| 所有事件带 run_id、递增事件 ID | ActiveRun.emit 统一信封；test_stream_emits_stable_run_id_and_ordered_event_ids | 通过 |
| run 开始和结束、节点进出、intent、plan | 实际管线边界测试及 HTTP 流；正常、异常、取消均有终态测试 | 通过 |
| tool.call 与 tool.result 分开 | 真实合成工具执行前后顺序、call_id 配对、ok 成败、敏感参数及异常不进入公开事件 | 通过 |
| citations | 规范事件实际出现在页面流及回放；不要求本卡实现引用侧栏或证明检索质量 | 通过 |
| heartbeat | HTTP wire 测试确实要求非空 heartbeat 列表，并检查 run_id/ts；测试将等待常量缩短为 0.005 秒。当前源代码 HEARTBEAT_SECONDS=15.0，未用该测试声称实测了真实 15 秒计时精度 | 通过协议及调度逻辑验证 |
| Last-Event-ID 回放、断流不停执行 | 真实 Chromium 页面请求、响应传输故障注入、GET 携游标续流、最终完整正文及刷新回读 | 通过 |
| 主体权限、撤销、取消、有界窗口 | 另一主体查询/订阅/取消拒绝、用户/租户/角色/token_version 撤销、非法及过期游标、缓存与容量、停机 Session 回收测试 | 通过 |

## 当前页面实演

离线服务仅监听 `127.0.0.1:18767`，复用 `tests/manual_sse_ui.py` 与 `docs/reviews/sse_page_probe_20261017.py`。每次启动新建临时 SQLite，Mock LLM/Embedding，禁止外部 DNS/socket，不读项目 .env。合成身份 ui-fixture 走真实 JWT/权限复核；登录 authenticate 夹具替换不证明密码认证。没有操作用户真实会话或发送真实模型请求。

browse 技能的 dist 二进制及 bun 不可用，使用已有 Playwright Core 和本机 Chrome headless 操作真实页面。先执行 `npm.cmd run build`，退出 0；最终成功页面运行在该次新构建之后。

复现命令（项目根目录，指定 Python；服务与页面脚本在两个终端执行）：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -u docs/reviews/sse_page_probe_20261017.py
node docs/reviews/sse_acceptance_20261018.cjs
```

1. 页面提交 `UI_REPLAY_PROBE 请验证流式恢复`。探针在初始响应两个 token 后抛出预设 ConnectionResetError，真实中断 HTTP 响应；这不是暂停网络再手工发送新消息，也不是跨进程重启测试。
2. 原问题只有一次 POST `/api/v1/chat/stream`。浏览器自动 GET `/api/v1/runs/d745976f38cb4248afd2834103be127e/events`，携 `Last-Event-ID: d745976f38cb4248afd2834103be127e:14`，服务返回 200。只发生一次续流 GET。
3. 最终正文与夹具预期完整字符串一致，仅一份；刷新后从可见“打开会话列表”入口重选会话，正文及原助手消息仍在。
4. 同会话另发 `CANCEL_PROBE 验证停止后保存部分正文`，生成中点击“停止生成”；只发一次 cancel POST。刷新并重选会话后，第二条助手消息保留部分文本“隔离界面验”和 stopped 状态，第一条完整回复仍在。
5. 持久化回读恰好四条消息，角色 user/assistant/user/assistant，四个 ID 各异，助手状态 complete/stopped；浏览器 pageerror 为 []。最终脚本及持久化断言均退出 0。

证据：[请求与游标](assets/sse001/browser-requests.json)、[结果与历史](assets/sse001/result.json)、[回放服务记录](assets/sse001/replay-server.json)、[恢复完成](assets/sse001/replay-complete.png)、[刷新完整回复](assets/sse001/after-refresh.png)、[刷新停止回复](assets/sse001/cancelled-refresh.png)。截图内容、身份及消息均为合成数据。

中间尝试如实登记：首次页面脚本因标签带必填星号导致 exact label 匹配失败；改为实际 input name 后恢复通过。随后刷新后点击仍关闭的会话抽屉条目超时；按真实可见入口展开抽屉后最终通过。没有 force click、修改页面状态或删断言制造成功。一次构建前指纹输出路径误以 frontend 为根而失败；未使用该空缺指纹，构建本身实际退出 0，最终页面重新验证。预设传输异常是本验收故障注入，不能作为未预期业务异常归因。

## 检查、版本和 CI

Windows 解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，Python 3.12.0，pytest 8.4.2，Ruff 0.8.4，mypy 1.14.1，Node v22.20.0。非敏感配置、依赖及 510 项输入清单沿用 `2026-10-18-chat-006-evidence.json`，本次重算 **510/510 匹配，0 漂移**；已有全量 2579 passed / 3 skipped、Ruff/mypy、前端四门 165 passed 的证据可复用。本次没有重跑该全量或把旧执行说成新执行；新增文档与独立浏览器脚本不改变上述检查输入。

本次实际专项命令：

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_sse_active_runs.py tests/test_sse_active_runs_review.py tests/test_sse_pipeline_events.py tests/test_stream_session_detach.py tests/test_chat_resume.py --basetemp data/pytest-tmp/sse001-acceptance-20261018 -q --tb=short
```

退出 **0，51 passed，1 warning，15.19 秒**。warning 为既有 ast.NameConstant 弃用。[专项日志](assets/sse001/pytest.txt)。`node --check docs/reviews/sse_acceptance_20261018.cjs`、`git diff --check`、任务 YAML/引用及指纹核对见机器证据。本次只新增文档和浏览器验收脚本，不触发业务输入变更后的全量重跑。

已通过 GitHub 连接器直接查询 actions/runs 的 head_sha 过滤及 jobs，确认 [CI run 39122233048](https://github.com/lwmjava/ai-assistant/actions/runs/39122233048) 为当前 SHA 的 push 运行，completed/success：

- [Backend](https://github.com/lwmjava/ai-assistant/actions/runs/39122233048/job/117659991962)：ubuntu-latest，Ruff/mypy/全量 pytest/RAG gate 均成功；日志明确 Linux Python 3.12.15、pytest 8.3.4；全量 2559 passed / 4 skipped / 4 warnings。五个本卡专项文件均有实际通过行，未被跳过。
- [Frontend](https://github.com/lwmjava/ai-assistant/actions/runs/39122233048/job/117659991912)：ubuntu-latest，类型、Vitest、ESLint、构建各步骤 success。
- [Agent official baseline](https://github.com/lwmjava/ai-assistant/actions/runs/39122233048/job/117659991876)：ubuntu-latest，official evaluation 与 report gate/regression probes 均 success。

[CI 元数据](2026-10-18-sse-001-ci.json)及[Ubuntu 日志节选](2026-10-18-sse-001-ubuntu-excerpt.txt)。本地和 Ubuntu 的全量统计分别记录，不混成同一环境的结果。

更正此前缺证结论：仅返回 PR 触发运行的 fetch_commit_workflow_runs 给空列表，combined commit statuses 也为空，但并不涵盖 push 的 Actions checks；查询完整 Actions 列表后找到上述成功运行。本机直接 Invoke-RestMethod 曾因 TLS 失败，最终用连接器完成核对，未绕过证书校验。

## 审查与边界

复核原卡、产品 §5.1.4–5.1.6、ADR-0012、历史独立审查及当前 ActiveRun/路由/前端传输和相关测试，结合本轮实机及最终 SHA CI，未发现本卡剩余阻断项。历史阶段报告中的缺当前页面、全量及 CI 项由本报告的当前证据补齐，不追改其历史结论。

只证明单进程短期缓存下的传输恢复；不承诺进程重启、多实例、HTTPS/反向代理部署、真实模型质量或检索质量。用户提供截图中的知识库 IndexUnavailableError 不因本卡关闭而视为修复。本次未核验远程分支保护/bypass，不宣称自动合并门禁已配置。回滚仍遵 ADR：停收新 run、等待活动运行、同步回退前后端协议；不删除业务消息。临时服务已停止。
