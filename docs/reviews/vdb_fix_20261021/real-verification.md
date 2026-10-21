# VDB 真实 Standalone / Lite 与 Linux 验证

日期：2026-10-21。验证角色：`/root/real_modes`，未修改业务源码。验证使用正式 `RAGService`、真实 Milvus 服务和 Lite 文件库，Embedding 为隔离合成实现，仅证明功能链路，不证明检索质量、性能或上线指标。不能代替同最终提交的 Ubuntu CI。

## 最终结果

| 运行 | 最终日志 | 退出码 | 结果 |
|---|---|---|---|
| Standalone prepare | real-bound-standalone-prepare.log | 0 | 实际启动模式、空库、首次摄取登记/远端写入/可检索、双租户隔离、真实健康探针 |
| Standalone 容器重启后 verify | real-bound-standalone-verify.log | 0 | 新Python进程读到SQL/向量，重解析旧向量ID消失/新正文命中，软删不可检索，显式向量删除清空目标文档，其他租户保留，真实健康探针 |
| Lite prepare | real-bound-lite-prepare.log | 0 | 实际本机连接拒绝触发Lite，缺失嵌套父目录可初始化，首次摄取/查询/租户隔离和真实健康探针 |
| Lite 独立进程 verify | real-bound-lite-verify.log | 0 | 上一进程已退出，重新打开文件并读取SQL/向量；重解析/软删/显式向量删除/其他租户保留/真实健康探针 |
| Lite 文件丢失 | real-bound-lite-missing-file.log | 0 | 将唯一已知测试文件重命名为保留文件，持久登记存在时拒绝创建空替身；finally恢复原文件，未删除向量数据 |
| Linux专项 | real-bound-linux-pytest.log | 0 | 63 passed，1条Starlette弃用警告，23.87秒 |

Standalone采用既有Bounded一致性。脚本最多40次、间隔0.25秒只读轮询等待写入可见，不改应用索引/检索配置；最终prepare第3次查询可见。这不是新增应用重试，也不是延迟达标证明。

删除语义分别验收：`RAGService.delete_document`是软删除，验证正式检索不可见；之后仅对该隔离文档调用 `MilvusVectorStore.delete_by_document` 验证远端清理，未把软删除描述成物理删除。

## 最终输入与环境

- HEAD：`c2fc1bf3e1f02327a6972539a0905371d5f4acef` 加未提交候选源码。最终容器输入位于 `/final-work`，没有读取宿主 `.env`、重要数据库或生产卷。
- 验收脚本：`tests/test_vdb_real_modes.py`，SHA256 `4ba502abfcb5984a7bdefbc6e896542499b365f8a3ff4ac0e7ca7f1ba34a4c79`。
- [实际输入指纹](real-bound-inputs.json)：259个源码/测试/配置/部署检查输入，已与宿主逐文件SHA256比较，`drift=[]`。任何相关源码变化后需重验，不能沿用本结果。
- Linux：`Linux-6.18.40.1-microsoft-standard-WSL2-x86_64-with-glibc2.41`；Python 3.11.16，符合项目Python范围，但不冒称Windows conda或Ubuntu CI环境。
- Docker Engine 28.5.1；服务镜像 `milvusdb/milvus:v2.5.11`，实际服务GetVersion为2.5.11。Standalone用内嵌etcd与本地容器存储，仅绑定宿主回环29530端口。
- SDK `pymilvus==2.5.11`；Lite `milvus-lite==2.5.1`；SQLModel 0.0.22、FastAPI 0.115.6、Pydantic 2.10.4。pytest8.3.4、pytest-asyncio0.25.0取自项目dev声明。完整实际依赖见 [清单](real-bound-linux-pip-freeze.txt)。
- 合成Embedding：provider `synthetic`、model `synthetic-vdb-lifecycle-only`、dim8。不发Embedding/LLM网络请求。

现有应用镜像缺SDK和测试包，只在本轮专用容器安装项目既有锁定SDK及其声明的Linux运行时依赖，未改项目依赖文件。下载并检查pymilvus2.5.11官方wheel元数据，Linux依赖为 `milvus-lite>=2.4.0`；固定在2.5.1实测，未升级SDK。初次默认appuser安装因目录权限失败，随后仅在专用容器以root安装成功，日志 `real-install.log` / `real-install-root.log`。最终依赖版本以清单为准。

