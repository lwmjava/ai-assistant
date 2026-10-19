# V1C-005 独立工程审查

日期：2026-10-19。审查者：独立 Agent `/root/v1c005_engineering`，未参与业务实现或测试编写。本轮使用 task-card-delivery 与 code-review-quality 技能。基线 HEAD：`9cbe94fffe269cde9d93c00fb6d1f6d25fd01264`；候选为该 HEAD 加工作区改动，尚未提交。当前为修复前审查记录，**不构成最终通过**。

## 契约和范围

依据 AGENTS.md、适用 Cursor rules、工程治理、原任务、批准计划和 Accepted ADR-0016，独立阅读组合 helper、Service 装配、ChatService 同步/流式接线、路由、共享授权回调与测试。重点核对原始来源、数值和单位、执行器复用、技能/身份、缓存、取消、附件、恢复和真实持久化。未修改业务或测试文件；反例测试由独立验证 Agent 编写。

## 修复前发现

| 编号 | 优先级 | 真实反例与影响 | 期望 |
|---|---|---|---|
| ENG-01 | P2 | 对“住宿每晚300元”“住宿每月300元”“住宿每小时300元”“住宿负300元”“住宿300元人民币/晚”，模型剪切引用“300元”并声明 CNY，均实际调用 calculator，输出成功金额300.00元。数字切片准确不能证明单位/符号完整。 | 拒绝不支持或不完整的原始上下文，零工具执行。 |
| ENG-02 | P2 | “住宿每人每日300元；住宿每人每天400元。”及第二项含空格的等价形式，选第一项加1人3天，均实际执行并输出900.00元。冲突检测按原始词法分组，未归一化同义单位或空白。 | 同一适用费率有冲突时澄清，零执行。 |
| ENG-03 | P2 | 验证 Agent 补充“元300”“人1”“每人元300”，通过移除数值后比较剩余字符的单位检验。配置合法最终维度后均实际执行。 | 数字与单位的原始相对位置须符合明确词法，不能重排后解释。 |

ENG-01/02 由本审查独立发现并先执行纯 validator 反例，随后在隔离 SQLite、真实 helper/ToolExecutor/calculator、合成提取模型中实际复现。ENG-03 由验证 Agent 补充，本审查独立运行验证。均不是生产检索或模型质量证据。

## 已执行命令与结果

平台 Windows；解释器 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。`DATABASE_URL=sqlite:///./data/v1c005-engineering-test.db`；每条 fixture 另建临时 SQLite，审计和数据库绑定至隔离引擎，不访问生产数据。

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_combined_tool_contract.py -k 'unsupported_original_qualifier or conflicting_policy_rate_synonyms or unit_cannot_be_validated_after_reordering' -q --basetemp=data/pytest-tmp/v1c005-engineering-red
```

修复前：退出码1，**10 failed / 90 deselected，6.38秒**。每条失败均为预期零调用，实际 calculator 调用一次；原始日志 `data/v1c005-engineering-red.txt`。这些为 E2 隔离集成反例：真实 helper、真实执行器与计算器，合成来源/模型；不以此声称真实检索质量。

另运行只读 stdin 脚本逐条驱动相同真实 helper 和隔离 SQLite：5条单位/符号剪切例均执行一次并输出300.00元，2条冲突例均输出900.00元；终端输出已发主 Agent。正式可复现证据以上述入库测试和日志为准。

## 当前结论与待验证

三项 P2 阻止工程审查通过，已及时通知主 Agent，等待独立修复与重验。主 Agent 原已知“tool.call让出后撤权缺拒绝审计”正在修复，本审查不重复计为新发现。静态已核对 Service 新会话复核、两种编排共用组合入口、提取局部缓存 bypass、内层生成器显式 close、附件正文不代替当前用户参数、失败重新生成保留原回答；这些仍需最终候选的实际入口验证，不以静态推导替代 E2。

全量回归、静态检查、最终源码指纹、最终独立审核、业务接受及同提交 CI 由主 Agent 汇总；本中间报告不能替代上述门禁。

## 首次修复复验（尚未收口）

实现者改为完整源子句有序词法、单位归一和冲突比较，并加入脱敏模型/工具 Trace。审查者实际运行：

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_chat_combined_tools.py tests/test_combined_tool_contract.py tests/test_fast_route.py -q --basetemp=data/pytest-tmp/v1c005-engineering-final
```

退出码0，**141 passed / 1既有警告，50.28秒**，日志 `data/v1c005-engineering-final.txt`。真实 Service 已覆盖四入口成功、新请求重新执行、成功/失败 regenerate、两编排外层 close、撤权后拒绝审计和 Trace 不记录原始业务参数。

