# 附件多模态与存量测试独立复核

日期：2026-10-16。审查者 `/root/inst_review`，被审实现者 `/root/batch_readiness`；主 Agent 修正的存量测试也在本次独立范围。依据 AGENTS.md、已批准附件计划及 Memory 单文件补充、ADR-0012、实际代码和测试差异。

## 结论及独立范围

在本报告列出的 LLM、Agent、Memory 多模态变更及三份存量测试修正中，未发现具体阻断缺陷。此结论仅为静态独立复核，不表示全量回归、Linux/CI、真实视觉或上线验收通过。

审查者本人实现的附件 API、Service、解析和迁移不由本报告自审批准；这部分另有 `2026-10-16-chat-007-backend-independent-review.md`。本报告只核对两侧接口边界，不代替该报告。

## 多模态与安全边界

- `ChatMessage` 保留字符串兼容，新增严格 typed text/image_url 内容段。图片 URL 不参与文本插值，相关模型隐藏验证错误中的输入。API 已校验图片结构后，模型层负责限定 PNG/JPEG data URL 和 low 精度协议；模型层的 base64 校验本身不被当作文件结构验收。
- 视觉预检只认可已核对的官方 HTTPS 部署及 gpt-4o-mini，拒绝用户信息、query、fragment 和未核对部署；同名模型或运营者文本窗口声明不能获得视觉许可。预检递归检查实际 fallback hops 与单跳包装委托，不发 HTTP。供应商实际发送前仍执行视觉能力与预算护栏，关闭文本能力开关不能关闭图片预算。
- 官方 low 图片预算按每图 2833 tokens 加完整文本及消息框架，再叠加输出预留和安全余量；不拿 base64 文本计数冒充视觉 token。缓存键使用完整序列化 payload、身份快照和部署等既有上下文，图片字节改变会改变摘要键；稳定计数器表示不含闭包地址或图片内容。
- Pipeline 理解、规划、行动、结构化推理、质量门、批评、反思和最终回答通过共同 helper 携带当前图片。Supervisor 三个模型节点也使用 typed 内容段，图片保留在请求实例私有字段，不放入 LangGraph graph_state；普通快速回答亦有适配，但附件 Service 当前不选择该快路。
- `retrieval_query` 保留原安全问题；Pipeline、Supervisor、快速检索及意图预检采用它，而模型回答使用增强正文。工具授权检测以原问题为用户授权文本，将增强附件文本置为不可信上下文，附件点名工具不能冒充用户明确授权。
- `content_text` 只提取 text 段，历史拼接及 Memory 摘要输入使用它。Memory 修改仅替换文本提取，保留原截断、压缩策略及调用流程。图片不出现在文本历史、摘要输入或 Pipeline 的字符串 Trace prompt 中。
- StateManager 在当前 state 对象上应用快照白名单，白名单不含 user_input、user_images、retrieval_query；恢复不替换对象。因此 Service 从原 Message 复原的当前附件及原问题不会被旧 checkpoint 的缺字段覆盖。图片仍依赖受鉴权原消息关联，checkpoint 不是图片的第二份持久存储。

## 三份存量测试修正

`tests/test_agent_state_resume.py` 的旧断言禁止六类以外任何事件，与 Accepted ADR-0012 新增真实节点和意图事件冲突。替换后不是仅扩大允许集合：它仍要求每个 data 为字符串、to_dict 精确保持旧 `{type,data}`；新增边界必须实际出现，节点名称与时间类型有效，结束状态限定，开始/结束节点序列相等。原 checkpoint/恢复及并发测试未删除。

`tests/test_p0_regression.py` 两个绕过构造器的 `__new__` fixture 仅补上构造器已有的空 `_user_images`，继续验证实际模型请求使用 ChatMessage，未替换生产节点或改弱类型断言。

