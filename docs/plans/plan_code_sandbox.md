# 计划：代码沙箱四层防护（骨架阶段）

## 目标
在 ai-assistant 的「行动」阶段新增 `code_sandbox` 工具，提供四层安全防护的 Python 代码执行环境，使 Agent 能够安全执行用户/模型生成的代码。

## 分支策略
- 基于 `agent-supervisor` 创建扁平分支 **`feat/code-sandbox`**
- 遵循 Conventional Commits：`feat(sandbox): 新增代码沙箱四层防护骨架`

## 配置项（app/core/config.py 新增）
- `SANDBOX_ENABLED: bool = True` —— 是否启用代码沙箱工具（关闭后 code_sandbox 不注册）
- `SANDBOX_TIMEOUT_SECONDS: float = 30.0` —— 单次执行最大秒数
- `SANDBOX_MAX_MEMORY_MB: int = 256` —— 最大内存（MB）
- `SANDBOX_MAX_CPU_SECONDS: int = 10` —— 最大 CPU 时间（秒）
- `SANDBOX_MAX_OUTPUT_CHARS: int = 100_000` —— stdout+stderr 最大字符数
- `SANDBOX_MAX_DISK_MB: int = 50` —— 最大写入磁盘量（MB）

## 1. 四层防护架构

### Layer 1：AST 白名单（编译期）
- **文件**：`app/agents/tools/sandbox/sandbox.py` `_check_ast()`
- **机制**：解析用户代码为 AST，遍历所有节点检查是否在白名单中
- **允许**：表达式、控制流、函数/类定义、数据结构、白名单模块导入（math/json/datetime/re/statistics 等）
- **禁止**：`Global`、`Nonlocal`、`Delete` 节点；`os`/`subprocess`/`socket`/`ctypes` 等危险模块
- **失败处理**：抛出 `SecurityError(reason=AST_REJECTED)`，外部捕获后转为 `SandboxResult(killed_by=ast_rejected)`

### Layer 2：进程隔离（运行时）
- **文件**：`app/agents/tools/sandbox/sandbox.py` `_run_subprocess()`
- **机制**：通过 `subprocess` 在独立进程中执行，模板脚本替换 `__builtins__` 为受限版本
- **受限内置函数**：仅允许 `print`/`len`/`range`/`zip`/`isinstance` 等安全函数
- **受限 import**：通过 `__SandboxImportWrapper__` 替换 `__import__`，仅允许白名单模块
- **SKELETON**：未限制文件系统访问（chroot）、未设置 nobody 用户、未网络隔离

### Layer 3：资源限制（运行时）
- **文件**：`app/agents/tools/sandbox/sandbox.py` `_build_limit_wrapper()`
- **机制**：在子进程入口通过 `resource.setrlimit()` 设置资源上限
- **CPU**：`RLIMIT_CPU` 限制 CPU 秒数，死循环会被 SIGXCPU 终止
- **内存**：`RLIMIT_AS` 限制虚拟内存，超限触发 MemoryError
- **磁盘**：`RLIMIT_FSIZE` 限制写入量，超限触发 IOError
- **子进程**：`RLIMIT_NPROC = 0` 禁止创建子进程
- **输出截断**：`max_output_chars` 超限则截断并标记 `truncated=True`
- **SKELETON**：Windows 上 `resource` 模块不可用，需通过 Job Object 实现；输出截断为后截断非流式

### Layer 4：超时 Kill（运行时）
- **文件**：`app/agents/tools/sandbox/sandbox.py` `_run_with_limits()`
- **机制**：`subprocess.run(timeout=...)` 超时抛出 `TimeoutExpired`
- **SIGKILL 检测**：`exit_code=-9` 或 `137` 标记为 `killed_by=timeout`
- **SIGABRT 检测**：`exit_code=-6` 或 `134` 标记为 `killed_by=resource_memory`
- **SKELETON**：未递归 kill 进程树（仅 kill 直接子进程）

## 2. 数据类型设计

### SandboxConfig
执行配置，全字段有默认值，调用方按需覆盖：
- `timeout_seconds`、`max_memory_mb`、`max_cpu_seconds`、`max_output_chars`、`max_disk_mb`
- `allowed_imports`：白名单模块列表
- `work_dir`、`env_vars`

### SandboxResult
统一执行结果，无论成功/失败/被 kill 均返回此结构：
- `stdout`、`stderr`、`exit_code`、`duration_ms`
- `truncated`：输出是否被截断
- `killed_by: KillReason`：被哪层防护拦截
- `to_observation()`：生成供 Agent 阅读的观测文本
- `to_dict()`：序列化供日志/审计

