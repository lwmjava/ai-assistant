# Skill 版本化与内置回归实现说明

日期：2026-10-13。任务 PAGT-005；用户批准 docs/ 范围及推荐设计。工程验收记录如下，用户业务验收未验证，保持 in_review。没有 Git 提交、推送、合并或部署。

## 实际行为

现有授权用户仍经 SkillService 的租户/owner/global 过滤使用技能；技能编辑仍使用既有 HTTP API。DB/API 声明 `version` 保持兼容，不自动递增。每份运行内容新增 canonical-v1 SHA256 修订及 `version#sha256:<完整修订>` 有效执行版本，编辑内容后下一次 Chat 的实际激活版本变化；真实消息包含新正文，安全日志记录选中的发布版、内容修订和 origin。既有写入审计继续记录操作者及资源，不把 Prompt 写入新版本历史。

register 与 get/list/match 使用隔离副本；外部修改不污染当前 Manager。旧 match 可以执行其旧快照，元数据准确标记旧内容。未达到触发阈值的候选不出现在激活版本/置信度中。Manager 全局保留最后 100 条旧/新身份元数据；重复同内容同来源注册不新增，回退或移除有记录。这里是有界 runtime 历史，不是用户技能数据库持久版本链。

内置 code_review 和 translator 原 YAML/Prompt 保持不变，新增 `builtin/releases.json` 初始发布账本。显式发布门禁绑定目录和同目录账本，包含禁用技能；验证当前内容/发布号、技能集合、数字版本递增、不同内容修订与旧账本历史前缀。修改内置内容后必须升发布号并追加摘要，未同步回归失败。正式用例复制真实 translator，在隔离文件中改正文，验证未升号拒绝，再追加合法发布并用新 Manager 实际注入新内容。没有为了演示改变仓库正式翻译指令。

Manager 的严格 load/reload 完成整批候选后才替换技能集合、历史与目录；坏文件、部分失败或目录不可用保留旧可用状态，修复后可以从原目录重试。合法删除形成空集合。普通 discover_skills 保持安全跳过坏文件的 Service 兼容路径。全局单例只有初始 load 成功后才发布，首次失败不会阻塞随后修复重试。

YAML 和直接 Manifest 均拒绝错误类型/大小、NaN/Infinity/超范围数值、非法或过深正则、重复 YAML 键、非法编码。深层账本解析与极大 match 置信度转固定安全错误。等价 0.0/-0.0 阈值内容身份一致。日志不输出原始 Prompt、正则、合成敏感内容或主机路径。版本元数据不改变工具权限，实际 L2 Executor 拒绝仍无执行副作用。

## 文件与需求映射

| 文件 | 交付 |
|---|---|
| app/agents/skills/base.py | Manifest 只读内容/执行版本属性；SkillContext 实际版本集合 |
| app/agents/skills/versioning.py | 有界严格内容验证、canonical-v1、显式持久账本门禁 |
| app/agents/skills/loader.py | 安全 YAML/重复键/大小校验，普通与严格发现兼容 |
| app/agents/skills/manager.py | 深复制、版本激活日志、全局有界历史、原子 reload、可重试单例 |
| app/agents/skills/builtin/releases.json | 两个内置发布版及内容修订、初始摘要 |
| tests/test_skill_versioning.py | 内容身份与文件发布演练、账本/数值/编码反例 |
| tests/test_skill_manager_versions.py | Manager 生命周期、回滚幂等、失败最终状态与日志安全 |
| tests/test_skill_version_entry.py | 独立真实 HTTP/Chat/新 Session/Provider 消息/审计/租户权限回归 |
| tests/test_skill_version_counterexamples.py | 独立入口反例及单例失败重入回归 |
| docs/agents/skill-version-spec-v1.md / docs/evaluations/skill-version-regression-v1.md | Skill 规格与 Smoke/Adversarial/Observed Regression v1 |
| docs/plans/plan_pagt_005_20261013.md / plan_pagt_005_review_fixes_20261013.md | 原契约/批准与审查修复计划 |
| docs/reviews/2026-10-13-PAGT-005-*.md / 本说明 / closure_pagt_005_20261013.md | 当前依赖、计划、入口、反例、最终独立审核与关闭对账 |
| tasks.yaml PAGT-005 | 批准范围、状态与残余验收登记；不改其它卡状态 |

