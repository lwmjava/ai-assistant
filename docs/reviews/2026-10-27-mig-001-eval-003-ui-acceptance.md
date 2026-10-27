# MIG-001 / EVAL-003 独立受托页面验收

日期：2026-10-27（Asia/Shanghai）。执行者：独立子 Agent `/root/mig_eval_ui_acceptance`，未参与实现。授权：用户明确要求“启动一个独立agent帮我完这两个任务的页面验收”。使用 browse 技能的场景/证据方法；该技能本机二进制未安装，实际浏览器操作通过 CUA Chrome 扩展执行，不使用 SSR 替代操作。

## 结论

- **EVAL-003：本轮受控本机环境的页面生命周期验收通过，未发现 P1/P2。** 实际登录、选资源/测试集、填预算、勾确认、点击启动，观察成功/失败/排队/运行/取消及刷新持久化，并实际点击完成任务的“查看报告”。
- **MIG-001：没有独立页面功能，直接页面验收不适用。** 当前未提交迁移代码在新隔离库上通过真实 `init_db()` 初始化到 `fb17e9f0a2b3` 后，FastAPI lifespan 再次启动、登录和评测页面访问正常。此为启动后的页面冒烟证据，不能替代迁移、数据保留或生产失败边界专项。
- 没有验证国内真实模型、真实检索质量或实际供应商费用；没有取得最终提交完整 CI。本报告不关闭任务、不宣称可发布、不冒充人工操作。

## 输入版本与环境

分支 `feat/EVAL-002`，HEAD `63f1ca227304086d7893aabe2f2021cb6b438f02`，加已有 MIG-001 未提交修改；执行中未修改实现、测试或 tasks.yaml。正式新增物仅本报告。源码与产物 SHA256 记录在 `data/validation/mig-eval-ui-20261027/evidence.json`：

| 输入 | SHA256 |
|---|---|
| app/core/database.py | `409aba812a2580bca5ea7dd750c9ac15880afaf5cc5c73bb879d2fb82233aeea` |
| app/core/migration.py | `cd2c041b2ae330860c138927231b92e5ceb7e270b209a6adc712b7887738d82d` |
| app/services/evaluation_jobs.py | `06c75b299cf124e47b653108d497cac01aef143056ed52e6dfaaee41053de269` |
| app/services/evaluation_orchestrator.py | `519d599f8804a45af3dd464c207958e4c33e3dca72905bf51ea7528a0ba01a9e` |
| frontend/src/pages/Evaluations.tsx | `ec308b3c3f366d0de8417ce7cb72f1228a44f88e6bbafc6d9170fe094daca680` |
| frontend/src/pages/EvaluationReports.tsx | `2809ac82d14c4e8048ad27f02ceea66b3af70de687bae278bb8f91324725f951` |
| frontend/dist/index.html | `4f5b5d6aaabf2c478bb78b19cbdc7d90aa2e8865b3a64fcb8f0148d13e625a64` |

Windows，指定解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。Chrome 真实浏览器新验收标签，URL `http://127.0.0.1:18795/evaluations`；当前构建产物由真实 FastAPI 同源托管，无前端拦截响应。数据库为新建 `data/validation/mig-eval-ui-20261027/ui.db`，使用真实版本化迁移，未访问用户数据库或 `.env`。后台并发 1，RAG 显式 local，系统管理员及文档/测试集均为独立合成 fixture。

Embedding 为 `http://127.0.0.1:18796/v1` 的受控本机 HTTP 服务，OpenAI-compatible 协议、`synthetic-ui-embedding`、64维；向量来自确定性 Mock 算法。显式注入仅供本机 fixture 的输入限制策略，不更改生产配置或校验。实际调用走产品 RAGService、预算 Embedding 适配器、execute_job 和队列；未替换评测 runner。服务禁止非 loopback 网络，真实供应商请求为 0、费用为 0；本机 HTTP 请求仍按外部调用契约计数。质量评测未启用。

## 实际页面场景

