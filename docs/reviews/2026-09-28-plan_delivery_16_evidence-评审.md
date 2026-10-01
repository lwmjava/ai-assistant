# plan_delivery_16_evidence.md 评审

> 状态：评审结论。未改被评审文档，未改任何业务代码。
> 日期：2026-09-28
> 对象：`docs/plans/plan_delivery_16_evidence.md`（EVD-001～EVD-004，前 16 项里未完成 6 项的证据补齐计划）
> 核对方式：文档中每条事实陈述均对照仓库实物逐项核实（引用文件存在性、`tasks.yaml` 依赖链、三份 RAG 计划第 8 节命令原文、`docker-compose.yml` 逐行、README 快速开始、`record_milvus_five_gates.md`、`.gitignore`、`pyproject.toml` / `requirements.txt`）。

## 1. 总体结论

**文档质量高，可以作为执行依据。** 四条任务的「问题—现状—方案—非目标—验收—允许改动」结构完整；密钥处理（进程环境变量、不落仓库与说明）、失败即停、不放宽验收、不把 skip / Mock 写成通过等约束写得明确且可判定；与 `AGENTS.md` 的证据规则、`docs/plans/` 落盘规则一致。

发现实质问题 1 个（P1，需裁决），小问题 3 个（P2/P3，文字级修订）。无阻断性缺陷。

## 2. 已核实一致的关键事实

| # | 陈述 | 核实结果 |
|---|---|---|
| 1 | 8 份被引用计划 / 记录文件 | 全部存在于 `docs/plans/` |
| 2 | EVD-001～004 顺序与依赖 | `tasks.yaml` 中 EVD-001 依赖 SEC-004，002→001、003→002、004→003 串行；FLOW-001 已依赖 EVD-004（传递覆盖四条） |
| 3 | EVD-001 三组命令 | 与 `plan_rag_011_control_plane.md`、`plan_rag_012_soft_delete.md`、`plan_rag_013_version_states.md` 第 8 节**逐行一致** |
| 4 | 「RAG-012 与 RAG-013 命令文本相同」 | 属实（第 8 节四行命令完全相同），「跑一次、两份贴同一输出」的省略成立 |
| 5 | EVD-002 compose 事实 | `JWT_SECRET_KEY` / `LLM_API_KEY` 用 `:?` 语法在解析期强校验；`RAG_VECTOR_STORE: local`、`MILVUS_URI: http://milvus:19530`、`SERVE_FRONTEND: "true"`、db/milvus/app 三服务，全部与验收字段吻合（`docker-compose.yml:50-54`） |
| 6 | 占位口令 | `change-me-in-production-use-a-random-secret` 与 `app/core/config.py:50` 一致 |
| 7 | EVD-003 README 五步 | 快速开始确为五步（填写环境 / 启动 / 建立管理员 / 注册或登录 / 发送第一条消息），名称吻合 |
| 8 | EVD-004 Milvus 事实 | milvus 服务确无 `ports`、无 `depends_on`（单独 `up -d milvus` 可行）、有 healthcheck（9091 /healthz）；软删除不调 `delete_by_document`、摄取不调 `MilvusVectorStore.add` 与 `record_milvus_five_gates.md` 代码事实一致 |
| 9 | pymilvus 为可选依赖 | `pyproject.toml` extras `milvus = ["pymilvus==2.4.7"]`；`requirements.txt:34` 为注释 |
| 10 | 卷与文件隔离 | `data/` 已被 `.gitignore` 忽略（核对库、备份不入库）；`.gitignore` 目前无 `gate.local.yml` 条目，与「允许改动 .gitignore」对应；`-p aigates` 独立项目名隔离卷的描述正确 |
| 11 | 来源文件名 | `plan_delivery_2027-03-25.md` 不是笔误：该文件头部为交付排期 2026-09-25 → 2027-03-25，以死线命名，第 6 节清点口径与 `record_delivery_16.md` 一致 |

## 3. 发现的问题

### P1（技术风险，建议裁决后再执行 EVD-004）— 客户端/服务端版本不匹配

文档锁定 `pymilvus==2.4.7`，但 compose 镜像是 `milvusdb/milvus:v2.5.11`（`docker-compose.yml:25`）。pymilvus 小版本惯例上要与服务端小版本对齐，2.4 客户端连 2.5 服务端可能握手失败或行为异常。

后果：五条门槛可能因**兼容性**而非代码原因失败，形成假阴性，卡住第 13 行。

候选处理（二选一）：

1. 在 EVD-004 风险节写明此风险，失败时按已有规则记 `blocked` 并写明原因是版本不匹配（不动任何 image，最保守）。
2. 允许本地 override（`docker-compose.gate.local.yml`）同时把 image 改为 `milvusdb/milvus:v2.4.x` 与客户端对齐——效果更接近「客户端/服务端同版本」的门槛语义，但这是在被禁改的已提交编排之外松了一个口子，且门槛结果对应的环境与已提交编排不一致。

### P2 — `scripts/milvus_five_gates.py` 尚不存在，但方案未明写「新建」

该文件现不在 `scripts/` 中。EVD-004 的允许改动列了它（隐含新建），第 116 行也说「脚本把每条收成可重复命令」，但方案第 1–5 步从未明写「新建该脚本」；`record_milvus_five_gates.md` 描述的是内联异步脚本，未提此路径。

建议：在 EVD-004 方案里加一句「将五条固化为 `scripts/milvus_five_gates.py`（新建）」，避免执行者误以为脚本已存在。

### P2 — EVD-003 缺一个执行细节

方案说了用 `data/readme_walk.db`，但没写怎么让 uvicorn 进程指向它（即启动前设 `DATABASE_URL=sqlite:///./data/readme_walk.db`）。README 第 2 步是裸启动命令，执行者照抄会连到现有开发库——正是该条最想避免的事故。

建议：在 EVD-003 方案里补一句启动前设置 `DATABASE_URL`，并写明该变量只在当次进程生效。

### P3（两处小点）

1. EVD-001～004 的允许改动都含 `AGENTS.md`，但四条任务均看不出需要动它的场景。建议要么写明用途，要么删掉。
2. EVD-004 第 4 条的备份是在 Milvus 运行中拷贝卷，非一致性备份。文档已自我声明「这不是生产备份演练」，可接受；若追求更干净可在 `down` 之后拷贝，但与第 5 步顺序冲突，二选一即可。
3. 「FLOW-001 改为依赖这四条」实际是直接依赖 EVD-004、经传递覆盖四条。表述无错，可写得更准。

## 4. 结论与下一步

- P1 需用户在两个候选间裁决后，才建议开始执行 EVD-004；EVD-001～003 不受影响。
- P2 两处为文字级修订，P3 为可选润色，确认后可直接补进 `plan_delivery_16_evidence.md`。
- 修订不涉及业务代码，不改 `tasks.yaml` 状态。
