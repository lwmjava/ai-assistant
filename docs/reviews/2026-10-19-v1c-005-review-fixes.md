# 知识库组合实现与独立审查修复记录

基线9cbe94f；用户已批准实施计划、允许路径和ADR-0016。此为迭代记录，不替代最终验证证据。实现Agent拥有Agent helper/提取Prompt，主Agent负责应用接线与共享文档，独立验证Agent拥有反例文件，独立工程Agent从原契约检查实际入口。

## 已发现与拟修复项

| 项目 | 实际证据 | 根因/处理 | 当前状态 |
|---|---|---|---|
| 原路由静默丢必要步骤 | route-red：4 failed/1 passed，2.62秒，退出1 | 显式双意图原来只能选TOOLS/RAG/SIMPLE；新增COMBINED保留原工具集 | 路由13项已绿，最终还需公共回归 |
| 合法元被拒 | first：18 failed/84 passed，79.13秒，退出1，其中亦有夹具错误 | 禁止货币词用了字符类，误禁单个元；改完整词匹配，保留单位/非法数字拒绝 | 独立helper后续通过，待最终冻结 |
| 首轮检索测试假阴性/ORM夹具错误 | first日志含Mock词面相似度低于原0.4阈值和DetachedInstanceError | 合成政策标题补业务关键词以建立确定性词面链路，不改阈值；另租户User提交后refresh/expunge | 不是产品失败或业务红证据，不删/放宽业务断言 |
| 合法紧连人数天数被拒 | second：4 failed/118 passed，50.20秒；pure validator复现source_mismatch | 原文1人3天里1人后紧连3并非截数字；边界检查过严 | 修后真实四入口1140通过 |
| tool.call事件后撤权缺拒绝审计 | audit-red：1 failed/4 passed，4.82秒；新Session改token后零执行但audit0行 | 外层紧贴执行check在Executor之外抛拒绝，原before_attempt审计未触达 | 已补仅该路径一条安全authority_changed拒绝审计，待绿 |
| 源子句单位/中文负号可被quote剪掉 | 独立工程pure validator实际接受每晚/每月/每小时/负300元/300元人民币每晚等5例 | 只检查有限相邻前后缀不能证明整个数值短语单位 | 工程Agent正在执行真实helper红；须完整子句/单位边界门禁修复后重验 |
| 同维度同类别矛盾标准可绕过 | 独立pure validator接受每人每日300与每人每天400、空白变体 | 冲突按原词法字符串分key且忽略空白变体 | 待真实helper红，按canonical单位+类别归一化冲突后修复 |
| 倒序数值单位 | 验证Agent补元300/人1等最终维度有效的入口反例 | 删除数字再校单位可能丢失位置约束 | 待工程红及有序词法修复 |
| 新组合缺既有Debug Trace | trace-red：1 failed/1 passed，3.41秒；金额1140已真实成功但trace模型/工具事件0 | 新helper未连接应用层已有trace | 补可选安全Trace注入，只记录真实调用与受控状态，不保存业务正文 |

独立工程红证据已完成：10 failed/90 deselected，6.38秒，退出1，日志data/v1c005-engineering-red.txt，10项失败均因真实calculator实际启动1次，非导入或夹具失败。三类P2必须修复并由工程Agent重跑后再称解决。audit缺记录修复后在trace-red同轮已通过，但不替代最终版本回归。

修复限制：不降低阈值、权限、预算或来源要求，不新增工具/架构/依赖；无法完整验证的源子句应拒绝/澄清，不用增加黑名单冒充通用语义证明。所有最终结论以最后源码/测试/Evaluation指纹与全量回归为准；中途源码或测试变化的诊断轮不当作最终绑定证据。

工程首修后三文件141 passed/1既有警告，50.28秒，只是中间诊断；独立审查继续构造冒号变体，实际4 failed/100 deselected、3.82秒、退出1（engineering-colon-red）。冒号/换行/逗号不能将前文费率限定从事实出处剥离，故不能以141绿关闭P2；必须以完整适用上下文检查/不支持结构澄清修复同根问题，再重验所有变体。

首次全库静态：Ruff仅两处新测试import排序失败；mypy218文件发现两个AsyncIterator注解不含aclose。修为实际AsyncGenerator准确注解，保留显式close，不放宽配置或用ignore压错。最终静态检查另跑，不把初次失败藏成通过。

最终修复补记：完整来源scope保留冒号/逗号/换行，只有完整数量子句或受控资料heading可结束scope；未知每晚/每小时等限定不能切掉。类别从完整标签归一化，冲突按类别+标准单位比较。工程三文件149 passed/1既有warning、58.77秒、退出0；独立新增超时重试、主体/技能撤权、数值上限、注入和检索取消后，独立文件126 passed、42.69秒、退出0。新增换行/逗号4例是在修复期间落盘，实跑4 passed/3.37秒，虽日志名含red，不能称独立修前红。此前冒号4红及首轮10红才是修复前证据。

Ruff最终全库no-cache退出0；mypy原先AsyncGenerator推断使旧_fast返回AsyncIterator赋值报错，改共享events变量准确AsyncIterator注解（close仍走既有getattr，组合内部显式aclose），218文件退出0。原始route/test_fast_route混合行尾已保留，减少纯格式diff。531项输入已冻结，完整pytest执行中，最终证据另绑定；未提交/未接受。
