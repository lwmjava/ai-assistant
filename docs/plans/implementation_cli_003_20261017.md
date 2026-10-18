# 部署生命周期命令实现说明

日期：2026-10-17。任务 CLI-003，计划见 plan_cli_compose_batch_20261017.md。当前为 in_review，不声明已通过最终提交 Ubuntu CI 或整个批次完成。

## 实际功能与失败行为

拥有宿主 Docker 访问权限的操作者可通过 ai-assistant start/stop/status/logs 管理显式项目。四命令强制 --project-name、--env-file，--compose-file 默认当前目录 docker-compose.prod.yml；不会为缺失部署参数选择隐式项目。该本机运维入口不使用应用用户/租户身份，不替代 Docker 的宿主权限。

- start：up --detach --wait，等待运行或健康；重复启动复用同一项目。
- stop：仅停止容器，容器和数据卷保留；没有 down 或删除卷入口。
- status：ps --all，包括已停止容器，成功查询不等于应用已就绪。
- logs：有限 tail，默认每容器100行，不持续跟随。凭据标记、env-file 原始/展开值、宿主敏感值和引用值命中时整行省略；未知且无标记的秘密不保证识别，不能替代源日志治理。

项目名、正整数超时/行数及文件可读性均校验；使用参数数组，无 shell。Compose 项目目录固定为编排文件父目录，COMPOSE_* 隐式选择清除，其他宿主变量保留常规覆盖优先级。不导入应用 Settings，因此无需先加载数据库或模型配置。

文件不可读或 Docker 无法执行返回1并说明检查方向；Compose 非零码保留（信号异常归为1），不回显底层输出。超时返回124、中断130，并提示 status 复查；容器操作可能继续，不宣称自动回滚。

## 代码与验收映射

app/cli.py 注册并分派命令，app/cli_lifecycle.py 实现隔离参数和输出处理，README 给出一致示例。tests/test_cli_lifecycle.py 是用户批准增加的精确测试路径；原 migrate/admin/init 未重写。

新增命令有效红：12 failed / 9 passed，失败来自原解析器缺少命令。独立审查补出 ${BASE} 与 $BASE 插值泄露反例；新增回归，后者单例红 exit1 后修复，最终专项23 passed、exit0。旧CLI基线22 passed / 3 skipped，exit0；中间候选与旧CLI组合43 passed / 3 skipped、exit0（当时21个新增用例，不充当最终源码全部检查）。三个跳过为原 Windows 权限/链接/竞争平台用例，保留缺口。

隔离 Docker：scripts/verify_cli_lifecycle.py 仅创建随机项目，使用已有 python:3.12-slim 镜像，禁网、无挂载卷、pull never；实际 start/status/logs/重复start/stop/status/重复stop 均0，重复start容器ID一致。最终项目 cli-smoke-ffd0d380af40 清理exit0，无卷删除。报告在 data/cli-smoke-ffd0d380af40/report.json，正式副本/摘要随批次证据提供。脚本 cleanup 非零使整体失败。

Linux：已有 nfr003-accept-20261017-app 镜像，源码只读、禁网；scripts/verify_cli_lifecycle_linux.py 用实际 Python 子进程和临时可执行 Docker 替身检查四命令、exit17与timeout124，6条均符合预期，Linux Python3.11.17。该 smoke 不是 Linux 真实 daemon 完整部署，更不是最终 Ubuntu Backend CI。首次 /tmp noexec 导致替身无法执行，属设施失败；改临时挂载 exec 后通过，未改变业务源码或断言。

## 命令、环境和最终门禁

Windows，D:\DepTooL\anaconda3\envs\ai-assistant\python.exe，Python3.12.0；pytest8.4.2、Ruff0.8.4、mypy1.14.1、FastAPI0.115.6、SQLModel0.0.22、langgraph1.0.10、croniter6.2.4。pytest 使用 PYTHONUTF8=1 与测试专用库，conftest 屏蔽本机 .env 和真实模型外发。

已执行：python -m ruff check . --no-cache 退出0；python -m mypy app/ 退出0（212源文件）；python -m pytest tests/test_cli_lifecycle.py -q --basetemp data/pytest-tmp/cli003-redaction-final 23通过、退出0；git diff --check 退出0。一次默认 Ruff 缓存访问拒绝，改用 --no-cache 同规则通过，不放宽门禁。

最终全量命令 python -m pytest --basetemp data/pytest-tmp/cli-compose-final，日志 data/cli-compose-final-pytest.txt；退出0，2553 passed / 3 skipped / 3 warnings，547.51秒。三个跳过保留原Windows平台用例缺口，三条警告为既有极值向量回归产生的NumPy数值警告。439个验证输入指纹与最终版本一致，见 docs/reviews/2026-10-17-cli-compose-evidence.json。之前 cli003-full 的运行因源码修复证据失效被终止，不计通过；首次 Stop-Process 宿主错误，精确PID taskkill成功后重跑。没有并行写同一测试库。

基线HEAD 6eacc586f7b2b8581095538aadbc78a7cc9e35cc；未提交候选采用该HEAD加最终输入SHA256，见批次证据。独立审查见 docs/reviews/2026-10-17-cli-003-review.md。

## 未做、风险和回滚

未推送、合并、部署、付费模型调用、真实.env修改、生产卷删除。未新增Kubernetes或本机PID管理。前端源码和HTTP契约不变，前端四门不适用。最终提交Ubuntu CI未运行，因此不能关闭。回滚仅恢复本卡代码和README，不停止既有服务或改数据。
