# 开发治理减负实施记录

任务：GOV-003。用户于 2026-10-22 明确授权先修订效能审计建议，再据此修改规则、AGENTS.md 与 tasks.yaml 等文档。

## 目标、现状与方案

规则包含过期台账；默认阅读范围过大；单卡计划、实现说明、关闭记录与多轮审核重复；docs 混有执行工具和原始产物。本次把建议落实为统一的按需阅读、风险分级、单份交付记录、评审退出和文件归属规则。

先修订审计文档的证据与推断边界，再改 AGENTS.md、任务台账元策略、适用 Cursor 规则、指导/治理文档、工作流、检查清单和模板。两个交付技能同步引用统一规则并更新流程 Evaluation。普通任务允许在同一记录中补充实施结果；复杂变更保留 ADR 和独立审查；现有测试、安全、Linux、最终版本与发布门禁保持。

## 范围与非目标

允许路径：AGENTS.md、tasks.yaml、.cursor/rules/00-global.mdc、docs/reviews/2026-10-22-harness效能审计与规则引用整改建议.md、本文、docs/AI辅助开发迭代指导.md、docs/governance/agent-harness-engineering.md、docs/workflows/end-to-end-development.md、docs/workflows/bugfix-and-incident.md、docs/workflows/nightly-autonomous-development.md、docs/checklists/anti-drift-checklist.md、docs/checklists/review-and-release-checklist.md、docs/templates/definition-of-done-template.md、docs/templates/work-breakdown-and-schedule-template.md、docs/ai-prompts/skills/task-card-delivery/、docs/ai-prompts/skills/module-batch-delivery/。

不改业务、正式测试、依赖、配置或 CI；不迁移/删除历史证据、脚本或 ZIP，不批量改写已有任务状态/契约，不创建新的治理平台，不执行真实外部模型、生产操作或 Git 发布。测试发现缺口和通用验证工具列为后续建议，不能宣称已修复。

## 基线与验收

- 基线 HEAD：3fcca05b54ce2acadbf39fb7856e78c77551a101；分支：milvus-default-vector-store；平台：Windows PowerShell。
- 已有改动：用户提供的审计建议未跟踪；其余 git status 干净。原审计作为本轮修订对象，保留来源和未复现数字的说明。
- 原台账：201 卡，141 done / 56 backlog / 3 in_review / 1 cancelled；本轮只追加 GOV-003。
- 检查：UTF-8/YAML 解析、任务 ID 唯一及已有任务逐项不变、依赖引用、改动文件链接、Skill frontmatter、git diff --check、AGENTS.md 第10节逐字不变、授权/门禁/历史约束保留与流程案例对照。
- Python：D:/DepTooL/anaconda3/envs/ai-assistant/python.exe。纯文档及开发流程说明变更，不改产品 Prompt/Skill、测试或 CI 执行输入，业务 Ruff/mypy/pytest、前端四项及 Ubuntu CI 不适用；不据此关闭任何业务卡。
- 所有新增/修改流程案例只验证文档契约与决策一致性；未进行跨宿主或独立模型行为实跑，不宣称实际提速或业务质量提升。

## 实际结果与验证

完成文档契约整改，GOV-003按纯文档例外关闭；其他201卡内容与状态逐项不变。本轮不代表业务CI、真实质量或效率提升已通过。

功能结果：所有遵守仓库规则的Agent可按当前卡/直接依赖读取，普通任务复用一份交付记录；复杂权限/公共执行链/迁移等仍需独立审查。无新相关输入或失败时评审退出；技术审查不称人工审核。历史完成流水账改为有效决定索引，旧指导映射明确历史时点；测试、安全与最终版本门禁保持。新增产物按用途归属，存量迁移先核对调用方。

任务映射：GOV-003包含审计修订、规则路由、工作流/模板同步、两个交付技能及流程案例。单卡技能1.4.0 / 案例v1.2共30条，模块技能1.1.0 / 案例v1.1共20条；保留旧40条授权/失败案例，增加10条减负边界案例。

### 实际修改文件

