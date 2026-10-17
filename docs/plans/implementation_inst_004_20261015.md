# 内置账号与首次改密实现说明

日期：2026-10-15。任务：INST-004。状态：`in_review`，未关闭、未提交、未发布。

## 实际行为

空用户库中，部署者配置管理员用户名 admin 与三个独立环境口令后，在同一事务创建 admin（system_admin）、system（default 租户 tenant_admin）、culture（member 且 is_test_user=true）及成员关系；口令均至少八位，数据库保存 bcrypt。两项新增口令不齐全时拒绝且不写账号。只配置既有管理员变量时保留单管理员引导；没有口令则不提供默认口令。已有用户库不认领同名账号、不改密码/角色或测试标记。CLI/setup 首次创建管理员也要求改密；旧用户的两个新标记默认 false。

待改密用户可以登录、刷新令牌、读取自身及改密；统一后端身份依赖拒绝其他业务请求。前端登录、刷新和业务深链均等待真实 /me，不能用持久化旧摘要放行。`POST /api/auth/change-password` 校验当前口令、新口令长度及差异，成功保存哈希、清首次标记、递增 token_version，返回新双令牌；旧 refresh 失效。并发请求按旧哈希和 token_version 条件更新，旧请求不能覆盖先完成的改密。错误凭据、相同密码及冲突返回固定 400 并记录失败审计，不回显口令。沿用原 access token 有效期语义。

管理员列表、改角色、启用/停用响应均回传真实标记。测试身份只读持久化字段，不按用户名推断。前端改密 POST 或 /me 的迟到响应不能恢复已退出的会话或覆盖后来建立的会话。

## 代码与契约

- 表与配置：`app/models/user.py`、`app/core/config.py`、`.env.example`。
- 引导/改密事务：`app/services/auth_service.py`；身份限制：`app/api/deps.py`。
- API/契约：`app/api/routes/auth.py`、`app/api/routes/admin_users.py`、`app/schemas/auth.py`，同步 README 与 `docs/swagger.json`。
- 迁移：`alembic/versions/f1a6c8d2b903_add_user_password_flags.py`，父节点为原真实 head c1d2e3f4a5b6；`app/core/migration.py` 增强 stamp 的用户列核对，未添加第二套手写补列流程。新建表已含字段时迁移幂等；最终修订缺列的损坏库明确拒绝。
- 页面/会话：`frontend/src/pages/ChangePassword.tsx`、`App.tsx`、`pages/Login.tsx`、`store/auth.ts`、`types/api.ts`。
- 用例：`tests/test_initial_accounts.py`、`tests/test_initial_accounts_review.py`、`frontend/src/first-password.test.ts`；现有权限 fixture 同步两字段。

原始卡及用户批准的路径扩充登记在 tasks.yaml，批次授权原话与完整基线见 `plan_platform_batch_20261015.md`。独立审查：`../reviews/2026-10-15-inst-004-review.md`。当前候选输入 SHA256：`../reviews/inst_004_inputs_20261015.json`，HEAD 仍为 873db9c0fb243b0847b4be57242c8f333f4f0b1e，加清单中未提交变更。

## 实际验证

Windows 11；指定 conda Python 3.12.0；pytest 8.4.2、Ruff 0.8.4、mypy 1.14.1；Node 22.20.0、npm 10.9.3。pytest 统一 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，使用全新 `--basetemp` 与隔离测试数据库，非生产库。

| 检查 | 实际结果 |
|---|---|
| 修改前全量 `python -m pytest --basetemp data/pytest-tmp/platform-batch-utf8-10ad24c9a6414d298152b66baafa9fc6` | 2329 passed / 3 warnings，退出 0；仅证明基线 |
| 最初两个业务红测 | 2 failed / 退出 1，明确缺少 system/culture、部分配置仍写用户 |
| 最初前端有效红测 | 5 failed / 退出 1，观察到业务布局泄漏及旧摘要保留；设施错误不算业务失败 |
| 首轮候选专项 | 45 passed / 8 failed，退出 1；新增迁移父节点错误，已修复 |
| 独立反例红测 | 3 failed / 退出 1；管理员丢标记与后端旧改密覆盖，已修复 |
| 前端延迟 POST 三种会话反例 | 三项失败后修复，退出 1→0 |
| `python -m pytest tests/test_initial_accounts.py tests/test_initial_accounts_review.py tests/test_auth_register.py tests/test_auth_revoke.py tests/test_cli.py tests/test_api.py tests/test_openapi_coverage.py --basetemp data/pytest-tmp/inst-review-green-20261015 -q --tb=short` | 60 passed，61.21 秒，退出 0 |
| 新增真实启动链第一次执行 | 1 failed / 2 passed，退出 1；测试禁网误拦 Windows 内部自连接，非业务失败 |
| `python -m pytest tests/test_initial_accounts_review.py --basetemp data/pytest-tmp/inst-lifespan-loopback-20261015 -q --tb=short` | 3 passed，10.21 秒，退出 0；仅放行 loopback，仍禁外部连接；真实系统 env→Settings→生命周期→三账户登录→新 Session 持久化 |
| `python -m ruff check . --no-cache` | 退出 0；无缓存用于隔离已有缓存目录权限问题 |
| `python -m mypy app/` | 205 文件，退出 0 |
| 前端 `npm run typecheck`、`npm test`、`npm run lint`、`npm run build` | 均退出 0，49 测试；SSR 警告保留，未当作浏览器验收 |
| Swagger 导出、`git diff --check` | 退出 0；修复 Windows 写入引入的 CRLF 差异，不改变逻辑 |

绝对命令解释器为 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`，上述 python 均指该路径。确定性安全/生命周期反例是版本化 Smoke/Adversarial 回归，不声称 Gold 或真实模型质量提升。

## 未完成门禁与回滚

当前候选尚未执行全量 pytest；批次最终源码稳定后必须全量执行并绑定最终输入，基线的全绿不能替代。Linux/最终 Ubuntu Backend CI 未取得，WSL 仅发现 docker-desktop、当前 Docker daemon 不可连接；无推送授权，不伪造 CI URL。浏览器完整改密路径、部署 HTTPS 及人工业务验收尚未证明。上述任一必要门禁未完成，不标 done 或可发布，也不作为其他卡已完成前置。

没有做导出排除细则、测试角色、生产补种/迁移、access token 全局撤销、真实模型调用、Git 提交/推送/合并或部署。回退源码时保留新增列与账号数据，不能把数据库降级删列当成安全回滚；真实数据操作需另行批准。
