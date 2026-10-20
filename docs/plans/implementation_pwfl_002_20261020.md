# 工作流超时与取消实现说明

日期：2026-10-20。状态：实施与独立审查中，尚未通过最终验收，不可关闭。

## 当前已实现行为

有workflows写权限的成员可创建/更新本租户任务的timeout_seconds（严格整数1–3600，默认300）；系统管理员沿用跨租户管理能力。每次执行快照总时限，覆盖实际执行及重试退避。engine独占子任务和终态写入；超时发起合作取消，等子任务finally退出后保存failed / WORKFLOW_TIMEOUT。手动取消接口保存failed / WORKFLOW_CANCELLED；请求与cron仍等待同一条执行记录最终结果。

`POST /api/v1/workflows/{workflow_id}/executions/{execution_id}/cancel`复核写权限、租户及记录绑定；错绑404、无本进程活动记录409、终态幂等返回。取消请求不共享执行Session，等待后新Session读终态；终态持久化失败返回安全503，不伪报成功。活动期间删除工作流409。owner缺失不回退触发者。

注册仅含句柄、实际Engine实例/租户/工作流绑定及完成信号；重复取消不打断清理，父调用取消完成收尾后仍传播。取消是合作式请求，不强杀同步阻塞，不撤销已完成外部副作用；不能保证清理在配置秒数内完成。成功后的webhook独立通知，不追溯改成超时。执行历史表及状态枚举未变，只用既有failed/error标记。

## 修改映射

- `app/workflow/engine.py`、`app/workflow/control.py`：总时限、注册、单次取消、唯一收尾、重试前身份复核。
- `app/models/workflow.py`、`alembic/versions/b5c8d3e0f271_add_workflow_timeout.py`：配置列、默认值、隔离库迁移与回退。
- `app/api/routes/workflow.py`：字段校验、取消API、活动删除拒绝及owner缺失拒绝。
- `tests/test_workflow_timeout_cancel.py`、`tests/test_workflow_cancel_api.py`、`tests/test_workflow_timeout_migration.py`、独立入口反例：合成Smoke/Adversarial workflow-timeout-cancel-v1。
- `docs/adr/0017-workflow-execution-cancellation.md`、ADR索引、README、Swagger、任务卡和实施计划：批准边界与契约。

## 已有过程证据（非最终版本验收）

基线实现HEAD为8e81bb2，文档关闭/计划提交8cc3b06。指定conda解释器Python3.12.0，Windows11 build22621。pytest8.4.2、Ruff0.8.4、mypy1.14.1、SQLModel0.0.22、Pydantic2.10.4、httpx0.28.1、Alembic1.14.1；CI启用的langgraph在环境中安装，未关闭相关功能规避检查。所有回归离线并使用各Agent独立SQLite与basetemp，不操作真实库。

- 开发前工作流基线59 passed、exit0；Ruff与mypy219文件exit0，详见实施计划。
- engine有效红测：配置1秒仍运行，断言失败，1 failed、exit1（data/pwfl002-engine-red.txt）；不是导入或环境错误。
- 模型/直接迁移红测2 failed；直接迁移和既有对账5 passed；随后补真实auto_migrate反例，缺列仍被stamp，2 passed / 1 failed、exit1，保持阻断。
- API有效红测13 failed；API与既有工作流26 passed、exit0。覆盖真实HTTP取消和finally、终态新Session回读、活动删除拒绝、幂等/权限/租户/owner及安全503。
- engine与原重试15 passed；追加cron真实_trigger、吞取消不伪success、退避撤权反例后engine13 passed、exit0（data/pwfl002-engine-green2.txt）。故意制造失败事务的SAWarning为反例预期，未隐藏。
- 前端四门：`npm run typecheck`、`npm run test`、`npm run lint`、`npm run build`各exit0；Vitest18文件167测试通过，3.11秒；日志data/pwfl002-frontend-*.txt。前端依赖和源码未改，使用当前已安装node_modules，未重装依赖。交付为API，无新增按钮或页面验收。
- 过程Ruff发现测试局部import顺序，已修；mypy220文件exit0。最终检查仍须绑定最终输入。

## 当前阻断与未验证

真实旧库启动路径存在缺列被直接stamp的问题：`app/core/migration.py`不在初始批准范围，追加方案已落实施计划并向用户请求；未批准前不改此文件。直接Alembic通过不代表自动升级已通过。

独立入口审查、最终全量pytest/静态检查与输入指纹仍在进行；最终Ubuntu三项CI未验证，未授权推送。用户尚未明确委托本卡业务接受，不能自动done。

## 回滚

先排空当前进程活动执行，再回退本卡代码。可保留新配置列与执行历史；如需降级迁移，仅撤回超时配置列，不删除历史。生产或真实重要数据迁移没有授权。没有新增分布式调度、并发池、重启恢复、前端取消按钮或硬杀兜底。

## 最终本轮交付状态

最终专项（原工作流、重试、调度、超时取消、API、迁移、独立反例和OpenAPI）102 passed、1 failed、2 warnings、35.51秒、exit1，唯一失败为未获追加范围的旧库自动迁移缺列。最终Ruff与mypy220文件exit0。最后版本前端四门再次各exit0，Vitest18文件167测试、2.91秒。独立Agent最终44项反例/回归通过，包括6项原始JSON非有限值修复。全量过程3137 passed/1 failed/3 skipped、679.11秒、exit1；执行期间输入有修订，只作过程证据，不能替代最终全量。完整命令、警告、已知失败、E2分类及每项关闭对账见 `docs/reviews/2026-10-20-pwfl-002-acceptance.md`，最终候选输入清单见 `docs/reviews/2026-10-20-pwfl-002-inputs.json`。

PWFL-002保持blocked：追加app/core/migration.py请求尚待用户答复，文件未改，自动验收未通过，未启动最终独立人工审核；最终Ubuntu CI与用户业务接受未验证。用户此前授权本地提交，本轮仅以WIP保存，不宣称完成交付，不推送。

## 独立审查过程补记

独立工程Agent `/root/execution_review` 实际运行32项通过（新增6项、engine13、API13），无新增未修复P1/P2。反例包括并发HTTP取消必须等finally真实释放、清理期删除409、父重复取消不打断timeout清理、退避期间owner禁用/换租户/tenant禁用、真实执行Session的commit失败时安全503和原running记录保留。后者是明确失败结果，不保证自动补偿或恢复；本卡不增持久化恢复队列。报告 `docs/reviews/2026-10-20-pwfl-002-engineering-review.md`。后续Engine实例绑定及API schema修订使相关首轮证据失效，需要最终复验。

最终格式复核：移除了独立反例测试末尾多余空行，业务代码未变。为绑定最终测试输入，专项按同一命令改用 `DATABASE_URL=sqlite:///./data/pwfl002-related-final2.db` 与 `--basetemp=data/pytest-tmp/pwfl002-related-final2` 重跑：102 passed、1 failed、2 warnings、27.42秒、exit1；仍唯一旧库启动迁移失败。日志data/pwfl002-related-final2.txt。Ruff再次exit0，mypy/前端检查输入未变沿用。输入清单已更新该测试SHA256；独立审查正文指纹反映修剪前版本，仅末尾空行变化，无业务差异。
