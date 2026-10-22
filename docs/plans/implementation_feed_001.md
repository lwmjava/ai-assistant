# 对话反馈实现与交付记录

## 授权、基线与范围

2026-10-22用户明确授权关闭EVAL-001后开始FEED-001。基线00ea6ee691f0df1c178de5c05729fd810e81bf2c，分支codex/eval-001-report-query。已有五个文档/台账修改是EVAL-001的CI补证记录，保留；本轮不擅自提交或切分支。

FEED-001范围沿用tasks.yaml：反馈表/迁移、失败反馈和有用/无用提交/查询/删除、对话页入口与入库前脱敏；不做语料快照、入集、反思/蒸馏消费、自动Gold或Prompt/模型修改。EVAL-002不提前实现。

## 现状与阻断的契约冲突

app/models/conversation.py只有Conversation/Message；未发现反馈业务表或接口。ChatRequest在app/api/routes/chat.py中，run_id为可选UUID，重复提交拒绝；当前有查询/订阅/显式cancel接口与resume_message_id续跑绑定。

原docs/plans/plan_chat_feedback.md（2026-10-01/03）阶段1要求客户端强制32位十六进制attempt_id，pending同ID可复用，断线与停止均stopped。Accepted ADR-0012（2026-10-15）及app/services/active_runs.py明确HTTP订阅断开不取消生产任务，重连只回放，不重跑；停止由cancel接口显式执行。共享拆分计划第FEED-001节明确要求发现这类方向冲突先停，不自行选择历史实现。

不能把正常断线重连落成失败/停止反馈，也不能为新增反馈破坏当前run去重、取消或续跑契约。用户现已授权做反馈，但尚未裁决这项旧文档与新状态协议的适配。

## 建议方案（待用户决定）

推荐沿用现行SSE契约，修订旧反馈方案的冲突条款并先补ADR-0012兼容说明：

- 保留run_id、事件回放、断线继续与显式取消；断线本身不产生失败反馈。
- 新增持久化ChatAttempt作为反馈关联记录，与现行run及用户消息/助手消息绑定，不替代或另造ActiveRun状态机。
- 新旧客户端兼容：不新增强制attempt_id造成破坏性400；服务端为非流式/未提供标识的请求生成稳定尝试标识，前端使用已有run_id关联。精确ID映射、续跑是否形成新attempt在补充契约中锁定，不能重复执行来恢复反馈状态。
- 仅服务端确定的api_error/no_response可提交失败反馈；显式cancel不可反馈为失败。无输出超时独立于断线，优先适配现有生产Task生命周期，不能给订阅流加一个会取消模型的本地计时器。
- 有用/无用仅所有者对成功完成助手消息提交；系统管理员不能代替用户提交。评论入库前按既有InputFilter再LogSanitizer处理，数据库唯一约束负责幂等/并发，返回固定脱敏Schema。

备选为严格按旧反馈协议引入强制attempt_id并改现有聊天生命周期，影响现有SSE客户端、取消和重连，需重新批准更大兼容迁移；不推荐。

## 后续实施与验证

用户裁决后补齐模型字段、状态映射、接口/查询/删除权限、迁移、失败边界与回滚的具体方案，再执行业务基线。涉及持久化与公共聊天链，执行Ruff/mypy/全量pytest、前端四项、必要Linux迁移/取消/持久化测试和最终同SHA CI，独立审查。使用指定conda解释器、隔离测试库，不操作开发主库或生产数据。已有00ea6ee的通过CI可作为原提交版本证据，新的业务输入必须重新验证。

当前仅完成开工调查，未修改业务代码/测试/依赖/CI，未运行或声称新业务检查通过。按AGENTS.md第12节等待方向裁决；不以任务in_progress冒充实现完成。回滚先回退路由和迁移接线，重要反馈数据删除另授权。

## 2026-10-22批准后的锁定实施方案

用户明确回复“可以，按方案实施”，批准上述SSE兼容适配。先补ADR-0012及原反馈详细方案的增量优先条款，不恢复旧断线即stopped语义。允许路径沿用卡片，README扩展单独询问，未答复前不写该文件的FEED内容。

