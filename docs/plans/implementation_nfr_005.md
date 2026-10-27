# PostgreSQL 生产路径验证

## 授权、基线与范围

2026-10-26用户指定task-card-delivery技能开始实施NFR-005。基线feat/EVAL-002、2549b43cf94e3a2f8ea0be0a8df618432bdd10a3；已有tasks.yaml中AUTH-005关闭记录完整保留。直接依赖REL-004已完成，其检索实验不采纳结论不受本任务影响。最新同SHA CI40256120463四项success（Ubuntu3575 passed/4 skipped）作为输入未变的开发前基线；无已知业务基线失败。指定conda ai-assistant Python3.12.0、SQLAlchemy2.0.36、SQLModel0.0.22、Alembic1.14.1；补装requirements.txt原已声明的psycopg2-binary2.9.10，不新增项目依赖。

沿现有Compose选择PostgreSQL16，使用本任务独占临时容器/数据库、随机运行时凭据与loopback端口，不挂载用户数据卷、不操作现有SQLite/Milvus/生产库，不修改.env。只在alembic、app/core、app/models、scripts、tests、docs、当前卡范围内修复实证兼容缺陷。SQLite继续为开发默认；不实现数据双向同步，不改变业务模型含义，不提前实施MIG-001。

## 实施方案

1. 在未调用create_all的全新PG库从base执行完整Alembic upgrade head，对照唯一head和实际表/列/索引/约束；重复升级验证幂等。另一个空库走正式init_db启动链，再用新连接检查持久化。
2. 先保存有效失败复现，再对迁移/类型/SQL方言问题做最小修复。版本化回归明确区分真实PG、SQLite兼容测试与无服务器的契约测试；缺PG不能冒称通过。
3. 复用真实Service/API路径，用隔离合成业务数据覆盖身份/租户及会话消息、权限拒绝、配置/配额、工作流、评测数据与任务、记忆快照、审计/运行历史等新增表关键读写。覆盖提交后新会话读取、唯一/外键/布尔/时间/JSON类文本字段、失败回滚及有限并发竞争；不调用外部模型、MCP或实际副作用工具。
4. 增加可复用的隔离PG验证入口，要求显式目标和防误用边界，输出脱敏版本/配置/源码指纹与逐项结果。SQLite与PG类型/事务/锁/约束差异及处理结论写本记录。
5. 专项及调用方、Ruff/mypy、适用Windows/LinuxPG验证；验证执行器首次行为需要本地不筛选全量pytest，同工作区只运行一轮。前端无契约影响时复用已有同输入证据。最终diff经独立于实现者审查，范围内P1/P2回归闭合后退出。

## 当前实证与待解决项

2026-10-26：已修复布尔迁移SQL、PostgreSQL旧CHECK反射、遗漏的运行时表/列/部分唯一索引、配额整数范围和连接口令百分号转义；新增隔离验证器及正式回归。真实PostgreSQL16.15验证13 passed、0 skipped、退出码0，容器清理完成，见data/validation/20261026T082323-postgres-db7e3f28a71d/result.json。Ruff退出码0，mypy app/共256文件退出码0；源文件指纹见运行记录。

本地不筛选完整回归data/validation/20261026T082607-0f1329800364：退出码1，17 failed、3600 passed、16 skipped。17项集中于文档版本发布及历史版重新发布：恢复唯一约束后，现有发布事务写入顺序触发IntegrityError。app/rag/service.py和必要时app/rag/import_jobs.py不在原卡allowed_paths，已请求用户批准最小范围扩展；批准前不修改，不移除约束或降低断言。独立审查先前覆盖迁移及PG专项，其结论不能覆盖这次新发现的发布回归，修复后需复核。

Linux容器首次测试停在依赖下载，不构成业务失败或通过；正在使用已下载的声明依赖离线重试。最终源码完整回归、受影响独立复核和最终SHA Ubuntu CI尚未满足，任务不关闭。未操作生产数据，无模型费用，未提交或推送。

用户随后明确批准上述两文件范围扩展。最小修复仅修改service.py：新版加入Session前先将旧当前版退位并flush，历史重新发布也复用同一事务内的退位flush；后续异常仍整体rollback，不引入独立提交。对应17项失败所在五个测试模块正在复跑。

Linux离线依赖改装入容器临时/work/deps后，真实PG及验证器测试35 passed、退出码0，见data/validation/nfr005-20261026/linux.log（Linux Python3.11，PG16.15）。该次执行先于service.py修复，只证明其记录输入，不证明后续发布修复。最终完整回归与CI仍待完成。

## 数据库差异及处理

