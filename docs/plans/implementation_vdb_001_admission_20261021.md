# VDB-001 准入修复与默认配置说明

日期：2026-10-21。任务仍是 `in_progress`，不能按完成关闭。

## 已完成

用户批准三个文件进入本卡后，正式回归覆盖了原先的领域反例，并修了对应入口：

- Milvus 候选回查同时限制分块租户和文档租户。远端返回的外租户标识或文档/分块租户不一致时，不返回正文。
- 激活前要求即将退役的生效索引上，每篇当前文档在新索引都有不少于原来的分块。不完整时不改登记计数，旧索引保持生效。
- 激活抽样使用调用方会话的数据库引擎。Milvus 目标会连接目标集合并检索样本；连接失败会撤销切换。零向量和非有限数值不能算作检索成功。
- 评测脚本和真实嵌入入口在响应模型与请求模型显式不符时拒绝。响应缺省模型只记为未核验，不当成已验证身份。嵌入入口同时拒绝非有限数值。
- 向量库只接受 `local` 和 `milvus`。默认配置、开发编排、生产编排和镜像安装改为 Milvus。隔离测试夹具仍显式使用 `local`，避免用夹具冒充默认验收。

## 代码位置

- `app/rag/vectorstore/milvus.py`
- `app/rag/index_registry.py`
- `app/rag/vectorstore/factory.py`
- `app/rag/embeddings/openai_compatible.py`
- `scripts/rag_hybrid_embedding_check.py`
- `scripts/check_production_compose.py`
- `app/core/config.py`
- `docker-compose.yml`
- `docker-compose.prod.yml`
- `Dockerfile`
- `tests/test_vdb_001_admission.py`
- `tests/conftest.py`

## 验证

解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。隔离库：`sqlite:///./data/vdb001-admission.db`。

| 命令 | 结果 |
|---|---|
| `pytest tests/test_vdb_001_admission.py --basetemp data/t/vdb001adm01` | 10 passed，退出码 0 |
| `pytest tests/test_rag_032_index_identity.py tests/test_embeddings.py` | 72 passed；另 8 条当时因共享临时目录权限未启动，随后用独立临时目录重跑准入文件通过 |
| `pytest tests/test_rag_032_remote_rebuild.py tests/test_rag_032_rebuild_cli.py --basetemp data/t/vdb001reb01` | 修复 CLI 覆盖夹具后，最终相关用例通过 |
| `pytest tests/test_vectorstore_policy.py tests/test_vdb_001_admission.py tests/test_rag_032_rebuild_cli.py::test_activate_refuses_index_whose_chunks_are_unreachable_via_cli --basetemp data/t/vdb001def01` | 14 passed，退出码 0 |
| `ruff check` 上述改动文件 | All checks passed |
| `mypy app/rag/index_registry.py app/rag/vectorstore/milvus.py app/rag/embeddings/openai_compatible.py` | 退出码 0 |
| `python scripts/check_production_compose.py` | 退出码 0，只证明静态契约 |

## 没做

- 没有启动真实 Milvus，没有做空库摄取、检索、删除、重解析和双租户闭环。
- 没有重跑不带筛选的全量 pytest，没有在 Linux 或最终 Ubuntu CI 上验证。
- 没有独立入口审查，没有人工审核，没有把任务写成 done。
- 没有实现 Lite，没有清库，没有付费模型调用，没有提交 Git。