ChatAttempt：id(32hex)、tenant_id/user_id、conversation_id/user_message_id/assistant_message_id可空、outcome(pending/success/api_error/no_response/stopped)、created_at/updated_at。流式id=规范化run_id，非流式复用可选run_id或服务端生成；续跑/重新生成是新执行、新尝试，回放不新建。新请求复用持久化id返回409，不重新执行。服务端第一次用户消息提交后开始90秒首token/终态预算，token后不再用首输出预算；无输出超时独立于断线并留下no_response记录。断线不取消，显式取消stopped；没有token不写空助手消息。

ChatFeedback：id、tenant_id/user_id、attempt_id、conversation_id可空、kind、comment、created_at；唯一(user_id,attempt_id)，重复返回第一行不改评论。MessageRating：id、tenant_id/user_id、conversation_id、assistant_message_id、attempt_id、rating(up/down)、reason_code/comment、created_at/updated_at；唯一(user_id,assistant_message_id)，再次提交更新值。原因只允许固定六类；down未给原因存unspecified，up不带down原因。无历史尝试记录的旧消息不伪造执行溯源，本卡不批量回填历史数据。

API均/api/v1/chat前缀：POST/GET /feedback，DELETE /feedback/{id}；POST/GET /ratings，DELETE /ratings/{id}；GET /attempts/{id}。提交只允许当前有效身份、同租户且记录/会话所有者；管理员不代评。本人查询自己的行，tenant_admin查询本租户，system_admin跨租户查询必须显式tenant_id；system_viewer不新增反馈管理权。本人或system_admin删除；其他主体404。page>=1、page_size<=50、created_at/id倒序；只返固定元数据和脱敏评论，不返消息正文或内部错误。

反馈前依次InputFilter、LogSanitizer，评论原始上限500字，不记录正文到日志/审计。数据库唯一约束解决并发冲突。反馈原表默认180天保留，自动到期任务不在本卡；不新增语料快照，撤销传播留EVAL-002批准契约。

本卡使用显式模型导入注册新表及幂等Alembic增量迁移（兼容已有create_all）；不改app/core/database.py以免超allowed_paths，不执行真实库迁移。降级不得自动删除非空反馈表。

基线复用同输入00ea6ee的已通过CI：Backend job119608102936、Frontend119608104741、Agent119608136235，及本地31条专项/完整3304 passed、前端172条等证据。下面的565个源码/测试/配置/依赖/评测输入原始SHA256全部匹配，当前已有改动仅上卡CI/关闭文档，故不重复无变化的整套基线；新增业务后重新全量验证。新检查使用指定conda解释器、独立data/validation/feed001-20261022目录及隔离SQLite。

基线复核：565项输入指纹匹配，退出0；Python3.12.0及依赖沿用已记录本机环境。开工已有文档改动保留，不覆盖；不提交/推送本卡。

2026-10-22 用户明确允许将README.md加入本卡allowed_paths，仅补反馈API与验收说明；已同步任务范围。

## 实际实现与当前验收状态（2026-10-22，未关闭）

实现了三张表、幂等增量迁移、失败反馈及回复评价的提交/查询/撤销接口；评论经InputFilter及LogSanitizer后保存，校验错误不回显原评论。查询有身份及租户边界，管理员不能代替所有者提交。界面按成功/失败终态展示入口，保留失败草稿。非流式兼容旧客户端，SSE继续使用原run_id、独立生产任务与回放；显式取消没有输出时不写空助手消息。

文件：app/models/feedback.py、app/models/__init__.py、app/schemas/feedback.py、app/services/chat_feedback.py、app/services/active_runs.py、app/services/chat_service.py、app/api/feedback_tracking.py、app/api/routes/feedback.py、app/api/routes/chat.py、app/api/router.py；alembic/versions/d7e0f3a21b44_add_chat_feedback.py；frontend/src/api/feedback.ts、frontend/src/components/chat/FeedbackControls.tsx、MessageList.tsx、frontend/src/lib/chat-feedback.ts及测试、frontend/src/pages/Chat.tsx、frontend/src/types/api.ts；tests/test_chat_feedback*.py及tests/test_sse_active_runs_review.py；README、Swagger、ADR兼容条款、版本化Evaluation及任务卡。

专项发现并修复“运行刚创建便取消、任务尚未首次执行时attempt仍pending”：正式失败用例cancel-red.log，修复由cancel/shutdown补齐pending终态，已完成终态不覆盖。修复后组合59 passed/2 warnings/10.71s，exit0（chain6.log/exit）。前一版仅反馈专项24 passed/1 warning/4.41s（target5.log），不是最终公共链门禁替代。

