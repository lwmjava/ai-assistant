# PAGT-005 独立工程计划审查

日期：2026-10-13。审查 Agent：`/root/pagt005_plan_review`。身份：仅计划审查，未参与实现。依据：原始 `tasks.yaml` PAGT-005（4792–4800 行）、AGENTS.md、04-skills.mdc、ADR-0004、`plan_pagt_005_20261013.md` 与实际 Skill/Chat 调用。完整读取 `C:/Users/123/.agents/skills/gstack/plan-eng-review/SKILL.md`，按其四类工程维度审查；子 Agent 模式不向用户单独发交互问题，由主 Agent 汇总尚未批准的设计。只修改本报告，不改计划、业务代码或技能外部配置；未运行 pytest，没有测试通过声明。

结论：**DONE_WITH_CONCERNS，方案可收紧后提交用户批准，当前不能直接实施。** 原始卡的“版本号变化”与保留 DB/API 声明版号之间存在需明确批准的语义选择。另有四项必要设计细节，均可在当前允许范围完成，无须增加数据库或 UI。

## Step 0：范围与已有实现

复用 SkillManifest/SkillContext、YAML loader、SkillManager 的匹配/注入链；复用 `manifests_for_chat` 的全局/私有可见集及 ChatService 单一匹配与不可信围栏。不要新建 Skill Service、数据库版本链、认证层或独立运行时。标准库 SHA256 + canonical JSON 足够，无须新依赖。TODOS.md 不存在，不创建另一个任务入口；无需分发新应用工件。

方案应控制在现有 base/loader/manager 加一个纯版本/账本模块；元数据使用简单记录，不引入超过两个独立业务服务。测试/规格/Evaluation 文件不计业务架构复杂度，但最终文件范围仍逐项核对。Git 历史中已有技能私有范围实现（`5cdd9b6`）与导入整理；本卡不得重新裁决 ADR-0004。

## 架构审查

1. **[P1，方向待批准]（confidence 10/10）原卡 acceptance 与计划“推荐 A”：明确哪一个版本号发生变化。** `SkillService.update_skill` 不改 `row.version`，`_row_manifest` 原样带入，而 ChatService 每次新建 Manager。新增 effective_version 不会让现有技能详情/列表的 version 变号。runtime 内容身份的解释合理，但不能在没有批准时声称与“修改技能内容后版本号变化”完全等价。

   - **1A 推荐**：批准“发布 version 保持兼容；内容更新自动改变实际执行 effective_version，上下文及安全激活元数据可观察；DB/API 发布版号自动递增不在本次实现”，并将批准原文关联原验收项。
   - **1B**：若用户要求原 API version 自动递增，申请 Service 及契约同步范围后设计，不能当前范围内伪装完成。
   - **1C**：仅给内容哈希但不在实际选用入口暴露，不满足本卡，拒绝。

   使用实际 HTTP 更新技能，提交后新 Session 读取，下一次 Chat 选择；验证前后 effective_version 不同而发布 version 保持兼容。明确 runtime history 是有界进程/Manager 元数据，不是数据库持久版本历史。内容修订可证明“哪份内容被执行”，不能单独证明“谁在何时修改”；已有写入审计/Git 负责后者，测试需要同时核对其实际记录。

2. **[P2]（confidence 9/10）Manifest 可变对象与缓存版本可能脱钩。** 当前 register/get/list_all/match 传递同一 dataclass 引用；Service 还会修改 origin。若 register 时算一次 hash，调用方后来改 prompt/trigger/list，实际注入可能属于不同内容，history 却没有记录。

   - **2A 推荐**：register 校验并深复制已完成 Service origin 赋值的 Manifest；get/list_all/match 返回隔离副本；activate 再对实际用于注入的 match 内容计算/校验版本，不能接受调用者伪造的 content_revision/effective_version；上下文同时保存对应身份。
   - **2B**：全面 frozen dataclass 会碰到 Service 的 origin 赋值兼容，不推荐。

   多技能只记录通过 `is_match` 的实际激活集合；confidence 的计算也应基于该集合。旧匹配对象跨 register/reload 激活，应明确是允许执行旧版本并准确标记，还是拒绝 stale match。推荐保持兼容、执行副本所代表旧内容并准确标记，不静默改成当前最新版。scope/origin 属信任语义，至少纳入独立元数据绑定；content_revision 是内容身份，不作为租户资源 ID 或授权凭据。

