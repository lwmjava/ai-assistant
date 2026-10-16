# 平台批次实现与验收记录（2026-10-16）

按用户授权使用 module-batch-delivery，顺序推进八张原始卡并加入 NFR-003 前置。可执行部分已实现，整批未达到关闭条件：七卡 in_review，一卡 ready，一卡 blocked。不能以本记录宣称全部完成或可发布。

## 版本与范围

基线与当前 HEAD：`873db9c0fb243b0847b4be57242c8f333f4f0b1e`；分支 `codex/platform-batch-20261015`。候选为 HEAD 加未提交改动。781 项输入 SHA256 见 [最终指纹](../reviews/platform_batch_final_inputs_20261016.json)，[执行时快照](../reviews/platform_batch_final_r2_execution_inputs_20261016.json)保留原始输入。执行后只有生产运行手册增加非执行性的备份/恢复说明和待验收注记，旧新指纹已记录；源码、测试、配置、依赖、评测输入未变，按 AGENTS.md §10 沿用结果。

主 Agent 串行整合，各卡内由 inst_review、inst_validation、batch_readiness 分担实现、验证及非实现者审查。最终 CHAT 的附件后端与多模态/Memory 分开独立审查，见 [后端审查](../reviews/2026-10-16-chat-007-backend-independent-review.md)与[多模态审查](../reviews/2026-10-16-chat-007-multimodal-independent-review.md)。完整实际修改文件清单见 [交付清单](../reviews/platform_batch_delivery_20261016.json)。用户原有 `.trae/` 未纳入。

## 功能与契约对账

| 卡 | 实际交付 | 状态与缺口 | 详细说明 |
|---|---|---|---|
| INST-004 | 空库三账号初始化、测试账号标记、首次改密限制、改密 API/页面、旧库迁移契约 | in_review；真实页面改密业务验收及适用平台证据待补 | [说明](implementation_inst_004_20261015.md) |
| CLI-002 | 独立 init、安全随机密钥、已有文件拒绝覆盖、失败清理、生产弱密钥拒绝 | in_review；Linux 文件权限及文件替换证据待补 | [说明](implementation_cli_002_20261015.md) |
| NFR-003 | 生产 Compose、PostgreSQL、可选 Milvus、健康检查、资源与卷、镜像排除和运行手册 | in_review；配置检查通过，Engine 不可用，未构建或启动 | [说明](implementation_nfr_003_20261015.md) |
| NFR-012 | 核对前置与现有开发启动路径，保留阻塞记录 | blocked；NFR-003 真实启动前置尚未验收 | [批次计划](plan_platform_batch_20261015.md) |
| API-001 | /api/v1 业务路由及前端、脚本、健康检查、README、Swagger、测试同步 | in_review；最终提交及 Ubuntu CI 证据待补 | [说明](implementation_api_001_20261015.md) |
| ERR-001 | 已纠正计数：八种错误码加静默刷新成功，共九种场景 | ready；移除第九码阻塞，端到端实施与验收尚未完成 | [批次计划](plan_platform_batch_20261015.md) |
| SSE-001 | ActiveRun 持有执行、鉴权回放/查询/取消、有界缓存、心跳序号、前端恢复及节点事件 | in_review；保留单进程边界，平台与外部验收待补 | [说明](implementation_sse_001_20261015.md) |
| LLM-001 | 四任务配置档、生成路由、有限回退、熔断/半开/取消、预算缓存兼容 | in_review；千问真实文本链路通过，第二供应商切换未验证 | [说明](implementation_llm_001_20261015.md) |
| CHAT-007 | 图片/文档/源码附件、单文件 1 MiB、安全与配额、关联持久化/续跑、视觉载荷、文本摘要、选择粘贴及历史展示 | in_review；真实视觉质量、旧 Office 样本及平台证据待补 | [说明](implementation_chat_007_20261016.md) |

各卡原始契约、批准范围、失败路径与回滚细节在对应计划、说明与评审中对账。非功能状态已同步需求文档与运行手册。默认 local、Milvus Partial 保持；集合外能力未借本批实施。

## 最终验证

Windows；解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，Python 3.12.0、pytest 8.4.2、Ruff 0.8.4、mypy 1.14.1、FastAPI 0.115.6、SQLModel 0.0.22、Pydantic 2.10.4；Node 22.20.0、npm 10.9.3。实际版本以指纹 JSON 为准。PYTHONUTF8=1、PYTHONIOENCODING=utf-8；测试库 data/test_ai_assistant.db，串行执行，独立 basetemp。最终 pytest 空凭据并阻断外部 DNS/socket，安全、RAG 与能力门禁保留。

