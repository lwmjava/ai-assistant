# 安全前置批次交付记录

日期：2026-10-30。授权：用户指定 module-batch-delivery，依次推进 NFR-007 → SAND-004 → RAG-041。原始契约：tasks.yaml 当前三卡；技能版本 1.2.0 及 acceptance-gates.md 1.2。

## 基线与 Git

HEAD 3b0e322b0775bcc4e18f03eeb005570e251ebeef，分支 codex/pagt-008，与本地 origin/develop 指针相同（未联网更新，不能视为远程最新证明）。PAGT-008/009 未提交改动保留；初始逐文件指纹见 data/validation/security-batch-20261030/baseline.json。沿用用户暂不提交的决定，不自动提交、推送、合并或迁移真实密钥。新批次正式分支起点待确定，暂仅调查和编制方案。

现有 PAGT 局部证据不能替代本批基线。开发前以声明 conda 解释器、隔离测试库运行相关基线；保留既有失败。完整回归及必要 CI 按 AGENTS 第10节，不以专项替代。

## 顺序、范围与终点

|卡|原始交付与验收|方案/所需决定|当前进度|
|---|---|---|---|
|NFR-007|密钥存储加密、运行读取解密、旧明文迁移；配置和数据库无明文|建议认证加密 Fernet 版本化密文；主密钥由独立环境变量/受控挂载注入，不与密文同存。覆盖模型主/备用/意图/重排、Embedding、OCR、搜索服务 API Key。数据库当前未发现 API Key 字段，不凭空新增凭据表。环境直接注入可兼容，持久配置使用密文；密文损坏、无主密钥明确失败，错误不带值。独立迁移命令只操作用户显式指定副本，先验证全部再原子写入，禁止默认触及 .env。需要批准加密库、迁移脚本/依赖声明/使用说明的最小范围扩展及兼容策略。|调查完成，待范围/设计决定|
|SAND-004|非特权、内核网络隔离、导入/继承链绕过拒绝、Linux实际执行|依赖 SAND-003 证据；先核对 Linux 可用机制与已批准边界，再决定不依赖容器的实现。不把 AST 拒绝冒充内核隔离。保留 Windows 明确支持边界，隔离建立失败拒绝执行。|pending|
|RAG-041|批准资源权限 ADR 和独立拆分方案；明确主体/资源/动作、检索过滤、引用/摘要权限、迁移回滚|依现有 ADR-0001/0007/0005 和治理11/14/17节形成可审阅决定，覆盖 rag-036 泄漏证据。当前卡只允许 tests/docs/tasks，不直接实现 ACL，ADR 未经用户接受不写 Accepted。|pending|

三卡之间按用户顺序串行，不把路径范围合并。直接前置状态另核对；必要证据不齐不能伪作 done。批次终点：三卡各自契约对账、适用专项/Windows/Linux、复杂边界独立审查、最终集成证据；缺最终 CI/批准/业务接受时保持 in_review/blocked。

## 验证、审查与回滚

NFR-007：合成凭据的加解密/错误主密钥/篡改/缺失主密钥/配置加载和迁移幂等、失败不改原件及日志错误脱敏；不读取真实 .env，不调用真实模型。加密库版本确定后核对官方文档和在线安全信息。复杂密钥生命周期方案及实现由非实现者独立审查。

SAND-004：真实子进程验证 UID/权限、socket/网络拒绝、反射绕过、资源限制、取消/超时/残留进程及直接调用方，Linux证据与最终 CI 均须满足；更新 Evaluation。

RAG-041：检查原始契约逐项覆盖、现有权限入口和直接调用方、文档引用与一致性；主观权限与迁移决定待负责人批准。无真实索引重建、无生产数据变更。

回滚仅还原本批具体文件改动；迁移不自动运行真实环境，既有 PAGT 改动不回退、不覆盖。最终记录实际命令、退出码、环境、候选指纹、发现和未验证项于本文件及当前卡，不另建重复实现/关闭报告。

