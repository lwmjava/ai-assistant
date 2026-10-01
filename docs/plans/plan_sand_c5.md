# SAND-001～SAND-002：沙箱隔离、进程树结束、代码结果展示

> 状态：SAND-001、SAND-002 已实现。
> 来源：`docs/plans/plan_remaining_delivery.md` 的 SAND-001、SAND-002；`tasks.yaml`
> 日期：2026-09-27
> 截止：2026-12-22
> 依赖：TEN-003 已完成。SAND-002 依赖 SAND-001。

本文件写完并完成三轮自查。SAND-001 与 SAND-002 已在允许路径内实现。

## 目标

1. 一次代码执行使用单独的临时目录。`open()` 和 `os.open` 这两种读法读不到这个目录之外的指定文件。现有的内存、CPU、磁盘和输出长度限制保持 `SandboxConfig` 的默认数值。超时或执行失败时，这次启动的进程树结束，不留子进程。
2. 对话里能单独看到这次执行的标准输出。失败或超时时能看到原因。结果里不出现宿主机路径。

## 现状

`CodeSandbox.execute()` 先做 AST 白名单，再在临时目录里写脚本，用 `subprocess.run` 执行。

- 直接调用 `open()` 时，脚本模板会从内置函数里删掉 `open`，所以这条路径现在读不到文件。
- Unix 上的资源限制包装脚本先 `import os`，再 `exec` 用户脚本。用户代码和包装脚本共用同一份全局名字，因此可以不写 `import os` 就调用 `os.open` 读沙箱外的文件。这一条在当前 Windows 开发机上不会出现，因为 Windows 不走这段包装脚本。
- 子进程环境是父进程环境的整份拷贝，其中可以有密钥和 `PYTHONPATH`。
- `SandboxConfig` 里的内存、CPU、磁盘和输出上限已经有默认值。Unix 用 `resource.setrlimit`。Windows 不设置这些限制。输出是进程结束后再按 `max_output_chars` 截断。
- 超时由 `subprocess.run(timeout=...)` 结束直接子进程。孙进程不在这个结束范围内。注释里写了进程组和 Job Object，代码没有做。
- `to_observation()` 在截断时写出字面量 `{max_output_chars}`，没有代入实际数字。标准错误里的回溯会带上临时脚本的绝对路径。
- 工具把观测文本交给模型。流式事件 `tool` 的内容是「调用工具：代码工具名」。消息上只有正文、来源和状态。刷新之后，界面看不到单独的标准输出、错误或超时原因。
- 意图分流判定为简单问题时，管线不进入工具循环，代码工具不会执行。这是现有行为。

本机是 Windows。仓库里没有持续集成配置。文件隔离和进程树结束都要在 Windows 上有可运行的测试；Unix 的资源限制和进程组结束也要写进同一套代码，不能只在 Windows 上改完就算完成。

## 和产品方案的差异

产品方案 §5.8.2 还要求命名空间隔离、以非特权用户运行，以及 Windows 上用 Job Object 做内存限制。

`tasks.yaml` 把本批收成：执行目录与宿主机工作区隔离，保留现有资源限制，超时或失败时进程树结束；界面展示标准输出和失败原因，不展示宿主机路径。非目标写明不引入新的远程沙箱，不开放网络访问，不做独立代码运行页。

本批按任务契约做。chroot、nobody、seccomp、Windows 内存与 CPU 限额、流式截断、审计日志和预热进程池留在骨架注释里，不在本批实现。

## 已定的取舍

每次执行继续使用新建的临时目录，执行完删除。`SandboxConfig.work_dir` 仍保留，不把它当成所有人共享的工作区，也不在 Windows 上去创建 `/tmp/sandbox`。进程已经结束、结果也已经拿到之后，若临时目录仍被占用而删不掉，留下这次的标准输出或失败原因。清理异常不盖住这次结果。

用户脚本仍由解释器直接执行。Unix 上先起一个极短的包装进程：设好现有资源限制后，用 `execv` 换成用户脚本。服务进程是多线程的，不用 `preexec_fn`，避免子进程启动回调在多线程里死锁。`execv` 替换之后，包装进程里的 `os` 不会留在用户代码的全局名字里。

