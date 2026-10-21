# VDB-003 组合验收说明

日期：2026-10-21。状态：`in_progress`，未关闭。

使用技能：`docs/ai-prompts/skills/task-card-delivery/SKILL.md`（v1.3.0）。本轮没有可用的独立审查 Agent 和人工审核 Agent，串行自审不计入独立审查。没有改 `app/`。

## 实际完成了什么

组合测试 `tests/test_vdb_003_combination.py` 走现有入口：

- 配置字段默认是 `milvus`；未知后端抛错，不退回本地库。
- 空库在读索引入口返回空，不报不可用。
- 启动探测不可达时登记 Lite；清掉进程锁后再探测就绪，仍保持 Lite。
- 生产环境超时不选 Lite；失败后进程没有锁定，登记行仍为空，配置仍是 `milvus`。
- 历史索引按 Standalone 归属，当前进程是 Lite 时拒绝读取。
- 零向量、NaN、无穷大不能当作探活成功。
- 构造对话检索钩子时不调用向量库；检索构造失败是 `unavailable`，不是 `no_hit`。
- 生产编排静态检查要求 Milvus。

启停、故障和回滚说明写在 `docs/workflows/production-compose.md` 的「两种 Milvus 模式的启停和回滚」。

## 契约对账

| 原始要求 | 证据 | 结果 |
|---|---|---|
| 默认 / 空库 / 双模式 / 归属 / 不可用 / 非检索隔离 | E2：隔离 SQLite 加注入探测，真实检索钩子 | 10 条组合测试通过。注入探测不是真实服务 |
| 真实 Standalone 与 Lite 矩阵 | 本机 `127.0.0.1:19530` 连接超时；`milvus_lite` 为 None | 未验证 |
| 启动 / 停止 / 故障 / 回滚手册和实验报告 | 手册已写入工作流文档；本文件是实验记录 | 手册是操作说明。真实容器启停未执行 |
| Ubuntu / Linux 与最终提交 CI | 未运行 | 未验证 |
| Mock 只证明链路，并记录环境 | pymilvus 2.5.11；平台 Windows-11-10.0.22621-SP0；HEAD `c2fc1bf` 加未提交的 VDB-002 工作区 | 已记录。不能写成检索质量或性能 |

## 失败边界

| 边界 | 反例 | 结果 |
|---|---|---|
| 数值 | 零向量、NaN、无穷大不作为探活成功 | 通过 |
| 失败路径 | 超时分类为 unready；生产环境启动失败不改后端、不写登记 | 通过 |
| 权限与归属 | 索引属于 Standalone、进程固定为 Lite 时拒绝 | 通过 |
| 身份与绑定 | 重启后的新进程仍读到已登记的 Lite | 通过 |
| 回滚 | 选择失败后没有进程锁，也没有把配置改成本地库 | 通过 |

这些反例使用注入探测和隔离库，级别是 E2。它们不代替连上真实 Milvus 的摄取、检索、删除和重解析。

## 验证

解释器：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。隔离库：`sqlite:///./data/vdb003-combination.db`。pytest 临时目录：`data/t/vdb003b`。

```text
ruff check tests/test_vdb_003_combination.py
退出码 0

pytest tests/test_vdb_003_combination.py -q --basetemp data/t/vdb003b
退出码 0，10 passed
```

测试文件 SHA256：`976094cd7dda1ead801e30c35ebeaa9fc2e177b4c803ead1f6556ad95e9b2dc4`。

全量 pytest、ruff 全库、mypy、前端和 Ubuntu CI 没有对当前工作区重跑。VDB-001 记录过的 79 个既有失败没有在本卡隔离。

## 没做的事

- 没有启动 Docker，没有跑 `scripts/milvus_five_gates.py`，没有 `--apply` 切换。
- 没有真实 Lite 写入、检索、删除、重解析或重启后仍能读到数据。
- 没有把 VDB-001、VDB-002 或本卡标成完成。Milvus 仍是 Partial。
- 独立审查和人工审核都没有做。
- 没有提交，没有推送。

## 等价计划审查

没有单独的计划审查技能。对照原始契约看过范围：本卡只改测试、验收说明和允许的文档；前两卡未冻结，所以不能关闭；注入探测不能冒充 E1。
