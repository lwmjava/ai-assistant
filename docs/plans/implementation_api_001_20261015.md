# 业务 API 版本前缀实现说明

日期：2026-10-15。API-001，状态 in_review。基线 873db9c0fb243b0847b4be57242c8f333f4f0b1e；分支 codex/platform-batch-20261015，未提交或推送。候选为 HEAD 加本批工作区，最终输入指纹及全量回归将在整合阶段重新绑定。

## 实际行为

所有业务路由只挂载 /api/v1。根入口继续提供控制台或服务信息，但不列入 OpenAPI；静态页面回退拒绝裸 /api、旧 /api/* 和未知 /api/v1/*，不会把 index.html 当作业务 API 成功响应。旧业务路由无别名与重定向。

前端 HTTP、认证刷新、SSE 和独立对话导出统一使用 http.ts 的 API_BASE。首次改密身份白名单同步到新路径，仍只放行当前用户信息和改密，不扩大业务权限。Dockerfile 镜像健康探针、Milvus 切换检查脚本、README 当前使用说明和生产编排运行手册同步。Vite 既有 /api 代理覆盖 /api/v1，无需改配置。Swagger 重新导出共 62 个路径。现有测试和内嵌子进程脚本迁移平台路径；新测试中的旧路径拒绝断言保持原值。

主要位置：app/main.py、app/api/deps.py、app/api/routes/audit.py（说明）、frontend/src/lib/http.ts、lib/sse.ts、api/export.ts、Dockerfile、scripts/milvus_switch_check.py、tests/test_api_versioning.py，以及现有测试调用方。仅路径变化，无数据库迁移或业务规则变更。

## 实际验证

环境：Windows；D:\DepTooL\anaconda3\envs\ai-assistant\python.exe，Python 3.12.0；PYTHONUTF8=1，PYTHONIOENCODING=utf-8。pytest 使用测试库和每次全新 basetemp；未人工读取或覆盖工作区 .env，但此前 Settings 会继承本机配置，不能将该轮历史结果视为离线验证。后续隔离纠正见 docs/reviews/2026-10-15-platform-test-network-isolation.md。前端沿用已安装依赖，Node 22.20.0/npm 10.9.3。

- 有效后端红测：python -m pytest tests/test_api_versioning.py --basetemp data/pytest-tmp/api-version-red-20261015 -q --tb=short，3 failed，退出 1，分别证明版本前缀缺失、新健康路径不存在与 SPA 裸 API 被吞。
- 前端红测：npm test -- --run src/lib/api-version.test.ts，3 failed，退出 1；统一前缀后 3 passed，退出 0。
- 一次错误命令包含不存在的 tests/test_auth.py，退出 1、未运行测试；不计业务结论。
- 组合专项命令：python -m pytest tests/test_api_versioning.py tests/test_initial_accounts.py tests/test_initial_accounts_review.py tests/test_auth_register.py tests/test_auth_revoke.py tests/test_cli_init.py tests/test_cli_init_review.py tests/test_api.py tests/test_openapi_coverage.py --basetemp data/pytest-tmp/api-version-green2-20261015 -q --tb=short。2 failed / 72 passed / 3 skipped，113.98 秒，退出 1。失败为旧测试仍要求根路径出现在 OpenAPI，以及 README 根入口仍列在业务端点表，均已修正。跳过属于既有 Windows 文件系统能力限制，不能作为 Linux 证据。
- 修正后：python -m pytest tests/test_api_versioning.py tests/test_api.py tests/test_openapi_coverage.py --basetemp data/pytest-tmp/api-version-green3-20261015 -q --tb=short，37 passed，26.92 秒，退出 0。
- 独立前端刷新/失败清理断言：npm test -- --run src/lib/api-version.test.ts src/lib/api-version-review.test.ts，5 passed，退出 0。覆盖 /api/v1/auth/refresh 和重放 /me，不回退旧别名。
- npm run typecheck、npm test（当时 52 项，独立两项随后增加）、npm run lint、npm run build 均退出 0。独立用例加入后的最终四门仍须绑定最终输入。
- 实现者实际执行全库 Ruff 与 mypy（206 文件）退出 0；主 Agent 整合后最终复跑待记录。python scripts/check_production_compose.py 退出 0，明确只证明静态编排契约，不能代替启动。

独立审查：docs/reviews/2026-10-15-api-001-review.md。旧根路径断言是本卡新增契约未同步，未当成基线失败或以跳过测试处理。

## 未完成门禁与回滚

本卡候选整合验证已完成：不带筛选的 python -m pytest --basetemp data/pytest-tmp/platform-batch-api-full-20261015，2362 passed / 3 skipped / 3 warnings，845.71 秒，退出 0；完整日志 %TEMP%/platform-batch-api-full.log。主 Agent 实际重跑全库 Ruff --no-cache、mypy（206 文件）退出 0，前端 typecheck、Vitest（54 项）、lint、build 全部退出 0。490 个源码/测试/配置/依赖输入在验证后无漂移，记录 docs/reviews/platform_batch_api_inputs_20261015.json。三项 skipped 对应 Windows 不能证明的 CLI 文件权限/符号链接/替换竞争，不算 Linux 通过。

NFR-003 七项 Compose config 已针对 /api/v1 镜像健康探针重新执行并更新五文件指纹，报告 production_compose_config_20261015.json；无容器启动。尚缺实际页面用户路径、相关 Linux/同提交 Ubuntu CI；未构建运行 Docker。后续 LLM/附件等输入变更会使当前全量证据成为中间版本，批次最终仍须重新验证。保持 in_review，不宣称可发布或整批完成。

回滚须同步回退后端挂载、前端调用、脚本/镜像探针及当前文档，避免客户端与服务端版本错配；本卡没有数据回滚操作。未新增旧路径兼容层，未改变模型或 SSE 协议。
