# 本机四项缺口补证

2026-10-22。本机具备 Docker Engine 28.5.1、镜像 `milvusdb/milvus:v2.5.11`，以及已安装 `pymilvus==2.5.11` 和 `milvus-lite==2.5.1` 的 Linux 运行容器。WSL 仍只有 `docker-desktop`，Lite 在该 Linux 容器内执行，不把 Windows 原生 Lite 写成通过。GitHub Ubuntu CI 不能在本机执行，测试修复随本次提交推送后由远端重跑。

## 1. 可选客户端导致的 CI 失败

`tests/test_vdb_acceptance_fixes.py` 不再在测试开始时导入真实 `pymilvus`。配置异常使用同名 `ConnectionConfigException` 和消息 `invalid connection configuration`。Lite 准备、健康探针和导入失败注入同名假模块。挡住真实包后该文件 28 条通过。文件 SHA256：`f7a587624377eec54cedcde1c5a72df325a207e527a5a245768fc12e91d060af`。

## 2. 本机全量 pytest

解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。`DATABASE_URL=sqlite:///./data/vdb-local-full-20261022.db`，`--basetemp data/t/vdb_local_full_20261022`，`PYTHONUTF8=1`。

结果：3273 passed，3 skipped，4 warnings，925.33 秒，退出码 0。日志 `docs/reviews/vdb_local_repair_20261022/pytest-full.log`。本机安装了 CI 未装的可选包，所以收集数量与上次 Ubuntu 的 3248 passed / 5 failed / 4 skipped 不同。上次 5 条失败就是本项测试里的真实 `pymilvus` 导入。

## 3. 真实 Standalone

容器 `vdb-acceptance-standalone-20261021` 只绑定 `127.0.0.1:29530`，镜像内健康检查为 OK，服务版本 2.5.11。当前 `app/` 与探针复制到运行容器 `/acceptance/repair-20261022`，下列 SHA256 与宿主一致：

| 文件 | SHA256 |
|---|---|
| `app/rag/vectorstore/runtime.py` | `177677f15b453ee259c3b3bd4f95bf7004ab1be080c5f95c8156bb53f5974623` |
| `app/api/routes/health.py` | `96943713133ca9949b09af6a8b185a5c5c86b88a29dc87ba0fb67fb7ef61b596` |
| `app/rag/index_identity.py` | `f874521de04c84ad91602f754150e450bc56b1b2c11b4c91d2bfee78b392838e` |
| `tests/test_vdb_real_modes.py` | `4ba502abfcb5984a7bdefbc6e896542499b365f8a3ff4ac0e7ca7f1ba34a4c79` |

`prepare` 退出码 0：实际启动模式、空库、首次摄取、租户隔离、健康探针。重启容器后第一次 `verify` 在服务刚恢复时超过检索 8 秒时限，退出码 1，见 `standalone-verify.log`。服务保持运行后的重试退出码 0，完成新进程持久化、重解析、软删、显式向量删除和其他租户保留。合成 Embedding 为 synthetic / synthetic-vdb-lifecycle-only / 8，不证明检索质量。没有清理既有 Milvus 卷。

## 4. 真实 Lite

同一 Linux 容器、同一份当前代码。`127.0.0.1:19530` 无监听时 `prepare` 因 `ConnectionRefusedError` 选择 Lite，退出码 0。随后把该回环端口转发到已恢复的 Standalone，确认 TCP 已连通，再跑新进程 `verify`，模式仍是 Lite，退出码 0。`missing-file` 退出码 0，拒绝创建空替身。日志在 `docs/reviews/vdb_local_repair_20261022/`。

## Ubuntu CI

提交 `7307402a9e5f471dccd2bd0ee3a5509d0a8a48c7` 的运行 [39634465447](https://github.com/lwmjava/ai-assistant/actions/runs/39634465447) 已完成且结论为成功：

- Backend：Ruff、mypy、pytest、RAG baseline gate 均为成功。作业 [119341108986](https://github.com/lwmjava/ai-assistant/actions/runs/39634465447/job/119341108986)。
- Frontend：类型检查、Vitest、ESLint、构建均为成功。
- Agent official baseline：成功。

三张卡的四项证据已经补上。状态仍保持 `in_review`，本次没有把它们改成完成。
