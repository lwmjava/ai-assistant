# 检索适配分数契约修复计划

日志复测补充：实际运行发现应用 logger 无 handler 时，Python lastResort 仅输出 WARNING，INFO 被丢弃。修复限定于 service.py：检索日志发出前，若该 logger 及祖先均无 handler，则添加使用项目 JsonLogFormatter 的控制台 handler；已有 handler 时复用。以无根 handler 的进程验证实际输出与重复调用无重复 handler，不改变全局日志级别。

追加批准范围（检索日志）：用户明确要求在 `app/rag/service.py` 输出实际后端，以便从页面发起检索后确认是否降级。仅在 `search` 已解析后端、调用 retrieve 前增加 INFO 日志，记录请求后端、实际后端、实例类型与 fallback；后端名称限制为已知枚举或 unknown，不记录问题与文档内容。验收使用受控实例捕获日志，覆盖 langchain 生效和降级 native；这条日志证明即将调用的后端，不证明检索成功。此次授权扩展原任务路径至 service.py。

任务：RAG-023。目标：LangChain 适配保留 RRF score 与稠密 similarity 两种语义，防止正常命中误被 0.4 阈值剔除。

现状：适配返回 h.score，重建 ChunkResult 时将其误作 similarity。方案：Document metadata 携带 similarity，返回融合 score 不变；消费 metadata 时验证数值、有限性和 [-1,1] 范围，缺失/非法相似度明确失败，不使用占位。

范围：app/rag/backend/langchain_backend.py、独立契约测试、相关文档和任务状态。非目标：不改 RRF、模型、阈值、Milvus、不证明真实检索质量。先写失败测试，覆盖正常高相似度、低分过滤、字段保真与非法值；再最小修复。

验收：新增测试先失败后通过，相关阈值/后端回归、ruff、任务 YAML/依赖与 diff 检查。mypy 在实际 ai-assistant 环境缺模块，记录未执行，不安装或换环境伪报通过。实施完成进入 in_review，不跳过独立审查和人工验收。

追加批准范围：用户授权修复 add_texts/from_texts 的继承类型错误。当前 mypy 1.14.1 已安装；add_texts 接受 Iterable[str]，from_texts 返回具体适配器类型，保留现有可空 embedding 兼容入口。两者显式接受基类 ids 关键字参数，写入仍直接 NotImplementedError；不新增写路径。新增生成器/ids 与 classmethod 拒绝用例，类型分析不加 ignore、不改配置。