子进程只保留启动解释器必需的系统变量。密钥、`PYTHONPATH`、`PYTHONHOME` 和用户目录变量不传进去。Windows 保留 `SYSTEMROOT`、`WINDIR`、`PATH`、`PATHEXT`、`COMSPEC`、`TEMP`、`TMP`。Unix 保留 `PATH`、`LANG`、`LC_ALL`、`TMPDIR`。`SandboxConfig.env_vars` 里不在这份名单上的键丢掉，不在名单之后再整份合并。实现时先用 `print(1)` 确认解释器能起来。进程没启动，不能算成隔离成功。

真正生效的限额是 `SandboxConfig` 的默认值：内存 256MB、CPU 10 秒、磁盘 50MB、输出 100000 字符，超时默认 30 秒。模型传入的 `timeout` 不是正数时，按 30 秒，避免 `0` 让每次执行立刻超时。`app/core/config.py` 里的 `SANDBOX_*` 目前没有接到 `execute()` 上。本批保持默认值，不去改配置接线。Unix 继续用 `setrlimit` 限制 CPU、地址空间、文件大小和子进程数，其中包括现有的 `RLIMIT_NPROC=0`。因此用户代码在 Unix 上不能再拉起孙进程。若这项上限让 Unix 上的解释器起不来、`print(1)` 失败，停下来报告，不把上限改大来换测试通过。Windows 本批仍不设置内存、CPU 和磁盘上限。输出仍在进程结束后截断。

进程树结束：

- Unix：包装进程 `start_new_session=True`。超时、失败或提前返回时，对进程组发 `SIGKILL`，再等待结束。
- Windows：用标准库 `ctypes` 创建 Job Object，设置关闭 Job 时结束其中的进程。用 `CreateProcess` 以挂起方式创建子进程，放进 Job 之后再 `ResumeThread`。不用 `subprocess.Popen` 的挂起标志：它返回前会关掉线程句柄，进程无法恢复，会一直挂到超时。超时、失败和正常返回的收尾都关闭 Job 句柄。不新增第三方包。
- Job 创建或绑定失败时，不启动用户代码，返回失败结果「无法建立进程隔离」。失败时不退回无隔离执行。绑定失败则不恢复线程。
- 进程树测试调用结束函数，由测试自己拉起一个睡眠中的孙进程。不把「用户代码里再起子进程」写成用例，避免为了让用例通过而去掉 `RLIMIT_NPROC`。

同步 `subprocess.run` 会占住事件循环，也杀不掉孙进程。Unix 在线程里用 `Popen` 等待，超时后结束进程组。Windows 在线程里用 `CreateProcess` 挂起创建、绑定 Job、再恢复，超时后结束 Job。这是结束进程树所需要的改法。

结果离开沙箱前，标准输出、标准错误和拦截说明都去掉绝对路径，换成 `<sandbox>`。要覆盖的写法是 `C:\...`、`C:/...`、`\\server\...`、`\\?\...`，以及以 `/` 开头的绝对路径。普通斜杠、`1/2` 和 `https://` 网址留着。`to_observation()` 的截断说明改成实际字符数。

给界面的结构在 `SandboxResult.public_view()`：

- `status`：`ok`、`error` 或 `timeout`
- `stdout`：已经产生的标准输出，已去掉路径；被截断时在末尾注明已截断。失败或超时若已经打出内容，也留在这里
- `reason`：失败或超时时的说明，已去掉路径

成功且没有输出时，`stdout` 为空字符串，界面仍显示结果块，并写「没有标准输出」。失败或超时有标准输出时，原因和输出一起显示。AST 拒绝、非零退出和空代码都是 `error`。超时是 `timeout`。

文件隔离的验收只覆盖 `open()` 和 `os.open` 这两种读法。沿类继承链拿到文件能力的绕过，本批不关闭，也不写成已经隔离了任意代码。

