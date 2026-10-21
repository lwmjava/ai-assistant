# VDB-001、VDB-002、VDB-003 问题验收

2026-10-21。验收身份未参与这三张卡的实现和既有审查。本轮只读当前提交上的源码并实跑命令，不把四份旧报告里的“已通过”或“正在执行”当成当前结论。没有改业务源码或测试，没有提交、推送、合并、部署，没有付费模型，没有读写 `.env`、`data/ai_assistant.db` 或生产数据。

## 结论

**不允许关闭。** VDB-001、VDB-002、VDB-003 保持 `in_review`。

本轮重新执行的 10 条历史缺陷反例均为通过：启动失败不再挡住普通接口，健康检查标明向量不可用且不退回 local；已登记 Lite 在关闭兜底、远程地址、运行库缺失时拒绝；同模式不同实例拒绝读写；空目标激活、已有 active 的写解析、历史认领在错实例时拒绝且不改原状态；SDK 配置异常分类为 config 且不选 Lite；Lite 准备阶段的导入失败和 disconnect 失败被收成向量不可用且不写运行登记，丢失的已登记文件在连接前拒绝；resolver 确认的空库搜索返回空列表，错实例不被空库分支吞掉；`RAG_VECTOR_STORE=unsupported-synthetic` 的真实 `health_check` 返回 HTTP 503、`status=error`、`vector_store.backend=unknown`。

关闭仍被挡住：全量 pytest 日志没有汇总；同提交 Ubuntu Backend 的 pytest 失败，Frontend 还没有结论；旧真实 Standalone/Lite 报告的输入指纹与当前文件不一致。本机没有 `milvus_lite`，Windows 原生 Lite 不是通过。合成或注入只证明对应代码路径。

## HEAD、分支、解释器

| 项 | 实际值 |
|---|---|
| 分支 | `milvus-default-vector-store` |
| HEAD | `c8268368b5ecdda6351efaacae216f47f33b4dea`（与指定的 `c826836` 一致） |
| 远端 | `origin` = `https://github.com/lwmjava/ai-assistant.git`；`origin/milvus-default-vector-store` 与 HEAD 相同 |
| 解释器 | `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，Python 3.12.0 |
| 依赖 | pymilvus 2.5.11，pytest 8.4.2，Ruff 0.8.4，mypy 1.14.1 |
| 平台 | Windows 10.0.22621 |
| 本机 Lite | `milvus_lite` 模块不存在 |

隔离库：`sqlite:///./data/vdb-issue-acceptance-20261021.db`。专项临时目录：`data/t/vdb_issue_acceptance_20261021`。环境变量：`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`。工作目录为仓库根。

`data/ai_assistant.db` 的最后写入时间仍是 2026-10-20 11:19:05，本轮没有写它。收集版本时执行了 `pytest --version`，conftest 因此对指定测试库 `data/test_ai_assistant.db` 做了一次 Alembic 标记（`b5c8d3e0f271` 到 `c6d9e2f10a33`）。这不是业务验收断言，也没有改业务源码。

## 问题逐条对账

旧报告只作为要复核的问题清单。下表结论只来自本轮命令。证据目录：[vdb_issue_acceptance_20261021](vdb_issue_acceptance_20261021/)。

