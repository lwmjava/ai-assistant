# 两家真实提供商有界验收

用户已在合成文本/图片、最多6次请求、费用不超过5元的询问后指定千问与DeepSeek。本脚本只占最多4次文本请求，为视觉保留2次；无重试，不读取工作区.env，密钥仅从进程DASHSCOPE_API_KEY、Deepseek_Key读取，不记录值。

技术HEAD d5311557c07273137104105f961cc20fe0e281b1；原607项输入与同SHA Ubuntu三类CI通过。本轮只新增LLM专属计划、手动脚本与报告，不改业务/测试/依赖/永久配置/tasks，不新增架构。

官方核对（2026-10-17）：[DeepSeek首页](https://api-docs.deepseek.com/)明确base_url=https://api.deepseek.com、模型deepseek-flash；[价格页](https://api-docs.deepseek.com/quick_start/pricing/)搜索索引确认1M上下文/384K输出，峰值每百万输入$0.3/输出$1.2。[Qwen模型页](https://help.aliyun.com/zh/model-studio/qwen-plus)确认qwen-plus文本、1M窗口、32768最大输出，北京每百万输入0.8元/非思考输出2元/思考8元；[兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)沿已验北京端点https://dashscope.aliyuncs.com/compatible-mode/v1。只在验收子进程声明Qwen能力，DeepSeek沿既有官方表；不扩大视觉身份。

每家新进程和独立合成SQLite，真实User/Tenant，调用原ChatService.chat→工厂→受控路由。langgraph simple使用既有公开支持短路径，技能/安全/预算保持打开，工具表沿原构建，RAG开启但空库+MockEmbedding只隔离检索，不能证明检索质量。非流式Service内部短路径仍使用真实stream_chat。预计技能语义匹配一次、回复一次，每家最多2 HTTP POST；额外请求在发送前拒绝。仅公开合成问候，不上传文件、不使用真实用户数据，不外发任意其他域。

每次请求max_tokens≤128、编码体≤32768字节。保守按每输入字节一token、DeepSeek美元单价按预算换算缓冲10元/美元（不是实时汇率）及峰值计入，统一上界输入3元/百万、输出12元/百万，每次<0.1元、4次<0.4元，远低于5元；断线未知usage亦按上界计。ledger先落尝试再发，禁止自动重跑同一家；HTTP状态、返回模型、usage、请求体字节数和固定合成回复摘要落报告，不记密钥、提示全文、响应全文或推理内容。

验收：每家至少一个真实200且生成含“你好”的非空回复；原Service保存complete助手消息、用户消息；新Session复核模型和原回复一致。未知/空回复、超次数/字节/预算、无凭据、传输或权限错误一律如实失败/阻断，不能拿HTTP200冒充成功，不切Mock、不裸HTTP替代产品链。厂商默认thinking原样保留，若128上限不兼容就停止而非绕护栏。实际次数最后向根Agent汇总，最多4；视图库剩余额度以实际发送次数为准。
