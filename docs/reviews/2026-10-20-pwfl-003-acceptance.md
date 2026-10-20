# 工作流并发与周期幂等关闭对账

日期2026-10-20，主Agent /root，基线ee1018435a757fe7e1eca97be6bc271442d277ae；候选为HEAD加[733项冻结输入](2026-10-20-pwfl-003-inputs.json)。批准来源：用户明确批准具体方案及列出的最小扩围。依赖PWFL-001已关闭，PWFL-002当前版本独立复核后按用户CI成功与关闭接受登记done，未把用户确认冒称独立GitHub审计。

状态：最终全量检查通过，最终独立人工审核技术通过；最终同SHA Ubuntu CI、用户业务接受未验证。此对账不允许关闭任务。

## 原始契约逐项对账

| 原始要求/出处 | 输入/前置与真实操作链 | 可观察结果 | 证据与类别 | 结果 |
|---|---|---|---|---|
| tasks.yaml 同任务同周期只执行一次 | 有效User/Tenant、隔离SQLite、HTTP/Engine/_tick | 重入409，历史与Bridge仅一次；cron终态同due/旧due拒绝，下due可运行 | tests/test_workflow_admission.py、admission_api、admission_scheduler、admission_review；入口报告实际144pass，E2合成合作Bridge+真实持久化 | E2通过，最终全量同输入通过 |
| 并发上限可配置 | Settings与env整数串→不同workflow真实重叠进入 | 默认4，边界1/1000，非法值拒绝；上限可达但不超 | config反例与capacity_can_reach_limit，HTTP容量拒绝；E2配置/入口 | 通过 |
| 同进程重复无重复记录 | 多请求含不同有效prompt并发，同线程与不同loop/thread | 相同workflow单历史，满容量零记录/Bridge | active_duplicate与review_threads，新Session回读；E2 | 通过 |
| 达上限排队或拒绝 | manual/cron共用Engine，选择契约允许的拒绝 | HTTP409，释放后再准入；被拒due可在后续tick重试 | HTTP、capacity_rejection_retries_same_due；E2 | 通过 |
| 不实现跨节点锁、不依赖OPS-001、不验证双实例锁 | 只审单进程registry及既有串行扫描 | 没有分布式依赖/锁/任务池 | diff及ADR0018，范围检查 | 通过范围检查，不声明跨进程能力 |
| 项目全量/静态/前端门禁 | 最终冻结输入、指定conda、独立DB | 所有适用命令exit0 | 见下表及evidence.json | 通过（Windows本地） |
| 项目Linux/同最终提交Ubuntu CI | 最终提交及Backend/Frontend/Agent official baseline | run/job URL+同SHA成功 | 未推送，未运行Linux或获取最终CI | 未验证，保持in_review |
| 技能最终独立审核、人工业务接受 | fresh /root/final_audit抽查，用户业务判断 | 独立报告+业务接受 | [人工审核报告](2026-10-20-pwfl-003-human-audit-1.md)技术通过；用户未委托本卡业务接受 | 技术审核通过/业务未验证 |

E2仅代表真实代码与隔离SQLite、合成Bridge及故障注入，不证明真实LLM质量、生产吞吐或分布式exactly-once。没有新增前端页面/交互源码，浏览器页面验收不适用；前端契约四门仍实际执行。

## 失败边界覆盖

数值非法bool/float/NaN/Infinity/0/负数/1001与合法1/1000；首次commit/refresh、create_task、timer和终态commit失败；父取消、显式取消清理期间保持容量；webhook未完成保持容量；不同Engine/tenant周期身份、不同loop/thread共享容量；同due成功/失败/取消后不重放、旧due拒绝、下due运行；manual不消费cron槽；删除成功清槽、删除提交失败保留槽、删除与启动交错互斥。旧身份/租户/超时/重试断言保留并纳入工作流与全量回归。具体正式用例见四个新增测试文件及原timeout/cancel/retry/schedule集合。

## 实际命令与结果

所有后端命令用D:/DepTooL/anaconda3/envs/ai-assistant/python.exe；PYTHONUTF8=1。完整环境、版本与日志SHA256见evidence.json。

