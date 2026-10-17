# PAGT-007 独立实施计划审查

日期：2026-10-13。审查 Agent：`/root/pagt007_plan_review`；角色独立于主要实现及依赖复核。

结论：**修订后计划可进入用户具体设计确认；当前没有未决的设计 P1/P2。** 本结论只针对计划，不表示实现、真实模型指标、GitHub CI 或业务验收已通过。未执行模型请求、pytest、发布或仓库配置变更。

## 依据、范围与执行方式

读取 AGENTS.md、任务卡 PAGT-007 原始交付/验收/非目标、task-card-delivery 1.3.0、`C:/Users/123/.agents/skills/gstack/plan-eng-review/SKILL.md`，以及项目迭代指导和 Harness 规范相关入口。读取适用规则 00、01、02、03、07、08、09、10、11。核对实施计划、依赖复核报告、现有运行器、CI、LLM factory 与 OpenAICompatibleProvider 和最近五条 Git 历史。

使用 plan-eng-review 的范围挑战、架构、代码质量、测试流图、性能、失败模式及独立角色审查维度。作为受委派的审查 Agent，不向用户追加互动问题、不运行升级/遥测/技能注入，不修改主计划或业务代码；项目任务范围和主 Agent 的明确文件授权优先于技能默认外部日志写入。仅落盘本报告。没有另行执行不同模型的 outside voice，不声称跨模型一致。

HEAD 为 `a0d02eb411a53c3191e81f0fc99528ea60d0d3c4`，同期工作区存在 PAGT-004/005/006 改动。本次审查对象为 `docs/plans/plan_pagt_007_20261013.md` 的本轮修订稿；实现后须以最终源码指纹重新验证，不能沿用计划结论关闭任务。

主 Agent 已核对并在计划引用 GitHub 官方 Secrets/events 文档，本 Agent读取该核对结果，未重复执行浏览。fork/Dependabot 缺 secret 失败闭合，不通过权限更高的事件绕过。

## Step 0：范围挑战与既有能力

范围接受，不扩张原卡。现有运行器已完成十条分流及两条工具选择预测，现有 Pipeline helpers 可构造真实消息，已有评分语义与冻结数据集；计划复用这些能力。新增的报告校验器、受控退化探针与独立 CI job 分别解决报告可信性、门禁有效性与持续触发，不引入新的运行时架构。

最小交付边界是 runner 增强、gate/probe、CI 和契约测试；不需要新增数据库、模型供应商、Agent Runtime、API 或框架。独立 job 优于串接 backend 末尾，因为现有 lint/测试/RAG 失败不应掩盖 Agent 检查。文档、Evaluation 和独立审查属于项目要求的证据产物，不应以文件数量为由删掉。

## 发现与修订复核

### 1. [P1] 模型身份期待值不能来自待验报告（confidence 9/10）

原计划只描述记录模型与端点哈希、检查错误模型，没有指定校验者的独立期待配置。报告可自述另一个模型和端点，其他字段仍自洽；“记录身份”因此不能证明本次使用批准配置。

推荐 1A：gate CLI 必填 `--expected-model` 和 `--expected-endpoint-hash`，CI 从与 official 相同的非秘密配置独立传入；严格比较并拒绝未知、Mock、fallback 身份，禁止从报告提取期待值。另一选择 1B 是只记录自述身份，成本低但不能闭合错误身份反例，不采用。

复核：修订稿 §4.2 已采用 1A，并规定端点规范化前拒绝 userinfo、query/fragment。**设计已闭合**。实现须测试错误模型、错误端点以及 chat real/intent Mock 的真实 runner/gate 入口。

### 2. [P2] 预测必须与原始回复解析一致（confidence 9/10）

原计划从 reported prediction 复算指标，仍可能接受解析/评分接线出错后写出的自洽报告。只复算正确率不足以发现“raw 为 NO、predicted 为 full”的错误。

推荐 2A：使用当前真实分流及工具解析器从 `raw_reply` / `tool_reply` 重建预测，分别对照明细和汇总，再检查阈值；不另造生产 parser。2B 只复算明细可减少少量代码，但不能封闭接线错误，不采用。

