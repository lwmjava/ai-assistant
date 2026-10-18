# 助手消息重新生成实现说明

日期：2026-10-18。任务 CHAT-006，状态 in_review；尚未取得用户业务验收，不声明整卡完成。

## 实际行为

具有 conversations/write 权限且可访问会话的成员，可以对末条助手消息重新生成。服务从已保存的原问题和附件恢复输入，经现有 ChatService、安全、配额、Agent 和持久化链路追加一轮普通问答。原问题、原回复与旧执行身份保留，新回复用消息 ID 和时间区分；不创建分支。重新生成绕过回复缓存，仍使用现有检索缓存。

生成失败保留原回复；正常请求语义允许留下这次的新用户消息，页面刷新实际记录并显示安全错误。没有成功新助手时不伪造成功。陈旧目标、并发请求、无权限、配额耗尽和损坏附件分别拒绝；生成期间撤权或停用租户后不保存成功助手。会话互斥仅限现有单进程能力。

页面仅在末条助手展示按钮，明确提示新轮次和配额。请求中保留旧气泡和草稿，禁用重复发送；切换视图隔离旧请求。独立审查复现的 P2 忙态残留已修复并用真实 hook 清理顺序复核。

## 修改映射

- app/services/chat_service.py：目标与身份复核、会话锁、恢复附件、追加问答、失败判定。
- app/services/cache_scope.py：重新生成时禁用回复缓存作用域。
- app/api/routes/chat.py：无请求体的 regenerate 端点及安全错误映射。
- frontend/src/api/chat.ts、pages/Chat.tsx、components/chat/MessageList.tsx、hooks/useRegenerate.ts：按钮、请求生命周期、消息刷新与说明。
- tests/test_chat_regeneration.py、tests/test_chat_regeneration_review.py：真实 API/Service 与隔离持久化入口反例。
- frontend/src/components/chat/regenerate.test.ts、frontend/src/lib/chat-regenerate.test.ts：展示与生命周期回归。
- tests/manual_chat_regeneration_ui.py：仅用于本机临时库和合成模型的页面验收夹具。
- README.md、docs/swagger.json、docs/ai-prompts/evals/chat-regeneration-v1.yaml：API 文档、导出契约、Smoke/Adversarial 案例。

计划见 plan_chat_006_20261018.md；独立报告见 ../reviews/2026-10-18-chat-006-review.md 和 ../reviews/2026-10-18-chat-006-frontend-review.md。

由未参与实现或既有审查的 /root/chat006_final_audit 完成最终独立审核，报告 ../reviews/2026-10-18-chat-006-final-audit.md；无剩余 P1/P2，510项输入指纹复算一致。此结论支持交用户验收，不代替尚未授权的人工业务接受。

## 关闭对账

| 原始契约 | 证据类别与位置 | 结果 |
|---|---|---|
| 最后助手消息可以重新生成 | E2：test_chat_regeneration.py 的真实 HTTP 请求、合成 provider、隔离 SQL 库；页面 final-success.png | 通过自动验证 |
| 保留可区分的新回复 | E2：新 Session/GET 回查；页面 final-refresh-ax.txt 中原助手 6e74083fd03343faa18a45b39d56ae84 与新助手 a167958328504910ab598dd1b4d2cce4 均保留 | 通过自动验证 |
| 失败时原回复仍在 | E2：provider 异常、撤权、附件损坏的最终数据断言；页面 failure.png / failure-ax.txt 显示安全错误和原回复 | 通过自动验证 |
| 不做对话分支 | E2：既有普通会话新增问答、未增加模型或迁移；代码范围核对 | 通过 |
| 独立审查及修复 | 入口级 E2 十项反例；前端真实 hook 反例 E3；两份独立审查报告 | 无剩余 P1/P2 |
| 人工业务验收 | 用户批准范围和继续交付，未明确委托 Agent 替代业务验收 | 未验证，保持 in_review |