## 沙箱隔离开工调查（2026-11-02）

用户明确要求开始 SAND-004。基线 develop/cec856309b47e68407ede42f35f2c5d908f77c1f，与 git ls-remote origin refs/heads/develop 一致；已创建 codex/sandbox-isolation-depth。开工时 tasks.yaml 与 tests/test_pdf_dependency_security.py 有前轮未提交修改，继续保留，不计为本卡实现。

指定 conda Python 执行 scripts/validate_delivery.py targeted，选择 tests/test_sandbox.py、tests/test_sandbox_linux_limits.py、tests/test_sandbox_limit_setup.py、tests/test_sandbox_process_status.py，收集与 pytest 均退出 0；证据 data/validation/20261102T013653-a6c31dfa4eba。首次误选不存在的 test_sandbox_linux.py，执行器退出 1，未执行测试，不计作业务失败或通过。Windows 跳过 Linux 用例不能证明内核隔离有效。

现状：Unix 包装脚本仅设置 rlimit 后 execv；Windows 仅建立 Job Object。AST 检查虽有导入白名单，但未限制私有属性、动态 getattr/vars/type 与继承链反射；现有模块包装器也需要审查正常导入兼容性。不能仅扩大禁止导入名单就声称实现网络隔离。

建议方案（待确认运行边界）：

1. 复用现有 subprocess/资源限额/超时执行链。在 Linux 子进程执行用户代码前设置 no_new_privs，验证非 root 身份且无有效能力，安装 seccomp BPF 网络系统调用过滤。过滤核对体系结构与 ABI，覆盖 IPv4/IPv6/Unix socket、兼容 ABI 与 io_uring 等旁路；未知架构、内核不支持或设置失败都拒绝执行。仅使用标准库和内核能力，不新增 Python 依赖、不引入容器。
2. 推荐要求 Linux 服务本身以非 root、无特权用户运行；root 启动的沙箱拒绝执行，不在 Web 服务里自行切换到共享 nobody 用户。自动降权方案会增加账号选择、文件归属和并发隔离契约；网络命名空间方案通常还需要额外特权或可用的用户命名空间，因此不作默认方案。
3. 加强 AST 与运行时白名单，拒绝私有属性、框架保留名字、动态反射与继承链绕过；从固定可信模块集合中取交集，调用方不能通过配置重新允许任意模块。覆盖正常计算、标准库导入及危险模块别名、拼接属性名、字符串格式反射等反例。
4. 推荐 Windows/macOS 继续保留现有平台能力，但明确不具有本卡的 Linux 内核网络隔离保证；不得在这些平台宣称安全隔离验收通过。若要求所有平台都必须满足同等隔离，则应在不支持的平台拒绝 code_sandbox，这属于兼容行为决定，需要用户确认。

验证：先增加失败用例；Windows 验证 AST、正常计算、错误脱敏与既有 Job Object 行为；真实 Ubuntu 子进程绕开 AST 直接验证网络系统调用被拒、有效 UID/能力、no_new_privs、安装失败不执行用户代码、ABI 旁路、资源限额与超时无残留。复核 Tool Executor 直接调用方与确定性 Evaluation。复杂安全边界需要非实现者独立审查；最终 SHA 的完整 Ubuntu Backend CI 与适用门禁通过前不关闭。没有真实模型费用授权，不触发付费评测。

内核依据：https://docs.kernel.org/userspace-api/seccomp_filter.html ，https://cdn.kernel.org/doc/html/latest/userspace-api/no_new_privs.html 。seccomp 不是完整文件系统沙箱；本卡不据此新增 chroot 或声称隔离所有宿主文件。

当前只完成调查、分支和基线；运行边界确认前没有修改产品源码。回滚只恢复本卡新增隔离模块及对应执行链、测试和评测改动，不自动回退前轮 YAML/PDF 修复。