| # | 问题 | 结论 | 本轮观察 |
|---|---|---|---|
| 1 | Milvus 选择失败不得阻止全部 API 启动；普通非检索请求仍可服务；健康检查表明向量不可用；不得退回 local | 通过 | 真实 lifespan：`GET /` 为 200，`/api/v1/auth/me` 为 401，健康检查 HTTP 503，`status=error`，`backend=milvus`，`vector_status=error`，`runtime=unavailable`。进程没有锁定实例，运行登记为空，检索入口拒绝，探测只调用 1 次 |
| 2 | 已登记 Lite 不得绕过关闭兜底、显式远程地址、运行库缺失；限制变化应拒绝 | 通过 | 三路都抛出 `RuntimeSelectionError`，探测函数没有被调用，没有继续返回 lite |
| 3 | 同模式不同实例必须拒绝读写，不能把模式相同当成实例相同 | 通过 | standalone 与 lite 的读、`ensure_index` 写和 `validate_add` 都被 `IndexUnavailableError` 拒绝。已登记 Standalone 换端点也被拒绝 |
| 4 | 空目标 `activate_index(allow_empty=True)` 在状态变化前核对实例；失败时原 active 与 notes 不变 | 通过 | 激活被拒绝。原索引仍为 active，目标仍为 preparing，两边 notes 都不变 |
| 5 | `resolve_write_index` 已有 active 时跨实例拒绝 | 通过 | 抛出 `IndexUnavailableError`，没有返回旧索引 |
| 6 | `adopt_legacy_chunks` 跨实例拒绝且不改 notes | 通过 | 拒绝后 notes 仍是 `original-adopt-notes` |
| 7 | pymilvus `ConnectionConfigException` 分类为 config，不得因此选择 Lite | 通过 | 分类 `config`，选择结果为拒绝启动兜底，不是 lite |
| 8 | Lite 准备阶段 ImportError 与 disconnect 失败必须被启动隔离为向量不可用，失败不写运行登记；丢失的已登记文件在 SDK 连接前拒绝且不创建空替身 | 通过 | 三次 `initialize_milvus_runtime` 都停在 `startup_selection_unavailable`，异常没有逃出，登记为空。丢失文件在 `connect` 之前拒绝，文件没有被创建。disconnect 失败的真实 lifespan 仍返回根路径 200、健康检查 503，`backend` 不是 local。本机没有 Lite 运行库，这一条只证明隔离路径 |
| 9 | resolver 确认的空库搜索返回 `[]`；错实例不得被空库分支吞掉 | 通过 | 空库结果是 `[]`，且没有调用连接。错实例抛出 `IndexUnavailableError`，不是空列表 |
| 10 | `RAG_VECTOR_STORE=unsupported-synthetic` 时，health 不得返回 HTTP 200 且 `vector_store.backend=local`、`status=ok` | 通过 | 子进程在导入前设置该值，并调用真实 `health_check`。初始化前后都是 HTTP 503、`status=error`、`backend=unknown`、`vector_status=error`。初始化后 `runtime=unavailable`，没有运行登记，也没有锁定实例 |

问题 1–10 的探针命令：

```text
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe docs/reviews/vdb_issue_acceptance_20261021/probe.py
```

退出码 0。输出：[probe.log](vdb_issue_acceptance_20261021/probe.log)。问题 10 的子进程同样使用上述解释器和隔离库，输出嵌在该日志的 `ISSUE 10` 行。

## 三卡原始验收条对账

