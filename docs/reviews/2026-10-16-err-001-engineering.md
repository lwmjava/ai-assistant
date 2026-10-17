# ERR-001 工程与前置审查

审查身份：`/root/err_contract_audit`，独立于本卡实现；2026-10-16。本次只读代码和文档，仅新增本报告；不运行 pytest，不读取 `.env`，不触碰 `.trae/`、整批已有改动或数据库。分支 `codex/platform-batch-20261015`，HEAD `873db9c0fb243b0847b4be57242c8f333f4f0b1e`，实际候选为 HEAD 加工作区文件。遵循 task-card-delivery v1.3.0 的原始契约、前置复核和失败边界要求；本报告按架构、兼容、权限、失败、测试和性能维度审查，未宣称执行 gstack 的宿主专用脚本。

## 原始契约与前置

`tasks.yaml` 本卡为 ready，依赖 SEC-002，限定八种命名错误码与静默刷新成功，共九种场景，不新增错误码、不做管理界面；另要求 Swagger 覆盖全部错误码。PRD §6.6 定义 `{error:{code,message,retry_after,request_id}}`，request_id 不显示给用户，禁止泄露堆栈和内部异常类。前端统一 apiClient 拦截，路由 ErrorBoundary 兜底属于明确契约。

前置 SEC-002 的实现说明 `docs/plans/implementation_sec_002.md` 明确未重写错误码；其历史报告也明确仅打开登录页，没有执行失败页面交互。当前 `app/main.py` 仍安装日志过滤器，`log_redaction.py`、`log_sanitizer.py`、`tests/test_log_redaction.py` 的 SHA256 与 `platform_batch_final_inputs_20261016.json` 完全一致。该匹配仅证明历史测试输入一致，不把历史报告冒称本次执行。主 Agent 已报告本次前置/适用基线 39 通过；该结果应由主 Agent 的命令和日志证据承接，本审查未运行测试。未发现记录中的未决 SEC-002 P1；仍须最终回归确认脱敏和页面无堆栈未退化。

## 当前实现缺口与最小方向

| 场景 | 当前事实 | 本卡需要 |
|---|---|---|
| session_expired | refresh 失效/过期返回 detail；前端丢弃刷新失败原因 | 稳定码、统一文案、会话清理和原请求最多重放一次 |
| token_revoked | refresh 已核对 token_version 并抛 TokenRevokedError，路由只 detail=str(e) | 类型化映射；保留撤销作用域，不扩成 access token 即时撤销 |
| user_disabled | authenticate 提前返回 None；get_current_user 混同不存在/禁用并返回401 | 正确密码后403；错误密码仍一般认证拒绝，不泄露账号状态 |
| tenant_suspended | get_current_user/refresh 已403，登录没有同等校验 | 全屏说明后登出；保留系统管理员既有管理例外 |
| rate_limited | HTTP 与 SSE 有局部 code/retry_after_seconds；前端有限正数解析 | 统一 retry_after，保留兼容字段，有限倒计时，拒绝 NaN/Inf/异常幅值 |
| quota_exceeded | 429 数值字段已有；文案与PRD横幅不同 | 保留数值与事务拒绝行为，横幅和 Admin 用量一致 |
| model_unavailable | SSE 已有安全 metadata.code；非流管线吞异常转 state.error 字符串，服务无条件 complete | typed safe code 贯穿非流入口并502；流内固定安全提示 |
| file_too_large | RAG UploadLimitError 明确413；会话附件统一 AttachmentValidationError 被映射400 | 现有上传入口413统一；附件也应类型化区分超限，保持上限原值 |
| 静默刷新成功 | 现有 refreshPromise 单飞，成功同步store | 无错误提示、并发一次刷新、请求重放和SSE重连保持既有语义 |

现有 App.tsx 只有 Suspense，并无 ErrorBoundary。已有 hideStackTrace 只挡 Python 回溯模式，不能替代固定错误目录和服务端脱敏。已有 request_id 中间件从 X-Request-ID/随机ID建立 log_context，可复用，不另建追踪体系；应绑定响应体与响应头，不把请求头任意文本作为用户提示。

### 非流模型错误是必须解决的入口问题