表中 python 均为上述完整解释器；前端命令在 frontend 目录执行。

| 实际命令 | 退出码 | 结果 |
|---|---|---|
| python -m pytest --basetemp data/pytest-tmp/platform-batch-final-r2-20261016 | 0 | 无筛选全量：2472 passed / 3 skipped / 3 warnings，537.71 秒 |
| python -m ruff check . --no-cache | 0 | 全库通过，关闭缓存仅规避本机缓存权限问题 |
| python -m mypy app/ --cache-dir data/mypy-platform-final-20261016 | 0 | 210 文件；此后应用、类型配置和依赖未变 |
| npm run typecheck | 0 | 通过 |
| npm test | 0 | 14 文件、120 用例通过 |
| npm run lint | 0 | 通过 |
| npm run build | 0 | 通过 |
| python scripts/check_production_compose.py | 0 | 静态通过；另有七种配置场景，不等于启动验收 |
| git diff --check | 0 | 通过 |

完整命令、日志路径、版本和跳过名称在最终指纹 JSON。三项跳过分别涉及 Windows 符号链接权限、POSIX 0600 和 Linux 打开文件替换身份，不能算通过。三条 warning 为既有 AST 弃用及合成 NaN/溢出检索测试提示。前端依赖未改，未宣称本轮执行 npm ci。

第一轮整合保留为 2460 passed / 7 failed / 3 skipped、687.59 秒、退出 1，见 [R1 记录](../reviews/platform_batch_regression_r1_inputs_20261016.json)。原因是旧 SSE 事件集合及旧 Supervisor/技能入口替身缺字段、签名或真实 Session；保留取消、权限、载荷与旧事件兼容断言，修正 fixture，相关三文件 40 项通过后重跑全量。没有跳过失败、关闭安全或仅增加调用总数换通过。新增五项附件边界、续跑及越权反例已包含最终全量。

## 页面与真实模型证据

[附件页面实测](../reviews/2026-10-16-chat-007-ui.md)覆盖选择、格式/大小拒绝、图片粘贴、移除、发送清理、刷新后附件元信息与消息持久化；[SSE 页面实测](../reviews/2026-10-15-sse-001-ui.md)覆盖发送、停止和刷新持久化。没有宣称浏览器断网故障注入通过。截图留在工具记录，无本地截图文件。既有时区显示偏移已记录，未混入修复。

按用户明确授权使用 .env 中已有千问凭据，隔离数据库、合成文本、有界调用真实 ChatService/AgentPipeline。最终 qwen-plus 五次 HTTP 200，回复“验收成功”，从新会话核对持久化；938 输入、230 输出、1168 总 token。见 [千问实测](../reviews/2026-10-16-llm-qwen-live.md)。先前探针设施失败或缺有效 HTTP 证据的尝试另行保留，不能作为通过，也不能将最终五次用量冒称全部尝试费用。未改 .env，未调用第二供应商或外发业务附件；这不证明视觉质量。

早期隔离遗漏 Embedding 凭据的实际外部调用、费用不确定性及修复已记录在 [隔离报告](../reviews/2026-10-15-platform-test-network-isolation.md)。不能宣称整个历史过程从未读取 .env 或从未外联；最终离线回归与后续获授权探针分开记录。

## 未验证项与回滚

缺少 Linux、同最终提交 Ubuntu Backend CI、远程 required checks 和分支保护核验，无 CI URL，不声称保护已生效。还缺生产编排实际启动、第二供应商切换、真实视觉业务质量及旧 doc/ppt/xls 完整样本证据。Mock Embedding 只证明链路，不是检索质量或生产达标证据。ERR-001 已于本日后续澄清计数并转 ready，详见 implementation_err_001_contract_correction_20261016.md；NFR-012 等待前置。业务卡未关闭，不写 done。

未提交、推送、合并、部署、迁移真实库或重建索引。临时页面服务已关闭。撤回按各卡文件清单处理本批改动，保留用户文件；发布前先备份并在空目标演练恢复，重要数据覆盖须另行授权。API 客户端须同步 /api/v1；SSE 状态仍为单进程，进程重启不提供持久恢复。真实库升级与回退尚未执行。