界面沿用检索来源的做法：流式事件单独发送，消息上单独保存，气泡下方单独渲染。不把结果拼进模型正文。模型自己写进正文的路径，本批不改写。结果块在渲染前再扫一遍绝对路径。

交给模型的观测文本不再附带代码参数里的绝对路径。今天 `ToolRegistry.run` 会把完整参数拼进观测。这条拼接要改，文件是 `app/agents/tools/base.py`，放进 SAND-002 的确认清单。其它工具的参数仍按原样交给模型。

一次回复里多次执行代码，按发生顺序都保留。沙箱模块不保存结果列表。`execute()` 返回结果。同一次工具调用用 contextvar 把 `public_view()` 交出去，管线立刻取走并清空，再放进这一次的 `AgentState`。取走之后缓冲是空的。持久的那一份只在这次管线状态里，不用进程级列表。两条对话同时跑代码时，结果不会串到对方的回复上。意图分流跳过工具循环的行为保持不变。默认编排是自研管线。可选的 Supervisor 本批不增加代码结果事件。用户中途停止时，已经跑完的代码结果随这次助手消息落库，和来源一样。停止后再刷新，结果块仍在。

## 方案

### SAND-001 隔离、限额与进程树

只改 `app/agents/tools/sandbox/` 和 `tests/`。

`execute()`：

- 临时目录作为子进程当前目录。
- 子进程环境使用上面的允许名单。名单外的 `env_vars` 不传入。
- Unix 用极短包装进程设置现有四项 `setrlimit`，再 `execv` 用户脚本。不用 `preexec_fn`。
- Windows 用 `ctypes` 的 `CreateProcess` 挂起子进程，放进 Job Object 后再 `ResumeThread`。不用 `Popen` 的挂起标志。不设置内存和 CPU 限额。收尾时关闭 Job 句柄。绑定失败则不恢复线程。
- 等待结束后，若进程仍在，结束整个进程树。正常退出则不额外发送结束信号。
- 返回前对输出和说明做路径清理，并提供 `public_view()`。失败和超时保留已产生的标准输出。
- 临时目录删不掉时，仍返回已经拿到的结果。

测试放在 `tests/test_sandbox.py`：

- `print(1)` 的标准输出是 `1`，用来确认允许名单没有让解释器起不来。
- 在临时目录外写一个文件，内容是一段不像路径的随机文本。执行 `open(该路径)` 和 `os.open` 两种读法。标准输出、标准错误、拦截说明、`to_observation()` 和 `public_view()` 都不含该内容，也不含该路径和临时脚本路径。
- 父进程设置一个哨兵环境变量，同时把它放进 `SandboxConfig.env_vars`。子进程环境里没有它。
- Unix 包装进程在 `execv` 之后不再把 `os` 留在用户代码的全局名字里。用替身记录最终执行的是用户脚本。Windows 上也能检查这一点。
- 进程树：测试拉起父进程和一个睡眠中的孙进程，调用结束函数后两者都不存在。测试用 `finally` 兜底结束，避免残留。不从用户代码里再起子进程。
- `while True` 配合短超时后，直接子进程不存在。
- Unix 的 `setrlimit` 参数仍是 `SandboxConfig` 的默认值，含 `RLIMIT_NPROC=0`。Windows 不因为本任务开始限制内存。不读取 `SANDBOX_*` 配置项。
- 路径清理把 `C:\...`、`C:/...`、`\\server\share`、`\\?\C:\...` 和 `/tmp/...` 换成 `<sandbox>`。成功输出里的 `1/2` 和 `https://example.com/a` 仍在。
- Job 无法创建时的失败结果是「无法建立进程隔离」，且没有用户进程残留。

### SAND-002 对话中的代码结果

`public_view()` 由 SAND-001 提供。展示穿过管线和消息保存。下列文件是本任务的改动范围，并已写入 `tasks.yaml` 的允许路径。

改动文件：