| 卡 | 原始验收条 | 结论 | 依据 |
|---|---|---|---|
| VDB-001 | 默认配置实际选择 Milvus；未知配置/连接故障不静默退回 local | 未验证 | 未知配置和连接故障不退 local 已由问题 1、10 通过。没有在本轮连接真实 Standalone 来证明默认配置实际选中服务，旧真实报告不能沿用 |
| VDB-001 | 真实 Standalone 下空库摄取、检索、删除、重解析、租户与索引身份校验通过 | 未验证 | `real-bound-inputs.json` 259 项里有 4 个当前文件哈希不一致。未新启 Docker 或真实 Milvus |
| VDB-001 | 普通非检索请求不因无关 Milvus 故障失败；检索故障明确 unavailable，不冒充 no_hit | 通过 | 问题 1 的 lifespan 与问题 9 的空库/错实例结果。专项里的对应测试包含在下面 65 条通过结果中 |
| VDB-001 | 当前混合候选窗口限制和回滚有记录，必要最终版本检查通过 | 失败 | 最终全量没有汇总；同提交 Backend pytest 失败。本轮没有把文档存在写成最终检查通过 |
| VDB-002 | 真实 Lite 摄取/检索/删除/重解析及重启持久化；服务可用时优先 Standalone | 未验证 | 本机 `milvus_lite` 不存在。旧 Linux 真实输入与当前 `runtime.py`、`health.py`、`index_identity.py` 不一致。未新启容器，也不把 Windows 原生 Lite 写成通过 |
| VDB-002 | 仅连接不可达/未就绪可启动兜底；配置/身份/Schema/凭据错误明确拒绝 | 通过 | 问题 2、7 与专项测试。这是注入分类和选择，不是对真实服务做凭据攻击 |
| VDB-002 | 进程内模式固定，跨重启不因 Docker 恢复静默换库；归属不一致拒绝而非空命中 | 未验证 | 归属不一致拒绝已由问题 3、5、6、9 通过。跨重启、Docker 恢复后的真实进程没有在本轮重跑 |
| VDB-002 | 生产与显式远程服务地址禁止本机 Lite 兜底；真实平台与 Linux 证据齐全 | 未验证 | 关闭兜底、远程地址、运行库缺失的拒绝已通过。Linux 证据和同提交 CI 不齐全 |
| VDB-003 | 默认/空库/双模式/归属/不可用/非检索隔离组合全部按锁定契约通过 | 未验证 | 合成组合专项在 65 条里通过。真实双模式输入指纹已漂移，不能把旧闭环算成当前全部通过 |
| VDB-003 | 同最终提交 Ubuntu 必要 CI 与适用 Linux 测试通过 | 失败 | 见下方 CI。Backend pytest 失败；Frontend 没有结论；旧 Linux 日志不能替代 |
| VDB-003 | 明确 Mock 仅证明链路，记录 SDK、服务版本、机器、命令、退出码和指纹 | 通过 | 本报告记录了这些项。合成 Embedding 与注入故障只证明链路 |

非目标没有在本轮实现或改写：没有迁移或清空现有测试库，没有引入 Chroma、重排或查询改写，没有把 Windows/WSL 写成原生 Lite 通过，没有双写、自动重建或静默切回 Standalone，没有发付费请求。

## 实际命令与退出码

解释器均为 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。pytest 均设置了上述 `DATABASE_URL`、`PYTHONUTF8` 和 `PYTHONIOENCODING`。

| 命令 | 退出码 | 结果 | 日志 |
|---|---|---|---|
| `python docs/reviews/vdb_issue_acceptance_20261021/probe.py` | 0 | 问题 1–10 均为通过 | [probe.log](vdb_issue_acceptance_20261021/probe.log) |
| `python -m pytest tests/test_vdb_acceptance_fixes.py tests/test_vdb_001_admission.py tests/test_vdb_002_runtime.py tests/test_vdb_003_combination.py tests/test_health.py -q --basetemp data/t/vdb_issue_acceptance_20261021` | 0 | 65 passed，14.05 秒 | [pytest-targeted.log](vdb_issue_acceptance_20261021/pytest-targeted.log) |
| `python -m pytest tests/test_openapi_coverage.py -q --basetemp data/t/vdb_issue_acceptance_openapi` | 0 | 4 passed，1.99 秒 | [pytest-openapi.log](vdb_issue_acceptance_20261021/pytest-openapi.log) |
| `python -m ruff check . --no-cache`（探针刚写入时） | 1 | 3 条都在新探针 `probe.py`：未使用导入、导入顺序、一行超长。不是既有业务诊断 | [ruff.log](vdb_issue_acceptance_20261021/ruff.log) |
| 只整理该探针的这 3 处样式后，再次 `python -m ruff check . --no-cache` | 0 | `All checks passed!` | [ruff-rerun.log](vdb_issue_acceptance_20261021/ruff-rerun.log) |
| `python -m mypy app/ --cache-dir data/t/vdb_issue_acceptance_mypy` | 0 | 223 个源文件无错误 | [mypy.log](vdb_issue_acceptance_20261021/mypy.log) |
| 指纹比对 `fingerprint.py` | 0 | 见未验证项 | [fingerprint.json](vdb_issue_acceptance_20261021/fingerprint.json) |

