# ERR-001 独立代码审查与修复回路

审查 Agent：`/root/err_contract_audit`。未实施业务代码；已编写独立验证文件 `tests/test_error_contract_review.py`，因此不兼任最终人工审核 Agent。本报告在工程预审之后，直接检查九场景原契约、实际 HTTP/SSE 错误处理、认证、刷新和会话调用方。最终审查尚未结束，模型边界实现由另一 Agent 进行中。

## 已实际执行的前端生命周期反例

反例直接用 Node 加载项目 `frontend/node_modules/typescript`，将读取时的真实 `frontend/src/lib/http.ts` 及其实际 api-errors/access-token/rate-limit/quota-message 依赖转译为 CommonJS 后在 VM 中执行。仅 localStorage、fetch、timer 和 SessionNotice 保存函数使用合成实现；没有复制 doRefresh 逻辑，没有真实网络、账户、密钥或生产副作用。未改业务/测试文件，未运行 pytest。这属于真实客户端代码加隔离协议后端的 E2 反例，不是浏览器页面证明。

### P1：旧刷新响应覆盖后来建立的会话

操作：存储合成账户 A → 调用真实 refreshTokens()，fetch 等待 → 将存储替换为合成账户 B（等价于 A 登出后 B 登录）→ 返回 A 的刷新成功响应 → 等待真实 Promise 完成。

实际命令为 PowerShell here-string 通过 `node` 执行，退出码0，输出：

```json
{"case":"late refresh after account replacement","accountBPreserved":false,"actualAccess":"account-A-refreshed-access"}
```

当前 doRefresh 抓取 A 令牌后没有会话版本或身份绑定，返回时无条件 writeTokens(next) 并调用 onTokensRefreshed(next)。因此可把 B 的会话换回 A，或者在登出后复活旧会话。auth store 的 sessionRevision 只保护 /me 结果，不保护 http 层写入。旧请求延迟401/terminalIdentity响应也可调用 notifySessionExpired 清掉新的 B，会话绑定必须涵盖成功与失败两侧，不能只阻止旧成功写入。

建议捕获发起请求会话身份/epoch，完成前复核；旧刷新不得更新新会话，旧请求也不得清空新会话。正式回归应覆盖注销后成功返回、替换账户后成功返回、旧请求terminal错误返回、旧失败刷新和新请求并发。

### P2：收到响应头后刷新JSON挂起，15秒超时无效

操作：真实 doRefresh 调用 fetch，隔离响应立即给出 ok=true，但 json() 返回永不完成的 Promise；等到真实代码进入 body 解析，触发代码安装的15秒 timer，再等待微任务结算。

实际 `node` 命令退出码0，输出：

```json
{"case":"refresh headers received, body stalls until deadline","bodyRead":true,"settledAfterDeadline":false}
```

Promise.race 仅覆盖 fetch 的响应头阶段；拿到 headers 后 race 已 settled，随后的 await res.json()/parseError 不再受 deadline Promise 约束。即使调用 abort，在适配器未使 body Promise 拒绝时 refreshPromise 仍永久挂起，后续共享请求全部卡住。当前仅模拟 fetch 不返回的超时测试不能发现这个边界。

建议将取响应、错误正文解析、成功JSON解析和字段校验整个操作包进同一有界 deadline，并保证 finally 释放单飞状态。正式用例必须覆盖响应头已到、正文停滞的成功和失败响应。

## 共享工作区输入变化事件

上述实际反例执行时读取的 http.ts 内容完整。随后继续审查 SSE/组件时，Get-Item/Get-FileHash 发现多份前端文件突然变为空文件，包括 http.ts、api-errors.ts、session-notice.ts、sse.ts、rate-limit.ts、App.tsx；http.ts SHA256 为标准空文件值 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。本 Agent未修改这些文件，已立即通知主 Agent核对共享写入。该事件使当前候选无法作为稳定审查版本，不能把先前的读取/反例伪绑定到随后空文件版本。恢复稳定源码后必须复核修复及最终文件指纹。

本次报告因此为未闭合审查记录，两项缺陷待修复，模型边界、恢复后的前端完整 diff、正式反例红绿与最终输入指纹仍待完成。存在未修复P1/P2时不允许关闭任务。

## 模型边界静态复核补记

已读取模型 Agent 冻结的四文件。Pipeline/Supervisor 在实际异常捕获点用 `error_metadata(exc)` 产生安全码，未通过异常正文分类；ChatService 非流 full/fast 两路消费该码，模型故障先保存 failed assistant，再抛类型化 ModelUnavailableError，由API映射502；执行 claim 的 finally 清理和缓存scope均保持原包装。预算分支仍独立，不误映射模型不可用。附件超限异常子类先于普通校验异常捕获，保留原1MiB阈值，空附件仍400。SSE规范化在API边界，保留原始run_id、event id和顺序，不更改ActiveRun任务所有权。此轮静态未发现新增阻塞，真实回归以主Agent串行执行结果为准。

该轮静态文件SHA256：

| 文件 | SHA256 |
|---|---|
| app/agents/pipeline.py | e82931c2936d6f59ac272cce5d7877b9e6aad8592975648455be4ba19ccefda7 |
| app/agents/supervisor.py | 56546a19813249b19f5f0f62082ca2242c2601b789e42d6f75ff62a4e7e004a6 |
| app/services/chat_service.py | 4f02f7036e64eee0828c6c93124f9992898311c33ca8dbc699cc058d507f3ddb |
| app/services/chat_attachments.py | a76194f27cae4a081943cb6c8d9849123a425f1a7a0ca7547fa72ac431b7dc0a |

另：request_id字符集过滤只能保证格式有界，并不能识别所有凭据（例如短sk-前缀令牌可能符合该字符集），不得把已有Bearer/等号/超长输入反例通过写成任意敏感header完全受控。已向主Agent说明可采用服务端生成ID或准确保留该证据局限。
