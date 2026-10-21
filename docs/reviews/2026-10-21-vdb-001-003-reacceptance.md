# VDB-001 / VDB-002 / VDB-003 修复后复验

2026-10-21。用户授权修复首次验收三个问题，随后明确批准仅将`docs/swagger.json`加入VDB-002范围、同步`runtime`和`fallback_reason`两字段。

当前结论：原三个问题及本轮独立/真实验证追加发现均已修复；最终三卡专项、真实Standalone/Lite、Linux专项、Ruff/mypy和前端检查已通过。冻结版本全量pytest正在执行，尚不能声称通过；未提交工作区没有最终SHA的Ubuntu CI，三卡暂不关闭。后续结果只在本报告据实补记，不以阶段全绿代替门禁。

## 版本与证据边界

基线HEAD及分支：`c2fc1bf3e1f02327a6972539a0905371d5f4acef`、`milvus-default-vector-store`。候选是该HEAD加原有未提交VDB实现及本轮修复。基线工作区与最初738输入摘要见[首次验收](2026-10-21-vdb-001-003-acceptance.md)；不得只引用HEAD冒称它已包含修复。

Windows Python3.12.0，指定解释器`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`；pymilvus2.5.11、SQLModel0.0.22、pytest8.4.2、Ruff0.8.4、mypy1.14.1、langgraph1.0.10。所有pytest独立`DATABASE_URL`和`--basetemp`；conftest隔离宿主dotenv/模型凭据、禁止外发。`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`用于Windows子进程一致解码，不禁用产品功能。

Linux专用容器Python3.11.16、pymilvus2.5.11、milvus-lite2.5.1，项目声明dev版本pytest8.3.4/pytest-asyncio0.25.0。真实Standalone2.5.11绑定独立本机29530端口，独立容器和数据；没有生产操作。合成Embedding provider/model/dim为synthetic / synthetic-vdb-lifecycle-only / 8，证明功能链路，不代表检索质量、生产性能或真实Embedding指标。

冻结全量输入摘要见[740项源码输入](vdb_fix_20261021/final-frozen-inputs.sha256.json)，独立审查11输入见[第三轮摘要](vdb_fix_20261021/reviewer-inputs-round3.json)，真实运行容器259输入与宿主逐字节一致，见[真实验收报告](vdb_fix_20261021/real-verification.md)。Swagger是纯文档导出同步，对应契约检查单独重跑，未改业务/测试/配置/依赖输入。

## 原始契约到证据

| 卡/验收 | 实际证据及等级 | 结果 |
|---|---|---|
| VDB-001 默认实际Milvus、未知配置和故障不退local | 63专项；真实Standalone/Lite实际startup_mode，E2 | 通过 |
| VDB-001 真实Standalone空库摄取/检索/删除/重解析/租户/身份 | 最终bound prepare/verify，独立容器重启，新进程SQL/向量持久化；错实例领域入口反例，E2 | 通过；合成Embedding仅证明链路 |
| VDB-001 非检索隔离、检索unavailable | 真实TestClient lifespan故障注入，普通根接口200/鉴权接口401、health503；空库和故障明确区分，E2 | 通过 |
| VDB-001 混合限制/回滚与最终检查 | 手册/ADR说明Bounded一致性、Dense窗口内BM25及受控回滚；Ruff/mypy/前端已通过 | 全量执行中，最终CI未验证 |
| VDB-002 真实Lite摄取/检索/删除/重解析/重启、Standalone优先 | 最终bound两模式prepare/verify，真实模式/只读health/父目录初始化，E2 | 通过；Windows原生Lite未支持、不冒领 |
| VDB-002 仅不可达可兜底，配置/身份/Schema/凭据拒绝 | 真实SDK配置异常反例及选择边界；独立审查，E2/E3按具体用例区分 | 通过；未知错误保守拒绝 |
| VDB-002 进程固定、重启归属、错库拒绝 | 持久运行登记；具体实例摘要；read/write/activate/adopt/delete及缓存反例，E2 | 通过；历史无完整绑定不自动认领 |
| VDB-002 生产/远程禁止Lite与平台证据 | 已登记/新选两分支限制、prod别名；Linux63条；真实SDK/Lite兼容，E2 | 本地平台检查通过；最终Ubuntu CI缺失 |
| VDB-003 全组合/故障/回滚 | 63专项及真实两模式多进程/重启、丢文件拒绝；手册已同步，E2 | 当前组合通过 |
| VDB-003 同最终提交Ubuntu必要CI | 工作区尚未提交/推送 | 未验证，不能关闭 |
| VDB-003 环境/Mock边界/命令/退出码/指纹 | 本报告、真实报告、独立审查及机器可读摘要 | 已记录，不宣称质量/生产就绪 |

## 缺陷修复对账

