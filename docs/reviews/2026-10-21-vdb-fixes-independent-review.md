# VDB修复独立入口级审查

日期：2026-10-21。审查身份：`/root/independent_review`，由主Agent分配，未参与业务实现。依据 task-card-delivery 1.3.0 第7/8步，读取三卡原始契约、AGENTS、治理入口、适用规则、现有修复计划、实际业务diff和调用方，独立构造真实领域入口反例。

## 第一轮结论

**不通过：两个未决阻断。** 主Agent所述读入口实例绑定、启动隔离和已登记Lite限制已有针对性修复，但不能据此关闭三卡。本审查未运行真实Standalone/Lite、全量pytest或Linux/最终CI，不替代其他验证角色的证据。

### P1：空目标激活仍可跨实例改变生效索引

位置：`app/rag/index_registry.py` 的 `activate_index`、`resolve_write_index`、`adopt_legacy_chunks`。

`activate_index(..., allow_empty=True)`未在状态变化前调用实例核验。新建于Standalone A的目标，在运行时固定为Standalone B时，激活仍成功并退役原active；此时没有向量样本，远端探针分支完全不执行，无法由_connect的防线兜住。`resolve_write_index`已有active分支也直接返回A的索引。真实领域反例输出：

```text
read wrong instance: rejected
resolve_write wrong instance: ACCEPTED True
activate wrong instance: ACCEPTED active old= retired
```

建议在激活的任何计数/状态变化之前统一检查目标归属，在写索引解析返回前检查；历史认领入口同样必须先验证目标归属。失败时目标/原active和notes/计数保持不变，补空目标allow_empty、已active写入、历史认领反例。不得以补一个更下游SDK检查替代领域入口门禁。

### P1：SDK配置异常仍可被误判为连接失败并选择Lite

位置：`app/rag/vectorstore/runtime.py` 的 `classify_probe_error`。

当前按异常类型加正文的字符串判断，泛匹配`connection`。真实pymilvus 2.5.11的 `ConnectionConfigException(message='invalid connection configuration')` 被分类为 `unreachable`，随后开发本机选择直接返回Lite。输出：

```text
SDK ConnectionConfigException classification: unreachable
SDK configuration error selection: lite
```

这违背VDB-002配置/凭据/Schema错误不兜底契约。建议优先识别SDK明确参数/配置异常和明确错误码；只对可证明的连接拒绝/超时/未就绪分类，未知错误保守config。补SDK配置类以及混合“timeout/auth/config”等字符串的优先级回归。

## 运行证据

命令：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe docs/reviews/vdb_fix_20261021/reviewer-probes.py`。

Windows，Python 3.12，pymilvus 2.5.11。退出码0表示反例脚本正常完成，**不是验收通过**。脚本先通过项目conftest隔离宿主.env与模型凭据，使用专属 `sqlite:///./data/vdb-reviewer-20261021.db` 和内存SQL引擎；未外发、未写真实向量库、未改业务代码。

源码：[反例脚本](vdb_fix_20261021/reviewer-probes.py)；结果：[输出](vdb_fix_20261021/reviewer-probes.log)。基线HEAD `c2fc1bf3e1f02327a6972539a0905371d5f4acef`加未提交候选。

第一轮报告阶段采集的工作区SHA256如下。主Agent同期收到缺陷后开始修复，此采集不是反例执行时的原子快照，不用作“第一轮复现源码精确绑定”或最终通过证据；第一轮缺陷依据执行输出和当时读取的入口代码，第二轮另绑定实际通过输入。

| 文件 | SHA256 |
|---|---|
| app/rag/vectorstore/runtime.py | 9b885ce903c08bc0b06820810a4a702ab5f135c4a1b9f64cfdcce4a6232c7d18 |
| app/rag/index_registry.py | ceb817dd4a75657b2e7bf4c65c4d99c02031ddaa659ed352ca045d5f15ffb5b4 |
| app/rag/vectorstore/milvus.py | 255dfc3f44cac27578f1d49226bfe4e1171bcfd4a901b20d1ab5828c27d1a368 |
| app/main.py | 9fb7a65a9cf216a7d05802353fb4e2184e48568d3e56397decf57b0c0c2f92af |
| app/rag/cache.py | 0cf605317c1e432ef7a86824d39c7b4bbdd0369acb251d1aa909430439d46b28 |

## 后续复核

第一轮结论保留为历史缺陷证据。最终全量、真实双模式和Linux/同提交CI缺失时仍不得done。既有审查者不兼任最终人工审核Agent。

## 第二轮独立复核

主Agent补充激活、已有active写索引解析和历史认领门禁；SDK配置异常优先分类为config，并在SDK探测前独立检查TCP不可达。第二轮由同一独立审查Agent实际重跑，不使用主Agent的摘要代替验证。

反例脚本为避免全库Ruff的E402检查，整理为main函数并排序导入；业务操作语义不变，第一轮日志原样保留，第二轮新增输出。运行命令仍为上文指定解释器及脚本，退出0，输出：

```text
read wrong instance: rejected
resolve_write wrong instance: rejected
activate wrong instance: rejected
adopt wrong instance: rejected
SDK ConnectionConfigException classification: config
SDK configuration error selection: rejected
```