`tests/test_skill_semantic_entry.py` 使用既有 isolated_session 替代 None，使 stream 生命周期能获取实际 engine；prepare stub 接受新增可选 attachments 且严格断言为 None。原始输入必须脱敏后才进 selector、selection 抛错传播、取消传播、执行 claim 释放及后续可重新认领的断言均保留。没有关闭相关安全配置、增加调用次数或跳过取消场景。

## 证据及待验事项

本次只读使用 Git diff、rg、Get-Content、Get-FileHash、Git 身份命令，未运行 pytest、模型/Embedding 请求或外部转换。已读取 `tests/test_chat_multimodal_llm.py` 的 payload、视觉预算、未知部署拒绝、阶段/质量门图片传递、缓存身份、Memory 提纯、错误隐藏及工具授权断言。该文件中的合成 PNG 仅用于模型协议/计数与缓存测试，不作为 API 图片完整性样本或真实视觉质量证据。

需要继续保留以下验收动作：

1. 主 Agent 对最终源码与测试输入执行全量回归并记录最终指纹；本报告不把先前失败版本或局部通过代替最终结果。
2. Linux 文件系统/Office 转换及同提交 required CI 仍须实际运行，不能用 Windows 静态复核替代。
3. 真实官方视觉请求与业务质量未验，离线记录器只证明协议载荷；未核对视觉部署继续明确拒绝，任务维持 in_review 直至必要证据齐全。

## 候选身份与 SHA256

读取时分支 `codex/platform-batch-20261015`，HEAD `873db9c0fb243b0847b4be57242c8f333f4f0b1e`，候选为 HEAD 加未提交文件。相关输入变化后需重新复核对应结论。

| 文件 | SHA256 |
| --- | --- |
| app/llm/base.py | 05f9d0295198b025918f450edb43445ea95a50b05b85ae2c9091c64026482af9 |
| app/llm/vision.py | fc1d295fa7d6bf27153caf3776208f09b1ad123ab4c8d05025cee61d5c2dcbcc |
| app/llm/counters.py | 7c9e054174d7ebf697a02fc55b690a07872d62a54326aea6db3774a213bca489 |
| app/llm/capabilities.py | eeb2cc2df4591e1e4e7cdeab648ed9b8a47ce228a45ae413701e17fb03ddf96c |
| app/llm/openai_compatible.py | d41a27d5747b41d2e1da5e8cfc9f5c36f4e63585271edbf2d76d11541f991847 |
| app/agents/pipeline.py | ccde6299d6a0e279678027813e07ce3588039525c155aa6d9a666ca2e1811020 |
| app/agents/plan_runtime.py | 63d212b19fd0b5cbd7b64abbfd89dba471beecf9bb4815df3cffde3ff1ab8b0e |
| app/agents/supervisor.py | 195714ad2f4301c7489cd09053a87d29fee200d5bf20fd4bee0235b8aa057ca6 |
| app/agents/fast_path.py | 43c6db88660cf36669f76b00b47e726acb9197e96418bb223d7a7f7972f9d05d |
| app/agents/state.py | 57c78269832fbc02cdadf4968abb61c74d199eed09432582f9c4f4e678a5737f |
| app/memory/compressor.py | 42350e32c18edff0c1ad8d9f3b35048b261b8699c396dfd8c8be9c08bb85ee7d |
| tests/test_chat_multimodal_llm.py | 57aa22b14d842277e996c70904c4610a0087171ef578097c4594c6f7a0896f42 |
| tests/test_agent_state_resume.py | 866e3afc00d52339bd5ee414bda88ea5fa2a79e0168d0f8595f82a0214a2a6d1 |
| tests/test_p0_regression.py | 3dd8537910d5592b6ca0f3abea35218474ccbc7244f197ffb6451c426d702041 |
| tests/test_skill_semantic_entry.py | 87ca3406bd952a1f04fa9fa9d817b8167429c0fe63afbf6cac7d8f2e39f59f7f |