3. **[P2]（confidence 8/10）内置发布账本须区分持久发布门禁与临时 runtime 注册。** 当前 discover_skills 接受外部目录并可覆盖同名技能；默认 Manifest.origin 为 builtin。若仅按名字/origin 强制套正式账本，外部同名文件会被当正式内置；若一律不验证账本，改内置却不升版的门禁容易成为仅检查当前 hash 的自证。

   - **3A 推荐**：提供显式纯账本校验入口，将被校验目录与账本一起绑定；正式回归验证受版本控制的内置目录，不将所有 runtime register 强行当发布。临时复制目录演练必须显式传同一副本目录及其账本。
   - **3B**：任意外部文件自称 builtin 即套正式账本或提高信任，不接受。

   账本必须拒绝同一发布版本对应多个 hash、发布号倒退/重复、末条与当前 YAML 不一致、漏技能及未知技能；新增发布记录须保留已知历史前缀，可通过旧账本作为输入比较新候选。只凭两个可同时改写的当前文件，不能证明历史从未被重写；持久历史完整性由 Git/评审与历史前缀校验共同承担，不宣称防篡改账本。版本比较用数字段而非词典序（1.10 > 1.9），旧两段 1.0 规范化后比较，不能靠改标签而 hash 不变声称内容升级。

## 代码质量审查

4. **[P2]（confidence 10/10）loader 吞错与 reload 原子保留必须形成显式契约。** 当前 `_load_one` 捕获错误后返回，discover_skills 只返回有效子集；`reload` 先 clear 再默认 load，无法区分“合法删除全部文件”和“全部文件解析失败”。仅去掉 clear 仍会被空结果覆盖。

   - **4A 推荐**：保持 Service 所用普通发现器的安全跳过兼容；Manager load/reload 使用有失败信号的严格发现方式，先完成全部解析、验证和候选状态，再一次提交 skills/history/目录配置；任何文件/目录失败则旧可用集和历史保持不变。
   - **4B**：失败时部分替换或回到默认目录，会静默改变技能，不接受。

   输入验证至少覆盖直接 SkillManifest 注册和 YAML 两入口：字符串版本/长度/规范、name 长度/字符、枚举值、list 元素类型、严格 bool（不能 bool('false')）、trigger 字典、有限 0..1 置信度、非法 regex、安全编码失败；不能 str(None) 或 str(dict) 掩盖坏值。重复 YAML 键应拒绝以避免操作者意图与实际值不同。错误仅固定代码和受限技能身份，日志不得输出 YAML 原文、正则原文、异常堆栈或绝对路径。Loader 失败从日志可见，Manager reload 则向调用方明确失败。

## 测试审查：真实入口与分支图

pytest 为既有测试框架。此报告不跑测试，正式失败边界需先红后绿；不使用 helper 通过数替代验收。

```text
内置/临时 YAML + 指定目录/账本
  -> 安全解析/类型验证
     -> 失败: 安全错误 + reload 旧集合/历史/目录不变
     -> 成功: canonical 内容身份 + 发布门禁
        -> 未升版/复用旧号/错 hash/改旧前缀: 明确拒绝
        -> 合法追加: 新 Manager 读取相同持久内容/执行版本
  -> Manager register(copy)
     -> 无变化: history 不增
     -> 内容改变/回退/来源变化: 有界安全变更元数据
  -> match(copy) -> is_match 判定
     -> 未激活: 不记录为执行
     -> activate: 使用实际副本内容 + 一致版本 -> inject

真实 HTTP 创建/更新 -> DB 提交 -> 新 Session
  -> Service 身份/租户/owner 可见性过滤
  -> 新 Manager -> 单一匹配 -> activate -> private/global 围栏
  -> Chat Provider 收到对应 Prompt
  -> 安全激活版本元数据 + 既有写入审计可比对
```

