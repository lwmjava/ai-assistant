# 开发验证、审核与 CI 交付效能修复方案

日期：2026-10-22。状态：2026-10-22用户批准按方案实施一轮，并明确选择NFR-011、FE-001试用；Git发布、付费模型与远程保护变更尚无新增授权。

基线提交：`1973299843658674eda258f05312653a8ec00469`。起草时工作区干净，分支及HEAD以实施前重新核对为准。用户请求：基于EVAL-001、FEED-001的实际耗时，形成完整修复方案。

关联：[审计建议](../reviews/2026-10-22-harness效能审计与规则引用整改建议.md)、[上轮规则减负记录](plan_harness_workflow_simplification_20261022.md)、[EVAL-001记录](implementation_eval_001.md)、[FEED-001记录](implementation_feed_001.md)。本方案补上上轮未改的验证执行、审核资源与CI组织问题，不以文档缩短宣称提速。

## 1. 问题与证据

全部时间采用Asia/Shanghai。Git提交时间不等于开工或代码完成时间；本地日志创建/修改时间只作执行区间辅助证据。进程累计运行时间、并发墙钟时间、用户等待时间分别解释，不相加冒充总工时。

| 问题 | 核对证据 | 需要修复的机制 |
|---|---|---|
| 环境故障长时间空跑 | EVAL首次基线08:33—09:14，2481.46秒；2109 passed、1165 errors；大量错误为同一旧pytest临时目录PermissionError | 全量前预检、唯一目录；明确基础设施故障及时止损 |
| 无变化基线与最终检查成本重复 | EVAL基线补跑953.23秒，最终758.08秒；另有中止/无结论全量 | 基线复用、先收敛后最终验证、证据按实际影响失效 |
| 同工作区重复全量 | FEED v2为931.03秒、v3为942.12秒；11:39/11:40先后启动且重叠，均得到相同4个失败 | 工作区全量互斥，先修已知失败再启动下一轮 |
| 存量调用方检查太晚 | FEED相关91项修复验证21.18秒，之后全量v4为862.07秒；取消、错误脱敏及限流事件测试暴露公共链兼容问题 | 开发阶段优先检查旧调用方、关联状态与失败路径 |
| 审核资源用尽且无结论 | EVAL审核CLI记tokens used=85,533并NO_FINDINGS；FEED记110,074后usage limit退出1 | 限定起始材料、渐进展开、时间/额度预算与失败处理 |
| 审核及UI调整发生在最终验证之后 | FEED全量v4通过后又修GeneratorExit分类并调整点赞/点踩交互 | 审核/页面走查前置；后续变更仅使相关证据失效 |
| 外部服务失败连带重跑静态/离线检查 | EVAL首次Backend的Ruff/mypy/3284项pytest通过，RAG真实Embedding连接/TLS超时；同SHA重跑成功 | 将外部真实评测拆成独立job，可单独重跑 |
| CI依赖安装波动 | EVAL首次Frontend总350秒，其中npm ci=330秒，四项检查约13秒；FEED同类job约30秒，安装6秒 | 查缓存命中/下载/安装耗时，保持确定性安装，不先删测试 |
| 收尾信息滞后 | FEED提交1973299的CI已成功，仓库记录仍写CI待执行 | 最终SHA查询、证据对账和最小状态更新 |

CLI tokens used是工具报告的累计计数，不代表单次上下文长度或账单金额。审核日志含大量读取并不证明全部无价值；应保留真正发现生命周期缺陷的审查。

原始日志位于本机 `data/validation/eval001-baseline-20261022/`、`data/validation/eval001-final-20261022/`、`data/validation/feed001-20261022/`。长期CI证据：