`AgentPipeline.run` 在异常处理里把异常转成 `state.error = str(exc) or type(exc).__name__`，只剩字符串；`Supervisor.run` 同样仅留下字符串。`ChatService.chat` 在 `pipeline.run` 返回后无条件将 assistant 保存为 `status="complete"`，返回 `(conv, result.answer)`。API 无法从这个结果可靠区分模型不可用、预算或其他执行失败。不能用匹配 answer/error 字符串、检查最新 assistant 的 complete 状态或只 mock service 抛异常来证明真实管线已对齐。

建议最小补充：AgentState 增加安全错误码字段，在两种编排捕获点按异常类型填充；ChatService 依据安全码保存 failed assistant 并向路由抛类型化模型错误，保留已发生的用户消息及既有执行清理。预算拒绝继续独立，不误映射模型故障。原始异常正文不得进入 API payload。该方案是既有错误结果契约的小步补齐，不改变运行所有权。

SSE 已由 `safe_error_metadata` 产出 model_unavailable，可在 API 的事件生成边界重映射固定文案和规范 error 主体，保留外层 code/retryable/run_id/id 等兼容字段；无需为此修改 ActiveRun 所有权。已发送 SSE 200 后的故障只能以 error 事件表达，不能伪称能改已发 HTTP 状态为502。

## 范围与前置审批

原范围 app/api、app/core、frontend/src、tests、tasks.yaml、docs/plans、docs/reviews 足够支持统一目录、HTTP适配、前端解析/兜底和测试。必要补充由主 Agent 统一批准登记后才能修改：

- `app/main.py`：注册全局异常处理，避免通过导入副作用隐式注册。
- `app/services/auth_service.py`：密码校验后区分禁用用户。
- `README.md`、`docs/swagger.json`：AGENTS §11 和本卡 Swagger 验收。
- `app/agents/pipeline.py`、`app/agents/supervisor.py`、`app/services/chat_service.py`：上述非流模型错误安全分类闭环；不能默默越界。
- `app/services/chat_attachments.py`：若覆盖会话附件超限413，增加超限异常子类型，不能按错误字符串猜测。

不需要新依赖、供应商、数据库迁移、架构顶层、真实模型费用、生产操作或新的错误码。当前批次遗留修改不归入本卡；回滚只能撤回本卡增量。

## 入口反例设计与验证门禁

本次是实施前工程审查，以下为设计，尚未执行，不能计为已通过的独立入口反例：

1. 真实 JWT 过期 access + 有效 refresh，HTTP 读取最终成功；两个并发401仅一次刷新，无提示；刷新失败/二次401不无限重放；撤销后区别 session_expired/token_revoked。
2. 真实禁用 User 用正确/错误密码分别403/一般401；停用 Tenant 的 member拒绝而系统管理员管理例外保留。跨租户/无权限撤销拒绝，不因统一错误放宽权限。
3. 注入合成故障 provider 经真实 AgentPipeline.run → ChatService.chat → 路由，模型故障502、failed assistant新Session可见；预算失败和未知异常不能冒充model_unavailable。Supervisor 同类入口单独断言。
4. SSE 首包前和流内模型失败，检查事件 fixed code/message、不含凭据/异常类，最终run failed且失败消息持久化；保留 run_id/cursor 和取消语义。
5. 限流有效正数、0、负数、NaN、Infinity、巨大值；Retry-After/规范retry_after/兼容字段一致且倒计时可结束。配额429后新Session无新增用户消息/文件；前端横幅及Admin数值可观察。
6. RAG multipart 与会话附件各测试上限恰好/超1字节，超限413和展示具体限制；无新文件/DB写入，不删除已有资源。不触碰真实目录或宿主敏感文件。
7. request_id 响应头/体关联；恶意异常正文、响应中内部类名/Traceback、请求ID不出现在页面；路由子树抛渲染异常实际触发ErrorBoundary安全兜底。
8. 页面九场景逐条操作并核对网络、控制台与刷新状态；类型检查/组件断言不能替代页面验收。失败后已有会话可重新读取，刷新幂等不重复创建消息。

最终必须执行无筛选全量 pytest、Ruff、mypy、前端四项并绑定最终输入指纹。旧整批2472通过只属匹配输入的历史证据；修改公共身份/LLM链后不能用它关闭本卡。存在缺失的业务验证、P1/P2、独立入口审查或人工审核时保持未完成。

## 本次指纹（SHA256）

以下为审查时点，不宣称是最终实现版本；主 Agent 后续修改会使相应行失效。