复核：修订稿 §4.2 已采用 2A。**设计已闭合**。正式测试需包含原始回复与预测不一致、工具回复与名称不一致，不能只测汇总被篡改。

### 3. [P2] 受控探针必须因指标失败，不能因格式失败（confidence 9/10）

报告副本若仅改变预测，新增 raw 一致性检查会在指标前拒绝；若 synthetic 标记成为 gate 禁止项，也会因来源拒绝。任意非零退出均算 probe 成功还会把 CLI 使用错误、脚本崩溃当成门禁生效。

推荐 3A：同时改变 raw、预测和汇总；合成性质写目录/独立 manifest，不给正常 gate 增加 synthetic 通过豁免；只从已通过 gate 的新鲜 official 报告派生。probe 必须要求 `exit=1` 且稳定 reason code 命中相应指标，输入/身份/完整性错误为 `exit=2` 并判 probe 失败。3B 仅要求任意非零不满足原卡的退化拦截证据，不采用。

复核：修订稿 §4.3 已采用 3A。**设计已闭合**。route 翻转两条非 followup，followup 翻转一条关键续问，tool 删除一条正例工具；不新增数据集样本或模型请求。探针只证明对应观测退化被门禁拒绝，不能作为质量通过证据。

### 4. [P2] 不可信报告需要尺寸与解析边界（confidence 8/10）

原稿有类型/NaN 检查，但没有报告或模型回复体积上限和重复 JSON 键处理；远端异常内容或畸形报告可能耗尽资源、产生不同读取语义或输出内部错误。

推荐 4A：读写前限制报告 1 MiB、单回复 16 KiB，拒非 UTF-8、重复键、非有限常量及超出固定报告结构深度的对象；输出安全分类错误。4B 只做指标类型检查较省代码，但不能覆盖异常输入失败边界，不采用。

复核：修订稿 §4.2/4.3 已采用 4A。**设计已闭合**。实现时明确 KiB 按编码字节计数；边界值、超一字节、深层 JSON 和重复键要从 CLI 验证。

## 架构与代码质量结论

架构发现 1 项，代码质量/输入安全发现 2 项，均已在设计修订。真实 provider 检查取实际 intent 实例，避免 `llm_availability()` 仅检查 chat 的漏洞；单一真实 provider 限制可以降低元数据歧义，但兼容限制必须明确记录。当前字符串返回接口不能验证服务端响应 model，报告应称“请求模型”。不需要为该 CI 改造改变 app/llm 公共契约。

gate 与 probe 可共用规范化及评分函数，但 gate 不能简单信 runner 写出的指标；必须独立解析明细并复算。错误日志不能输出原始 API 错误、Authorization、Key、凭证 URL。文件创建需排他且只清理自己本次创建的失败文件，避免删除原有证据。

## 测试审查：真实入口流与要求

项目后端采用 pytest。以下为计划中的覆盖要求，**尚未执行，也不标记为已测试**：

```text
push / pull_request
  -> independent agent job
     -> credentials/profile validation
        | missing/fork/Mock/fallback -> safe failure, job red
        -> official runner [E1 real model required]
           | timeout/API/oversize/cancel -> nonzero, no success report
           -> exclusive bounded report
              | existing path -> preserve prior evidence
              -> gate CLI
                 -> expected identity + current hashes
                 -> exact ten cases + safe JSON
                 -> raw parser -> predicted detail -> recomputed metrics
                 | invalid identity/input/integrity -> exit 2
                 | below 0.9/1.0/1.0 -> exit 1 + metric code
                 -> valid official -> exit 0
                    -> three synthetic fault copies [E2 mechanism only]
                       -> same gate CLI -> target exit 1/code required
                       | crash/exit 2/exit 0 -> probe failure, job red
     -> upload only safe scoped evidence
```

新路径和失败验证均需正式回归：