### 已批准方案的本地实现与验证

2026-11-02 用户明确批准上述方案。源码现已实现：Linux 子进程验证真实/有效/保存 UID 非零及有效/许可/可继承 capabilities 为空；设置 no_new_privs，安装检查原生 x86-64/AArch64 ABI 的 seccomp BPF，拒绝网络系统调用、x32/兼容 ABI、io_uring 和相关旁路。未知架构或设置失败拒绝进入用户脚本。Unix 解释器使用 `-I -S`，过滤跨 exec 保留。Windows/macOS 保留既有支持边界，未新增内核网络隔离；未实现 chroot，不能据此声称宿主文件完全隔离。

语言边界改为固定模块导出 facade：调用方仅可缩小可信导入集合，私有属性、保留名字、动态反射、帧访问及字符串格式属性遍历拒绝。修复原包装器不可调用导致普通 import 失败的问题。审查发现完整 json/statistics 模块通过公开属性泄漏宿主能力的 P1，已修复；functools.update_wrapper/wraps 也不导出，防止借内部反射复制 globals。正式测试包含哨兵文件读取反例。取消协程会通知 worker 结束受控进程并等待回收，随后清理目录，重复取消也保留回收等待。

实际验证：

- 失败用例首轮 22 failed/2 passed/1 Linux skipped；复现反射、导入配置扩张与正常导入失败，证据 data/validation/20261102T020601-86f522aaafe6。
- 首次修复回归 131 passed/12 skipped；扩大专项一轮 183 passed/1 failed/12 skipped，唯一失败为 Windows 注入测试漏固定 platform.machine，未走到预期 seccomp 安装失败点；已固定受支持架构，未放宽产品拒绝条件。
- 最终 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe scripts/validate_delivery.py targeted` 选择 test_sandbox_isolation、test_sandbox、test_sandbox_limit_setup、test_sandbox_linux_limits、test_sandbox_process_status、test_tools、test_pagt001_tools、test_tool_executor、test_tool_contract、test_pipeline_budget、test_auth_revoke、test_switch_tenant（均为 tests/ 下 .py 文件）：184 passed/12 skipped/1 既有 ast.NameConstant warning，退出 0。证据 data/validation/20261102T021531-ddc8d1de382c，含完整命令、输入指纹、日志、JUnit。12 项均需要真实 Linux，不声称覆盖。
- `python -m ruff check . --no-cache` 退出 0；`python -m mypy app/ --cache-dir C:/Users/123/AppData/Local/Temp/sandbox-isolation-mypy` 262 文件退出 0；`git diff --check` 退出 0。早期 getresuid 的 Windows 类型错误、长行与换行检查失败已修正；不把早期失败记为通过。所有 python 均为上述指定 conda 解释器。
- 独立审查者 sandbox_security_review 只读审查当前源码及测试，修复 P1 后复核受影响范围无剩余 P1/P2；覆盖 Linux 过滤构造、导出与反射、取消生命周期，未运行 Linux 实测或最终 CI，不能替代其证据。

Evaluation 为 docs/ai-prompts/evals/sandbox-isolation-v1.yaml，确定性合成用例，不调用真实模型。无 API/schema/前端行为契约变化，前端检查不适用。未变共享 fixture/CI/验证执行器；最终不筛选全量回归由最终 SHA Ubuntu Backend CI 承担，不能用本地专项替代。

当前本机仅有 docker-desktop WSL，无 Python，Docker 引擎不可连接；未安装运行时或下载镜像。真实 Linux 过滤安装、跨 exec、直接系统调用/平台资源限制证据、最终 SHA 完整 CI 与业务接受仍待。汇总与指纹在 data/validation/20261102-sandbox-isolation/summary.json；本卡保持 in_review，尚未提交推送，不触发付费官方评测。后续记录文字不改变源码/测试输入，可复用本轮专项结果。