前端 helper/SSR 测试属于 E3，不能单独替代页面与 API 的 E2。合成模型仅证明链路，不证明模型质量或生产指标。本卡无质量提升指标、真实模型或 Milvus 启用要求。

## 页面运行证据

本地 127.0.0.1:18806，临时 SQLite 和合成 provider；不加载真实 .env，不发生付费模型调用。浏览器实际操作发送、重新生成、草稿保留、故障提示、刷新后重新选择会话。最终构建 Chat-PKMc-cq8.js；584×673 窄窗口按钮与提示可见，未声称 375px 或所有设备通过。

截图与 AX：../reviews/assets/chat006/final-success.png、final-success-ax.txt、final-refresh-ax.txt、failure.png、failure-ax.txt。失败截图先于前端忙态修复，后端失败输入未变化；最终构建成功及刷新证据重新取得。删除会话的完整浏览器操作未执行，清理/切换边界由实际 hook 独立反例验证，不将其写成页面 E2。

最终页面控制台warn/error记录为空（console.json）。验收后已核对并停止本次临时夹具进程，未触及开发服务或生产数据。

## 验证与版本

基线提交 7d3570827b165198daa049f3802933ab6e2ce572，分支 codex/cli-lifecycle-compose-hardening。该提交保存用户要求的既有 CLI/编排改动，未推送。当前实现未提交；最终输入摘要与命令结果将在独立证据清单中登记。

Windows，D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，PYTHONUTF8=1；pytest 使用 conftest 隔离测试库、屏蔽真实外发。基线相关后端 28 passed，前端四门退出码均为 0。

有效红测试最初 8 failed / 3 passed，发现缺失入口；实现后额外暴露 provider 异常被既有管线转换成正常结果的问题，已对重新生成失败显式拒绝。相关回归 77 passed、exit 0；独立反例 10 passed、exit 0。一次命令错误引用不存在的测试文件，exit 4、未执行测试，属于命令选择错误，见 data/chat006-evidence/related.txt，不计作业务通过。

最终全量 `python -m pytest --basetemp data/pytest-tmp/chat006-final`：2579 passed、3 skipped、3 warnings，648.66秒，退出码0。三个skip均为既有CLI测试：Windows无符号链接权限、POSIX 0600权限语义、打开文件替换身份保护；不以Windows结果替代其Linux证据。warnings为既有ast.NameConstant弃用和RAG数值对抗样例的invalid/overflow。

`python -m ruff check . --no-cache` 退出码0；`python -m mypy app/` 退出码0，212源文件。前端 `npm run typecheck`、`npm test`、`npm run lint`、`npm run build` 均退出码0，165 passed / 17 files；细节见 ../reviews/2026-10-18-chat-006-frontend-validation.md。前端早期 ESLint hook 命名错误和测试红项均已修复，保留在该报告。

完整命令、依赖版本、510项最终源码/测试/配置输入SHA256与日志/页面产物摘要见 ../reviews/2026-10-18-chat-006-evidence.json；实际日志在 data/chat006-evidence/。后端运行开始后未改业务或收集的测试输入；手工页面夹具仅调整import顺序，已过最终Ruff。前端修复后独立重跑四门并取得最终构建页面证据。OpenAPI仅保留旧行换行格式，未改其JSON语义。后续说明/状态文件不改变上述检查输入。

## 未做、风险和回滚

不做分支、覆盖答案、跨进程锁、新迁移、依赖或架构升级。用户点击停止或离开页面只取消客户端等待，不承诺已开始的服务端模型调用被撤销。失败可能保留新用户消息，因此最新目标须以刷新后的真实记录为准。

未执行生产部署、真实模型质量评测、推送、合并或本卡提交。本卡未新增平台条件、子进程或文件系统业务行为；手工页面夹具涉及本机测试文件，不等于发布平台能力。Linux/Ubuntu 本卡 CI 尚未运行，不声称最终提交 CI 通过。

回滚恢复本卡 Service/API/前端和契约文档；历史追加的问答仍是普通消息，无需删库或迁移。必要门禁或业务验收未齐时继续保持 in_review。
