# PostgreSQL 路径独立审查

- 审查者：`/root/pg_review`，非本批实现者。
- 基线：`2549b43cf94e3a2f8ea0be0a8df618432bdd10a3`，分支 `feat/EVAL-002`。
- 当前状态：独立审查完成，最终受影响差异已复核，范围内无未闭合P1/P2。此前`review_incomplete`为中间状态；本结论不代替主批次全量、Linux/最终CI及业务接受门禁。
- 范围：当前卡三项验收、`docs/plans/implementation_nfr_005.md`、迁移兼容补丁、新运行时表迁移、Document索引、PG验证执行器及定向测试。未展开其他台账、生产库或.env。

## 已核对的边界

初次静态预检指出布尔值与整数比较、PostgreSQL CHECK反射为ANY、create_all/stamp跳过迁移专属索引三个验证点。实现者随后用真实PostgreSQL复现，并提供最小修复与正式测试；结果已由下述独立执行及证据核对覆盖。

当前差异保持SQLite开发默认；布尔回填改为`NOT is_current`，不改变false/true语义。两个值域迁移在原IN表达式之外识别PG反射ANY表达式，只替换目标列的值域CHECK，保留其他约束。新显式迁移声明已有运行时表和缺列，不导入未来变化的动态业务模型，downgrade保留已落数据。真实测试涵盖空库全链、模型列覆盖、正式启动、旧窄CHECK保留行升级、实际API角色/租户、提交后新连接读取、JSON/中文文本、记忆/历史、外键与唯一失败回滚、配额并发与当前版本唯一性。Mock不作为PG证据。

执行器采用参数数组而非shell拼接，随机实例/库名和密码、tmpfs及loopback发布，不挂用户数据卷。JUnit空选择、跳过和失败不能报告为真实PG通过。独立审查未执行生产操作、模型调用或Git提交。

## 已闭合发现

