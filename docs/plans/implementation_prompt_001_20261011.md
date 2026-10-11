# PROMPT-001 实现说明与关闭对账

日期：2026-10-11。原始契约：tasks.yaml 的 PROMPT-001。实施分支：codex/prompt-version-registry；起点：8d96ab3e8a3d6ef3b8011804ae00ba3828eaebc4。

## 当前状态

自动化实现与验证完成，等待人工业务验收，任务保持 in_progress。任务卡的三份历史引用在当前 checkout 缺失，已在计划与独立计划审查登记，不凭不存在的资料补造授权。无前置依赖。

开发/运维人员可直接执行 app/agents/version_registry.py 的 check、snapshot、export-config：校验源码/配置是否与选定登记一致，生成候选登记，导出旧版本非敏感参数用于恢复。当前 prompt-model-v1 覆盖 190 份应用源码/内置 Skill YAML；来源基点为起始 HEAD，源码身份为实际工作树内容摘要。默认模型配置由 AST 读取，不执行 Settings、不读取 .env。模型、provider、路由、关键预算与 Embedding 参数可追踪，端点和能力来源只存摘要，已知密钥/数据库连接串被排除，未知键与非法数字被拒绝。成功返回版本和指纹，失败非零退出且不输出输入原文/内部堆栈；三个入口都不会修改源或登记。

没有改 Prompt 内容、既有配置、模型路由或对话热路径。用户动态技能/工作流模板属于运行时输入，不声称其版本治理完成。没有 A/B、模板平台、生产启用或真实模型质量认证。

## 实际修改文件与任务映射

| 文件 | 对应交付 |
|---|---|
| app/agents/version_registry.py | 源内容/配置版本、元数据和路径校验，候选登记，旧配置导出 |
| docs/ai-prompts/prompt-model-registry.json | 来源、版本、生效时间、回滚说明、190 份源摘要与模型快照 |
| docs/ai-prompts/prompt-model-registry.md | 使用、历史追加、精确来源语义、回滚与秘密管理限制 |
| docs/ai-prompts/prompt-model-registry-evaluation.json | AI 编写 Smoke/Adversarial v1 场景，非 Gold、非生成质量评测 |
| docs/ai-prompts/tests/test_prompt_version_registry.py | 30 条正式回归，含审查修复反例 |
| docs/ai-prompts/tests/test_prompt_version_registry_independent.py | 29 条独立新进程入口回归 |
| docs/plans/plan_prompt_001_20261011.md、本说明 | 实施计划、实际功能、关闭对账 |
| docs/reviews/2026-10-11-PROMPT-001-*.md | 计划审查、缺陷修复计划、最终独立审查和验证 |
| tasks.yaml | 仅本卡状态、证据备注与实际验收命令 |

## 实际分工与技能

- 主 Agent：计划、工作区保护、新分支、原始契约整合和任务卡状态。
- `/root/prompt_implementation`：实现与正式回归；其文件范围为版本工具及 docs 内登记、文档、测试。
- `/root/prompt_contract_review`：独立工程计划审查与最终代码审查，不实现业务代码。
- `/root/prompt_verification`：独立入口集成反例与证据，不修改实现。

实际使用 task-card-delivery 1.2.0；brainstorming 用于方案取舍，按显式调用的单卡技能连续执行常规范围内工作；plan-eng-review 用于计划审查维度，未执行遥测、用户目录脚本或完整交互提问；最终独立审查使用 code-review-quality，遵单卡技能不凑发现数量。

## 修改前验证

