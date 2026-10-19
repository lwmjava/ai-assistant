# 技能跨编排路径接线实现说明

2026-10-19。V1C-003（用户输入V1C-0003，按任务表对应卡处理）。基线d886560ca211930f7c35d886788496599c6b6d9e，分支codex/v1c-001-lazy-retrieval；候选为HEAD加[521项输入SHA256](../reviews/2026-10-19-v1c-003-evidence.json)，尚未提交或推送。用户已明确批准[计划和允许路径](plan_v1c_003_20261019.md)及ADR-0014。

当前结论：本地自动门禁通过（第三轮全量2797 passed /3 skipped，退出0；Ruff/mypy退出0），新的独立Agent最终审核正在进行。任务保持in_review，用户业务接受与最终候选Ubuntu CI未验证。下文验证过程保留先前失败及待批准状态的历史，不代表当前仍在等待夹具批准。

## 实际完成的功能

现有用户仍从原ChatService.chat/chat_stream进入，最多使用一条授权技能。默认管线、简单/知识库/工具短路径继续注入Prompt；真实Supervisor现在只给research/draft节点注入授权技能正文，控制节点不接收。私有/全局正文沿用不可信围栏，技能有效版本仍来自实际manifest的内容摘要，不来自调用方自报。

工具模式通过SkillManager.collect_tools消费绑定，保留原注册表和ToolExecutor做权限、参数、超时、有限重试、预算及审计。运行时把绑定与原路径允许集取交集；即使模型点名另一个已注册工具，或结构化计划写入未绑定工具，执行仍被拒绝。默认简单短路的工具白名单不扩大，fast TOOLS不超出原route.tool_names。空绑定、部分/全部未注册绑定给出固定说明，不回退全工具。fast SIMPLE/RAG与Supervisor暂不执行工具绑定，明确告知不支持；未把这两条后续组合/执行需求纳入本卡。

技能匹配、注入、绑定可用/缺失、unsupported、实际ok/error/denied/cancelled分开记录，带origin、effective_version、实际执行路径；不以skill_names代表执行成功。新增工具名日志只接受当前注册表的合法限长标识，模型提供的未知/换行名称记录unknown，不把该原文写入新技能日志。没有新增API字段或消息列。

Service在每次技能注入和每次新工具开始前用独立Session重读授权与内容版本，Agent只持回调，不直接查询数据库。结构化工具执行在tool.call事件yield回来后紧贴实际执行前再次校验，防止流式消费者停用技能后仍启动工具。已开始的外部动作不承诺撤回，不做逐token或后台轮询；已成功副作用和预算指纹保留。

## 恢复与失败行为

现有state_json现在有明确空或非空的skill_binding；包含技能身份版本/来源、模式及绑定名单，不包含Prompt正文、凭据或工具参数。恢复从当前授权manifest重建原上下文并精确比较，不重新语义选型，不信任JSON自称的mode/tool_names。相同版本保留成功前缀、cursor、预算和指纹，只执行未完成后续步骤。

旧快照缺技能绑定、绑定畸形/篡改、技能停用/变版/撤销均在恢复前拒绝；若编排切换到不支持该checkpoint的Supervisor，即使发生在memory await之后也拒绝。Service在原存储读取器之前先识别技能兼容冲突，避免被原“损坏快照discard”逻辑删除。冲突使用固定安全文案，经既有ResumeRejectedError终结，零新模型/工具，不把旧checkpoint改成done/error，不自动丢弃后重跑。与技能绑定无关的旧存储损坏/过期处理不在本卡重写。

普通成功消息、来源和工具结果仍经原持久化；流式取消继续保留已交付文字的stopped助手记录，未把“取消”写成成功，也未承诺取消后不留记录。低分拒答保持原RAG_REFUSE_MESSAGE和空来源，同时保留技能unsupported/缺失提示；不让提前终态跳过说明。

## 代码与需求映射

| 文件 | 实现 |
|---|---|
| app/services/chat_skills.py | 请求内授权/版本租约、collect_tools、活动快照预检与精确上下文恢复 |
| app/services/chat_service.py | 两入口装配、恢复前置、typed拒绝、编排恢复支持检查；不放数据库访问到Agent |
| app/agents/skills/base.py、manager.py | 模式信息、SkillRuntime、安全元数据及有界绑定schema |
| app/agents/pipeline.py、plan_runtime.py | Prompt和工具政策、结构化reason/执行关口、恢复绑定、失败与取消 |
| app/agents/fast_path.py、supervisor.py | 支持矩阵接线与确定性提示，Supervisor不新增工具能力 |
| app/agents/state.py | 现有JSON快照新增可校验绑定，不新增表迁移 |
| tests/test_skill_path_contract.py | 主线36项：路径Prompt/工具交集/真实执行/缺失/unsupported、数值与安全日志 |
| tests/test_skill_path_contract_review.py | 独立57项：隔离/围栏/变版撤权/结构化恢复与yield竞态/失败取消/低分终态 |
| docs/ai-prompts/evals/skill-path-contract-v1.yaml | 1.0.0、AI-authored Smoke/Adversarial；非Gold、非质量指标 |
| README、ADR-0014/索引、计划/审查、tasks.yaml | 支持矩阵、兼容/回滚、批准范围及待验收状态 |