1. **P2：既有表缺失的当前版本索引未由新向前迁移补齐。** 新Document模型索引只覆盖全新create_all路径，旧表缺索引仍有并发约束缺口。实现者已在显式新迁移开头补建索引，增加旧f9库缺索引升级及重复current失败保留行的两个真实PG反例；独立复跑通过，闭合。
2. **P2：允许PostgreSQL18参数但tmpfs路径沿用旧布局。** 官方18镜像将默认PGDATA改为`/var/lib/postgresql/18/docker`并调整VOLUME为`/var/lib/postgresql`。实现者已将入口标签限制为14—17，拒绝18，不再承诺未覆盖布局。依据：[官方镜像文档](https://hub.docker.com/_/postgres)。本次实际数据库为PG16，不冒称14/15/17的运行结果。

## 后续发现及闭合

**P2：远程Docker防误用守卫采用字符串前缀，可被userinfo URL绕过。** 独立无网络复现`tcp://localhost:2375@db.example:2375`被接受、真实hostname为`db.example`。实现者改用urlsplit，严格核对scheme/hostname/port、拒绝userinfo/query/fragment，npipe只接受本机路径；远程userinfo/npipe拒绝和IPv6 loopback等正式反例通过，独立复核闭合。未再次执行PG全轮。

实现者已补URL百分号转义并删除Alembic配置读取失败时静默回退SQLite，PG真实目标密码包含`@%/`；创建容器超时按唯一标签做拥有者清理。最终受影响单测已独立通过。

**实际PG配额整数溢出修复复核。** 实现者真实场景复现API允许的10GB存储配额写入PG INTEGER失败，将Tenant四个配额/累计列声明为BigInteger，新显式迁移仅PG扩大现有列且已是BigInteger时跳过。该扩大保留现有整数值、NULL和非空属性，不改变配额业务判定或SQLite原有64位整数存储语义，downgrade不做有损收窄。独立复核实际Service设置10GB、50亿token上限并累计`2**31+7`后新Session回读成功；没有把内存Python大整数当数据库成功。

## 独立真实PG验证

`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe data/validation/nfr005-20261026/run_pg_tests.py`，仅使用主Agent拥有的私有临时实例目标，未打印凭据。该helper调用正式`validate_delivery.py targeted --test tests/test_postgresql_integration.py`。证据`data/validation/20261026T081014-687477da09e9/result.json`：passed，collection/pytest exit0，12 passed、1 warning、25.66秒。覆盖前述实际迁移/持久化/并发与两个旧索引反例。输入由验证器记录SHA256；未执行本地full，未替主批次声明最终CI或生产切换成功。

## 最终复核待办

最终受影响复核：指定conda解释器通过临时隔离启动器执行`python -m pytest tests/test_postgresql_validation.py tests/test_postgresql_integration.py -k "large_storage or remote_daemon or local_daemon or percent_encoded" --basetemp <run>/tmp -o cache_dir=<run>/cache --junitxml <run>/pytest.xml`。证据`data/validation/pg-independent-final-ff144ffc71/result.json`、pytest.log、pytest.xml；exit0，12 passed、23 deselected、6.73秒，含真实PG大配额往返单例，未重跑数据库全轮或full。临时启动器只从主Agent私有目标配置构造进程环境，不输出凭据，不触及用户库。

主Agent最终正式执行器证据`data/validation/20261026T082323-postgres-db7e3f28a71d/result.json`经独立读取核对：PostgreSQL16.15、13/13通过、0 skipped、exit0、cleanup complete，且其backend输入快照与当前源码相同。该记录证明带特殊字符口令的完整正式入口和清理已执行；不冒称是本审查者重新跑了一遍全套。

主Agent仍负责唯一一轮本地全量、Linux实际执行、同最终SHA必要CI、业务接受及任务状态。必要证据未齐时不done；独立审查不代替这些门禁。现有任务允许范围内未发现其他应阻断的P1/P2；未审查任意用户自行改造的表、触发器或生产数据迁移，也未扩大为迁移单轨架构重写。

## 最终输入指纹

基线HEAD加以下SHA256绑定未提交业务候选；临时result.json也保存相同清单，后续相关源码变化需重新判断受影响证据。

| 文件 | SHA256 |
|---|---|
| scripts/validate_postgresql.py | 92db492a02680d2e8398b72cffb3af1698b3b199d3e3ac1418207bec703f1591 |
| alembic/env.py | 97ab176d91b19d8b98503d3674c2e08685ad4889bef1bdcfe7201dac84564238 |
| app/core/migration.py | a73e28fcec627b12d1fca9300f83087f196f7d2695b67fd2d70936f0bab1ec23 |
| app/models/user.py | 109b0f296ef8c3704587d09c9971c68d7fed9369000bdf2917cf2949c4bacb29 |
| app/models/rag.py | 4fad7ae16391c279488ff97238a777e5c242d33d9c5eaf2a77dec2f3e55ecc52 |
| alembic/versions/f8a4b6c7d9e0_expand_real_evaluation_constraints.py | 1215f053d66a0bac04a206c14c17c8eeb4eb94fe46a25f5c51fc60906980ec03 |
| alembic/versions/f8b2d4e6c173_add_document_version_state.py | 4d695540dcd4cad3ac0994b55396e779802f9d1a723b41450700d3dc1fafb03f |
| alembic/versions/f9b5c7d8e0f1_expand_dataset_family_constraint.py | 5af78156e96e76fa06f8258a0adab3ccea60b4b467a563b767a73e489dcf250f |
| alembic/versions/fa06d8e9f1a2_complete_runtime_persistence_schema.py | cca1e4fe9f1d64faf33ac0116b8fe1b8442756464b51b6a3433b2d917c234bf6 |
| tests/test_postgresql_integration.py | d3c87e23ea2b42914c9d06a3308819366d7fcc216477214e0ac65f35de577cc3 |
| tests/test_postgresql_validation.py | 4d29731026aa2ee3eb78794743373ec8ac85457cf5bbb4df5938fb1b95fdfb0d |

## 文档发布顺序修复的追加独立复核

首轮完整回归暴露恢复唯一索引后的17项真实文档发布/升级失败，不能沿用此前PG全绿推断最终全量成功。用户明确批准扩大到RAG发布路径；本次实际业务差异仅`app/rag/service.py`。独立检查确认：先把同组旧current降级并flush释放唯一键，再插入或发布新current；flush仍处于调用方同一事务，不提前commit。历史重新发布复用相同helper。正常新版本持久化的后续异常由原有rollback整体撤销；导入任务的异常与取消仍由既有外层事务处理。没有改变权限条件、评测模型或费用策略。

审查者补入两个正式真实PG回归：反向主键排序的历史版本重新发布，防止ORM更新顺序造成瞬时唯一冲突；新版本已降级旧current后模拟向量写失败，从新Session验证旧current和published状态恢复、无新版本残留。MockEmbedding仅避免外部调用，事务断言实际运行于隔离PostgreSQL16。

独立实际命令：指定conda解释器执行`python -m pytest tests/test_postgresql_integration.py -k "historical_document_republish or new_version_failure_after_demotion" --basetemp <run>/tmp -o cache_dir=<run>/cache --junitxml <run>/pytest.xml`；环境显式设置私有临时PG目标和required标记、SQLite守卫路径，不输出连接串。证据`data/validation/pg-independent-rag-7274bfeff0/result.json`、pytest.log、pytest.xml：Windows、Python3.12.0、pytest8.4.2，exit0，2 passed、13 deselected、11.49秒。受影响新增差异未发现未闭合P1/P2。

此次追加替换上表测试指纹并新增service指纹：`app/rag/service.py` SHA256 `37e1694801a4fc0f6dad43fa51a3923a274c99fe3b79d409f6cf50e43259f0be`；`tests/test_postgresql_integration.py` SHA256 `d9114a6425858d80290bcd7d573f610f9cb2762b936d8a71a1c4baed49758144`。此前13项PG全轮为该service差异之前的历史证据；实现者负责重新固定这次输入执行正式PG与最终完整回归。本追加不声明最终full或CI通过，也不代替任务关闭门禁。