- `app/agents/tools/builtin.py`：空代码也记一条 `error` 结果。`timeout` 不是正数时按 30 秒。正常执行仍把 `to_observation()` 返回给模型，并把 `public_view()` 放进这次调用的 contextvar。
- `app/agents/tools/base.py`：代码工具返回给模型的观测不再附带参数中的绝对路径。其它工具的参数拼接保持原样。
- `app/agents/pipeline.py`：每次代码工具返回后，立刻从 contextvar 取走结果并清空，追加到这一次 `AgentState`。完整工具循环和质量门里的再次执行都记录。流式时发出 `code_result`。意图短路仍不执行工具。并发的另一次对话看不到这次的结果。
- `app/models/conversation.py` 与 `app/core/migration.py`：助手消息增加可空的 `code_results` 文本列，存 JSON 数组。已有库在启动时补列，旧消息为空。
- `app/services/chat_service.py`：流式事件把结果数组交给前端，并在落库时写入该列。一次性对话同样写入。用户停止时，已经产生的代码结果写入这条状态为停止的助手消息。
- `app/api/routes/chat.py`：消息和一次性响应带上该数组。字段只有 `status`、`stdout`、`reason`。
- `README.md`：对话响应里写明这个字段。项目规则要求接口变更同步 README。
- `frontend/src/`：类型、流式解析、消息气泡。结果块放在来源说明同一位置。成功显示标准输出，失败或超时显示原因；已经产生的标准输出同时留下。不新开页面。阶段条上的「调用工具」提示保持原样。
- `tests/`：用假的模型输出驱动管线，断言事件和落库字段。路径哨兵不能出现在字段里。

上述文件，加上 `docs/plans/plan_sand_c5.md`、`docs/plans/plan_remaining_delivery.md`、`tasks.yaml` 和 `AGENTS.md`，构成 SAND-002 的允许路径。

## 非目标

不引入远程沙箱，不开放网络，不新增依赖。不做 chroot、切换运行用户、seccomp、Windows 内存与 CPU 限额、流式截断、沙箱审计和进程池。不关闭沿类继承链拿到文件能力的绕过。不把 `SANDBOX_*` 配置项接到 `execute()`。不改变工具的参数形状。不改变简单问题跳过工具循环的行为。不改 Supervisor。不做独立代码运行页。不改写模型写进正文的文字。

## 验收

- `open()` 和 `os.open` 读沙箱外的指定文件时，文件内容不出现在执行结果里。
- `print(1)` 能得到标准输出 `1`。
- 超时后，这次启动的子进程和测试拉起的孙进程都不存在。
- Unix 上的内存、CPU、磁盘、子进程数和输出上限仍是 `SandboxConfig` 的默认值。
- 成功时，对话气泡下能看到标准输出。没有输出时能看到「没有标准输出」。
- 失败或超时时，同一位置能看到原因；已经产生的标准输出也在。
- 结果块、事件 payload、消息字段，以及交给模型的代码工具观测里，没有宿主机绝对路径。
- 两次并发执行的代码结果不会写进对方的回复。
- 刷新会话后，结果仍在对应的助手消息上。流式、一次性和中途停止都如此。停止后再打开这条会话，已跑完的代码结果还在。

## 允许路径

SAND-001 只改沙箱包和测试，已经实现。

SAND-002 若只改前端，页面没有可展示的标准输出和超时原因。工具观测在模型上下文里，流式事件只有工具名，历史消息也没有这个字段。因此允许路径包括：

- `app/agents/tools/builtin.py`
- `app/agents/tools/base.py`
- `app/agents/pipeline.py`
- `app/services/chat_service.py`
- `app/api/routes/chat.py`
- `app/models/conversation.py`
- `app/core/migration.py`
- `frontend/src/`
- `tests/`
- `README.md`
- `docs/plans/plan_sand_c5.md`
- `docs/plans/plan_remaining_delivery.md`
- `tasks.yaml`
- `AGENTS.md`

这些路径已写入 `tasks.yaml`，并已按该范围实现。

## 验证

实现时执行，不在本计划里宣称已经通过。

SAND-001：

```powershell
pytest tests/test_sandbox.py -v
pytest tests/ -k sandbox -v
ruff check app/agents/tools/sandbox tests/test_sandbox.py
```

