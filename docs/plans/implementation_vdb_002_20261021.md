# VDB-002 实现说明

日期：2026-10-21。任务保持 `in_progress`，不能关闭。

## 已完成的行为

开发环境在 Milvus 模式下于启动时选择一次实例：

- 本机 Standalone 就绪时使用它。
- 只有连接拒绝或超时才会改用 Lite 文件，并把模式和原因写入 `rag_vector_runtime`。
- 生产环境、远程地址、鉴权失败、Schema 错误和未知配置错误都不会进入 Lite。
- 已经记下 Lite 之后，再次启动不再探测，也不会因为 Standalone 恢复而自动切回。
- 同一进程里的第二次选择直接返回第一次的结果。
- 新建的 Milvus 索引记下所属实例。旧索引没有记录时视为 Standalone。当前进程模式不一致时，检索入口拒绝，而不是返回空结果。
- 健康检查和管理状态增加实际模式与兜底原因，不写出口令。

## 代码

- `app/rag/vectorstore/runtime.py`
- `app/models/rag.py`
- `alembic/versions/c6d9e2f10a33_add_vector_runtime_binding.py`
- `app/main.py`
- `app/rag/index_registry.py`
- `app/api/routes/health.py`
- `app/api/routes/admin_system.py`
- `tests/test_vdb_002_runtime.py`

## 验证

解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。隔离库：`sqlite:///./data/vdb002-runtime.db`。

| 命令 | 结果 |
|---|---|
| `python -c` 检查 `pymilvus` 2.5.11 与 `milvus_lite` | 模块不存在，平台 win32 |
| `pytest tests/test_vdb_002_runtime.py tests/test_health.py --basetemp data/t/vdb002a` | 17 passed，退出码 0 |
| `ruff check` 本卡改动的 Python 文件 | All checks passed |
| `mypy` 本卡改动模块 | 见同一次命令输出 |

这些测试注入探测结果，证明选择规则。它们不是真实 Lite 服务。

## 没做

- 没有真实 Lite 上的摄取、检索、删除、重解析和重启持久化。当前 Windows 环境没有 Lite 运行库，按决定不安装新版本，也不退回本地向量库。
- 没有 Linux 或 WSL 证据，没有全量 pytest，没有最终 CI。
- VDB-001 仍未关闭。本说明不把前置缺口写成已闭合。
- 没有独立审查，没有人工审核，没有提交。