解释器：D:/DepTooL/anaconda3/envs/ai-assistant/python.exe。

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -m pytest tests/test_agent.py tests/test_agent_chain.py tests/test_supervisor.py tests/test_llm.py tests/test_llm_route.py tests/eval/test_agent_chain_dataset.py -q` | 1 | 81 passed，1 setup error；共享临时目录 data/pytest-tmp/run 权限错误，未当成业务断言失败 |
| 同上，加 `--basetemp=data/prompt001-baseline-20261011` | 0 | 82 passed，1 既有 ast.NameConstant 弃用警告 |
| `python -m ruff check .` | 0 | All checks passed |
| `python -m mypy app/` | 0 | 188 source files，无错误 |

红灯由实现 Agent 实跑：已有 app/agents/prompts.py 存在，但要求的版本登记文件不存在，正式业务断言失败；不是模块导入错误。最终正式回归应保留可复现的版本登记断言。

## 未验证与关闭门禁

当前用户明确授权新分支和使用技能实施，没有明确把本卡人工业务判断替换为 Agent。按技能 Constraints 原文“仅调用本技能而没有上述明确授权时，不自行取消项目的人工验收要求。自动化工作先完成，人工判断集中为最终最小缺口。”，人工业务验收完成前不将卡标 done。

不自动提交、推送、合并、部署、收费模型调用或生产回滚；用户目前仅授权创建本地分支。

## 最终验证、独立审查与修复

以下命令均用上述解释器实际执行：

| 命令 | 退出码 | 最终结果 |
|---|---|---|
| `python -m pytest docs/ai-prompts/tests/test_prompt_version_registry.py docs/ai-prompts/tests/test_prompt_version_registry_independent.py tests/test_agent.py tests/test_agent_chain.py tests/test_supervisor.py tests/test_llm.py tests/test_llm_route.py tests/eval/test_agent_chain_dataset.py -q --basetemp=data/prompt001-final2-20261011` | 0 | 141 passed，0 skipped，23.95s；1 既有弃用警告 |
| `python -m ruff check .` | 0 | All checks passed |
| `python -m mypy app/` | 0 | 189 source files，无错误 |
| `python app/agents/version_registry.py check` | 0 | prompt-model-v1 / source-defaults，版本与登记一致 |
| `git log --oneline -- app/agents/prompts.py` | 0 | 原任务要求的来源历史可读 |
| `git diff --check` | 0 | 差异格式检查通过；新增文件另经 Ruff/测试验证 |

初轮最终检查出现 6 条独立测试 E501 和一条 AST expr/None 类型错误；修复后上述检查均通过。此前 139 项通过对应修复前中间版本，未拿来替代最终 141 项证据。

独立审查实际构造配置错拼、非对象、坏数值、假 UTC、秘密端点/来源、源码增删与恢复等反例，发现问题按 docs/reviews/2026-10-11-PROMPT-001-review-fixes.md 修复并补回归。F01 未知键拒绝；F02 非对象/非法数字安全失败；F03 严格 UTC；F04 能力来源摘要化；F05 Git 基点与工作树指纹明确区分并核对基点；F06 AST 读取前绑定保护。Windows 无创建 symlink 特权时，独立审查使用隔离临时 junction 实跑越界配置源拒绝，不修改真实源。最终报告及指纹见独立 code-review、verification；未决 P1/P2 为零。

独立验证 29 条均为 E2（新进程、真实隔离磁盘文件与合成配置）；独立审查核对 190 个登记源与起点 Git 内容，missing=0、mismatch=0。模型与端点修改后校验失败，恢复旧非敏感快照和合成秘密端点后校验通过；这证明登记恢复而非真实部署恢复。只读失败时旧源/登记保留，重复检查稳定。未调用真实收费模型，不评价答案质量。

## 关闭对账记录

| 原始要求及出处 | 输入/操作链与可观察期望 | 证据类别、位置 | 结果 |
|---|---|---|---|
| tasks.yaml deliverables：Prompt 来源、版本、生效时间、回滚方式 | 新进程候选登记→保存→check；增删改源失败→恢复旧源→check 通过 | E2 独立测试 source 生命周期、独立验证报告、Git 190 源对账；登记 JSON 和维护文档 | 自动验收通过 |
| acceptance：每次 Prompt 改动可追溯到版本与来源 | 保守全部应用源码发现，Memory/Evolution/RAG 新源不能漏过；版本/摘要与来源关联 | E2 独立新源参数化、源码恢复和基点对账；源码登记限于代码模板，动态业务输入明确另列 | 自动验收通过 |
| deliverables/acceptance：模型配置有版本且可回滚 | 模型/provider/路由/端点变化改变版本；导出旧字段，重组旧合成秘密材料后恢复版本 | E2 独立模型与秘密端点恢复；verification；非敏感字段快照持久化文件 | 自动验收通过；不宣称生产回滚 |
| acceptance：登记文档到位 | 可复现命令、追加历史、默认/部署区别、范围和回滚步骤齐全 | 独立原始契约审查 + E2 实跑文档入口；prompt-model-registry.md | 自动验收通过 |
| 独立审查和五类失败边界 | 最终代码入口反例、190 源 Git 核对、junction 越界失败 | E2 final code-review / verification；helper 类型读前保护另属 E3 | 通过；无未决 P1/P2 |
| AGENTS/技能：人工业务验收 | 负责人判断当前登记粒度、维护步骤与回滚方式满足业务需要 | 无；当前用户未委托本卡 Agent 代替人工判断 | 未验证，tasks.yaml 已原样注明，禁止 done |

## 最终文件指纹与版本

- 工具 SHA-256：BC70BFA04DCA6E7453FE30940ECD021EEDE9EDC293648F6E652D0ADA06CD081C。
- 登记 SHA-256：19FDE5CDB111935818F2DFA4D1D9C7608117F4482B1B0BFD352395577A9C261C。
- 正式测试 SHA-256：CA70F4C39F0AAAFBB5F0A2B6716CB6BBF11117B5FF14A5C9C2A5ED1C50494EE3。
- 独立测试 SHA-256：6662F88B5D6D9BBF8F71BA7AD4EAEA852CF4868884A34242048E30FB798C2D3B。
- source_version：32d7a3a3fa2ff976d6c23b5d23c46d4472c78d97c7ad1525ccf1474b38068192。
- model_version：eef69460f8a0996a9793ec2349e3f8ba66a4919d35b31161f45fe876735efaf8。

## 未执行与回滚

未运行完整全仓 pytest、前端检查/浏览器、外网模型评测：本卡是隔离开发登记工具，没有修改运行期生成或前端；相关 82 项原链回归和新增 59 项足以覆盖变更，页面不适用。docs 下正式测试不在默认 pytest testpaths，必须执行本卡显式命令；没有擅自改 CI allowed_paths。当前检出分支未涉及生产数据、数据库迁移或收费 API。

默认登记不能证明部署环境配置与其相同；部署版本需运维显式提供授权配置和秘密管理版本。生产端点/凭据不能从摘要恢复，必须保有秘密管理备份；真实生产启用/恢复仍需授权，属于本卡证据限制而非本次已执行结果。

本地撤销只针对上述本卡新文件和 tasks.yaml 本卡字段，不 reset/clean、不触及他人改动。发布后回滚按登记文档先恢复对应发行源码与非敏感参数，再恢复秘密材料、重启、check 与业务验证。当前未执行该生产流程。