- [EVAL首次失败Backend](https://github.com/lwmjava/ai-assistant/actions/runs/39710843032/job/119601539366)：10:20:33—10:28:02；pytest step 348秒，RAG step 55秒。
- [同SHA Backend重跑](https://github.com/lwmjava/ai-assistant/actions/runs/39710843032/job/119608102936)：10:42:48—10:50:20；pytest step 355秒，RAG step 46秒。两轮间隔含调查、授权及派发，不能全部归为GitHub排队。
- [FEED CI](https://github.com/lwmjava/ai-assistant/actions/runs/39732094037)：14:08左右启动，14:19左右完成，三个job成功；Backend约636秒，Frontend30秒，Agent51秒。本事实不自动关闭尚未逐项对账的业务卡。

## 2. 目标、边界与路线选择

目标是减少无效与重复工作，使正常任务尽快进入可用验收，并维持真实缺陷的发现能力。不能承诺任意两个任务五小时内完成；卡片复杂度、外部故障和用户业务判断仍影响周期。

三条路线比较：

| 路线 | 收益与代价 | 选择 |
|---|---|---|
| 只继续精简规则文案 | 风险小，但无法消除全量重复、环境错误和CI耦合 | 不单独采用 |
| 按影响做本地验证，前置审核，最终全量交给CI，拆开外部评测 | 直接对应今天证据；需修改验证契约并建立轻量执行入口 | 推荐，本方案范围 |
| 默认只跑改动文件，取消完整回归/真实门禁 | 快但容易漏调用方与跨模块回归，无法证明质量收益 | 不采用 |

非目标：不改业务权限/取消/重试语义，不切Mock制造通过，不降低指标阈值，不新增pytest并行插件或完整治理平台，不批量重排全部任务，不全面迁移docs历史文件，不自动提交/推送/合并/部署，不默认外发源码或产生模型费用。

本方案包含的两项新政策须明确批准后才生效：

1. 任务关闭所需的完整后端回归允许由最终SHA的合格Ubuntu CI承担；本地不再无条件重复同等全量。
2. 已在CI通过且与最终候选绑定的Ruff/mypy/前端检查允许作为该检查的正式证据；必须保留本机特有路径的验证，不以Linux替代Windows特有行为。

这改变检查执行地点与复用方式，保留必要检查及通过标准。现行AGENTS第10节未修订前，不能据本提案自行跳过检查或关闭任务。

## 3. 新交付流程

```text
开工：范围/依赖/授权/现有有效基线 + 环境预检
→ 开发：失败用例、改动模块与直接调用方专项
→ 收敛：相关专项通过 + 适用独立审查 + 页面走查/业务反馈
→ 候选：记录源码/测试/配置/依赖/评测输入与环境指纹
→ 本地就绪检查：专项 + 廉价静态/契约检查
→ 用户已授权时提交/推送最终候选；否则交付可推送状态
→ CI：完整确定性回归 + 分离的真实评测并行执行
→ 同SHA结果对账 + 已取得的业务接受 → 状态同步
```

本地验证、独立审查、页面技术走查、用户业务接受、CI分别记录，不相互冒充。用户业务接受可以在CI前完成，不要求CI成功后重新点击同样页面；最终相关行为变化后才重验该场景。

只有获授权的下一任务可在CI等待期间推进。独立任务可继续，依赖尚未通过的能力不能作已完成前置；不为等CI而阻塞所有方案准备，也不自动启动整个backlog。

“正常最终全量一次”是操作目标，不是次数上限：失败后必须修复、专项验证并重跑必要最终检查，不能为满足次数目标隐藏失败。

## 4. 本地测试分层与影响范围

### 4.1 检查决策矩阵

| 变更 | 开发阶段重点 | 最终要求 |
|---|---|---|
| 纯说明、历史记录、无执行作用的规则解释 | 范围/引用/语法/一致性 | 文档契约；不触发业务全量 |
| Python局部确定性逻辑 | 新失败用例、模块测试、直接调用方 | 后端Ruff/mypy；完整pytest由最终CI承担 |
| 鉴权/租户、ChatService、Tool/Skill执行、LLM、缓存、工作流桥、全局配置 | 身份/撤销/跨租户、取消/重试/预算、旧fixture及公共调用方 | 独立审查 + 最终CI完整pytest；无法可靠分析影响时本地也全量 |
| 持久化、迁移、文件系统、子进程/信号 | 旧库/新库、失败/回滚/幂等及相应平台测试 | Ubuntu完整CI与必要Linux场景；Windows特有行为在Windows专项验证 |
| 前端页面/交互 | 页面相关用例、实际操作、错误/权限/刷新 | 前端typecheck/Vitest/ESLint/build；最终CI统一承担，必要浏览器场景本地保留 |
| 前后端契约 | API/schema、前端消费者、错误/空值/权限 | 后端/前端完整CI及相应集成 |
| requirements/pyproject、conftest、测试选择器、CI执行配置 | 依赖/收集/安全隔离与影响判定反例 | 完整验证；首次修改验证基础设施还需本地全量与Ubuntu通过 |
| docs中的可执行脚本、被代码读取JSON/YAML、产品Prompt/Skill/评测输入 | 按实际消费者与行为分类 | 不使用纯文档豁免；相关Evaluation/回归及必要真实评测 |

“只测改动行”不作为选测规则；优先使用现有任务契约与测试文件建立明确清单，覆盖调用方和共享fixture。第一版不写复杂的自动Python依赖图或仅靠文件名猜测试。

### 4.2 选测说明与退回全量

每个任务在现有交付记录保留四项：改动模块、调用方/契约、选中测试命令、未覆盖风险。脚本输出最终选择，空集合或未知路径不能算通过。

遇到影响不明确、共享fixture/依赖变化、跨模块失败，或专项未覆盖的回归，退回本地全量。若失败来自本卡兼容缺口，先补入该模块的固定专项，下一次不再等全量才发现。

FEED类任务专项必须纳入现有的 `tests/test_chat_feedback.py`、`tests/test_chat_feedback_api.py`、`tests/test_chat_feedback_migration.py`、`tests/test_sse_active_runs_review.py`、`tests/test_chat_combined_tools.py`、`tests/test_log_redaction.py`、`tests/test_rate_limit_retry.py`。EVAL类任务以 `tests/test_evaluation_reports.py`、`tests/test_evaluation_api.py` 及实际变更的身份消费者为起点。该清单只是已知起点，不能据此排除未来新增调用方。

### 4.3 检查结果按域复用

- 后端证据绑定后端源码、测试、共享fixture、依赖、相关配置、输入与检查命令/工具版本。
- 前端证据绑定前端源码、测试、依赖锁、构建/测试配置、接口契约及浏览器场景。
- 真实评测绑定其代码、数据、模型/能力声明、Prompt、索引、参数和阈值；缓存不可替代真实质量证据。
- 仅前端图标变化不使后端结果失效；后端响应结构变化同时使前端契约相关结果失效；换行修改真实源码字节后不得无证据声称输入没变。
- 报告明确“在哪个SHA执行”和“为什么适用于当前候选”；不能把旧SHA结果伪造成当前SHA已执行。完整交付仍要求最终SHA的必要CI结果，文档说明不取代远程必需检查。
- 基线复用匹配解释器、extra、关键依赖/配置及对应输入指纹；没有可用基线则先取得必要基线，不能标通过。Windows结果不能冒充Linux。

## 5. 验证执行入口与故障止损

### 5.1 最小实现

新增一个基于现有Python环境/标准库的 `scripts/validate_delivery.py`，复用现有pytest/Ruff/mypy/npm命令与现有RAG/Agent runner。相关逻辑过大时再拆内部辅助模块，不新增第三方编排框架。

拟提供 `preflight`、`targeted`、`full`、`summary` 四种模式；前两者提供本地快速反馈，full供确需本地完整验证和CI复用，summary只读现有证据。任务记录给出显式测试列表；不拼接任意shell、不执行任务描述中的外部命令。

一次运行使用唯一run_id，在 `data/validation/<run-id>/` 保存日志、JUnit/JSON、退出码、命令、平台/版本、起止时间、输入指纹与最终摘要；长期必要报告归受控CI artifact或批准归档，不向docs复制终端输出。

### 5.2 预检

1. 检查指定conda解释器存在、能导入实际必需检查模块，记录版本和extra。CI使用其已声明Python，不硬编码Windows路径。
2. 对本轮目录做创建、写入、读取和重命名探针；只清理本轮自建探针，不修系统ACL、不递归删除旧目录。
3. SQLite测试库限定在临时验证目录，连接创建独立空库并校验路径；拒绝重要业务库和未知外部连接串，报告只输出脱敏身份。
4. 显式传入唯一 `--basetemp`、pytest cache、Ruff/mypy cache和DATABASE_URL，避免复用 `data/pytest-tmp/run`。
5. 核对测试收集成功且选择非空；不导入产品配置读取.env来做环境“自检”，延续conftest禁止密钥继承和非loopback外连的保护。
6. 默认pytest入口的临时目录也改为唯一目录，避免绕过执行器时继续复现旧问题；核对测试库隔离及兼容性后修改 `tests/conftest.py`，不能只改新脚本而保留坏默认值。

建议预检目标为60秒以内；这是待验证工程目标，不是超时后伪造通过的阈值。

### 5.3 全量互斥与输入变化

同一物理工作区的完整pytest使用一个原子锁，保存run_id、进程身份和启动时刻。第二次请求返回“已有全量进行中”及运行路径，不再启动另一轮；不同独立检出且资源隔离时可以并行。

正常退出释放锁，异常退出保存已知失败/中止状态；陈旧锁须先证明所属进程已结束，不能仅凭PID不存在/复用便盲目终止其他进程，无法确认则报告恢复动作。Windows与Linux都测试。

执行器不能锁住Cursor编辑文件；通过启动/结束指纹及可用的运行期检查识别相关输入变化。发现变化将本轮标记invalidated，按用户已有授权安全停止自己启动的测试进程，或等待结束后明确不采纳；保留日志。一次性锁与运行目录不得被提交。

### 5.4 故障分类与停止

| 类别 | 处理 |
|---|---|
| 预检目录/数据库/解释器失败 | 不启动pytest；显示具体修复事项 |
| 明确的相同基础设施故障重复出现 | 保存首个完整错误与代表性样本，结束本轮；不能继续产生上千同源ERROR |
| 普通业务断言失败 | targeted可用 `--maxfail=1` 快速定位；最终完整检查保留完整失败集合，不全局改pytest配置掩盖失败 |
| 外部连接/TLS/服务超时 | 标external_infrastructure_failure，不称质量通过；按预算/授权只重跑失败评测 |
| 超时/用户取消/进程消失 | 标timeout/cancelled/interrupted，不把无退出码当成功 |
| 无法判断 | 标unknown并保留证据，不用统一自动重试掩盖问题 |

基础设施止损优先靠预检；仅在有确定证据时运行期提前停止，不能把同样文案的业务失败随意归成环境错误。以上为运行报告状态，不新增tasks.yaml状态枚举。

## 6. 审核和业务走查前置

### 6.1 顺序与独立性

相关专项通过后即可开始适用独立审查，与浏览器技术走查并行；稳定前不启动最终全量。业务反馈若会改变主要交互，尽量在这个阶段取得。审查修复后由原审查者检查变更范围，不无条件再换人重做全部审核。

普通局部任务由实现者自审；权限、公共链、迁移等保持真实独立审查。实际可用且获授权的独立Agent/同事审查优先；不强制每卡再启动外部Codex CLI，也不能由同一Agent改名冒充独立。

### 6.2 材料包与预算

起始包只含当前卡的目标/验收/批准决定、最终diff、相关源码/测试、直接调用方及已知疑点。AGENTS只读必要规则；不整份读tasks.yaml、全量manifest、历史评审、完整测试日志或整个无关runner。证据摘要按需展开原件，无法判断时报告具体缺口。

建议预算：起始材料目标不超过约12k tokens；10分钟为一次审查的软上限，达到时先返回已覆盖/未覆盖/具体发现。若宿主支持实时计数，累计20k tokens时提示收敛；该数值为试用建议，不是账单或质量SLO。宿主不支持计数时按材料范围/时间控制，不承诺可设置不存在的CLI硬预算。

配额不足、网络失败、预算耗尽都输出review_incomplete；不称NO_FINDINGS、不自动无限换模型或追加调用。未覆盖的必要审查保留缺口，选择已有授权的替代审查渠道或交用户决定。审核工具不运行它无权启动的写测试或真实模型。

有效审查至少说明实际身份、检查输入版本、已核对调用方/场景、发现与剩余覆盖；不按问题数量或耗时判定质量。

### 6.3 一次开工说明集中授权

开工时一次列明必要文档同步路径、已有架构决定、Git动作范围、是否需要外发源码、评测provider/模型/次数与费用上限、是否允许外部基础设施失败后重跑一次。已获授权的范围不重复询问；没有授权的外发、真实费用和Git动作仍不能执行。

README/Swagger等必需同步文件在任务准入时核对，缺失时集中解决，不到实现中途才被allowed_paths卡住。真正的新方向冲突仍需单独升级，不能以“一次授权”扩大数据或生产权限。

## 7. CI 拆分、缓存与最终门禁

### 7.1 推荐拓扑

继续以 `.github/workflows/ci.yml` 为主，保留现有技术栈及三个检查名称，新增独立的 `RAG official baseline` job：

| Job | 内容 | 失败后重跑 |
|---|---|---|
| Backend (ruff / mypy / pytest) | 安装声明extra、全库Ruff/mypy、不筛选完整pytest | 仅该job；失败用例先定位 |
| Frontend (typecheck / vitest / eslint / build) | npm ci及现有四项 | 仅该job |
| Agent official baseline | 现有真实Agent runner/gate/probe | 仅该job；费用沿授权 |
| RAG official baseline | 原Backend中的真实Embedding检索baseline及原gate | 仅该job；不再重跑已通过的Backend |

RAG和Agent使用原数据、指标、预算、超时和阈值。本轮推荐方案不减少最终候选的真实评测要求，不将夜间报告或Mock替代最终结果。未来按影响/定时降低真实调用频率属于另一个明确决定，不作为本方案完成条件。

在四个job的必要安装完成后并行执行，真实评测凭据只给对应job。断开RAG失败与离线回归重跑，不意味着RAG失败不阻断最终交付。

### 7.2 安全迁移顺序

现有Backend成功包含RAG门禁，直接移走会使原required check的含义变弱。按以下顺序迁移：

1. 只读核对默认分支、required checks、rulesets与bypass，记录权限可见范围。读不到时标未核验，不声称远程自动保护已生效。
2. 先新增独立RAG job，暂保留旧Backend中的RAG步骤，首次允许一次迁移重复；取得新job可用证据。
3. 获明确远程管理授权后，把RAG新check加入required集合，保留Backend/Frontend/Agent。由有权限的负责人核验配置生效。
4. 确认新RAG门禁受保护后，才移除Backend内的重复RAG步骤。最终提交重新验证；不留保护空窗。
5. 无法取得远程权限时停在兼容态，保留旧RAG阻断链，不以仓库文档替代保护配置。该状态只算部分交付。

如选择固定汇总check，必须实际校验所需job结果，并拒绝缺失、失败、取消及不被允许的skip；不能依赖“job跳过也显示Success”。第一版无需引入汇总job或路径过滤即可获得拆分收益。

GitHub官方说明：工作流被paths等条件跳过时required check可能一直Pending；job级条件跳过可显示Success。必须先明确门禁语义再做过滤。[必需状态检查排障](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)

### 7.3 缓存、安装与重复触发

- 保留 `npm ci` 与lockfile，不缓存node_modules替代安装；记录setup-node缓存命中及安装耗时，区分首次冷缓存、包下载和安装脚本。
- 给现有setup-python添加pip下载缓存，键覆盖OS/Python及实际requirements/pyproject；安装仍声明完整extra，不缓存带数据库/密钥的工作目录或整个虚拟环境。
- 第一轮不合并或删除重复pip安装命令，先确认requirements与editable extra的依赖语义。只对测得的瓶颈优化，不能靠漏装extra提速。
- 不因单次安装慢就更换registry、关闭TLS、加不可信镜像或删lockfile。依赖调整另走兼容与供应链检查。
- 保留现有concurrency；开放PR时push与pull_request可能重复触发，先检查真实run事件及SHA再治理。去重不能让默认分支/合并结果或必要检查消失，取消中的旧结果不能作为新SHA成功。
- 上传Backend/Frontend/RAG/Agent的必要摘要、JUnit/失败诊断及输入身份，使用always保存失败证据；不上传数据库、凭据或原始敏感业务内容。沿用现有14天artifact保留作为初始策略，长期证据按批准要求另行归档。

GitHub缓存说明：缓存用于减少依赖获取耗时，未命中仍应能完整安装，不能替代有效测试。[依赖缓存](https://docs.github.com/en/actions/concepts/workflows-and-actions/dependency-caching)

## 8. CI 后的验收与任务收尾

在现有交付记录维护一张状态表：实现结果、适用专项、独立审查、页面/业务接受、本机特有测试、最终CI、残余风险。任务状态继续沿用现有枚举；内部字段如ci_pending只是证据项，不另造第二套任务系统。

拟在同一执行入口的summary模式中只读汇总本地记录与用户提供/已授权查询的GitHub结果，输出差异和建议补丁，不以含糊的提交简称查询，也不因PR过滤API返回0条就判断没有push run。

关卡：

1. 用完整SHA查询run，检查事件、attempt、job名称、最终状态与需要的额外平台证据。
2. 确认工作区/最终候选相关输入未变化，审查发现均闭合，用户接受仍覆盖当前行为。
3. 已有业务接受时复用，不要求重新发送“关闭”口令；无接受或委托边界时只保留真正需要用户判断的项。
4. 仅在完整证据满足后更新当前卡状态及链接，原历史日志不反复追加“未完成→完成”长段落；保留可追溯变更即可。
5. 用户未授权提交/推送时交付清楚的就绪摘要，不为等待Git授权而无期限占住当前执行；CI未跑则如实保持in_review。

FEED-001作为一次收尾演练：CI success是已取得的新事实，但本方案不直接关闭卡。实施时核对1973299是否覆盖最终修复/页面、现有用户接受和独立审查，补齐真正剩余的检查后同步，不重新跑无变化的整套验收。

## 9. 实施包、改动范围与顺序

以下是方案内工作包，不是已登记任务卡；本轮不批量修改tasks.yaml。批准实施时按依赖登记边界清楚的少量卡，复用本文相应章节为计划，已批准内容不再重复写方案。

| 包 | 交付与建议范围 | 依赖 | 完成条件 |
|---|---|---|---|
| A 本地验证可靠性 | scripts/validate_delivery.py、tests/conftest.py及执行器/隔离测试；docs下仍被依赖的正式测试核对收集入口 | 无 | 唯一目录、库与锁、预检、失败/中断/过期状态均有反例，Windows/Linux通过 |
| B 规则与审核顺序 | AGENTS第9/10/11/13节、Cursor全局/测试规则、台账全局策略、两个交付技能及流程Evaluation、工作流/清单 | A的接口可用 | 规则允许最终CI承担全量，保护必需证据；材料包、审核退出、授权和业务接受一致 |
| C CI拆分与收尾 | .github/workflows/ci.yml、必要runner集成/测试、summary；受授权的远程required checks | A/B就绪 | 拆分单独重跑有效、远程保护确认、最终SHA所有必要检查通过，FEED收尾对账 |
| 试用观察 | 接下来三个自然获授权任务，在原交付记录填写耗时/缺陷 | A/B/C生效 | 重复执行和环境故障可量化，失败发现能力不退化 |

A内部先交预检/唯一资源/互斥，再补影响清单和摘要；不先建设自动依赖图。B不改变安全红线或测试通过阈值。C首次迁移的必要重复单独记录，不纳入普通任务“无重复”统计。

估算仅供容量安排：A约半天至一天，B约2—4小时，C约半天至一天；环境权限、CI配额和真实模型授权等待另记。属于初始估算，非已测工时或固定承诺；先完成A的最小可用切片并观察效果，不把修复排成数周治理工程。

本轮方案文件是唯一新增正式产物；后续实现结果补在本文对应工作包，独立审查按需另归reviews，不再每包生成计划/实现/关闭三套说明。

## 10. 必须验证的反例与命令

| 验收场景 | 期望 |
|---|---|
| 旧pytest临时目录不可写 | 不再使用旧目录；新预检失败即退出，不启动3000项测试 |
| 两个工具同时申请同工作区全量 | 只有一个进程执行，另一方得到明确占用信息 |
| 中途杀进程/崩溃 | 留下可辨别的interrupted记录，无伪造退出0；陈旧锁不误杀PID复用进程 |
| 测试中修改相关源码 | 本轮invalidated，旧结果不可关闭当前候选 |
| 只改前端图标/只补说明 | 只使适用证据失效，不重跑无关本地后端全量；最终SHA CI要求仍满足 |
| 单行租户过滤/公共fixture变化 | 不能归为小改，覆盖拒绝/旧调用方并完整最终回归 |
| 选测为空、未知路径、收集失败 | 不算通过；报告原因，必要时退回全量 |
| 审查耗尽配额或超预算 | incomplete并列缺口，不称NO_FINDINGS或自动无限重试 |
| RAG连接故障但Backend通过 | RAG job失败且阻断交付，单独重跑，不重装/重跑Backend |
| required检查跳过/缺失/旧SHA/取消 | 不算最终成功，不自动done |
| CI已绿且已有业务接受 | 只做状态对账，不再要求相同操作或关闭口令 |
| docs含执行代码、Prompt或消费型JSON | 不能被文档路径豁免自动跳过 |
| 外部PR/无费用授权 | 不读取或外发密钥，不自动执行真实模型，不以跳过真实检查写通过 |

实施时建议命令（下列新脚本/测试尚不存在，不代表本轮执行过）：

```powershell
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -X utf8 -m pytest tests/test_validation_runner.py tests/test_validation_isolation.py
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -X utf8 -m ruff check .
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -X utf8 -m mypy app/
# A改变测试设施，首次验收仍须本地完整回归，使用新的唯一run目录和隔离库。
D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -X utf8 scripts/validate_delivery.py full
```

前端执行输入变化时在frontend执行npm run typecheck、npm test、npm run lint、npm run build；依赖安装沿用lockfile。Linux/CI在同最终SHA执行对应设施反例及完整pytest。检查设施自身必须先按现行更严格规则验收，不能用尚未批准的新规则自证通过。

两个流程技能变更增加授权、审核预算、顺序、有效证据复用与收尾案例。结构检查与Agent情景实跑分别标记，不能只凭YAML存在宣称行为通过。

## 11. 成功标准、监测与回滚

### 11.1 立即可验证

- 普通未发生新故障的候选，本地不再无条件做整套全量；完整最终pytest由CI承担。
- 同工作区同时运行的最终全量为0或1；不再出现未处理的相同失败导致的重复启动。
- 缓存/目录基础设施问题在预检或首次明确异常止损，留原始错误，不复现1165个同源ERROR。
- 真实评测失败只重跑对应job，已通过离线回归证据保留；所有必需门禁最终仍须通过。
- 审核无结论能明确区分配额/网络/未覆盖；不再无条件发送整份台账/manifest。
- CI新结果进入当前交付对账；旧“待CI”文字不再被当成实际状态。

### 11.2 三项任务试用

直接在原记录记：开工、首次专项通过、审核开始/结束、候选固定、push、CI开始/结束、业务接受、最终关闭；各检查执行次数、失效原因和等待类型。缺时刻写未知；观察验收后7天缺陷/重开，不新建报表平台。

建议目标：正常本地反馈尽量在5分钟内、确定性CI在15分钟内；均为试用目标，不是硬性取消长任务的阈值。通过新流程前后相近复杂度任务比较，不以代码行数、文档数或done比证明提速，不把并发进程时间相加当用户等待。

### 11.3 风险与回退

选测漏调用方由最终完整CI兜底，并把遗漏回归补回专项。锁/目录隔离失败时退回唯一人工验证命令并记录，不能恢复使用已知坏共享目录。审核预算不足不自动通过，需要补齐真实独立审查。CI拆分受远程权限阻断时保留旧阻断链。

按工作包的具体diff回退，不重置其他用户改动；不删除测试日志、冻结评测报告或业务数据。分支保护迁移反向操作先恢复旧阻断链，再移除新必需check，避免回滚失去保护。任何取消/重试只处理本轮自身进程/指定job，不取消其他任务，不改变业务请求预算。

## 12. 本轮交付与待批准事项

本轮仅新增本文。未修改AGENTS、tasks、业务/测试、依赖或CI，未推送、重跑真实评测、设置远程保护或关闭FEED-001。语法、引用、范围及一致性检查结果在最终交付说明中列出。

实施前需明确批准：第2节的最终CI承担全量及证据复用政策；A—C范围；必要的提交/推送；独立审核外发范围；真实评测的provider/模型/次数/费用预算及单次重跑；远程required checks变更由具备权限的负责人执行。可以只先批准A/B，C缺权限时保持兼容态；授权不能由本方案文本自行产生。

GitHub技术依据为第7节所链接官方文档；项目事实以本轮已查提交、日志和Actions API为准。方案目标和估算均未实测，实际效果须在自然业务任务中验证。

## 13. 本轮实施清单与记录

用户授权本轮调整，试用卡明确为NFR-011、FE-001。登记VAL-001统一交付A/B/C兼容阶段；先取得本地工具专项与首次完整回归，再逐卡试用。远程保护读取403，保留旧Backend RAG步骤并新增独立评测，不宣称远程保护完成；不提交/推送或产生真实费用。

### VAL-001：本地验证、规则与CI兼容调整

目标：实现第5节执行器和唯一资源，按第4/6节同步规则与技能，第7节只交兼容CI。
允许范围：scripts/validate_delivery.py、tests/conftest.py、tests/test_validation_runner.py、tests/test_validation_isolation.py、.github/workflows/ci.yml、AGENTS.md、.cursor/rules/00-global.mdc与07-testing-evaluation.mdc、README.md、tasks.yaml、本文、docs/workflows/end-to-end-development.md、docs/checklists/review-and-release-checklist.md、docs/ai-prompts/skills/task-card-delivery/与module-batch-delivery/。一次性日志位于data/validation/，不提交。
非目标：不移除原RAG门禁、不调整阈值/依赖、不自动管理远程保护，不迁移全部历史docs测试。
验收：唯一目录和库、全量互斥、无效选择拒绝、失败/中止/指纹变更不通过；Ruff/mypy及首次本地完整pytest；Linux与最终CI未完成则in_review。记录真实baseline与候选指纹，不冒充完成。
基线：b4efcc5d3e4493c12a73338c1fdca7fb848931be，分支codex/eval-001-report-query；已有改动仅本文未跟踪。1973299到HEAD只变更说明与台账，现有Ubuntu CI作为此前代码基线；本地Ruff/mypy与设施相关专项另跑，不把CI误称Windows证据。

### NFR-011：Embedding健康元数据试用

目标：在既有health增加本地配置/实际注入provider的模型、维度、Mock与可用模式，不发网络请求、不改变生产缺密钥行为。
方案：factory提供不调用embed的只读状态函数；health复用该函数并保留既有整体连通性语义。配置身份与可观测provider身份分开，未知自定义provider不伪造真实身份。报告只声明configured/override/mock/unavailable，不以健康元数据证明线上网络或检索质量。现有评测报告已含模型/维度的部分核对复用，不额外重跑真实调用。
允许路径：原卡health/embeddings/config/tests/tasks，加本轮批准的必要README、docs/swagger.json、产品需求第六章及本文（同步现有API与NFR文档契约，不改权限）。
验收：Mock显式、开发缺密钥、配置真实provider、生产缺密钥、注入provider及不可用状态有契约断言，不调用embed、不泄露密钥/endpoint；现有health/Embedding回归通过。最终CI及用户接受另记。
非目标：不改Embedding模型/索引/启动fail-fast，不调用真实模型。

### FE-001：403页面试用

目标：已登录但无页面权限者显示完整403页，未登录仍去登录页；保持既有权限矩阵与有权用户路径。
方案：沿用后端权限镜像can与现有RequirePermission，统一到有返回聊天按钮的403页面；管理页面自身的既有角色拒绝也展示同一页面，避免只改变部分路由。相关路由包含users/tenants/status及其他已有资源守卫，按真实页面权限核对而不粗暴拦截所有非系统角色。
允许路径：原卡frontend/src/tests/tasks，加本文用于单份交付记录。验收：无权角色显示403、有权角色正常、未登录回登录、页面可返回，前端四项及适用浏览器走查通过；不改后端授权。
非目标：不改变权限矩阵、不新增管理能力，不将前端403当后端授权保障。

### 实际进展

实施中。各卡分别记录专项/审核/最终验证时间；本轮不把文档或测试数量当提速证据。

### 验证核对（2026-10-22 续）

对照交接记录、方案第 5、10、13 节和当前工作区复核。本轮没有启动完整 pytest，也没有修改规则、CI、NFR-011 或 FE-001 的业务代码。

工作区与交接一致，另外存在交接文档本身：

- HEAD `b4efcc5d3e4493c12a73338c1fdca7fb848931be`，分支 `codex/eval-001-report-query`。无 `data/validation/full.lock`。
- 已修改：`tasks.yaml`、`tests/conftest.py`。未跟踪：本方案、交接文档、`scripts/validate_delivery.py`、两份设施测试。
- 用当前 `input_snapshot(["backend"])` 对比 `data/validation/20261022T065816-f7cfdce0e6d9/result.json`：543 项指纹的新增、删除、变化都是 0。同目录 `pytest.log` 为 20 passed、7.47 秒。解释器为 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，Python 3.12.0，平台 win32。
- 这份证据只证明设施专项仍对应当前执行器输入。它不包含全库 Ruff、mypy、完整 pytest、Linux 或最终 CI。

方案第 10 节与当前执行器的差异：

| 验收场景 | 核对结果 |
|---|---|
| 唯一目录和独立库 | `tests/conftest.py` 使用 `run-<uuid>`；预检在本轮目录建探针库。已有测试。 |
| 同工作区全量互斥 | `O_EXCL` 锁和陈旧锁不自动删除已有测试。第二次全量在 CLI 中变成笼统的 `infrastructure_error`，响应里没有锁文件中的 pid、run_id 和路径。 |
| 中断 | 子进程超时记为 `timeout` 且退出码非 0，已有测试。执行器自身被终止时，`result.json` 可以停在 `running`，没有 `interrupted` 记录的反例。 |
| 输入变化 | 每条命令结束后比对指纹。`summary` 只在返回值里把已变化的 `passed` 改为 `invalidated`，不回写 `result.json`。没有运行中修改源码的 CLI 反例。 |
| 空选择和非法路径 | 空选择、路径穿越、缺失文件和外部文件有测试。`summary --run data/pytest-tmp` 退出码 1，同时抛出未捕获的 `ValueError` 堆栈。 |
| 证据字段 | 环境只记录解释器、Python、平台和库路径，没有依赖版本、extra 和基线 SHA。 |
| 运行期同源权限错误 | 只靠预检。pytest 输出中的重复 `PermissionError` 不会提前停止。 |
| 规则与审核预算 | `AGENTS.md` 和第 7 节相关 Cursor 规则未改。第 2 节两项新政策尚未生效，不能用 CI 代替本地全量来关闭 VAL-001。 |
| CI 与远程保护 | `ci.yml` 哈希与上述专项证据一致。RAG 门禁仍在 Backend job 内，没有独立 RAG job，`setup-python` 没有 pip 缓存。远程保护保持未核验。 |
| 最终 SHA 汇总 | `summary` 只读本地 `result.json`。 |

快照还会漏掉方案要求纳入的一部分可执行文档。`docs/ai-prompts` 在指纹内；`docs/reviews`、`docs/evaluations`、`docs/plans` 中的 Python，以及 `docs/export_swagger.py`，不在指纹内。这些文件变化不会使当前后端证据失效。

NFR-011 仍为 backlog，设计与现码核对后保持可实施，并修正交接里的一处范围描述：

- `/health` 已返回 `database`、`vector_store`、`llm`、`tools`。`vector_store.embedding_index` 已包含登记索引的 provider、model、dim。这是索引身份，不是进程里实际生效的 `EmbeddingProvider`。
- 无密钥的开发环境会构造 `MockEmbeddingProvider`，`model` 为 `mock-embedding`，维度来自配置。生产环境无密钥仍抛 `RuntimeError`。注入入口是 `set_embedding_override`。
- 试用应新增只读可用性，并与 `embedding_index` 并列；配置或登记模型不能写成实际 Mock。现有评测报告已有 `embedding_model` 和 `dim`，核对时复用，不另跑付费评测。

FE-001 仍为 backlog。`RequirePermission` 在无权时进入 `/chat`，目前包住评测、对话、知识库、智能体、工作流和技能。`/users`、`/users/new`、`/tenants`、`/tenants/new`、`/status`、`/audit` 没有这层守卫，页内拒绝仍是 `EmptyState`。`canViewAudit` 包含 `system_viewer`，`canManageTenants` 只有 `system_admin`。统一 403 页必须同时覆盖这两类入口，并保留审计员的审计页。

结论：方案第 13 节的路线可以继续实施。VAL-001 保持 `in_progress`。20 项设施专项不能写成完整交付。下一步按交接第 7 节，先补占用信息、非法路径的干净失败、输入变化和中断反例，以及证据字段。

### 执行器边界补齐（2026-10-22）

已补上上一节列出的执行器缺口，并用新的专项证据替换此前 20 项记录。旧目录 `data/validation/20261022T065816-f7cfdce0e6d9` 仍保留，但不能关闭这次改过的执行器。

- 第二次全量返回 `lock_held`，带出 pid、run_id、启动时间和锁路径；陈旧锁不删除，也不结束锁里的进程。
- 非法 `summary --run` 以退出码 2 和错误说明返回，不输出堆栈。
- 命令结束后的输入变化会把本轮标为 `invalidated`。`summary` 发现已通过记录的输入变化，或发现 `running` 记录里的进程已经结束时，把状态分别落成 `invalidated`、`interrupted`；进程仍在或无法确认时不改原文件。
- 同一条 `WinError 5` / 旧 pytest 临时目录权限错误在日志中达到 5 次时，结束本轮子进程并记为 `infrastructure_error`。普通业务断言里的单次 `PermissionError` 不走这条止损。
- 后端指纹纳入 `docs/**/*.py`。前端指纹纳入 svg、png、ico；这些图标变化不改变后端指纹。
- 环境记录解释器、Python、平台、库路径、pytest/ruff/mypy/sqlmodel 版本、`pyproject` 声明的 extra，以及 `git rev-parse HEAD` 和是否有未提交改动。

验证：

```powershell
$env:PYTHONUTF8='1'
$env:RUFF_CACHE_DIR='data/validation/val001-lint-cache'
& D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m ruff check scripts/validate_delivery.py tests/test_validation_runner.py tests/test_validation_isolation.py tests/conftest.py
& D:/DepTooL/anaconda3/envs/ai-assistant/python.exe scripts/validate_delivery.py targeted --test tests/test_validation_runner.py --test tests/test_validation_isolation.py
```

Ruff 退出码 0。专项运行 `data/validation/20261022T072849-01a911d93b53` 退出码 0：收集 3.094 秒，pytest 7.25 秒，30 passed、4.34 秒。解释器为上述 conda Python 3.12.0，平台 win32。记录版本为 pytest 8.4.2、ruff 0.8.4、mypy 1.14.1、sqlmodel 0.0.22。pytest 的安装版本高于 pyproject dev extra 所钉的 8.3.4；这次只记录实际环境，不调整依赖。修订为 `b4efcc5d3e4493c12a73338c1fdca7fb848931be`，工作区 dirty。输入变化仍是每条命令结束后检查，不是运行中实时中止。执行器自身被强制结束后，要等下一次 summary 才能把残留的 `running` 改成 `interrupted`。

规则、CI、全库 Ruff/mypy、完整 pytest、Linux 和两张试用卡都还没做。VAL-001 继续 `in_progress`。

### 用户目标与规则同步（2026-10-22）

用户确认主题是用 AI 加快可验收交付并保持质量，而不是继续加治理文档。认可四条，并决定存量无用文档这轮只停止新增：

1. 先改验证和审核规则，减少空转和审核额度浪费。
2. 用 NFR-011、FE-001 在现有记录里记墙钟、检查次数、审核是否有结论、有没有再写无用文档。
3. 新任务不另写计划/实现/关闭三套说明；旧文档不批量删除。
4. 不以文档行数、done 比例或规则变短证明提速。

已把第2节两项政策写入 `AGENTS.md` 第9.4、10、11、13节，并同步 Cursor 全局/测试规则、台账策略、端到端工作流、评审清单和两个交付技能。VAL-001 仍是验证基础设施首次变更：新规则允许后续业务卡把完整 pytest 交给最终 SHA 的 CI，本卡仍须本地全量，不能用尚未跑过的 CI 关闭。

本段写在已有方案里，没有另建计划、实现说明或关闭文件。

### CI 兼容阶段（2026-10-22）

已改 `.github/workflows/ci.yml`，停在兼容态：

- Backend 仍无条件执行原来的 RAG baseline 和 gate，脚本、数据、阈值和 `EMBEDDING_API_KEY` 范围未改。
- 新增并行 job `RAG official baseline`。默认 `if: vars.ENABLE_RAG_OFFICIAL_JOB == 'true'`，变量未设时跳过，push/PR 不会重复付费。打开该变量才会跑一次迁移重复，远程 required checks 仍未核验前不得删 Backend 内旧步骤。
- 三个 Python job 的 `setup-python` 增加 pip 缓存，键覆盖 `requirements.txt` 和 `pyproject.toml`。安装命令和 extra 未合并、未删减。
- Backend 上传 pytest JUnit，RAG job 上传 baseline JSON，Agent 仍上传原目录，保留 14 天。不上传数据库或密钥。Frontend 仍只有 Actions 日志，没有单独 junit 产物。
- 触发事件仍是 push 和 pull_request。没有改 concurrency，没有宣称远程分支保护已生效。本轮 GitHub 连接器不可用，沿用交接时 main 分支保护 403 的未核验结论。

验证：

```powershell
$env:PYTHONUTF8='1'
$env:RUFF_CACHE_DIR='data/validation/val001-lint-cache'
& D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m ruff check scripts/validate_delivery.py tests/test_validation_runner.py tests/test_validation_isolation.py tests/conftest.py
& D:/DepTooL/anaconda3/envs/ai-assistant/python.exe scripts/validate_delivery.py targeted --test tests/test_validation_runner.py --test tests/test_validation_isolation.py --test tests/eval/test_agent_eval_runner.py --test tests/eval/test_agent_gate_entry.py --test tests/eval/test_agent_ci_endpoint_capture.py
```

Ruff 退出码 0。专项 `data/validation/20261022T080135-6e085e40b184` 退出码 0，96 passed、66.73 秒。未推送，因此没有这次改动的 GitHub run。未设置 `ENABLE_RAG_OFFICIAL_JOB`，未调用真实 Embedding。

### VAL-001 本地全量（2026-10-22）

验证设施首次改动按第10节完成本地 full，未改运行中的源码。墙钟 16:10:57—16:24:58（Asia/Shanghai），约 14 分钟。锁在结束后释放。随后 `summary` 仍为 passed，退出码 0。

```powershell
$env:PYTHONUTF8='1'
& D:/DepTooL/anaconda3/envs/ai-assistant/python.exe scripts/validate_delivery.py full
```

运行目录 `data/validation/20261022T081057-88d6a61997db`。解释器为上述 conda Python 3.12.0，平台 win32。库为该目录下 `pytest.db`。HEAD `b4efcc5d3e4493c12a73338c1fdca7fb848931be`，工作区 dirty。

| 检查 | 退出码 | 耗时 | 结果 |
|---|---|---|---|
| collection | 0 | 10.297 秒 | 收集 3365 项 |
| pytest | 0 | 771.641 秒 | 3362 passed、3 skipped、5 warnings、765.62 秒 |
| ruff check . | 0 | 0.265 秒 | All checks passed |
| mypy app/ | 0 | 56.953 秒 | 231 个源文件无问题 |

3 项 skip 均为 Windows 上预期的 Linux 文件系统用例：`test_init_refuses_symlink_without_touching_target`、`test_init_file_permissions_are_private`、`test_write_failure_does_not_delete_replacement_file`。没有失败，没有同源 PermissionError 洪泛。这是本机 Windows 全量，不能替代 Linux 或最终 SHA 的 Ubuntu CI。NFR-011、FE-001 尚未开工。VAL-001 保持 `in_progress`。

### NFR-011 试用（2026-10-22）

用户确认试用可开始后，按新规则只跑专项，不另写计划/实现/关闭文件。墙钟约 16:45—16:51（Asia/Shanghai）。检查 3 次：Ruff、mypy、隔离专项；另按卡片命令补了一次 `pytest -k health`。实现者自审覆盖 health/factory 与现有 health 调用方，未外发独立审查（L1）。没有新增计划、实现说明或关闭文件。

实现：`embedding_availability()` 只读报告 mode/provider/model/dim/is_mock，不构造真实提供商、不 embed、不联网；`/health` 增加并列的 `checks.embedding`。开发缺密钥为 mock 且 `quality_evidence=false`；合成密钥走 configured 且构造器被断言未调用；生产缺密钥仍 `RuntimeError`，health 为 unavailable 且不改整体连通性；未知注入不冒充配置模型。官方检索报告复用已有 `embedding_provider/model/dim`，未付费重跑。

| 检查 | 退出码 | 结果 |
|---|---|---|
| ruff check factory/health/test_health | 0 | All checks passed |
| mypy 上述两处 app 模块 | 0 | 2 files, no issues |
| targeted `tests/test_health.py` `20261022T084923-92dd438dec77` | 0 | 14 passed，pytest 4.734 秒 |
| `pytest -k health` | 0 | 25 passed / 3347 deselected，7.80 秒 |

解释器 conda `ai-assistant` Python 3.12.0，平台 win32。HEAD 仍为 `b4efcc5d3e4493c12a73338c1fdca7fb848931be`，工作区 dirty。未跑完整 pytest、Linux 或 GitHub CI；未改生产 fail-fast；未调用真实 Embedding。本机已启动进程的 `/api/v1/health` 含 `checks.embedding`（configured / quality_evidence=false / connectivity_verified=false），未出现密钥或 Base URL。NFR-011 保持 `in_progress`，不能 done。

### FE-001 试用（2026-10-22）

紧接 NFR-011。墙钟约 16:51—16:54（Asia/Shanghai）。检查 1 次隔离前端四项（typecheck/test/lint/build）。实现者自审覆盖路由守卫、管理页拒绝分支与权限函数，未改 `permissions.ts` 矩阵。没有新增计划、实现说明或关闭文件。

实现：新增完整 `ForbiddenPage`（403 文案、说明、返回对话）。`RequirePermission` 不再静默跳到 `/chat`；`/users`、`/tenants`、`/status` 走平台管理员守卫，`/audit` 保留 `system_viewer`。各管理页与评测无权分支渲染同一 403 页。

| 检查 | 退出码 | 耗时 | 结果 |
|---|---|---|---|
| frontend typecheck | 0 | 0.766 秒 | 通过 |
| frontend test | 0 | 4.281 秒 | 23 files / 181 passed |
| frontend lint | 0 | 3.735 秒 | 通过 |
| frontend build | 0 | 11.672 秒 | 通过 |

运行目录 `data/validation/20261022T085324-3a6536cc424c`。浏览器：未登录打开 `/users` 回到 `/login`。没有可用的已登录 member 会话，未在真实浏览器点开 403 页；该路径由 SSR 路由测试覆盖。未改后端授权。FE-001 保持 `in_progress`，不能 done。
