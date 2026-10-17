# CHAT-007 附件后端独立工程复核

日期：2026-10-16。审查者：`/root/batch_readiness`；被审实现者：`/root/inst_review`。依据：`AGENTS.md`、原始 `tasks.yaml` 卡、`docs/plans/plan_chat_007_20261015.md` 及已批准的必要路径补充；审查使用 `code-review-quality` 技能。

## 结论与边界

在下列文件和所述路径内，未发现需要阻断当前候选的具体实现缺陷。此结论是独立静态复核结果，不代替最终运行门禁，也不关闭任务。

本次独立范围仅为附件解析/校验、Service/API 接入、Message 关联、配额事务及增量迁移。审查者本人参与的 `app/llm`、`app/agents`、`app/memory/compressor.py` 多模态实现不属于本报告的独立批准范围。恢复链仅复核了附件后端与现有 StateManager 的接口兼容性。

## 核对结果

- 扩展名与原卡白名单一致；不额外允许 txt、jsx、tsx，不新增个数限制。严格 base64 与解码后 1048576 字节检查均在解析/写消息前；整批任一失败不返回半批准备结果。图片不只检查魔数，还执行格式核对、verify 和完整 load；解析错误使用固定提示。
- 文件名仅为显示数据；文档解析使用固定合成文件名。复用现有文档解析服务，不调用知识库摄取、Document/Chunk 创建或索引入口，不添加依赖/供应商。旧 Office 继续依赖原转换能力，不等于本报告已证明本机或 Linux 的转换可用。
- 文档正文经过现有安全治理后进入 JSON 转义的不可信附件围栏，换行和尖括号不能拼出独立的结束边界。技能选择与检索仍使用原安全问题，附件正文不取得用户授权地位。图片 URL 不拼进文本正文。
- 同步 Service 在用户消息写入前准备附件并预检视觉能力。SSE 路由在创建 ActiveRun 前完成同一预检，生产 Service 接收已准备的不可变附件对象；恢复场景重新从原消息还原，拒绝请求替换附件。
- 恢复必须满足会话主体、租户、原用户消息归属及原消息正文匹配。Service 重建当前图片与原检索问题；现有 StateManager 仅原地回写快照白名单，未覆盖 `user_input`、`user_images`、`retrieval_query`，因此不会用缺少附件的旧快照替换整个状态。
- 附件关联随用户 Message 在原 `accept_user_message` 事务中提交，沿用配额与权限路径；不增加绕过 Service 的写入入口。读取会话仅返回名称、大小、MIME 摘要，未公开正文/base64 或新下载 ID。
- 迁移仅新增可空 attachments 列，旧 Message 保持正文且默认为空；schema/stamp 检查要求存在该列。升级有重复执行保护；回退代码可保留列，本报告未执行真实库迁移或降级。

## 测试与证据限制

只读检查使用 PowerShell `Get-Content`、`rg`、`Get-FileHash` 和 Git 身份读取；未运行 pytest、模型请求、Embedding 或外部转换进程。已审阅实现测试与 root 独立请求测试的断言，覆盖非法批次无消息/KB/模型副作用、全部源码类型、真实本地生成的现代 Office/PDF 正文、附件消息关联、未知视觉拒绝、真实图片内容段及历史摘要、截断 JPEG 与安全治理拒绝。

测试文件存在与静态断言合理不等于测试通过。Mock/离线记录器只证明载荷与边界，不证明真实视觉理解或生产检索质量。缺少必要证据时任务保持 in_review。

### 最终输入补审与主 Agent 运行结果补记

原独立请求测试补入边界、恢复与越权用例后，本审查者重新只读审阅了完整文件及提取为 `_vision_recorder` 的离线 helper，未修改测试或业务代码。精确 1048576 字节用例证明文件验证接受边界，既有超出 1 字节用例证明拒绝；此边界接受不宣称模型能容纳 1MiB 文本。

恢复新增用例分别经同步与 SSE 入口，从持久化原用户消息及不含图片的 action 检查点恢复：记录器必须看到原图 data URL 与原文档正文，最终只有一条用户消息且有 complete 助手消息。租户消息上限设为 1，恢复成功且用户消息不增加，避免把恢复当作新消息消耗配额；随后替换附件请求必须 400 且消息/知识库数与模型调用数均不变。另以同租户另一主体与跨租户主体分别验证详情读取和恢复均 404，且无模型/数据写入副作用。上述断言有效覆盖本次补审目标，未发现具体阻断项。

以下运行结果来自主 Agent `/root` 的最终 R2 回传，本审查者没有重复执行或独立观察这些运行：独立附件请求文件 19 passed，5.01s，退出 0；不带筛选的全量 pytest 收集 2475 项，2472 passed / 3 skipped / 3 warnings，537.71s，退出 0，且包含本次补审文件。主 Agent 同时回传 Ruff 退出 0、mypy 210 个源文件退出 0，以及前端 120 passed、类型检查/测试/lint/build 四门退出 0。完整命令、环境、日志路径和最终全范围输入指纹仍以主 Agent 交付记录为准，本报告仅更新本次审阅文件的指纹。

Linux 转换/文件系统、同提交远程 CI、旧版 Office 真实转换及真实模型视觉理解仍未由本报告验证，不据本地离线全绿宣称生产验收或关闭任务。

## 候选版本与文件指纹

读取时分支为 `codex/platform-batch-20261015`，HEAD 为 `873db9c0fb243b0847b4be57242c8f333f4f0b1e`；以下为未提交候选的 SHA256。相关文件变化后本报告须复核。

| 文件 | SHA256 |
| --- | --- |
| app/services/chat_attachments.py | 227b458cec1dfe1a2d93ec2e1e715d307706ca8083d3f9c05bee62d9b6d84f12 |
| app/services/chat_service.py | 91209bb100e4658f7696915e3e496b2f94cc57c11bb30841d4436a53c42c5a2a |
| app/services/quota.py | e8f75cf5338cb112a1c86be8e6fba0d24604da4d3d050e988f715775b2b70f19 |
| app/api/routes/chat.py | a2b6bd8257ad92b0d6b2315c8fbff6d96c2d444dde0f3c88cd07d21c13acccd9 |
| app/models/conversation.py | 6dc55586f0bb6f4fc930f00ae9a9d2917f195d11369f9e884fc7b0760c2bb9c6 |
| app/core/migration.py | 8fde43f509e17402c6a222d3bdd74c211635f66c433a4998e2542d1c3cd4b678 |
| alembic/versions/a4b7c2d9e160_add_message_attachments.py | d76feebfca1375c44a3142a988ba640e41165b4086d4f16a3a3029e3533325d7 |
| tests/test_chat_attachments.py | 76f26e6a31db0cbc85b1a646ae2a01a981d118fccf5f28f5c2861be35802f9b0 |
| tests/test_chat_attachments_review.py | b58cc449c2332fc42ba5ae91639800b04847a977ad08dbe72ab38433d2612720 |
