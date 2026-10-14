# SKILL-003 独立实施计划审查

日期：2026-10-14。审查者：独立 Agent `skill003_plan_review`，未参与业务实现；本报告为计划审查，不是最终人工业务验收。

依据：AGENTS.md、tasks.yaml 原始 SKILL-003 契约、task-card-delivery 技能、plan_skill_003_20261014.md，真实 SkillManager、SkillMatch/SkillContext、ChatService、skill_service 与 LLMProvider/OpenAICompatibleProvider。采用 plan-eng-review 的架构、代码质量、测试与性能检查维度；不执行其全局工具安装、遥测、发布流程。

结论：批准确定性匹配优先、未命中时单次有界语义建议的方向，无新增 P1 阻断。用户已经批准 chat_service.py 与 docs/ 扩展，无需再次授权。以下 P2 细节应在实现计划和测试中补全，最终代码审查核对是否落地。

## P2 补足项

1. **整体输入预算必须有明确字节上限。** 计划已给用户输入 4096、描述 512、候选 32 的边界，但 JSON 名称、转义与系统提示也占用预算，“整体 payload 有界”还不足以形成可断言契约。明确完整消息内容的 UTF-8 字节上限，超过限制不调用模型，并测试中文、多字符转义、最大名称与 32 个候选组合。不要在预算外静默截断成无效 JSON。
2. **候选模型适配依据选择模型自身身份。** ChatService._intent_llm() 可以与最终回答模型不同；models 非空候选必须对实际语义 provider.model 校验。缺失、空或 unknown 身份时，不得让模型限定候选进入选择。用“回答模型符合但意图模型不符合”及反向案例测试，避免错误身份绑定。
3. **取消须验证真实入口释放请求占用。** 技能选择发生在 _prepare_user_message/claim_execution 之后，stream 的 conversation/message 事件之前。除了 helper 中传播 CancelledError，应覆盖 chat 和 chat_stream 的这一阶段取消，检查 guarded_chat/guarded_stream 的 finally 清理后允许后续受控请求，且取消不会新增助手回复或触发 Tool。保留已接收的用户消息符合现有生命周期，不声称撤销已持久化消息。

## 实施与反例清单

权限在 manifests_for_chat 取候选时完成：内置、系统全局与当前创建者私有技能；模型输出必须绑定此请求的已过滤快照，不从全局管理器或 DB 任意名称回查。候选快照只含名称/描述，禁止 skill 正文、来源路径、历史和其他用户数据进入选择消息。描述作为不可信数据围栏；输出不能自行授权工具、修改 prompt 或恢复执行状态。

严格 JSON 要覆盖未知名称、额外键、重复键、非对象、多个对象、fenced JSON、NaN/Infinity、bool confidence、负值/大于 1、超长输出。未匹配合法结果应有明确定义，例如空选择；拒绝与失败只写固定分类日志，禁止异常原文和完整消息日志。

原匹配方法返回的匹配对象可能低于该技能 trigger.min_confidence，activate 会自行过滤。新增语义方法不得为了让测试命中而绕过 trigger 阈值；确定性返回结果与是否实际激活的兼容行为须写清。确定性路径和 SKILL_ENABLED=false 都应证明零新增请求，语义失败继续现有对话路径；超时以外部 async 边界约束为主并传 LLMOptions timeout=5/max_tokens=128/temperature=0，无本层重试。

覆盖两种真实 ChatService 入口、私有/全局围栏、skill_names 保存后新 Session 回读以及已有 tool 权限/安全约束。语义质量由真实模型证据支持；合成 provider E2 只证明选择与接线契约，不能写成模型理解能力或生产误命中率已验证。当前计划正确保留未授权真实调用与业务验收的未验证状态。

不新增配置、架构、供应商、缓存或自动淘汰；回滚恢复同步匹配，没有数据迁移。既有 PAGT-007 工作区变更保持独立归属。此次审查仅静态阅读，没有运行测试，也未修改业务代码或任务状态。