Service、API、数据库、配置、前端无本卡修改；工作区已有 PAGT-004/006 变更完整保留，不归本卡。初始指纹与最终机器可读证据分别见 ../reviews/pagt005_entry_fingerprints_20261013.json、../reviews/pagt005_delivery_evidence_20261013.json。以工作区文件 SHA256 而非仅 HEAD 绑定最终候选。

## 实际验证命令与中间失败

所有 pytest 串行，解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe；环境 RAG_BACKEND=native、DATABASE_URL=sqlite:///./data/test_ai_assistant.db；使用 `-q -p no:cacheprovider --basetemp=data/<独立目录>`。下列简写命令均由该解释器 `-m` 执行，没有换到 base 环境。

| 实际命令/运行者 | 退出码与结果 |
|---|---|
| 依赖 Agent：PAGT-001 核心/旧工具回归 | 0，119 passed、0 skipped；扩大 health 首轮125 passed/1 failed已隔离为共享测试库历史未知索引分块合法 degraded，未删除数据或改测试 |
| root：pytest tests/test_skill_selection.py tests/eval/test_skill_tenant_selection.py tests/test_skill_smoke.py | 0，9 passed，15.43s；smoke 是模块断言，不冒充正式新增案例 |
| root：pytest tests/test_skill_manager_versions.py（实现前） | 1，4 failed，对应对象污染/无执行身份/reload 忘目录/NaN覆盖的业务红 |
| 版本实现 Agent：版本属性两案例（实现前） | 1，2 failed，内容身份/执行版本缺失，不以导入错误当红 |
| 独立入口 Agent：entry + selection + tenant_selection | 1，18 passed/1 failed，34.02s；错误为 POST/PATCH 列表响应没有 version，改用 GET 详情，不改 API |
| root：版本/Manager/入口/选择/租户/smoke 第一轮 | 1，90 passed/2 测试基础设施错误；超长字符串参数自动 case ID 导致阶段切换错误，改为短类型 ID；不是业务失败，未删除案例 |
| root：同六文件修正后 | 0，91 passed，33.79s |
| 独立反例 Agent：test_skill_version_counterexamples.py 初轮 | 1，6 passed/1 failed，2.47s；发现 P2 首次失败单例无法重试 |
| root：4 个正式修复反例（singleton_failed/extreme_match/zero_threshold/extremely_nested） | 修复前1，4 failed；修复后0，4 passed/78 deselected，2.00s |
| 独立反例 Agent：counterexamples + versioning + manager_versions | 0，82 passed，2.46s；原 P2 修复通过 |
| 独立反例 Agent：test_skill_version_entry.py | 0，10 passed，17.15s；完整 HTTP 更新下一 Chat 链已独立重跑 |
| root：pytest -k skill --junitxml=data/pagt005-final-skill.xml | 0，103 passed/1967 deselected/0 skipped，38.62s；1 个既有 ast.NameConstant 弃用 warning |
| root：ruff check . --no-cache（最终修复后） | 0，All checks passed |
| root：mypy app/ --cache-dir=data/mypy-pagt005-full（最终修复后） | 0，205 source files；未放宽配置或添加 ignore |
| root：git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check | 0；base/loader/manager 恢复 HEAD 的 LF，减少无意义全文件 diff |