Swagger：`tests/test_openapi_coverage.py` 核对的是路由是否出现在 OpenAPI、README 路径和流式媒体类型，它不断言 `runtime` 与 `fallback_reason`。字段同步另用只读比较：当前进程 `app.openapi()` 与已提交的 `docs/swagger.json` 中 `VectorProbeStatus` 相等，两边都有 `status`、`backend`、`runtime`、`fallback_reason`。没有重新生成或覆盖 swagger。比较结果在 `probe.log` 的 `SWAGGER_COMPARE` 行。此项结论为通过。

Ruff 的有效结果是第二次退出码 0。第一次退出码 1 只由本轮新增证据脚本引起，整理样式后已重跑；没有为了退出码去改业务代码或放宽规则。

## 未验证项与原因

1. **全量 pytest：未验证。** `docs/reviews/vdb_fix_20261021/final-frozen-pytest.log` 存在，2511 字节，31 行，最后一行是 68% 的进度点，没有 `passed`/`failed` 汇总。文件时间是 2026-10-21 21:16:51。本轮进程列表里没有正在写这份日志的全量 pytest。按要求没有新启一次全量 pytest。另外，该日志对应的 740 项冻结指纹相对当前树有 4 个漂移：`app/api/routes/health.py`、`app/rag/index_identity.py`、`app/rag/vectorstore/runtime.py`、`tests/test_vdb_acceptance_fixes.py`。即使日志当时跑完，也不能绑定当前提交。

2. **同提交 Ubuntu CI：失败，不是缺少 URL。** 本机没有 `gh`，因此没有执行 `gh run list`。用 GitHub API 按同一 HEAD 查询，SHA 一致。记录见 [ci-api.txt](vdb_issue_acceptance_20261021/ci-api.txt)。
   - 运行：<https://github.com/lwmjava/ai-assistant/actions/runs/39619518045>，`completed` / `failure`。
   - Agent official baseline：成功，<https://github.com/lwmjava/ai-assistant/actions/runs/39619518045/job/119288902184>。
   - Backend：失败。Ruff 与 mypy 步骤成功，Pytest 步骤失败，注解为进程退出码 1。日志接口没有给出失败用例名称。<https://github.com/lwmjava/ai-assistant/actions/runs/39619518045/job/119288902523>。
   - Frontend：状态仍是 `queued`，结论为空，开始时间 2026-10-21T14:44:37Z。<https://github.com/lwmjava/ai-assistant/actions/runs/39619518045/job/119288902728>。这个 job 还没有可引用的通过或失败结论，不能算通过。
   - 本地旧 Linux 日志不能替代这次 CI。

3. **真实 Standalone / Lite：未验证。** `real-bound-inputs.json`（259）和 `real-final-inputs.json`（224）都与当前文件有漂移，至少包括 `app/api/routes/health.py`、`app/rag/index_identity.py`、`app/rag/vectorstore/runtime.py`。259 项清单还多了 `tests/test_vdb_acceptance_fixes.py`。没有新启 Docker 或真实 Milvus。合成探针只证明本轮注入的路径。

4. **Windows 原生 Lite：未通过，也未验证成支持。** `find_spec("milvus_lite")` 为假。问题 8 的通过只覆盖启动隔离和丢失文件拒绝。

5. **混合候选窗口与回滚记录：** 本轮没有重读手册来重新确认条文，也没有用最终全量和全绿 CI 关闭它。

## 是否允许关闭

不允许。问题 1–10 的本轮反例通过，专项 65 条、OpenAPI 契约 4 条、整理探针后的 Ruff 和 mypy 也通过，但原始交付里的真实 Standalone、真实 Lite、全量 pytest 和同提交 Ubuntu 必要 CI 至少有失败或未验证。三张卡的 `status` 保持 `in_review`，只更新了 `progress` 与 `blocked_by`，指向本文件。没有把状态写成 `done`，也没有提交 Git。