参考官方材料：[Milvus 2.5 Lite](https://milvus.io/docs/v2.5.x/milvus_lite.md)、[2.5.11内嵌Standalone脚本](https://raw.githubusercontent.com/milvus-io/milvus/v2.5.11/scripts/standalone_embed.sh)。仅参考启动配置，不执行其中删除/升级命令。

## 可复现命令

专用runner：`vdb-acceptance-python-20261021`，现有镜像 `ai-assistant-app:latest`，通过明确复制 `app/`、测试、迁移和所需静态契约输入到 `/final-work` 执行，未挂载宿主代码或数据。

```text
docker exec -u 0 -e PYTHONPATH=/final-work -w /final-work vdb-acceptance-python-20261021 python tests/test_vdb_real_modes.py --mode standalone --uri http://host.docker.internal:29530 --scratch /acceptance/bound-standalone --phase prepare
docker restart vdb-acceptance-standalone-20261021
# 等待该专用服务 /healthz 为OK
docker exec -u 0 -e PYTHONPATH=/final-work -w /final-work vdb-acceptance-python-20261021 python tests/test_vdb_real_modes.py --mode standalone --uri http://host.docker.internal:29530 --scratch /acceptance/bound-standalone --phase verify
docker exec -u 0 -e PYTHONPATH=/final-work -w /final-work vdb-acceptance-python-20261021 python tests/test_vdb_real_modes.py --mode lite --uri http://127.0.0.1:59999 --scratch /acceptance/bound-lite --phase prepare
docker exec -u 0 -e PYTHONPATH=/final-work -w /final-work vdb-acceptance-python-20261021 python tests/test_vdb_real_modes.py --mode lite --uri http://127.0.0.1:59999 --scratch /acceptance/bound-lite --phase verify
docker exec -u 0 -e PYTHONPATH=/final-work -w /final-work vdb-acceptance-python-20261021 python tests/test_vdb_real_modes.py --mode lite --uri http://127.0.0.1:59999 --scratch /acceptance/bound-lite --phase missing-file
docker exec -u 0 -e PYTHONPATH=/final-work -e DATABASE_URL=sqlite:////acceptance/linux-bound-pytest.db -w /final-work vdb-acceptance-python-20261021 python -m pytest tests/test_vdb_acceptance_fixes.py tests/test_vdb_001_admission.py tests/test_vdb_002_runtime.py tests/test_vdb_003_combination.py tests/test_health.py -q --basetemp /acceptance/linux-bound-pytest-tmp
```

最终Standalone verify实际执行时额外通过 `python -c` 包装 `faulthandler.dump_traceback_later(45,repeat=True)` 后 `runpy.run_path(...,run_name='__main__')`，参数与上述相同；未触发45秒栈输出即通过。旧快照某次verify无输出等待约2分钟，明确终止该单独进程，退出143；该旧结果不计通过、不替代最终日志。

`prepare`应使用全新scratch，重复使用会与已创建租户/用户冲突；`verify`必须使用对应prepare目录，不清空或覆盖旧数据。59999在runner内无监听，使Lite测试通过真实连接失败触发兜底。

## 发现与修复验证

1. 最初正式空库search抛IndexUnavailableError，父Agent修正明确空库分支后重跑通过。
2. Lite默认路径父目录缺失时SDK确实拒绝，`real-lite-missing-parent.log`记录原始边界；父Agent增加初始化后最终缺目录路径通过。
3. Lite2.5.1不实现GetVersion，最初调用返回gRPC UNIMPLEMENTED；父Agent将Lite健康探针改为受支持的list_collections后最终实际探针通过。
4. Linux首轮1失败/60通过，唯一失败因容器副本遗漏 `.dockerignore`；补齐同仓库输入后61通过；最终异常边界修复再同步后63通过。保留历史日志，不以副本缺失归因业务回归。

## 保留资源与未完成项

本角色未删除容器/卷或重要数据。保留两个明确本轮创建的容器：`vdb-acceptance-standalone-20261021`（独立服务和合成集合）、`vdb-acceptance-python-20261021`（隔离源码、依赖和 `/acceptance` 数据）。未创建命名数据卷；服务数据在本轮容器可写层。runner继承镜像应用健康检查但只运行sleep，显示unhealthy不代表SDK测试失败。

本报告不关闭三卡：Windows全量回归、独立审查/人工验收和同最终提交Ubuntu CI由主Agent对账；本角色无Git提交/推送授权，未发布任何变更。Linux容器专项及真实双模式通过不等于Ubuntu最终CI通过。