1. 启动选择异常由`initialize_milvus_runtime`隔离为固定不可用状态；向量入口显式拒绝，不重探测换库。
2. persisted Lite也复核当前环境/开关/本机地址/包/路径；Standalone归属变化或故障不静默切Lite。
3. 实例不再只用mode，索引和缓存绑定规范化位置摘要；同模式不同实例拒绝。历史绑定不明保守隔离。
4. 独立审查发现激活、已有写索引和adopt绕过，统一核验放在状态修改前；原active/notes保留。
5. SDK配置异常不再按connection字符串误判不可达；TCP不可达独立探测，配置异常优先拒绝。
6. 真实空库搜索只对统一resolver确认的空库返回[]，故障/错实例不吞掉。
7. Lite父目录创建和实际SDK只读连接先于登记；丢失既有文件不创建空替身。健康探针使用Lite支持的list_collections。
8. 独立第三轮发现import/finally异常逃逸，全部收敛选择错误，初始化失败不写归属。

正式失败与绿色证据均保留。`lite-parent-red.log`只是新helper不存在的AttributeError，不能单独当业务失败；其根因由真实SDK缺目录失败`real-lite-missing-parent.log`证明。细节见[实现说明](../plans/implementation_vdb_acceptance_fixes_20261021.md)、[独立三轮审查](2026-10-21-vdb-fixes-independent-review.md)。

## 实际检查

证据目录：[vdb_fix_20261021](vdb_fix_20261021/)。以下Python均用指定conda解释器，Shell输出的模块退出码单独记录；不得用PowerShell自身退出0覆盖模块结果。

| 检查与命令 | 结果 | 日志 |
|---|---|---|
| 三卡：`python -m pytest tests/test_vdb_acceptance_fixes.py tests/test_vdb_001_admission.py tests/test_vdb_002_runtime.py tests/test_vdb_003_combination.py tests/test_health.py -q --basetemp data/t/vdb_lite_cleanup_green` | 63 passed / 0 / 22.83秒 | lite-cleanup-green.log |
| 独立第三轮相同五文件，独立DB/temp | 63 passed / 0 / 28.65秒；入口反例实际拒绝且无状态变更 | reviewer-tests-round3.log / reviewer-round3-green.log |
| Linux最终相同五文件，独立容器和路径 | 63 passed / 0 / 23.87秒，1条依赖弃用warning | real-bound-linux-pytest.log |
| 真实两模式prepare/verify及Lite missing-file | 五段均0，Standalone容器重启后持久化通过 | real-bound-standalone-* / real-bound-lite-* |
| `python -m ruff check . --no-cache` | 0；All checks passed | ruff-frozen.log |
| `python -m mypy app/ --cache-dir data/t/vdb_fix_mypy` | 0；223 source files | mypy-frozen.log |
| frontend：`npm.cmd run typecheck` | 0 | frontend-typecheck.log |
| frontend：`npm.cmd test -- --run` | 0；18文件/167测试 | frontend-test.log |
| frontend：`npm.cmd run lint` | 0 | frontend-lint.log |
| frontend：`npm.cmd run build` | 0 | frontend-build.log |
| Swagger同步后：`python -m pytest tests/test_openapi_coverage.py tests/test_workflow_alert_privacy.py tests/test_health.py tests/test_vdb_acceptance_fixes.py -q --basetemp data/t/vdb_contract_sync` | 45 passed / 0 / 27.20秒；导出与当前完整OpenAPI相等，其余路径/schema无变化 | contract-sync.log |
| 冻结无筛选：`python -m pytest -q -o faulthandler_timeout=60 --basetemp data/t/vdb_final_frozen_20261021`；DB=`sqlite:///./data/vdb-final-frozen-20261021.db` | 正在执行，未汇总 | final-frozen-pytest.log |
| `git diff --check` | 2；原有CRLF转换造成的trailing whitespace，不擅自重格式化用户文件 | diff-check.log |

此前运行包括已明确中断的过程全量，不能当通过；首轮GBK解码失败经仅增加PYTHONUTF8=1隔离，完整RAG门禁80条通过。调查及环境命令见[基线调查](vdb_fix_20261021/baseline-agent-investigation.md)。真实环境早期副本遗漏.dockerignore、镜像用户无目录权限、旧verify有限中断等也明确记为非通过；最终bound证据替代这些过程尝试，不删除失败记录。

## 关闭边界与回滚

尚无最终候选提交SHA及对应Ubuntu Backend、Frontend、Agent official baseline run/job URL，未核验远程required checks或bypass。Linux本地容器通过不能替代最终CI；不能因为“完成验收”请求而伪写done。未授权Git提交/推送、生产变更或付费模型；后续发布步骤须取得对应授权。

专用验收容器和全部数据保留，详见真实报告。回滚不删文件/集合/卷/登记，不自动迁移或合并两库；旧代码不识别完整绑定时先受控核验。默认Milvus仍Partial，既有Bounded一致性约束明确保留，不承诺同地址数据卷被替换的自动检测。