| 场景与动作 | 可观察结果 | 判定 |
|---|---|---|
| 从 `/evaluations` 登录合成系统管理员 | 正常登录并显示目标租户、资源、请求上限、确认勾选和测试集 | 通过 |
| 选择合成文档、审核单条版本，预算2但不勾外发确认 | 启动按钮禁用；勾确认后启用 | 通过 |
| 有资源及确认但预算0 | 启动按钮禁用；改为正预算后启用 | 通过 |
| 点击启动单条检索评测 | 任务自动更新为 succeeded、1/1；出现“查看报告” | 通过 |
| 点击该任务“查看报告” | 自动选中关联 `job-d00f7...`；显示 `ui-small-v1`、openai-compatible/模型/64维/local/索引身份，以及逐用例外部调用1 | 通过 |
| 选择HTTP失败合成用例并点击启动 | HTTP400收敛为页面 failed、0/1、`external_request_failed`；新会话读库调用数保留1 | 通过 |
| 失败后启动100条受控慢请求任务 | 页面正常进入 running，进度自动增长，说明失败未停止调度 | 通过 |
| 运行中再次点击启动 | 页面同时显示 running和queued；两项都有取消入口 | 通过 |
| 点击 queued 的取消 | 收敛cancelled、0/100，新会话读库调用数0 | 通过 |
| 点击 running 的取消 | 收敛cancelled、93/100、cancelled错误码；调用数94包含取消时已预占的进行中请求 | 通过 |
| 刷新页面、重新选择同租户 | 两个cancelled、failed及succeeded终态继续显示 | 通过 |
| 浏览器错误日志 | 本轮页面错误级日志为空 | 通过 |

任务结果另经独立 SQLite 新连接只读核验并执行断言（退出码0）：

| 任务ID | 终态 | 已完成 | 持久调用次数 |
|---|---|---|---|
| d00f7dbb08f14f0eb0f4c9bceb0fd635 | succeeded | 1/1 | 1 |
| f5aad6b14fca432ab1df8d4fd65c5256 | failed | 0/1 | 1 |
| 3d4de8eac4664e6ca36be68d35c3b65a | cancelled | 93/100 | 94 |
| 7b4d1a4d9f1149229d60c893f53a1515 | cancelled | 0/100 | 0 |

运行状态卡当前不展示累计调用次数，因此失败和取消后的次数由同一页面创建任务对应的持久记录复核，不将数据库读取表述为“页面显示次数”。成功报告页面确实显示逐用例调用1。取消时本机供应商响应写回遇连接中断，属于请求已关闭的 fixture 日志，不是任务遗留running。

## 命令、证据和清理

实际启动：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe data/validation/mig-eval-ui-20261027/server.py`，日志 `backend.log`。该进程完成真实迁移、种入合成数据、托管后端/前端及本机HTTP供应商；UI状态成功可见，不以命令存活代替验收。

截图均来自本轮 Chrome：

- `01-completed.png`：成功任务及查看报告入口。
- `02-report.png`：关联报告、版本身份、逐用例调用。
- `03-failed.png`：HTTP失败终态。
- `04-queue.png`：running及queued同时存在。
- `05-cancelled.png`：排队与运行取消结果。
- `06-refreshed.png`：刷新后任务记录。

以上文件位于 `data/validation/mig-eval-ui-20261027/`；配套 `server.py`、`fixture.json`、`evidence.json`、`success-report.json` 保留复核。成功报告 SHA256 `3c950f96551b16aa045a3d6d7b5a102213b1d1c57af18aac2c632d4366896ad1`。产品执行器新生成的唯一报告在完成跳转验收后复制到本轮目录并移除原处临时文件；未改已有报告、冻结基线或阈值。

初次证据导出将 sqlite Row 直接JSON序列化失败，转换为字符串列表后重跑成功；未将此探针错误计为业务失败或隐藏。一个精确label定位未匹配，读取DOM后改用实际combobox名称完成操作，没有绕过UI。最终截图已实际读取核对。

结束时向本任务自有服务发送中断并退出；随后本机连接核验18795、18796均返回10061（拒绝连接），进程已清理。Get-NetTCPConnection/CIM只读检查因权限拒绝，改用本机socket端口核验；未执行扩大权限的进程终止。验收标签未标记保留。工作区除本报告外与开工时一致，未提交推送。

## 未验证与接受边界

本次为用户委托的独立自动化页面接受，覆盖受控本机 RAG 生命周期和真实界面路径，不能证明真实国内供应商质量、实际扣费、Agent/Workflow质量或生产数据库迁移。未重复已通过且输入未变的技术专项，也未取得最终候选CI。MIG-001的核心接受继续依赖既有Windows/真实PG/Linux和独立技术审查证据；本报告仅补页面适用性说明与启动后页面冒烟。两卡的最终关闭应由主流程核对全部必要证据，不能仅凭本报告标记done。