| 命令 | 隔离/日志 | 结果 |
|---|---|---|
| -m pytest -k 'workflow or idempot' -q --basetemp=data/pytest-tmp/pwfl003-preflight | DATABASE_URL=sqlite:///./data/pwfl003-preflight.db；data/pwfl003-preflight.txt | 基线128passed/3029deselected/2warnings/45.05s/exit0 |
| -m pytest tests/test_workflow_admission.py -q --basetemp=data/pytest-tmp/pwfl003-engine-green | 独立engine-green库 | 11passed/6.45s/exit0，后续修复纳入最终全量 |
| -m pytest tests/test_workflow_admission_api.py tests/test_workflow_cancel_api.py -q --basetemp=data/pytest-tmp/pwfl003-api-deletion-final | DATABASE_URL=sqlite:///./data/pwfl003-api-deletion-final.db，实际终端记录 | 33passed/5.16s/exit0 |
| -m pytest tests/test_workflow_admission_scheduler.py tests/test_workflow_schedule_contract.py -q --basetemp=data/pytest-tmp/pwfl003-scheduler-green | scheduler-green隔离库 | 36passed/30.51s/exit0 |
| -m pytest -k workflow -q --basetemp=data/pytest-tmp/pwfl003-review-03 | Reviewer独立隔离库，入口报告含实际输出统计 | 144passed/3053deselected/2warnings/70.90s/exit0 |
| -m pytest --basetemp=data/pytest-tmp/pwfl003-full-final | DATABASE_URL=sqlite:///./data/pwfl003-full-final.db；data/pwfl003-full-final.txt | 3197 collected；3194passed/3skipped/4warnings/855.25s，exit0 |
| -m ruff check . --no-cache | data/pwfl003-ruff-final.txt | exit0 |
| -m mypy app/ | data/pwfl003-mypy-final.txt | 221 source files，exit0 |
| npm run typecheck | frontend，data/pwfl003-frontend-typecheck.txt | exit0 |
| npm run test | frontend，data/pwfl003-frontend-test.txt | 18files/167tests passed，exit0 |
| npm run lint | frontend，data/pwfl003-frontend-lint.txt | exit0 |
| npm run build | frontend，data/pwfl003-frontend-build.txt | built，exit0 |

没有更改依赖，使用现有node_modules，未重跑npm ci。Node22.20.0/npm10.9.3。Python3.12.0；pytest8.4.2与CI dev固定8.3.4存在版本差异，已安装langgraph extra；最终CI证据仍必需。没有关闭相关功能或跳过失败测试来制造通过。

## 审查、修复与限制

计划审查/root/pwfl003_scope_review；配置/API实现/root/api_impl；scheduler实现/root/model_migration；准入/执行主实现/root；独立入口反例/root/execution_review；最终审核/root/final_audit未参与本卡实现与既有审查。实际使用task-card-delivery 1.3.0，多Agent按角色独立；工程/代码检查按等价维度完成，不声称运行了未运行的命名gstack流程。

两项P2先登记[入口报告](2026-10-20-pwfl-003-entry-review.md)，修复后正式反例通过；旧fixture固定ID问题见[归因](2026-10-20-pwfl-003-fixture-regression.md)。当前入口审查无未决P1/P2。最终全量和独立技术审核已通过，最终CI/业务接受未齐之前本对账不声称本卡完成。

回滚先停止触发并排空活动执行/回调，再回退代码配置；无数据库迁移，不删除或回放真实数据。只本地提交，未推送、合并、部署；不声称分支保护或required checks已独立核验。


## Windows跳过说明

本轮全量输出已出现test_cli_init.py两项、test_cli_init_review.py一项skip。源码明确条件分别为：Windows未授予符号链接能力；POSIX 0600权限语义；Windows替换打开文件身份保护。没有为本卡新增skip或修改跳过条件。这三项不能记为通过，需最终Ubuntu CI覆盖。

文档范围/语法/引用检查：tasks.yaml用PyYAML解析成功；计划、实现说明、验收对账和ADR0018本地链接存在；git -c core.whitespace=cr-at-eol diff --check退出0。只修改批准路径，没有依赖、模型、Alembic或前端源码变更。


最终后端全量：3194 passed、3 skipped、4 warnings、855.25秒（14分15秒）、退出0。四条警告为既有AST弃用、RAG数值反例的invalid divide/overflow，以及工作流事务故障注入的SAWarning，未屏蔽。最终733项输入逐项SHA256重算，变化0；只新增报告不改变检查输入。完整全量与六项静态/前端日志已归档本目录，evidence.json列出路径和日志SHA256。