SAND-002 在路径确认并实现后：

```powershell
pytest tests/test_sandbox.py tests/test_chat.py -k "sandbox or code_result" -v
ruff check app/
cd frontend
npm run typecheck
npm run build
```

文件隔离和进程树用进程与文件断言，不用模型自评。界面在浏览器里核对成功、失败、超时和刷新后仍在。没有登录条件时，记下未在页面上操作，不把类型检查写成页面已核对。

## 自查

### 第一轮：和现有代码、任务原文是否冲突

对照了沙箱四层实现、工具返回值、管线事件、消息字段和允许路径。

- 产品方案比任务更宽。本批保留 Unix 上已有的限额数值，补上文件隔离和进程树结束。Windows 内存限额仍不做。
- 只测 `open()` 时，当前 Windows 已经读不到文件。本批把验收收成 `open()` 和 `os.open` 两种读法，并堵住 Unix 包装脚本把 `os` 留在用户全局名字里的缺口。沿类继承链的绕过留在非目标里。
- 超时只结束直接子进程。进程树由结束函数的测试覆盖。用户代码在 Unix 上受 `RLIMIT_NPROC=0` 约束，不能再拉起孙进程，所以不从用户代码里造孙进程来测。
- 子进程拷贝全部环境变量，密钥会进入代码进程。隔离执行目录时改成允许名单。名单外的 `env_vars` 不在名单之后再合并。`print(1)` 用来确认解释器仍然起得来。
- 对话页读不到沙箱结构。不改管线、消息和接口，SAND-002 的验收不能成立。
- 简单问题不跑工具，是另一条路径。本批不顺便打开它。

本轮的待确认项是上面列出的路径外文件。

### 第二轮：企业里会不会这样用

- 每次一个临时目录，用完删除，避免多次执行共用一个可写目录。
- 限额沿用已经写在配置对象里的数值，避免本批同时改隔离策略和限额大小，出了问题无法判断是哪边。
- Windows 用 Job Object 结束进程树。子进程先挂起再绑定，避免孙进程在绑定前出生。Unix 用进程组，资源限制放在 `execv` 之前的包装进程里，不用 `preexec_fn`。两边都不用新的沙箱服务。
- 结果和来源一样单独放在消息上，并跟这一次请求绑定。刷新、一次性发送和流式发送看到的是同一份已保存结果。失败时已经打出的标准输出和原因一起留下。
- 结果块和交给模型的代码工具观测都去掉绝对路径。模型自己写进正文的路径仍保留。

本轮没有待改的设计问题。

### 第三轮：失败时会不会说错，以及能不能被测试抓住

- 文件测试用随机内容，断言内容本身不出现，避免只断言「报错了」而文件其实已经被读出。
- 进程树测试在 `finally` 里再结束一次，测试失败也不会把睡眠进程留在机器上。
- Job 创建失败时拒绝执行，避免悄悄变成无隔离进程。
- 路径清理有单独用例，覆盖 `C:\`、`C:/`、UNC、`\\?\` 和 Unix 绝对路径。`1/2` 和网址不被删掉。临时目录删不掉时，测试仍能拿到这次的标准输出。
- 界面用例检查结果块、事件和代码工具观测里没有哨兵路径。模型如果自己把路径写进正文，那一段仍可能出现。
- 空输出仍有结果块。失败或超时若已有标准输出，结果块里同时有输出和原因。
- 并发用例用两次重叠的执行，断言 contextvar 取走后为空，结果只留在各自的请求状态里。
- 停止保存的用例断言已经跑完的代码结果写在停止消息上。
- 非正数的 `timeout` 按 30 秒执行，不会立刻超时。

本轮没有待改的设计问题。

## 实现说明（SAND-001）

代码执行每次使用新建临时目录。子进程环境只保留启动解释器必需的变量。Unix 用极短包装脚本设置现有内存、CPU、磁盘和子进程数上限后，`execv` 进入用户脚本。Windows 用 Job Object 结束进程树：`CreateProcess` 挂起创建、绑定后再恢复；Job 创建或绑定失败时不运行用户代码。超时后结束进程组或 Job。结果离开沙箱前去掉宿主机绝对路径，并提供 `public_view()`。临时目录删不掉时仍返回已经拿到的输出。

`open()` 和 `os.open` 读沙箱外指定文件时，内容不出现在结果里。沿类继承链的绕过、Windows 内存限额、网络隔离和对话页展示不在本任务。

代码位置：

- 执行与 AST：`app/agents/tools/sandbox/sandbox.py`
- 结果与配置：`app/agents/tools/sandbox/base.py`
- 环境、超时、路径清理、Unix 包装：`app/agents/tools/sandbox/runtime.py`
- 进程树与 Job：`app/agents/tools/sandbox/proc.py`
- 测试：`tests/test_sandbox.py`

验证：

```text
python -m pytest tests/test_sandbox.py -v --tb=short
15 passed

