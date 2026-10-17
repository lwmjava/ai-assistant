# 初始化配置实现说明

日期：2026-10-15。CLI-002，状态 in_review；尚未关闭、提交或发布。

## 实际功能

`python -m app.cli init` 或已安装的 `ai-assistant init`，使用标准库生成随机 JWT_SECRET_KEY，默认独占创建 cwd/.env；`--env-file` 可指定新输出。输出只有固定提示，不含密钥，不创建数据库、不导入 Settings/认证服务。已有文件、目录或链接均拒绝，原内容保持；写失败关闭描述符，并仅在目标仍是本次创建的文件时清理。POSIX 创建请求为 0600，Linux 实际语义尚待验证。

运行应在项目根目录；自定义输出不会更改应用固定的配置加载路径。验收只在临时目录运行，从未执行工作区 init 或覆盖真实 .env。已有 migrate/admin 参数兼容，改为分派后延迟导入。非 development 启动补齐空串/空白密钥拒绝，保留已有默认密钥拒绝；校验在 lifespan 的建表和账号引导之前，错误包含 init 或环境变量解法。拒绝启动的进程不会同时提供 /health。

实现：app/core/bootstrap_config.py、app/cli.py、app/core/security.py、app/main.py、tests/test_cli_init.py、tests/test_cli_init_review.py；README 同步实际用法。原始契约与非目标见 plan_cli_002_20261015.md；独立审查见 ../reviews/2026-10-15-cli-002-review.md。未更换实际密钥或会话，未引入依赖。

## 验证与限制

Windows 11，指定 conda Python 3.12.0；PYTHONUTF8=1、PYTHONIOENCODING=utf-8，测试库/输出文件均隔离。

- 有效红测：真实 CLI argparse 缺 init，首用例退出 1（内部 CLI 退出 2），没有导入失败。为避免旧代码加载工作区 .env，红测子进程仅屏蔽 dotenv 读取；最终另用真实 -m app.cli init 及导入屏障证明新路径不加载配置。
- `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_cli_init.py tests/test_cli.py tests/test_initial_accounts.py tests/test_initial_accounts_review.py tests/test_api.py tests/test_auth_register.py tests/test_auth_revoke.py tests/test_openapi_coverage.py --basetemp data/pytest-tmp/cli-init-green-20261015 -q --tb=short`：75 passed / 2 skipped，88.29 秒，退出 0。跳过是 Windows 链接权限、Linux 0600；不算验收通过。
- `...python.exe -m pytest tests/test_cli_init_review.py --basetemp data/pytest-tmp/cli-init-review-20261015 -q --tb=short`：2 passed / 1 skipped，1.72 秒，退出 0；并发独占创建、fdopen 失败清理通过；打开文件替换保护须 Linux 实跑，保留缺口。
- `...python.exe -m ruff check . --no-cache`：退出 0；`...python.exe -m mypy app/`：206 文件，退出 0；`git diff --check`：退出 0。前端源码和 HTTP 契约无本卡变更，本卡前端检查不适用；批次其他前端变更仍须最终门禁。

最终批次公共路径全量 pytest、Linux/最终提交 Ubuntu CI 尚未执行，必要门禁未齐不得 done。候选以 HEAD 加未提交输入绑定，批次最终清单再更新；不能用开工 2329 passed 代替新版本回归。不改 Docker、模型、生产环境或重要数据；回滚仅撤回本卡代码，不删除用户已生成的配置，不旋转或恢复真实密钥。