### KillReason 枚举
| 值 | 含义 |
|----|------|
| `none` | 未被拦截，正常完成 |
| `ast_rejected` | Layer 1：AST 白名单拒绝 |
| `resource_memory` | Layer 3：内存超限 |
| `resource_cpu` | Layer 3：CPU 时间超限 |
| `resource_output` | Layer 3：输出字符数超限 |
| `timeout` | Layer 4：执行超时 |

## 3. 工具注册

在 `app/agents/tools/builtin.py` 中新增 `code_sandbox` 工具：
- **名称**：`code_sandbox`
- **参数**：`code`（必填，Python 代码字符串）、`timeout`（选填，超时秒数）
- **执行函数**：`code_sandbox()` → 调用 `CodeSandbox.execute()` → `result.to_observation()`

## 4. 关键设计决策

| 决策 | 理由 |
|------|------|
| **Layer 1 + Layer 2 双重防护** | AST 编译期拦截（快速失败）+ 运行时 `__builtins__` 替换兜底，双重保障 |
| **SandboxResult 而非异常** | Agent 管线不会因沙箱异常而中断，`to_observation()` 生成模型可读文本 |
| **KillReason 枚举** | 精确标记被哪层拦截，审计日志可分类统计安全事件 |
| **全默认值配置** | `sandbox.execute(code)` 即可使用，无需了解底层参数 |
| **平台兼容** | Layer 1/2/4 跨平台；Layer 3 Windows 静默跳过（不阻塞），标记 SKELETON |
| **全局单例** | `get_sandbox()` 返回进程级单例，每次 `execute` 创建独立子进程，无共享状态 |

## 5. 文件清单

| 文件 | 操作 | 内容 |
|------|:--:|------|
| `app/agents/tools/sandbox/__init__.py` | 新增 | 公开 API 导出 |
| `app/agents/tools/sandbox/base.py` | 新增 | 数据类型（SandboxConfig/SandboxResult/KillReason/SecurityError） |
| `app/agents/tools/sandbox/sandbox.py` | 新增 | 四层实现（CodeSandbox） |
| `app/agents/tools/builtin.py` | 修改 | 注册 code_sandbox 工具 |
| `app/core/config.py` | 修改 | 新增 SANDBOX_* 配置项 |

## 6. 验证结果

```
Test 1: 安全代码 print(1+1)     → exit_code=0  stdout="2"           ✅
Test 2: 禁止 import os          → killed=ast_rejected              ✅
Test 3: 复杂循环 for/range      → exit_code=0  stdout="0 1 4 9 16" ✅
Tool 注册: code_sandbox 已加入   → ['calculator','code_sandbox',...] ✅
```

## 7. 骨架标记（SKELETON）— 内核打磨阶段待补充

| 标记 | 内容 | 优先级 |
|------|------|:--:|
| 进程隔离加固 | 文件系统访问限制（chroot/work_dir）、nobody 用户运行、网络隔离 | P1 |
| Windows 资源限制 | Job Object 实现 CPU/内存/磁盘限制 | P1 |
| 进程树强杀 | 递归 kill 所有子进程/孙进程，确保不残留 | P1 |
| 流式输出截断 | 子进程内流式读取并实时截断，非全量捕获后截断 | P2 |
| 禁止模块列表 | 参考 Python 3.11 标准库做全量审计补充 | P2 |
| 异步子进程 | `subprocess.run` 改为 `asyncio.create_subprocess_exec` 避免阻塞事件循环 | P2 |
| 安全审计日志 | 每次沙箱执行写入审计日志（代码摘要、结果、被哪层拦截） | P1 |
| 沙箱预热池 | 预创建子进程池减少冷启动延迟 | P3 |

## 8. 风险与注意

- **Windows 兼容性**：Layer 3 `resource.setrlimit()` 在 Windows 上不可用，当前静默跳过。生产环境建议 Linux 部署以启用完整四层防护。
- **subprocess.run 阻塞**：当前骨架使用同步 `subprocess.run`，在 asyncio 事件循环中会阻塞。内核打磨时改为 `asyncio.create_subprocess_exec`。
- **AST 节点白名单**：当前白名单覆盖 Python 3.11 核心语法，后续 Python 版本新增语法节点需同步更新。
- **安全边界**：沙箱仅防护代码执行，不防护 LLM 输出内容本身（如 Prompt Injection 诱导生成恶意代码）。内容安全由 `app/security/` 模块负责。

## 9. 后续演进

- **Phase 1 内核打磨**：补齐 SKELETON 标记的 P1 项（进程隔离加固、Windows 支持、进程树强杀）
- **Phase 2 RAG 集成**：沙箱内可访问知识库检索结果（通过安全的数据注入通道）
- **Phase 5 管理后台**：沙箱执行统计仪表板（执行次数、拦截率、平均耗时）