| 文件 | SHA256 |
|---|---|
| task-card-delivery/SKILL.md | 97248dec90e7b927da655e6c336dccd12ae4c634d3cc4ff9e8cc4e9dd806c2dd |
| docs/plans/plan_err_001_20261016.md | 110f62f7f7f4c5917a2eaa4ff23b46548f1b3535aa5e8cebf3c57b8c9d8bba15 |
| tasks.yaml | 31c24138237130f88bfacc03f58c580cf259705c54c219781d9b2a23eecfe969 |
| app/api/deps.py | 5efed5c07d06f19b0d3041b6d6e45a839e08304fd8fbf2f9eef5dcdfd950d0d9 |
| app/api/routes/auth.py | 5f550c766e5687fc0b73ad75545126dd056c801859c22db803746a49ed06c08f |
| app/services/auth_service.py | 8a1a0746f92e8905110c102d0c687b77d112605e2eacbdf36e18d1d075fcf52b |
| app/services/chat_service.py | 91209bb100e4658f7696915e3e496b2f94cc57c11bb30841d4436a53c42c5a2a |
| app/services/active_runs.py | 5ca8534a8403acd2b8e46ea2cec27f8bdee7a41d2dc1df6724347a87ff118452 |
| app/agents/pipeline.py | ccde6299d6a0e279678027813e07ce3588039525c155aa6d9a666ca2e1811020 |
| app/agents/supervisor.py | 195714ad2f4301c7489cd09053a87d29fee200d5bf20fd4bee0235b8aa057ca6 |
| app/security/log_redaction.py | 56d74f5877123f4d85f4902fe9a44f9dacf02be578c0e0301fbc502264c10b69 |
| app/security/log_sanitizer.py | d838864b334a565643aea3898d8389cb48a5da93b6a8a21883f840dace75d0b8 |
| tests/test_log_redaction.py | 3d78f56eff1892a914bfa2dd42799a3fc85a63e7556e39c331f3600dfccce7e7 |
| frontend/src/lib/http.ts | 3aa44f9a31dab7de2ccc383f4874f17f1dac231c3492de3ae09a31625ba3bea7 |
| frontend/src/App.tsx | 21589de8e1ac8cb50224bd92def1a18d93c7a73b607bc876d5c8b40bbac71fbe |

实际命令：Get-Content/rg读取；git branch --show-current 和 git rev-parse HEAD均0；Get-FileHash及历史输入逐项比较均0，三项 historical_match=True。初次尝试不存在的知识库文件名和PowerShell通配rg路径返回路径错误，随后按实际 `app/api/routes/rag.py` 读取；不属于业务失败。未执行测试、浏览器或外部调用，不提供虚构的验证通过结论。

结论：原始九场景方向清晰；计划整体可行，但非流模型故障类型被吞没、disabled分类、会话附件超限和API文档同步需要上述必要范围支持。获得对应补充批准前不得越界实施；即使局部映射测试通过，也不能代替真实pipeline/service入口闭环。

## 独立验证角色补记

主 Agent 随后明确委派本 Agent 编写 `tests/test_error_contract_review.py`，只拥有该新测试文件，不修改业务代码。该文件以真实 HTTP 路由、持久化合成身份、真实配额与限流器、隔离上传目录构造反例；当前 SHA256 `eda9b1327f0eb6b4b40fea8787b61653b5ba34a19f85589fcbbf30157717a120`。覆盖实际系统管理员撤销另一租户成员、新Session复核token_version、旧刷新拒绝及新登录恢复；member/tenant_admin越权撤销无写；禁用用户/停用租户在登录、刷新、me、chat、stream入口拒绝且无业务行；quota/rate拒绝无行；单/批上传size/quota拒绝保留既有文件；敏感X-Request-ID不反射且头体关联；OpenAPI枚举与响应例子。

流内限流正确保留 SSE HTTP200，断言单error事件的规范主体及run.finished failed；非流要求429和Retry-After，不要求已发送的流改变HTTP状态。数值异常边界由主 Agent 的独立正式用例覆盖。

实际执行 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m ruff check tests/test_error_contract_review.py --no-cache`，初次发现两行E501后仅拆行，最终退出码0。按主 Agent 共享数据库串行安排，未自行运行pytest，业务断言仍未验证，待主 Agent记录真实红绿结果；本 Agent已参与测试实现，不能兼任技能要求的最终独立人工审核 Agent。
