# SKILL-003 独立审查修复计划

独立入口审查 /root/skill003_entry_review：10例中8通过/2失败；等待语义模型返回期间，另一Session提交停用User或Tenant，候选回查未检查主体有效性，仍激活技能（P1）。证据tests/test_skill_semantic_counterexamples.py与docs/reviews/skill003-entry-review-20261014.xml。

修复：调用前冻结主体id/tenant/role，语义等待后从当前Session重新读取主体与租户，按现有API鉴权规则复核active/tenantactive（系统管理员既有例外保留）、身份租户角色绑定。失效时阻止当前对话继续，不能只丢弃技能后照常答复；CancelledError仍传播。避免吞掉安全拒绝，采用既有ResumeRejectedError固定公开403。新增正式反例已由独立审查Agent提供，修后复验两chat入口和Skill隔离验证。无新架构或权限模型决策。