**仍不通过**：继续构造后发现 ENG-01 冒号变体未修复。对“住宿每晚：300元”“住宿每小时：300元”“住宿每人每日：300元”，只引用“300元”并声明 CNY，实际 validator 均接受300。`_CLAUSE_SEPARATOR` 把冒号前单位限定与数字分开，所谓完整子句仍可丢单位；其中人日费率明确属于支持单位，也不能被声明为总额。已通知主 Agent 与验证 Agent 补真实 helper 反例后继续修复。以上141通过不能作为最终无缺陷证据。

验证 Agent 另补“住宿：每人每日300元；住宿：每人每天400元”的冲突反例。审查者实际执行 `python -m pytest tests/test_combined_tool_contract.py -k 'colon_cannot' -q --basetemp=data/pytest-tmp/v1c005-engineering-colon-red`：退出码1，**4 failed / 100 deselected，3.82秒**，`data/v1c005-engineering-colon-red.txt`；三条限定剪切实际执行300元，冲突项实际执行900元。已交实现者继续修复。首修141绿为修前诊断，不绑定最终候选。

## 第二次修复复验与工程结论

完整来源 scope 改为保留冒号、逗号、换行两侧的限定，只有完整数值子句或有限允许 heading 才可关闭来源域，未知限定不会因句号等硬标点消失。单位校验按原文数值位置回映射，类别/单位先归一后比较冲突，有序数值词法拒绝“元300/人1”。审查额外探针“知识库差旅报销金额计算标准：住宿每人每日300元；住宿每人每天400元。”亦按冲突拒绝；未发现通过通用 heading 吞掉未知限定的旁路。

先单跑新增换行/中文逗号4项：退出码0，4 passed / 104 deselected，3.37秒。日志 `data/v1c005-engineering-punctuation-red.txt`；文件名含red只是预定命名，**实际已绿，不声称这4项先红**。

随后在实现者冻结源码时运行：

```powershell
$env:DATABASE_URL='sqlite:///./data/v1c005-engineering-test.db'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/test_chat_combined_tools.py tests/test_combined_tool_contract.py tests/test_fast_route.py -q --basetemp=data/pytest-tmp/v1c005-engineering-final2
```

退出码0，**149 passed / 1既有警告，58.77秒**。日志 `data/v1c005-engineering-final2.txt`，SHA256 `4f5776962caf125f6375302ca00d3b70346a351aa67c9761bd26c06eebb86b17`。原 ENG-01/02/03 的反例和支持的1140.00元业务正例均通过。修复不降低来源、数值、权限、审计或计算门槛。

该时点源码指纹：

| 文件 | SHA256 |
|---|---|
| app/agents/combined_tools.py | 59bfbf38d5a6573ffcecbaeb19d9320b2fb44f15af5c49c9d1afc79c7a988d56 |
| app/services/combined_tools.py | 133ef4308b28a2515f6d3bf97b3fdb3043cb21da6e5d4cc0e6ad8b44cb5a13c0 |
| app/agents/route.py | ef691680a0c1c1f54fdefa932de905416eff7d1da5bc7b23325b25fd360c6de1 |
| app/agents/prompts.py | bd7cf33b989121f0e33f5e4e552690dec70b5c6a8b208d71a79750e79faa620d |
| app/services/chat_service.py | 15db1679eacab1cbd24359219c5be5d6a5e56dab46a69f3b959fbd68c4e3e79d |
| app/services/supervisor_tools.py | a04c8bb9a8bd2e55c3c9048bbda751fd2bce14b71855abee84d8a7a8b5c700de |
| tests/test_chat_combined_tools.py | d7c4ce479a71939f27e6c90e88058391cfea33c355e2fe2e1a6aeb735aaa94f4 |
| tests/test_combined_tool_contract.py | e7cea0ac426a1abc64b265083dfabea66eed2981ecb3b6f6312ef77619470dd4 |
| tests/test_fast_route.py | 3c5d88906a264966cafe2361db8f066bc75a5dcadfc16209c2b5f5b7efb46ffd |

**本轮工程结论：所列三项P2已修复，该源码时点未发现未解决P1/P2。** 该结论限已实际审查和运行的边界，不等于最终全量、业务接受或发布通过。主 Agent 已安排验证 Agent 继续补强超时、重试撤权、技能变化、AST、选块与取消测试；149项结果是该次诊断快照，新增测试和任何相关源码变化后仍须最终重验与统一指纹绑定。完整源码覆盖的最终审查与关闭结论以其后最终审核为准。