调用方专项、最终依赖指纹核对、最终人工审核结果在下方后续记录；未执行内容不能从以上结果推导通过。计划审查 /root/pagt005_plan_review；依赖 /root/pagt005_dependency_check；版本模块 /root/pagt005_version_model；入口验证 /root/pagt005_entry_validation；反例及修复复验 /root/pagt005_counterexample_review；root 整合业务、文档、红绿回路与最终命令。独立验证角色发生容量错误后恢复并如实区分自己与 root 的运行，不冒充执行身份。实际使用 task-card-delivery、brainstorming、plan-eng-review、code-review-quality（反例角色按其维度）。

最终调用方专项：上述环境，指定解释器 `-m pytest tests/test_agent_chain.py tests/test_chat.py tests/test_chat_resume.py tests/test_pipeline_budget.py tests/test_tools.py tests/test_mcp.py tests/test_sandbox.py tests/test_supervisor.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt005-caller-regression --junitxml=data/pagt005-caller-regression.xml --tb=short`，退出 0，142 passed / 0 skipped，16.69s，1 个既有弃用 warning。不是全仓 pytest；JUnit 字节指纹纳入机器可读证据。两个范围重叠的批次不合并成独立测试数量。

关闭前实际指纹核对：前置报告 11 个关键文件全部匹配；两内置 YAML 和 SkillService/ChatService 四文件与开工指纹一致。最终证据绑定 20 个源码/调用方/测试/规格/计划文件、两份 JUnit（103 与142测试）、本卡规范化内容 SHA256。报告文件和本说明是逐步追加的证据日志，不列入可变的自引用哈希；最终人工审核另记录实际读取与抽查结果。

最终独立人工审核第1轮已通过，角色 /root/pagt005_manual_review_1 未参与本卡实现或既有审查；报告 ../reviews/2026-10-13-PAGT-005-manual-review-1.md。其实际运行 `pytest -k skill` 103 passed/1967 deselected/0 skipped、38.10s；前置核心工具119 passed/0 skipped、4.05s，均退出0。20交付文件、11前置文件、两JUnit及本卡规范化SHA均匹配，另实际抽查两内置文件与快照激活；无未决P1/P2。此后仅追加交付文档结论，业务/测试/任务卡版本与证据保持不变。用户业务验收仍未验证，in_review不是done。

## 范围、未验证与回滚

用户没有委托 Agent 代行业务判定，业务验收未验证；最终独立人工审核也不替代这项授权。真实模型翻译/审查质量、用户历史全文重放、数据库发布版本自动递增、跨进程历史、生产并发/性能未验证，不属于已批准的交付保证。未跑前端页面/类型检查/构建，本卡无前端/API契约变更。`pytest -k skill` 的 1967 deselected 明确不代表全项目全量通过。

回滚只恢复本卡 base/loader/manager 并移除版本模块与发布门禁集成，保留旧声明版本及 Service/API 行为；不迁移或删除业务数据。撤销发布时应恢复 YAML 与对应账本当前条目一致性，历史记录通过正常版本控制追溯，不能把已发布历史强行改写。没有新外网调用、收费模型、权限授权、配置或依赖。


## 2026-10-14 已授权业务验收交付补记

用户本轮明确委托独立人工验收Agent代行可复现业务操作。技能正文/触发词提交后新Session回读与下一Chat有效版本变化、真实内置合法升号及历史篡改拒绝验证通过；105技能专项、119前置与最终8独立探针退出0。逐项原契约E2业务结果及当前指纹见[独立验收报告](../reviews/2026-10-14-PAGT-005-人工验收第1轮.md)；报告的相关回归/独立探针存在覆盖重叠，不累加为独立用例总数。原“用户未委托/未验证”是此前时点记录，本轮授权和实际验证已解除该项，本卡状态同步为done。

本轮仅新增验收文档/隔离探针并同步任务和交付记录，业务源码未变；原批准范围及功能回滚方式不变，真实LLM质量、生产PostgreSQL、多实例、生产性能及断电exactly-once仍没有本轮验证，不升级为生产可用或整体上线通过。无收费模型、CI触发、提交、推送、合并、部署或重要数据操作。
