# 会话附件实现说明

日期：2026-10-16。原卡 CHAT-007，批准计划 plan_chat_007_20261015.md。基线 HEAD 873db9c0fb243b0847b4be57242c8f333f4f0b1e，工作分支 codex/platform-batch-20261015，尚未提交。状态 in_review，不把以下阶段性结果写成已完成发布验收。

## 用户可见行为

对话输入区可以选择多个文件、粘贴 PNG/JPEG、逐项移除，或只发送附件。每项显示读取状态、名称、大小及固定错误；非法类型、超过 1048576 字节或读取失败时不能发送，移除问题附件后可继续。发送使用本次草稿快照；成功只清除本次已发送项，取消/失败保留，切换会话或移除后晚到的读取回调不能把附件放回。

文件白名单严格为 png/jpg/jpeg；md/doc/docx/xls/xlsx/ppt/pptx/pdf；go/py/css/html/htm/java/js/json/xml/yaml/yml/c/h/cpp/cc/cxx/hpp/vue/ts。不新增 txt/tsx/jsx，不另设文件数量上限。文件大小限制不代表任何数量附件都能装入模型上下文，后续实际模型预算仍独立生效，不能静默截断附件以放行。

流式和一次性请求均使用 attachments 数组，每项只有 name/content_base64。整批文件先验证再保存；扩展名、严格 base64、解码字节数、图片格式/完整解码、UTF-8 或文档解析任一失败，都不新增本次用户消息、知识库文档或分块，也不调用模型。流式在创建 ActiveRun 前验证，不能先启动运行再报告非法附件。

## 数据、权限和模型边界

用户消息与附件通过配额原有单事务保存。新增可空 JSON 文本列保存当前消息所需的正文或规范图片数据；旧消息按无附件兼容。历史接口只返回 name/size/mime_type，不返回二进制或全局下载 ID；继续使用既有会话主体/租户权限。续跑从原消息读取附件，不接受新附件替换，不新增用户消息或重复扣配额。

文档复用原 DocumentParserService，源码按 UTF-8；旧 Office 沿用原转换能力，缺依赖或损坏时固定拒绝。原始文件名不参与解析器路径。每份文档正文与名称经过现有输入安全规则，注入阻断开关保持原行为；不额外消耗限流次数。模型正文使用 JSON 转义的不可信围栏，附件中的工具名不能构成用户授权。检索、技能选择及工具授权仍使用原始安全问题。

图片通过 typed image_url 内容段进入当前消息和相关 Agent 阶段，包括质量检查/修正、计划动作及最终回复；不把文件名当图片内容。仅已核对官方 gpt-4o-mini 的 low 图片预算放行，每图 2833 tokens，另计完整文本、框架、输出和余量；其他部署/模型或仅文本窗口声明在消息写入前拒绝图片。逐跳实际预算和缓存继续检查；图片字节改变缓存身份，不把原始图片编码写入文字历史、Checkpoint、Trace 或异常回显。Memory 仅提取文字段，原压缩策略、窗口和调用次数不变。

## 代码位置与兼容

- 后端：app/services/chat_attachments.py、chat_service.py、quota.py、app/api/routes/chat.py、app/models/conversation.py。
- 模型与阶段：app/llm/base.py、vision.py、capabilities.py、counters.py、openai_compatible.py、mock.py，以及必要 app/agents 消息构造、原始检索/授权输入适配。
- 记忆：app/memory/compressor.py，仅用户批准的文字提取适配。
- 前端：Composer、MessageList、Chat 页面、useChatStream、useChatAttachments、lib/chat-attachments 与类型。
- 迁移：alembic/versions/a4b7c2d9e160_add_message_attachments.py，父修订 f1a6c8d2b903；app/core/migration.py 核验列与 stamp，不新增另一套手写补列。README 与 Swagger 已同步，当前 65 个路径。

增量迁移仅在临时数据库测试；没有对真实库升级或降级。Pillow 是既有 python-pptx 必需依赖，本次复用做图片验证，不新增或安装包。恢复旧代码可保留新增可空列；不自动删除已保存附件或对重要数据执行降级。

## 实际验证

指定 conda Python 3.12.0、Windows、PYTHONUTF8=1/PYTHONIOENCODING=utf-8，统一离线 conftest 与每轮新 basetemp。有效初始红测：后端两项附件被忽略、模型四项多模态缺口、前端四项选择/错误/历史/发送缺口；均失败于业务断言，没有以缺少 import 作为红测。后续截断 JPEG 与 Memory 纯文本反例也实际失败，修复后保留。

联合命令：

```powershell
D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_chat_attachments.py tests/test_chat_attachments_review.py tests/test_chat_multimodal_llm.py tests/test_chat.py tests/test_chat_resume.py tests/test_quota.py tests/test_cli.py tests/test_initial_accounts.py tests/test_initial_accounts_review.py tests/test_sse_active_runs.py tests/test_sse_active_runs_review.py tests/test_chat_controls.py tests/test_pipeline_budget.py tests/test_response_cache.py --basetemp data/pytest-tmp/chat-attachments-verified-20261016 -q --tb=short
```

151 passed、1 warning、98.24 秒、退出 0；日志 platform-chat-attachments-verified.log。之后补 1 MiB 精确边界、流式/一次性原图片与正文续跑、禁止附件替换、同租户他人/跨租户拒绝，独立文件 19 passed、1 warning、5.01 秒、退出 0，basetemp chat-attachment-lifecycle-20261016。现代 DOCX/XLSX/PPTX/PDF 与 Markdown 使用真实生成文件解析；全部 19 种源码扩展在同一条消息到达模型，知识库数不变。视觉录制桩使用真实预算检查，仅证明载荷，不证明真实视觉质量。

全库 Ruff --no-cache 退出 0；mypy app/ 使用 data/mypy-platform-final-20261016，210 文件、退出 0。前端最终四项分别退出 0，Vitest 14 文件/120 用例；完整日志 platform-batch-final-frontend-test-20261016.log 与 build 对应日志。真实浏览器的选择、粘贴、移除、发送、刷新后持久化见 docs/reviews/2026-10-16-chat-007-ui.md。

整合首轮不带筛选 pytest 为 2460 passed/7 failed/3 skipped，687.59 秒、退出 1；失败已归因旧 SSE 集合和旧内部替身签名/绑定，保留原断言后相关三文件 40 passed、退出 0。第二轮不带筛选完整回归为 2472 passed / 3 skipped / 3 warnings，537.71 秒、退出 0；最终输入绑定见 platform_batch_final_inputs_20261016.json。Windows 跳过项与 Linux/最终 CI 缺口仍保留。独立审查分开覆盖附件后端和多模态/Memory，见两份 2026-10-16-chat-007-*-independent-review.md；实现者不独立批准自己的部分。

## 明确未验收

没有向真实视觉模型发送附件；用户后续授权千问的真实验收仅使用合成文本，不能替代本卡视觉质量。旧 doc/ppt 的 LibreOffice 转换及真实 xls 样本没有本轮完整运行证据；当前无 Linux 和同最终提交 Ubuntu CI 证据。没有知识库摄取/索引、额外格式、租户模型管理、生产部署、真实库迁移或 Git 提交/推送。必要平台、真实业务判断及最终整合门禁未齐，保持 in_review。