并断言拒绝后旧索引仍active、目标仍preparing、历史认领notes未改变，证明失败不留状态副作用。命令 `python -m pytest tests/test_vdb_acceptance_fixes.py tests/test_vdb_001_admission.py tests/test_vdb_002_runtime.py tests/test_vdb_003_combination.py tests/test_health.py -q --basetemp data/t/vdb_reviewer_round2` 使用指定conda解释器、独立 `DATABASE_URL=sqlite:///./data/vdb-reviewer-regression-round2.db`，退出0，**58 passed in 17.09s**。

证据：[第二轮反例](vdb_fix_20261021/reviewer-probes-round2.log)、[独立专项回归](vdb_fix_20261021/reviewer-tests-round2.log)、[输入指纹](vdb_fix_20261021/reviewer-inputs-round2.json)。反例脚本自身Ruff检查退出0。

第二轮代码审查结论：**第一轮两项P1已修复；本次审查范围内未发现剩余P1/P2。** 原三项修复的启动隔离、已登记Lite限制和实例身份相关专项均通过。对进程未初始化的CLI仍保留旧离线行为，是修复计划明确兼容边界；其测试不能证明真实应用启动后的归属约束可绕过。历史模式记录未被自动认领，属于保守失败。

本结论仅是代码与隔离入口复核通过，不代表三张业务卡最终验收完成：真实双模式、完整回归、最终Linux/CI和独立人工审核仍由对应证据决定。第二轮后若相关源码再改，须按指纹判断影响并重审，不能引用第一轮或第二轮旧输入关闭新候选。

## 第三轮：真实双模式追加修复复核

真实服务验证推动新增空库语义、Lite文件预初始化和Lite健康探针后，第二轮指纹仅保留为旧输入证据。本轮独立读取新增实现并运行 [第三轮反例](vdb_fix_20261021/reviewer-round3.py)，指定conda解释器、独立SQLite，输出 [第三轮结果](vdb_fix_20261021/reviewer-round3.log)，退出0表示探针正常完成，不是全部验收通过。

空库修复复核正确：真实 `MilvusVectorStore.hybrid_search` 在resolver确认无索引且无块时返回空；已有绑定但错实例时仍抛 `IndexUnavailableError`，不会被 `_EmptyIndexError` 分支吞掉。

**新增P2待修复：Lite准备阶段的import/cleanup异常可破坏启动隔离。** `prepare_lite_runtime` 在try外导入pymilvus，缺失SDK时原始ImportError逃出；finally中的disconnect若失败，覆盖已包装的RuntimeSelectionError，导致initialize_milvus_runtime未固定故障状态而向lifespan传播。独立反例确认：

```text
disconnect_error False => isolated startup_selection_unavailable binding None
disconnect_error True => ESCAPED RuntimeError binding None
missing SDK => ImportError
actual empty library => []
wrong instance => explicit unavailable
```

普通prepare失败不登记已经正确；建议将SDK导入和清理纳入一致的异常边界。清理失败不得覆盖初始化主异常，任何初始化/清理失败都不save运行登记，且由启动封装转为固定向量不可用，非检索API继续服务。主Agent已收到证据，待修复后追加复核结果与最终输入指纹。

### 第三轮修复后独立复核

主Agent将SDK导入及包含disconnect的初始化过程包在最外层异常边界内。独立重跑同一第三轮脚本，退出0，结果为：

```text
disconnect_error False => isolated startup_selection_unavailable binding None
disconnect_error True => isolated startup_selection_unavailable binding None
missing SDK => RuntimeSelectionError
lost persisted file => rejected before SDK, no replacement
actual empty library => []
wrong instance => explicit unavailable
```

因此SDK缺失、初始化失败以及cleanup失败均转为向量不可用，不再向应用启动传播未分类异常，不写运行登记。丢失的已登记Lite文件在SDK连接前拒绝，独立断言文件未被新建。嵌套异常保留异常链，外层错误类别固定；不会用cleanup异常把初始化失败误报成功。

同时独立重跑第一/二轮领域反例，错实例read/resolve_write/activate/adopt继续拒绝，状态与notes不变，SDK配置错误仍不兜底。再以指定conda解释器和独立库 `sqlite:///./data/vdb-reviewer-regression-round3.db` 执行：

```text
python -m pytest tests/test_vdb_acceptance_fixes.py tests/test_vdb_001_admission.py tests/test_vdb_002_runtime.py tests/test_vdb_003_combination.py tests/test_health.py -q --basetemp data/t/vdb_reviewer_round3
exit=0
63 passed in 28.65s
```

结果：[第三轮修复后反例](vdb_fix_20261021/reviewer-round3-green.log)、[前轮反例重验](vdb_fix_20261021/reviewer-probes-round3.log)、[独立63专项](vdb_fix_20261021/reviewer-tests-round3.log)。本轮11个输入文件指纹见 [第三轮输入](vdb_fix_20261021/reviewer-inputs-round3.json)，前两轮输入仅作历史记录。两个reviewer脚本Ruff通过。

**第三轮代码审查结论：新增P2已修复，本次复核范围无剩余P1/P2。** 空库分支、Lite真实准备前的失败隔离、丢文件拒绝及健康探针模式分支与修复计划一致。真实服务/Linux结果以对应验证Agent报告为准，本报告不冒领其运行证据；整卡关闭仍需全量、最终CI及独立人工审核。
