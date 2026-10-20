# 工作流超时迁移追加修复独立审查

日期：2026-10-20。审查者：独立子 Agent migration_review，未参与业务实现。本轮仅新增本文，不改业务或测试输入。采用 task-card-delivery 第7步入口级反例复核；不是最终人工审核或用户业务接受。

## 范围与结论

读取 AGENTS.md、推进指导、Harness治理、tasks.yaml 原始 PWFL-002 契约、批准实施计划、现有实现说明及工程审查，适用规则00/01/07/10/11，核对相对8cc3b06完整业务变更及相对b7fab9d的迁移追加diff。用户明确批准追加app/core/migration.py；未授权真实库迁移或推送。

未发现本轮新增未修复P1/P2。新增10行在既有schema探测中检查workflows.timeout_seconds；确实缺列时只利用原stamp父修订及Alembic upgrade补列，不另建DDL补丁。已经标到head却缺列时开发返回False，生产拒绝启动，不伪报迁移成功。旧工作流与执行历史真实数据库回读保持；已有head仍不能证明任意全部schema无漂移，本补丁仅扩充本卡所需探测。

## 实际入口反例与证据

E2集成证据为合成Smoke/Adversarial，不是生产数据迁移、Gold或真实LLM质量证据。

| 反例 | 实际观察 |
|---|---|
| 父修订a4b7c2d9e160缺超时列，通过auto_migrate | 返回True，新增非空超时列，旧任务读回默认300，旧历史输出保留 |
| 记录更早9a5修订、实际为a4 schema混合库 | 经父修订对账后升级，未重跑已存在的建表DDL |
| 已记录head却缺列，开发/生产 | 分别False/RuntimeError，缺列仍缺列且版本未伪修复 |
| create_all后修订落后、直接升级/降级/再升级 | 幂等兼容；WorkflowExecution列结构不变 |
| 独立临时探针：无版本旧库/9a5旧版本库，真实database.init_db两次及第三次auto_migrate | 两种场景均通过；旧任务name与历史output保持，任务/历史各1行、历史列顺序保持、版本到head、默认300 |
| 既有超时取消及独立HTTP/Engine反例、旧重试 | 44项实际复验通过，包含权限拒绝、资源清理、重复取消、事务失败、双内存库绑定、非有限数422 |

临时探针使用隔离临时SQLite目录，先Alembic升级到a4，再插入旧任务与历史、删除版本表或stamp至9a5，然后调用真实init_db（含create_all及auto_migrate），以新Session及SQL回读验证。第一次手写fixture INSERT漏填workflow_executions.updated_at，exit1/IntegrityError；发生在业务入口之前，不是迁移失败。补齐该必填字段后两场景exit0，日志保留，不隐去初次错误。没有新增永久测试文件，主流程可保持冻结输入。

## 命令与结果

环境：Windows-11-10.0.22621-SP0；D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，Python3.12.0；SQLAlchemy2.0.36、Alembic1.14.1、pytest8.4.2。DATABASE_URL=sqlite:///./data/pwfl002-migration-review.db；PYTHONUTF8=1；conftest离线网络保护及独立模型/配置隔离生效，不关闭业务安全功能。

```powershell
$env:DATABASE_URL='sqlite:///./data/pwfl002-migration-review.db'
$env:PYTHONUTF8='1'
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest tests/test_workflow_timeout_migration.py tests/test_migration_reconcile.py tests/test_workflow_timeout_review.py -q --basetemp=data/pytest-tmp/pwfl002-migration-review
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest tests/test_workflow_timeout_review.py tests/test_workflow_timeout_cancel.py tests/test_workflow_cancel_api.py tests/test_workflow_retry.py -q --basetemp=data/pytest-tmp/pwfl002-migration-review-44
```

第一条16 passed/7.81秒/exit0；第二条44 passed/7.92秒/exit0。第二条1条SAWarning来自刻意注入失败事务的重复ORM身份，用例正常断言。两组有7项重叠，不能相加宣称60个独立场景。中间单跑review及timeout_cancel为20passed/exit0，无新业务输入，不替代上述完整44项。日志：data/pwfl002-migration-review-tests.txt、data/pwfl002-migration-review-44.txt、data/pwfl002-migration-review-engine.txt、data/pwfl002-migration-review-probes.txt（初次fixture失败）、data/pwfl002-migration-review-probes-final.txt（修正后两项通过）。临时探针通过stdin执行python -，具体构造与断言见前节，不把它伪称永久回归套件。

## 输入绑定与剩余门禁

候选：HEAD b7fab9d6db4646ee3a08cc69c1f627b4cc418ee9加当前工作区；业务基线8cc3b060e45d0f6686d99e94ce1ee71bf10c2f3e。关键追加输入SHA256：

| 文件 | SHA256 |
|---|---|
| app/core/migration.py | CD7E8B1960EC3DDE77067D0F2E713F3FADD03D161DA355E599B2AE67E2B27D75 |
| alembic/versions/b5c8d3e0f271_add_workflow_timeout.py | 68E0023647646D37F86B78C864206D0989C4E860FE5D1A9A9290600BE587E1D5 |
| tests/test_workflow_timeout_migration.py | 0FD4F362B7A302305C176EB85F81A9A9E6C836482BAFABAB084083823CB61777 |

原engine/API/control/model及44项输入指纹与2026-10-20-pwfl-002-engineering-review.md所列保持一致；主流程应将全部最终检查输入及本追加修复绑定到最终提交。相关源码/测试变化须重跑对应检查。

本报告不认定全量pytest、前端四门、全量Ruff/mypy或Linux/Ubuntu CI通过；这些须主流程最终证据。仍须最终独立人工审核与用户业务接受，不能由本审查替代。数据库不可用的终态持久化恢复、合作取消不响应的下游、跨进程强杀仍不在已证明范围。未推送、合并、部署或操作真实数据库；回滚应先排空活动执行、回退补丁，保留历史，迁移降级仅在获授权场景执行。
