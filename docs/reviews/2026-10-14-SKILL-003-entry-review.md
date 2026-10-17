# SKILL-003 独立入口反例审查

审查 Agent：`/root/skill003_entry_review`，未参与业务实现或实施计划审查。使用 `code-review-quality` 的逻辑、安全、异步边界与可测试性检查；按 `task-card-delivery` 的入口反例门禁运行。核对原始任务 SKILL-003（语义命中关键词未命中技能，不替代护栏，不改变执行）及用户已批准范围扩展、`plan_skill_003_20261014.md`。PAGT-007 历史收尾文档不归本卡变更。

审查范围：`SkillManager.match_semantic`、`ChatService._match_skills_async`、`chat/chat_stream`、`manifests_for_chat`、声明版本/不可信围栏、实际消息持久化和执行 claim 的 finally 清理。适用规则：AGENTS.md、全局、Agent Runtime、Skill、Testing Evaluation、安全规则。语义提供商为合成 `ReviewProvider`，没有调用真实模型，没有读取模型密钥或生产数据。

## 首轮结论：P1，未通过关闭门禁

在远端语义选择等待期间，另一数据库 Session 已提交停用当前用户或其租户。`session.expire_all()` 后虽会刷新用户对象，但 `manifests_for_chat` 只检查 Skill 的 enabled/owner/tenant，不检查主体与租户 is_active。因此两个反例仍返回已激活的私有 SkillContext。旧同步入口的认证前置不覆盖新增 await 窗口，不能将旧认证当作等待后授权证据。

位置：`app/services/chat_service.py` 的 `_match_skills_async` 返回前回查；反例 `test_committed_principal_revocation_during_selection_is_rejected[user/tenant]`。建议冻结发起时主体身份，在等待后从当前数据库复核主体/租户仍有效并身份绑定一致；遵守现有系统管理员的停用租户例外。撤销时不得激活候选，且失败日志不得含主体敏感数据。修复后重跑两个反例及聊天回归。

## 自行构造并实际执行的反例

| 入口与场景 | 首轮结果 | 证据级别 |
|---|---|---|
| `_match_skills_async`，独立 Session 停用、删除、改 owner、改 tenant、改正文、改 version | 6 通过：不激活过期或越权候选 | E2 |
| `_match_skills_async`，独立 Session 停用 User / Tenant | 2 失败：仍激活私有技能 | E2 |
| 完整 `ChatService.chat`，语义选择、真实 simple 短路径回复、实际落库，新 Session 回读 `skill_names` | 通过，保存 parcel 且正文符合合成答案 | E2 |
| 完整 `ChatService.chat_stream`，上述相同链路 | 通过，done + 保存 parcel | E2 |

完整聊天使用真实现有 `langgraph` simple 短路径，关闭 RAG/MCP/进化以隔离，不构造 Supervisor。该证据证明两条真实对话入口选择与保存，不代表默认五阶段管线全部行为，不代表真实模型语义质量。选择 Prompt 中没有 Skill 正文，选择调用每条请求为 1 次。其他验证 Agent 的错误 JSON/数值/预算/不同模型/确定性优先及取消 claim 证据需由主 Agent 一并核对；本报告不把未执行项写成实测。

命令（UTF8 环境）：`D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_skill_semantic_counterexamples.py -q --basetemp data/pytest-tmp/skill003-entry-review --junitxml=docs/reviews/skill003-entry-review-20261014.xml`。退出码 1：8 passed / 2 failed，5.80 秒。数据库为 basetemp 下全新 SQLite，与共享 conftest 测试库及生产库分离；执行已按主 Agent 排程串行。

首轮文件 SHA256（原始字节）：manager.py `fbd20580fd1d8cab50db227fc20427b8990c5749c3841bc7fe55650cd052ccbf`；chat_service.py `5589be304ce1b52727ab106fdede48bc576cd2ada7a23b49c33c336fc5c4e71e`；反例 tests 文件 `18af546e6ba12d05ef0205be95dcba8465c26fe94ec92b2b958560f0924a78ec`。报告绑定当前工作区快照，不将 HEAD 当作未提交业务代码指纹。

Ruff 初次由于 `.ruff_cache` 临时文件拒绝访问退出 1，属于检查基础设施失败，随后使用 `--no-cache` 保留同一检查规则重验。修复复核结果须另附，不覆盖本轮失败 XML。

## 修复后独立复核：通过入口反例门禁，P1 已闭合

主 Agent 先落盘 `docs/plans/skill003_review_fixes_20261014.md`，随后在语义等待前冻结主体 id/tenant/role/token_version，返回后重新读取 User/Tenant，有效性或绑定变化时抛既有固定 `ResumeRejectedError` 403，专门传播而不吞掉拒绝；系统管理员停用租户的既有例外及 auth-disabled 固定身份兼容保持。本人未参与业务修复，仅扩展反例并独立重跑。

18 项全部通过，7.57 秒，退出码 0：

- 原 6 项 Skill 删除/停用/ownership/tenant/content/version 变化继续安全拒绝。
- User/Tenant 停用，User role/token_version/tenant 变化、User 删除，6 项返回前身份复核均固定 403。
- 真实 chat/chat_stream 成功保存 skill_names，两项新 Session 回读仍通过。
- 两个真实聊天入口分别遭遇 User 或 Tenant 停用，4 项在新 Session 只看到已接受的 user 消息，没有 assistant；执行 claims 回到调用前集合，未继续生成回复。

命令同上，basetemp 改为 `data/pytest-tmp/skill003-entry-review-green`，JUnit 改为 `docs/reviews/skill003-entry-review-green-20261014.xml`。原始 red XML 保留。Ruff 对新增测试追加 import 后报告 I001，调整导入顺序后 `python.exe -m ruff check --no-cache tests/test_skill_semantic_counterexamples.py` 退出码 0；仅导入顺序变化，无断言或场景削弱。供主 Agent 的最终完整回归覆盖最终测试字节。

修后业务 SHA256：manager.py `fbd20580fd1d8cab50db227fc20427b8990c5749c3841bc7fe55650cd052ccbf`；chat_service.py `6ec002f01defac3b3defeaa8046d5b7e61c387bded9eeeacbfcaa588dbe42b26`。该结论仅为已执行入口边界验收；真实模型语义质量仍未执行，本报告不替代独立最终人工审核，也不授权 done、提交或发布。