python -m pytest tests/ -k sandbox -q --tb=line
15 passed, 1 skipped, 377 deselected

python -m ruff check app/agents/tools/sandbox tests/test_sandbox.py
All checks passed
```

本机是 Windows。Unix 的 `setrlimit` 与进程组结束由包装脚本和进程组代码覆盖，并有源码断言；未在 Linux 上实机跑通。

没做的事：不把 `SANDBOX_*` 接到 `execute()`。不关闭沿类继承链的文件绕过。不做 Windows 内存与 CPU 限额。对话中的代码结果展示见下方 SAND-002 实现说明。

## 实现说明（SAND-002）

登录后的对话里，代码工具跑完后，助手气泡下来源说明旁边出现结果块。成功时显示标准输出；没有标准输出时显示「没有标准输出」。失败或超时时显示原因，已经打出的标准输出同时留下。流式发送、一次性发送和中途停止都会把结果写进这条助手消息，刷新会话后仍在。结果块、流式事件、消息字段，以及交给模型的代码工具观测，都不含宿主机绝对路径。简单问题仍不执行工具。阶段条上的「调用工具」提示保持原样。

代码位置：

- 本次调用交接：`app/agents/tools/builtin.py`
- 代码工具观测去掉参数路径：`app/agents/tools/base.py`
- 请求状态与 `code_result` 事件：`app/agents/pipeline.py`
- 流式、一次性和停止落库：`app/services/chat_service.py`
- 响应与消息字段：`app/api/routes/chat.py`
- 消息列与启动补列：`app/models/conversation.py`、`app/core/migration.py`
- 气泡结果块：`frontend/src/components/chat/MessageList.tsx`、`frontend/src/hooks/useChatStream.ts`、`frontend/src/pages/Chat.tsx`
- 测试：`tests/test_sandbox.py`、`tests/test_chat.py`

验证：

```text
python -m pytest tests/test_sandbox.py tests/test_chat.py -k "sandbox or code_result" -v --tb=short
23 passed, 8 deselected

python -m ruff check app/agents/tools/builtin.py tests/test_sandbox.py tests/test_chat.py
All checks passed

cd frontend
npm run typecheck
npm run build
均退出码 0
```

`python -m ruff check app/` 退出码 1，共 83 项。报错在技能、审计、安全等既有文件，以及 `pipeline.py`、`chat_service.py`、`base.py` 里本任务没有改动的类型注解和未使用导入。本任务新增代码不在这份报错里。

本机是 Windows。没有已登录的页面会话，没有在浏览器里点开发送、失败、超时和刷新。类型检查和构建只证明页面能编过。Unix 限额与进程组结束仍未在 Linux 上实机跑通。

没做的事：不把 `SANDBOX_*` 接到 `execute()`。不改 Supervisor。不改写模型写进正文的路径。不打开简单问题的工具循环。不做独立代码运行页、远程沙箱、网络、chroot、seccomp、Windows 内存与 CPU 限额。`docs/AI辅助开发迭代指导.md` 仍写着下一项是 SAND-002，该文件不在本任务允许路径内，没有改。
