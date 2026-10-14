# 技能语义触发实现说明

关联 SKILL-003 / plan_skill_003_20261014.md。用户已批准 docs/、chat_service.py 范围扩展与确定性优先方案。此次只实现此卡，保留此前PAGT-007未发布收尾内容。

已实现：SkillManager.match_semantic保留关键词/正则/always优先，仅未命中时让现有intent provider依据授权候选名称、描述及脱敏请求建议一个技能。两种ChatService对话入口调用新异步helper，沿原activate/技能正文不可信围栏/工具执行/skill_names持久化。原同步_match_skills保持兼容。

限制：一轮选择最多32候选，请求4096 UTF8字节，描述512，完整消息正文32768，输出1024；单次5秒、temperature0/max_tokens128、无本层重试，不新增缓存/供应商/配置。严格JSON限定name/confidence键，拒绝重复键、未知名、NaN/Infinity/bool/范围或阈值异常。只有已启用且模型适配的候选可选择；模型不授予权限，不发送正文/历史/路径。失败仅固定元数据日志，取消传播；原有provider自身护栏仍生效。

独立审查P1：等待期间主体/租户被另一Session停用仍可能激活。修复计划skill003_review_fixes_20261014.md；现在等待后复核数据库主体有效性、id/tenant/role/token_version，租户active（既有系统管理员例外保留），失效抛固定403并阻止执行。候选等待后重新读取权限和effective_version/origin，删改停用均拒绝旧提议。AUTH_ENABLED=false既有固定合成管理员路径保留；没有新增权限豁免。

实际验证环境：D:\DepTooL\anaconda3\envs\ai-assistant\python.exe，PYTHONUTF8=1/PYTHONIOENCODING=utf-8，独立basetemp及隔离数据库。初始基线误填tests/test_skills.py不存在，退出1/no tests；更正smoke/versioning/selection路径后67passed退出0。初版新增helper尚不存在的19fail仅证明接口未实现，不当业务E2红证据；相同输入的同步入口未命中与异步入口命中的独立对照证明交付行为。manager初版19passed，入口初版12passed；主体撤销审查8passed/2failed确为有效业务红证据。最终受影响回归/独立复验以收尾追加记录为准，不拿初版指纹关闭最终源码。

初期Ruff --no-cache与mypy两个受影响业务文件退出0。Evaluation协议skill-semantic-select-v1与合成Observed Regression/Adversarial数据记录docs/evaluations/skill-semantic-match-v1.md。真实模型评测与本卡业务验收当时未授权；下文最终补记记录用户后续授权和执行结果。不以合成成功声明真实语义质量已达到。未发布/合并/部署；回滚两对话入口改回同步匹配并撤match_semantic即可，无持久化迁移。


## 最终验证补记

权限撤销P1独立18passed/exit0；独立入口13passed/exit0（默认完整AgentPipeline及新Session回读）；最终合并206passed/exit0，37.17秒，skill003_final2_20261014.xml。首次合并205passed/1failed源于旧AGT持久化用例未存User/Tenant，补有效fixture且原断言保持后通过，旧失败报告保留。mypy app/205源0，受影响源/测试/执行器Ruff0。

用户明确批准6次真实模型与独立业务验收；真实deepseek-flash单provider/无缓存无重试，6实际调用，4语义正例命中、2无关输入未激活，结果6/6（E1 Smoke，不是Gold或通用质量）。第6条name=null对象协议无效，被安全拒绝；不能称协议遵从100%。原回复/指纹/模型/数据集留skill003_real_smoke_20261014.json。无凭据导出，未获取usage不能杜撰实际费用。Prompt正式v5/check0，旧版本保留。关闭对账closure_skill_003_20261014.md；最终业务验收结论待独立新Agent。


## 最终独立业务验收与关闭

用户明确委托的最终新Agent /root/skill003_final_business_accept 审核通过，报告docs/reviews/2026-10-14-SKILL-003-最终人工审核第1轮.md；独立重新执行两入口/反例31passed/0skipped/exit0（8.55秒），skill003_final_business_20261014.xml；真实6次报告绑定两源码和数据集指纹匹配，registry v5独立check0；无未决P1/P2。原契约全部闭合；上表最终审核待审核是结论前阶段，现在通过。tasks.yaml SKILL-003更新done，completed_at2026-10-14。真实action6/6及schema5/6限制保留，不升级Gold/通用生产质量声明。仍未提交/推送/合并/部署，未开始下一卡。