| 路径 | 必须观察的行为 | 证据要求 |
|---|---|---|
| runner official | chat real/intent Mock、未知或 fallback provider 拒绝；无自动 smoke 降级 | 隔离真实代码入口 E2；模型质量仍须 E1 |
| runner 成功/超时/部分失败 | 12 次逻辑请求、每次及整体限时；失败无完整成功报告 | E2 计数与故障注入；真实 official E1 |
| 报告幂等 | 已有文件不改；失败文件仅清理本次所有权；新路径可重跑 | E2 CLI 文件观察 |
| gate 安全输入 | 非 UTF-8、重复键、深层对象、过大体积、NaN/bool/错误类型均退出 2 | E2 CLI，不以 import error 当失败 |
| gate 身份/完整性 | 独立 expected 配置、所有案例及指纹、raw 与预测绑定 | E2 CLI 原始反例 |
| gate 数值 | route 0.9 可通过、0.8 失败；followup/tool 0.5 失败 | E2 CLI，阈值不得降低 |
| probes | 三类合法结构退化因目标指标 exit 1；错误 CLI/损坏报告 probe 自身失败 | E2 全流程，不作模型质量证据 |
| CI | independent、无跳过/fail-open、不注入 install secrets、安全 artifact | workflow 契约补充 + GitHub 实际 run E1 |
| 副作用 | Tool/MCP/RAG/DB 真入口禁止执行；模型 HTTP 请求分开记录 | E2 入口哨兵，不能引用固定零字典 |

测试发现 1 项（探针假成功路径），设计已修订。计划应把以上断言纳入正式回归，不只测 helper。自动验收尚未通过前不安排最终人工审核 Agent；真实 CI 未跑应原样登记未验证。

## 性能与失败模式

无新增数据库访问、队列或并发。既有 provider 直接使用 httpx，没有 SDK 自动重试。串行 12 请求、单次上限 60 秒、整体 780 秒和 job 20 分钟合理，计划不加重试或缓存，避免隐性模型调用与成本。按字节上限拒绝 oversized 回复而非截断，保证 raw 复算不失真。模型服务端身份和 nondeterminism 仍是明确残余限制。

| 失败模式 | 计划处理 | 用户/CI 可观察结果 |
|---|---|---|
| 模型不可用或超时 | safe error + bounded request/overall timeout | job 红、无貌似成功报告 |
| 缺 secret 或意图 provider 为 Mock | official 前拒绝 | 明确非零，不跳过制造通过 |
| 旧/伪造/损坏报告 | 独立 expected 配置、当前 hash、raw 复算及 safe parser | exit 2 分类错误 |
| 行为退化 | 固定阈值及目标 metric code | exit 1、probe 明确验证 |
| 输出路径冲突或取消 | 排他写入、限定清理所有权 | 旧证据保留、可用新路径重跑 |
| artifact 上传失败 | 不忽略原 gate 状态 | 原失败仍保留，上传错误单独可见 |

修订后没有“无测试要求、无错误处理且静默成功”的设计关键缺口；这是计划判断，不是已运行覆盖结论。

## Non-goals 与角色顺序

不扩数据集：原卡禁止；十例仅有限硬退化判别力。不开 RAG/Query Rewrite：不属于本卡。不开分支保护/发布：本卡只交付 check，实际 required-check 设置不可推导。不开新模型接口或供应商：请求模型身份限制已明确。不会自行提交、推送、部署或运行收费模型。

主 Agent 负责 runner/CI；实现 Agent 负责 gate/probe 专属文件；先约定报告协议，再分文件并行。两者均位于 scripts/，模块级存在潜在冲突，禁止同时编辑共享 helpers。测试使用共享 SQLite 必须串行。独立验证及入口审查随后进行；最终人工审核必须新角色、自动关闭对账已通过后启动。

未新增 TODOS.md 项；本卡不得用“后续做”解除 P1/P2 修复义务。现有未提交其他任务不并入本卡提交或证据。

## Completion Summary

- Step 0：scope accepted as-is；复用既有 runner/helpers/数据集。
- Architecture：1 个 P1，设计已修订。
- Code Quality：2 个 P2，设计已修订。
- Test Review：已产出入口覆盖图；1 个 P2，设计已修订。
- Performance：无新增阻断问题，有限调用与报告体积边界已明确。
- Critical gaps：0 个未决设计缺口。
- Outside voice：本 Agent 为独立计划审查；没有额外不同模型审查。
- 决策：推荐四项完整修订均已进入计划，4/4；须由主 Agent 提交具体设计给用户确认。
- 最终状态：**DONE_WITH_CONCERNS（计划审查完成，真实实现与 CI/模型验收未执行）**。