未修改app/services/agent_state_service.py、models、API/schema、前端、依赖、CI、.env或真实知识库。允许路径中的两个旧恢复/结构化测试文件未发生修改，现有fixture用新StateManager生成显式空绑定。

## 基线与验证过程

Windows；项目解释器D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，Python3.12.0；pytest8.4.2、Ruff0.8.4、mypy1.14.1、SQLModel0.0.22、Pydantic2.10.4、langgraph1.0.10。设置PYTHONUTF8=1/PYTHONIOENCODING=utf-8。conftest隔离data/test_ai_assistant.db，使用独立basetemp；非loopback网络被测试隔离，未发起真实收费模型请求。

新开工专项212 passed / 1 warning，33.71秒，退出0；Ruff/mypy均0。上次全量2704项与本轮开工516项输入指纹相符，仅作为明确标记的历史证据；不当成本卡最终结果。前置d886560的Ubuntu Backend、Frontend和Agent official baseline均已成功，[运行记录](https://github.com/lwmjava/ai-assistant/actions/runs/39182362484)只证明前置，不替代本卡最终CI。

先写8项真实Service红测试：8 failed，8.55秒，退出1；缺陷为Supervisor缺Prompt、工具绑定不生效、缺失绑定无说明，而非模块导入失败。最小实现后8 passed，4.63秒。独立Agent扩展正式入口反例后发现checkpoint删除、取消metadata、低分终态、reason注入问题，按[缺陷与修复计划](../reviews/2026-10-19-v1c-003-review-fixes.md)逐项补测试和修复。

独立首轮38项26通过/12失败（15.47秒）；2条是测试误把现有error写作fail，修为真实终态，未降低断言。第二轮46项28通过/18失败（17.08秒）；18项对应真实缺口。修后46项通过（16.24秒）。新增恢复/yield反例7项5失败/2通过（5.23秒），修复后与合法身份篡改mode/tools反例一起共57 passed / 1 warning，18.32秒，退出0。fast元数据路径另有主线32项30通过/2失败（10.07秒）；空绑定和不可信name日志6项4失败/2通过（4秒）；均保留正式反例并修复。

独立Agent最初Ruff默认缓存遇WinError5、退出1，改--no-cache后通过；不当成业务验证。主线mypy初轮发现helper可空字典下标2条错误，显式类型收窄后通过，未放宽mypy配置或添加忽略。

首版专项与相关回归340 passed /1 warning，63.82秒，退出0。随后首轮全量2 failed /2795 passed /3 skipped /3 warnings，623.82秒，退出1：两个旧测试通过SupervisorGraph.__new__绕构造器，缺少新增可选属性。为未注入实例添加typed类级None默认，不修改旧测试；正常实例仍有请求独立属性。相对首轮520项输入只改变supervisor.py（包含所需类型import），旧全量保留为失败历史。修后相关四文件123 passed /1 warning，29.02秒，退出0；最终16文件专项 **370 passed /1 warning，61.75秒，退出0**（本卡93项、相关既有277项），Ruff全库无缓存0、mypy app/0（214源文件）。第二轮无筛选全量已结束：2796 passed /1 failed /3 skipped /3 warnings，599.23秒，退出1。失败是既有重新生成测试的等时间戳排序问题，已用基线方法在隔离数据复现；业务排序代码未改。最小夹具修正涉及允许路径之外的 tests/test_chat_retrieval_lifecycle.py，等待用户明确批准追加路径，未修改该文件。详见 docs/reviews/2026-10-19-v1c-003-regeneration-fixture-plan.md。520项核心输入保持冻结，新增复现脚本作为调查证据；全库Ruff（含复现脚本）再次实际执行，退出0。专项通过不能替代失败的全量门禁。

## 实际命令与证据

以下python均指上述项目解释器，完整专项命令见机器证据：

```powershell
python -m pytest tests/test_skill_path_contract.py tests/test_skill_path_contract_review.py tests/test_agent_state_resume.py tests/test_structured_plan_runtime.py tests/test_skill_semantic.py tests/test_skill_semantic_entry.py tests/test_skill_semantic_counterexamples.py tests/test_skill_versioning.py tests/test_skill_version_entry.py tests/test_skill_version_counterexamples.py tests/test_skill_manager_versions.py tests/test_skill_selection.py tests/test_supervisor.py tests/test_tool_path_contract.py tests/test_error_model_boundary.py tests/test_p0_regression.py --basetemp data/pytest-tmp/v1c003-contract-final2 -q --tb=short
python -m ruff check . --no-cache
python -m mypy app/
python -m pytest --basetemp data/pytest-tmp/v1c003-full2 -q --tb=short
```

原始输出data/v1c003-baseline-20261019/，最终日志将随机器证据归档到docs/reviews并记录SHA256。所有pytest串行，独立Agent实跑也占独立窗口。E2使用真实Service、SQLite、ToolExecutor、Supervisor与合成LLM/工具；只证明接线/权限/恢复契约，不证明模型选择准确率、真实工具或检索质量。局部非法confidence单测为E3补充，不能单独支撑必交付。

无前端/API/schema改动，前端四门与新增页面操作不适用；未伪造截图或页面验证。已有三个Windows平台跳过的最终数量与具体警告以本次全量结果补记。当前没有本卡最终提交的Ubuntu CI，不借用前置CI关闭本卡。

## 分工、状态与回滚

主Agent负责批准登记、Service/helper、主线测试、文档与总门禁。/root/v1c003_runtime实现7个Agent文件并做Ruff，不运行pytest；/root/v1c003_engineering负责独立方案与实现静态审查，未参与实现；/root/v1c003_validation自行构造并实跑57项真实入口反例，记录修复和指纹。最终还需由未参与本卡实现及上述审查的新Agent，在自动门禁/关闭对账之后审核。

实际读取并使用：task-card-delivery（单卡流程、证据、独立审查），brainstorming（方案选择和用户批准）；工程审查按项目的架构、兼容、失败、权限、测试、开销维度执行，没有宣称运行完整交互式plan-eng-review。

当前未提交、推送、合并或部署。本卡用户业务接受未授权Agent代行，前卡授权不沿用；全量、最终独立审核、用户业务接受、最终候选Ubuntu CI未齐时不得done。

回滚同时恢复接线、执行约束及快照字段，保留技能、消息、审计和checkpoint。新快照不能交给忽略新约束的旧版自动恢复，回滚期间关闭恢复入口，显式新请求由用户决定；不自动重放副作用，不做数据迁移。Supervisor工具能力、组合任务、路由优化、技能市场或新供应商均未实现。

追加批准后的验证：用户明确批准最小夹具修正，已仅添加timedelta并使问题时间早于原回复，保留全部既有业务断言。检索生命周期与两组技能测试164 passed /1 warning，63.00秒，退出0；Ruff全库无缓存退出0。第三轮全量使用新的basetemp运行中，521项输入已绑定（新增调查复现脚本）；此前等待批准的文字为历史状态。业务等时间戳排序缺陷仍未修复。

最终本地自动门禁：第三轮无筛选全量2797 passed /3 skipped /3 warnings，630.25秒，退出0；521项冻结输入一致。夹具与技能164项通过，已有370专项不受该专项文件之外夹具修改影响；Ruff0，mypy0（app输入保持一致，沿用最终检查）。日志已归档docs/reviews/2026-10-19-v1c-003-log-*.txt并绑定摘要。三个Windows跳过为符号链接能力、POSIX0600及打开文件替换身份；Linux证据仍待最终候选Ubuntu CI。任务in_review，等待最终独立审核及业务接受，不代表已关闭。

Final independent audit completed: 93 passed /1 warning, 35.05s, exit 0; 521 inputs and 10 archived logs matched, no in-scope P1/P2. Report: [independent final audit](../reviews/2026-10-19-v1c-003-人工审核-第1轮.md). Status remains in_review, pending user business acceptance and final-candidate Ubuntu CI. No commit/push/merge/deployment performed. Only documentation changed after verification.

2026-10-19 additional user authorization: independently delegate business acceptance; after it passes, compare code and commit/push to GitHub. This supersedes the earlier lack of delegation/push authorization, but does not predeclare acceptance or CI success. /root/v1c003_business_acceptance is independent of implementation, engineering review, entry review and final technical audit.

Independent user-delegated business acceptance passed: 93 tests plus four real-entry resume version/revocation counterexamples, with unchanged 521 frozen inputs. User authorized code comparison and branch commit/push; final-candidate Ubuntu CI remains pending. Business acceptance report: [report](../reviews/2026-10-19-v1c-003-business-acceptance.md). No production deployment or merge is included.

## Closure on 2026-10-19

User explicitly approved closing the task. Final candidate e005b4d08b5139080423f780db8c60f92d5d219e passed all three jobs in [CI run 39202782711](https://github.com/lwmjava/ai-assistant/actions/runs/39202782711): Ubuntu Backend job117899112475, Frontend job117899112477, Agent official baseline job117899112203. Independent business acceptance and final technical audit already passed; task is now done. No source/test/config/evaluation input changed for closure. Earlier pending statuses are historical. This is not a merge/deployment authorization or a claim about branch protection. The pre-existing timestamp ordering defect remains outside this task and is not declared fixed.
