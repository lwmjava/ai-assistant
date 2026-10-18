# CLI 与 Compose 批次交付记录

授权集合：CLI-003 → NFR-012。基线 6eacc586f7b2b8581095538aadbc78a7cc9e35cc，新分支 codex/cli-lifecycle-compose-hardening。只创建本地分支，未提交、推送、合并或部署。用户后续明确批准追加 tests/test_cli_lifecycle.py。

## 实际交付与状态

| 任务 | 本地交付 | 最终状态及未验收项 |
|---|---|---|
| CLI-003 | 四个显式Compose命令、参数检查、错误处理、日志脱敏、文档及隔离生命周期验证 | in_review；最终提交Ubuntu Backend CI未执行，原Windows三平台用例仍跳过，Linux替身smoke不等于完整Linux CI |
| NFR-012 | 开发编排口令与跨域默认收紧、示例及运行说明、只读检查器、14项真实配置展开验证 | in_review；README旧Docker段补数据库口令的精确范围授权待答复，最终版本CI未执行 |

这不是整个模块完成或可发布声明。前置NFR-003的既有验收只核对，不作为本批新交付。既有用户改动及其他会话文档未覆盖；tasks.yaml和产品/运行文档保留原NFR-003、INST-004、CHAT-007验收补记。

## 验证

- 解释器：D:\DepTooL\anaconda3\envs\ai-assistant\python.exe，Python3.12.0；pytest8.4.2、Ruff0.8.4、mypy1.14.1。完整版本/非敏感配置及439个输入SHA256见 2026-10-17-cli-compose-evidence.json。
- 最终全量：python -m pytest --basetemp data/pytest-tmp/cli-compose-final，exit0，2553 passed / 3 skipped / 3 warnings，547.51秒。日志 data/cli-compose-final-pytest.txt。3个原平台跳过不包装成已验证；NumPy极值回归3警告保留。测试隔离工作区.env和真实外发，未以关闭安全/检索功能筛绿。
- 最终 python -m ruff check . --no-cache、python -m mypy app/ 均exit0；212源文件；git diff --check exit0。缓存访问拒绝改为无缓存同规则运行，不改变检查范围。
- 新CLI专项23通过；旧CLI基线22通过/3跳过。有效红证明命令缺失；独立审查发现 ${BASE}、$BASE 日志泄露，经回归修复后独立复核关闭。
- scripts/verify_cli_lifecycle.py：随机隔离项目 cli-smoke-ffd0d380af40，已有Python镜像、禁网、无卷，8次命令操作及清理exit0，重复启动同容器、停止后状态exited；清理失败会使脚本失败。未调用模型或真实应用数据。
- scripts/verify_cli_lifecycle_linux.py：Linux，Python3.11.17，实际Python子进程和临时Docker替身，6路径通过；首次noexec设施失败保留记录，修正挂载后通过。源码只读，禁网；不能替代真实Linux部署或最终提交Ubuntu CI。
- python deploy/check_compose_defaults.py --verify-compose：exit0，2份编排×7案例，14项通过；合法值exit0，缺失/空值exit1；未启动服务。原两项默认值有效红exit1后修复。Linux静态检查exit0。
- 前端检查不适用：无前端源码或HTTP契约变更。真实Embedding/RAG质量评测不适用：未更改相关运行能力，未作质量/性能提升声明。

先前 cli003-full 运行因后续脱敏修复使证据失效而停止，不计为通过；最终已完整重跑。最终439个源/测试/配置/依赖输入摘要核对无变化，后续只补验收文档和任务记录。

## 独立分工与边界

主Agent负责源码、测试、真实隔离Docker、Linux及最终回归；/root/cli_plan_review负责准入、独立入口反例和最终代码复核；/root/compose_final_review负责NFR原契约、14项真实配置矩阵及失败反例。两个审查报告包含身份与代码指纹，不把主Agent执行冒称独立重跑。使用 module-batch-delivery 及其验收门禁，以等价工程/代码审查完成技能指定环节。

实现说明：docs/plans/implementation_cli_003_20261017.md、docs/plans/implementation_nfr_012_20261017.md。审查：2026-10-17-cli-003-review.md、2026-10-17-nfr-012-review.md。

## 待处理

README旧Docker部署步骤仅列JWT/LLM，缺新增必填数据库口令；NFR-012原范围不含根README。已请求精确追加，未获批准不借CLI卡的文件许可合并范围。此缺口阻止NFR-012关闭。后续如获批准，只改该操作段并更新文档证据，无需重跑不受影响的业务检查。

平台最终CI需要最终提交及相应Git授权，当前未获提交/推送授权，不自行获取CI或触发真实模型调用。没有可自动关闭的卡；两卡均保持in_review。恢复工作时先核对分支、共享文件和输入摘要。回滚只恢复本批文件，不更改生产配置或已有卷。


2026-10-18 文档收尾：用户已明确批准将 README.md 加入 NFR-012 范围，已登记 allowed_paths，并补齐旧 Docker 操作段的数据库口令、JWT、LLM、CORS 要求。以上历史待授权项现已解除；业务源码、测试及运行配置未变，原439项输入指纹保持，原回归证据按文档补充规则沿用。最终提交Ubuntu CI仍未通过，不关闭两卡。用户本轮明确要求提交全部当前新增及修改文件，本次按保存待验收工作提交，不声明验收完成。