检查环境：Windows、指定conda Python3.12.0；测试通过conftest屏蔽宿主.env及模型密钥，显式隔离SQLite，local向量库仅用于离线测试。完整命令由data/validation/feed001-20261022/run_full_v3.ps1和run_frontend.ps1保存；输入SHA256见manifest.json，HEAD仍00ea6ee691f0df1c178de5c05729fd810e81bf2c加未提交文件。前端npm run typecheck/test/lint/build四项exit0，Vitest174 passed；报告frontend-*.log/exit。Ruff全仓no-cache exit0；mypy app/231源通过（mypy-now.log）；最终全量pytest仍运行中，不提前记通过。首轮full及v2在启动后发生相关输入/换行规范变化，不作为最终门禁，只认v3冻结版本。

其他验证：Swagger导出成功；git -c core.whitespace=cr-at-eol diff --check退出0。本机WSL查询返回E_ACCESSDENIED，Linux证据未取得；同最终提交Ubuntu Backend/Frontend/Agent official baseline CI未执行。外部Codex只读独立审查授权已询问，当前未收到本卡授权，未发送本卡源码或数据。技术专项通过不等于业务接受；没有真实LLM/Embedding调用、生产迁移、Git提交/推送/合并或部署。

回滚：先回退反馈入口及聊天追踪接线，保留已写数据；非空表降级会主动拒绝，删除或迁移重要数据须另获授权。未做：历史消息批量回填、自动保留期清理、候选入集、真实模型质量评价。开工时间沿本记录调查时间，精确时刻未知；可验收/接受时间及7天缺陷观察尚未发生；重复验证包括上述取消修复与换行规范变化造成的过期full，耗时以日志为准，等待独立审查授权耗时尚未结算。

后续全量v2/v3均为4 failed、3325 passed、3 skipped；失败不能标为通过。两条取消用例原断言要求写空助手消息，与本卡已批准契约冲突；更新为不写空回复，同时保留未执行工具/无工具审计断言。通用异常恢复既有HTTP500并保留安全反馈标识。限流缺失run.finished的根因是订阅以run.status提前终止，而生产任务仍在异步收尾；改以finished_at（终态事件发布后设置）判断结束。相关91 passed、2 warnings、21.18s、exit0（fix8.log/exit）。已启动冻结输入后的全量v4；v3不再作为最终输入证据。前端输入未改变，四项结果继续适用。外部审查授权、Linux、最终CI与业务接受仍待完成。
Linux环境补查：提权只读WSL列表成功，仅docker-desktop；docker info只读失败，dockerDesktopLinuxEngine命名管道不存在。没有可用Linux运行时，未自行启动Docker或变更系统服务，Linux证据仍待完成。final冻结清单补齐原本未变化的README/Swagger、docs/ai-prompts及构建/CI配置输入，未修改检查输入。

2026-10-22用户明确授权本卡相关源码、测试及权限实现发送给外部Codex只读独立审查，不含.env、凭据或真实业务数据。审查已启动，限定静态只读，不运行写文件测试，不访问真实模型，不改Git状态；结论未出前不记通过。审查提示与原始输出存data/validation/feed001-20261022/review-prompt.txt、codex-review.log/txt，正式结论将归docs/reviews/。

同日只读审查发现生产循环异常后的 `aclose()` 会把尝试写成 `stopped`。修复为只有 `CancelledError` 记 `stopped`，`GeneratorExit` 保留已确定结果。新增 `test_consumer_failure_close_stays_api_error`。修复前该用例失败；修复后反馈三份测试加 `tests/test_sse_active_runs_review.py` 为 43 passed、1 warning、退出码 0，隔离库 `sqlite:///./data/feed001-p2-fix.db`。全量 pytest、Linux 与同一提交 CI 尚未对这个修复重跑，任务不关闭。

2026-10-22用户在对话页使用点赞/点踩后说任务卡可以关闭。业务接受按该授权记录：成功回复用点赞/点踩，点踩可填写补充说明。对话页随后把文字按钮改成小图标，点踩先打开说明再保存。该界面改动和上述失败分类修复之后，没有重跑全量 pytest、Ruff、mypy、前端四项，也没有 Linux 或同一提交 Ubuntu CI。因此台账保持 in_review，不写 done。