- `.cursor/rules/00-global.mdc`
- `AGENTS.md`
- `docs/AI辅助开发迭代指导.md`
- `docs/ai-prompts/skills/module-batch-delivery/SKILL.md`
- `docs/ai-prompts/skills/module-batch-delivery/evaluation.yaml`
- `docs/ai-prompts/skills/module-batch-delivery/references/acceptance-gates.md`
- `docs/ai-prompts/skills/task-card-delivery/SKILL.md`
- `docs/ai-prompts/skills/task-card-delivery/evaluation.md`
- `docs/ai-prompts/skills/task-card-delivery/evaluation.yaml`
- `docs/checklists/anti-drift-checklist.md`
- `docs/checklists/review-and-release-checklist.md`
- `docs/governance/agent-harness-engineering.md`
- `docs/plans/plan_harness_workflow_simplification_20261022.md`
- `docs/reviews/2026-10-22-harness效能审计与规则引用整改建议.md`
- `docs/templates/definition-of-done-template.md`
- `docs/templates/work-breakdown-and-schedule-template.md`
- `docs/workflows/bugfix-and-incident.md`
- `docs/workflows/end-to-end-development.md`
- `docs/workflows/nightly-autonomous-development.md`
- `tasks.yaml`

### 验证命令与结果

平台Windows，解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，Python3.12.0 / PyYAML6.0.3，采用UTF-8模式；未加载业务密钥或外部模型。候选为基线HEAD加上述20个文档文件；最终逐文件SHA256和环境记录在本地 `data/validation/gov003/manifest.json`，验证程序及日志也在该被忽略目录，不追加到docs。该目录为本轮一次性验证产物，长期复核以Git diff、正文命令及声明的不变量为准。

| 完整命令 | 结果 |
|---|---|
| `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -X utf8 data/validation/gov003/validate_docs.py` | 退出0；YAML/ID/范围/引用、旧任务对象不变、AGENTS第8/10/12节原文不变、旧案例保留及新增案例结构检查通过 |
| `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -X utf8 C:/Users/123/.codex/skills/.system/skill-creator/scripts/quick_validate.py docs/ai-prompts/skills/task-card-delivery` | 退出0，Skill is valid |
| `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -X utf8 C:/Users/123/.codex/skills/.system/skill-creator/scripts/quick_validate.py docs/ai-prompts/skills/module-batch-delivery` | 退出0，Skill is valid |
| `git diff --check` | 退出0，无空白错误 |

首次quick_validate未开启UTF-8，因Windows默认GBK读取UTF-8失败，两个命令各退出1；修正命令编码后通过。首次diff检查因工作区CRLF与Git基线LF不一致退出1；按仓库基线换行修正后通过，不改Git配置、不绕过检查。Git status有用户全局ignore读取权限警告，仓库级状态/差异查询仍退出0，未修改全局配置。

### 流程案例自审

- 普通说明任务：同一记录与文档核对足够，无子Agent不阻断。
- 单行鉴权变更：仍进入复杂边界审查及公共路径全量回归。
- 阻断项闭合且证据有效：结束评审，不要求新Agent追加审核。
- 仅补说明、相关输入/环境未变：复用有效检查；行为变化则旧报告失效。
- 技术验证通过但业务接受缺失：不得冒充人工或关闭业务任务。
- 无关改进建议：不扩大范围、不阻断已满足验收的任务。
- 批次共享最终回归：可复用同一结果，但逐卡契约与模块整合必须满足。

以上为当前Agent对文档决策契约的自审，不是独立模型行为实跑，不以50条案例文件的存在声称行为测试全通过。

### 未执行、风险与回滚

未执行业务pytest/Ruff/mypy、前端四项、Linux/Ubuntu CI：未改相应执行输入；本轮不关闭任何业务失败。未执行独立模型/跨宿主流程实跑；未迁移docs测试或修复默认收集入口，未建设统一验证执行器，未移动注册表/删除历史证据，旧ZIP未重打包。后续三个自然获授权任务用于观察效能，尚无提速或缺陷下降结论。

回滚按本轮20个文件的diff逐项恢复，保留用户后续修改与原始审计来源，不执行hard reset或删除数据。新增卡和新增交付记录如需回退，与规则一起核对处理。Git提交、推送、合并、部署均未执行。