| 差异/真实反例 | 处理与边界 |
|---|---|
| SQLite接受布尔列与整数0比较，PG拒绝 | 历史迁移使用NOT is_current，保持原意 |
| PG反射IN检查可能返回ANY表达式 | 仅识别已知字段旧值的窄约束，保留其他CHECK |
| create_all掩盖历史迁移缺表/列 | 新迁移补齐现有模型结构，空库不依赖create_all；重复启动及升级复验 |
| PG Integer仅32位，SQLite整数可容纳64位 | 配额计数模型与迁移使用BigInteger，实测10GiB及超过32位token计数 |
| 直接建表漏掉已有迁移的当前版本唯一索引 | 模型声明索引，历史库补索引；重复当前版数据报错且不清理用户数据 |
| 恢复唯一索引暴露版本发布顺序冲突 | 同一事务先flush旧版退位再发布新版，异常整体回滚；文件/URL/历史发布正式反例 |
| PG并发事务锁不同于SQLite | 两连接同时消费最后一条配额，仅一成功，落库消息/会话各一；有限超时防挂起 |
| 连接密码含百分号时ConfigParser插值 | 传入Alembic配置时转义，真实特殊字符连接及独立配置契约测试 |
| 生产配置读取异常曾静默回退SQLite | 移除迁移入口的静默回退，不改变开发默认SQLite |

验证脚本只接受本机Docker daemon及显式官方PG14—17镜像，默认PG16；已有镜像缺失即失败，不自动下载。每次拥有独立tmpfs容器和随机数据库，拒绝用户业务库名，凭据不入报告；未执行生产切换、真实索引重建或数据迁移。本卡验证PG16，不声明逐一实测PG14/15/17。长期回归在tests/test_postgresql_integration.py和tests/test_postgresql_validation.py，复跑入口为指定conda解释器执行scripts/validate_postgresql.py。

发布顺序修复后，五模块专项110 passed、退出码0：data/validation/20261026T104217-f6a68e293d77。独立审查新增真实PG历史发布和写失败回滚用例，固定输入后启动完整回归data/validation/20261026T104625-509f3b65ae2f；结果待写入。

## 固定候选的专项结果

候选仍为基线HEAD加工作区改动，非最终提交。完整源码/测试/配置/依赖/评测输入SHA256清单保存在正式PG运行result.json；复核与当前backend input_snapshot完全一致。发布源码及PG测试关键指纹也记录在独立审查报告。

| 实际执行 | 环境/结果 |
|---|---|
| 指定conda Python scripts/validate_postgresql.py | Windows Python3.12.0、PG16.15；15 passed/0 skipped，exit0；data/validation/20261026T104638-postgres-0540835a67a1，cleanup complete |
| 指定conda Python scripts/validate_delivery.py targeted，五个RAG调用方模块 | 110 passed，exit0；data/validation/20261026T104217-f6a68e293d77 |
| 指定conda Python -m ruff check . --no-cache | exit0，All checks passed |
| 指定conda Python -m mypy app/ | exit0，256 source files |
| Linux隔离容器内python -m pytest tests/test_postgresql_integration.py tests/test_postgresql_validation.py -q | Linux Python3.11、真实PG16.15；37 passed，exit0；data/validation/nfr005-20261026/linux.log；仅MockEmbedding用于阻止外部模型调用，不作检索质量声明 |
| 独立审查及新增PG两反例 | pg_review，2 passed，exit0；docs/reviews/2026-10-26-nfr-005-postgresql-review.md，无未闭合P1/P2；不代替全量CI |

前端未改源码或API契约，前端专项不适用本次差异；仍不豁免最终提交的项目CI门禁。技术验收覆盖隔离数据库的实际读写/迁移/回滚，不代表已切换或验收生产库。

## 本地全量及退出状态

固定候选不筛选pytest已完成：3617 passed、18 skipped、5 warnings，exit0，1618.49秒。15项PG测试因未显式设置目标在默认全量中门控跳过，已由上述真实PG15/15和Linux37/37证明；另3项是Windows不适用的CLI文件权限/符号链接/替换文件测试，不能据此声称这些平台分支已经Linux验收。证据data/validation/20261026T104625-509f3b65ae2f/pytest.xml及pytest.log。首次17失败如实保留，不以新结果改写旧日志。

技术实现、隔离确定性业务验证和独立审查已完成；当前状态in_review。最终提交同SHA的Ubuntu Backend CI及必要项目CI尚待取得，用户未授权本卡Git发布，所以不自动提交推送、不声明done或可发布。未进行生产数据库操作；无需人工在生产库重复测试本批次隔离场景。

最终验证器data/validation/20261026T104625-509f3b65ae2f/result.json已返回status=passed、进程exit0：收集、完整pytest、全仓Ruff、mypy均exit0，输入一致性校验通过。最终仅补本记录和当前卡，不改变检查输入。YAML可解析、任务ID唯一、范围扩展已核对；git按cr-at-eol核查diff无空白错误。所有本批次临时容器已按唯一名称/所属标签核验并清理。

## 验收与回滚

三项原始验收分别绑定空库全链迁移、实际PG关键读写和无未处理高危差异。受托确定性场景按用户实施目标验收，不把Mock或HTTP200当真实数据库成功。记录实际命令/退出码/文件指纹/环境及未验证项；最终同SHA必要CI与适用独立审查未齐时保持in_review，不能提前done。

回滚撤销本任务兼容补丁和验证入口，保留SQLite默认及已有数据。临时容器只清理本任务拥有的实例，任何包含用户数据的迁移降级/覆盖另需授权。本次无Git提交推送、生产切换或真实模型费用授权。
