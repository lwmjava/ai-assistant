# 五项性能指标压测报告

- 开始：2026-11-12 01:54:24 (Asia/Shanghai)
- 结束：2026-11-12 01:54:26 (Asia/Shanghai)
- 时区：Asia/Shanghai
- 复现：`python scripts/run_perf_benchmark.py --output-dir docs/evaluations`

## 环境

| 项 | 值 |
|---|---|
| 主机 | DESKTOP-NSMV8MF |
| 操作系统 | Windows 11 |
| 处理器 | Intel64 Family 6 Model 158 Stepping 10, GenuineIntel |
| 物理核 / 逻辑核 | 6 / 12 |
| 内存 | 23.86 GiB |
| 应用进程数 | 1 |
| 是否清单机 4C8G | 否 |
| Python | 3.12.0 |
| 运行方式 | 进程内隔离，单应用进程；不对生产或共享在线栈施压 |

## /health 所见 Embedding 与依赖

| 项 | 值 |
|---|---|
| 服务状态 | ok（HTTP 200） |
| 环境 | development |
| Embedding provider | mock |
| Embedding 模型 | mock-embedding |
| Embedding 维度 | 64 |
| 是否 Mock | True |
| connectivity_verified | False |
| quality_evidence | False |
| 向量库 | local /  |
| LLM 模式 | mock |

检索延迟只有在 Embedding 非 Mock、且向量库为 Milvus 时，才能作为上线数字。

## 指标

| 指标 | 实测 | 目标 | 样本 | 数值达标 | 可作为上线证据 | 不能作为证据的原因 | 错误 |
|---|---|---|---|---|---|---|---|
| API 响应时间 (P95，不含 LLM) | 8.126 ms | <= 500 ms | 20 | 是 | 否 | 当前机器不是清单机 4C8G | — |
| 首 Token 延迟 (P95) | 105.9 ms | <= 2000 ms | 8 | 是 | 否 | 当前机器不是清单机 4C8G；LLM 模式为 mock，不得写成上线对话性能数字 | — |
| 知识库检索延迟 (P95) | 12.53 ms | <= 300 ms | 20 | 是 | 否 | 当前机器不是清单机 4C8G；Embedding 为 Mock，不得写成上线性能数字；向量库为 local，检索指标要求 Milvus | — |
| 并发对话数 | 50 conversations | >= 50 conversations | 50 | 是 | 否 | 当前机器不是清单机 4C8G；LLM 模式为 mock，不得写成上线对话性能数字 | — |
| 文档上传处理（10MB PDF 到已向量化） | 0.05371 s | <= 30 s | 1 | 是 | 否 | 当前机器不是清单机 4C8G；Embedding 为 Mock，不得写成上线性能数字 | — |

## 文档样本

文件体积 10485760 字节，可提取正文 206 字符。
文件体积为 10MiB；填充位于未引用对象，不进入文本层，不能代表全文密排 PDF 的向量化耗时。

## 处置结论

五项指标均已测得数值。 本跑不能作为上线验收证据。 实际机器为 6 核 / 23.86 GiB，不是清单机 4C8G。 检索与文档处理使用了 Mock Embedding，不得写成达标。 首 Token 与并发对话使用了非真实 LLM，不得写成上线数字。 当前向量库为 local，不是 Milvus。 要形成可关卡的上线证据，需要在清单机 4C8G、单应用进程、真实 Embedding、Milvus 以及真实 LLM 上重跑同一脚本，并且五项都达到目标。不得为达标改机器规格或压测场景。

## 约束

- 隔离本地基准，不打生产或共享在线栈。
- 付费模型默认 Mock；Mock 结果只证明链路，不写成达标。
- 不做多副本或跨实例压测，也不做极限容量规划。