必加反例：同版本变 Prompt；关键词/置信度/工具/模型/启用变化；仅路径/映射顺序/注释变化；入参及 get/list/match 的嵌套修改；register 后旧 match 激活；多 match 部分未达阈值；history 溢出/返回值篡改；正常删除与加载失败区分；目录失败/坏 YAML/部分文件失败；旧 release 复用/倒退/重复/历史重写；两租户同名不同内容/同租户不同 owner；global 与 private 同名覆盖；外部 YAML 与正式 builtin 身份区分；合成敏感正文及路径不进日志。每项验证最终可用集合/注入内容/版本元数据，而不只 assert raises。

内置 code_review 与 translator 回归应验证真实 YAML 的触发、未匹配、Prompt 中现有约束和输出要求进入真实 Pipeline 消息。缺代码的友好询问约束存在，但若用合成 Provider，只能证明约束传递，不能证明真实模型正确询问；翻译 YAML 没有显式缺原文/缺目标语言分支，不得文档写成现已自动拒绝。L2 权限必须实际 Executor 拒绝并确认没有执行副作用，不能靠 Prompt 文本。

## 性能审查

5. **[P2]（confidence 8/10）history 条数有界不等于整体内存有界。** 按 skill name 分桶各保留 N 条，动态注册大量名字仍无限增长；如果在每次日志/属性访问重新编码过长 Prompt，也会扩大现有按请求发现开销。

   - **5A 推荐**：Manager 全局固定上限的 history 队列（如 100 条），仅保留元数据；输入文本/集合总规模有限，注册/激活快照各计算一次而非每行日志重新 canonicalize。
   - **5B**：每技能独立无限桶或保存历史 Prompt 正文，不接受。

不新增数据库查询，不在每次 Chat 做 Git subprocess，不引入线程/文件监控/跨进程缓存。正式账本一致性属于回归发布门禁，若运行时校验也启用须明确目录绑定及失败策略。没有生产性能数字，不编造达标指标。

## 失败模式与错误可见性

| 路径 | 现实失败 | 必须测试/错误处理 | 可观察结果 |
|---|---|---|---|
| YAML 读取/编码 | 文件损坏或无权限 | loader 安全错误，reload 原子保留 | 调用方失败；旧技能继续可用 |
| 注册 | 非法数值或列表/对象变异 | 双入口验证与隔离副本 | 无部分覆盖，版本与内容一致 |
| 发布 | 忘升号/改旧账本 | 真实文件门禁/历史前缀 | 回归失败，不能发布 |
| 选择/激活 | stale match 或同名覆盖 | 快照身份断言 | 实际执行版本准确，无租户内容混合 |
| 观测 | 原始异常包含秘密 | 日志捕获检查/固定错误 | 无正文、秘密及路径泄漏 |
| 历史 | 多名称洪泛 | 全局容量/副本 | 元数据有界，明确非持久历史 |

设计尚未收紧的五项属于待决/测试缺口，不是已实现代码缺陷；收紧后仍必须独立反例运行，不据此报告验收通过。

## 不在范围与执行安排

DB 持久版本链、历史全文重放、API/UI 自动升声明版号（取决于 1A 批准）、技能市场、租户授权重写、真实模型翻译/代码审查质量及全局 Trace 持久化不在当前推荐实施；前三项语义差异必须用户明确知悉，不用非目标避开原要求。

主实现 Lane A：版本模型 -> loader/manager 整合（共享 skills/，串行）；独立验证 Lane B：正式入口用例/规格与 Evaluation（tests/、docs/，等待 A 的接口定义）；发布账本内容与版本工具归 A，避免同模块并发冲突。pytest 共享 DB 时只按主 Agent 时隙串行运行。无需新增 worktree 或外部模型调用。本 Agent 不再派生审查者，后续入口反例及最终人工审核由主 Agent 按技能安排独立身份。

## 完成摘要

Step 0 范围保留，禁止扩大平台化；架构 3 项，代码质量 1 项，测试图与分支清单已给出，性能 1 项。建议全部采用 A，但**不代替用户批准**；待决 1 项方向语义，其余四项需要主 Agent 明确收入计划。未修改 TODOS、未运行外部 review CLI、未写技能用户配置/遥测/审查仪表盘，原因是本次受委托仅写项目评审报告，当前沙箱没有该外部写入授权。工程结论为待设计批准；本报告不宣称 GSTACK 运行仪表盘已通过，也不启动实现或发布。
