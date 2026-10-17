# PAGT-005 独立真实入口验证

日期：2026-10-13。验证 Agent：`/root/pagt005_entry_validation`。该角色未参与业务实现，只构造 `tests/test_skill_version_entry.py` 并运行本报告首轮入口验证；业务代码由其他角色修改。依据原始任务卡、用户批准的发布/有效执行版本方案、实施计划及独立计划审查。未改 Service、API、数据库模型或权限规则。

## 实际命令与结果

首轮由本 Agent 实际运行，共享测试库独占时隙：

```powershell
$env:RAG_BACKEND='native'
$env:DATABASE_URL='sqlite:///./data/test_ai_assistant.db'
& D:\DepTooL\anaconda3\envs\ai-assistant\python.exe -m pytest tests/test_skill_version_entry.py tests/test_skill_selection.py tests/eval/test_skill_tenant_selection.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt005-entry-01
```

结果：退出码 1，18 passed / 1 failed / 1 warning，34.02 秒。失败是测试错误：测试假设 POST/PATCH 响应含 `version`，而既有 `SkillListOut` 不含该字段，GET 详情 `SkillDetailOut` 才包含。错误发生在 `test_skill_http_update_commits_new_content_next_chat_logs_new_execution_version`，KeyError: version。该错误不是版本实现业务失败，不能作为有效业务红证据；首轮此用例只执行到 PATCH 后，不能据此前半程宣称下一次 Chat 已验证。

主 Agent 随后修正测试，新增创建/更新后实际 GET 详情并对比声明版本，未修改 API 契约。恢复本验证角色时，主 Agent 告知合并入口及相关回归批次 **91 passed，33.79 秒**，其中包含修正后的入口测试。这是主 Agent 实际执行的结果，不是本 Agent 独立重跑；确切批次命令、退出码和完整记录由主交付报告登记。本 Agent 只核对修正后的测试内容及下列当前文件指纹，未在该通知后再运行 pytest（共享测试库由反例审查角色使用）。

唯一 warning 为既有 sandbox `ast.NameConstant` 弃用提示，不用其推导本卡业务失败。未使用默认 base Python；未调用外网或收费模型。

## 真实入口覆盖与证据边界

新增 10 个 pytest 案例（含内置技能参数化两例），并连同存量技能选择 9 例运行。

| 原始要求/批准决定 | 真实操作链与最终断言 | 证据及首轮结果 |
|---|---|---|
| 执行内容变化可追溯 | 临时 YAML 文件 → Manager load → match → activate → inject；改文件后 reload，新内容进入注入且 effective_version 改变；旧 match 仍注入旧快照及旧版本 | E2，首轮通过 |
| 失败重载保留旧状态 | 同目录加入坏 YAML → reload 失败 → 旧内容仍能 match/activate，版本和 history 不变；删除坏文件再修改原文件，reload 使用原目录并生效 | E2，首轮通过 |
| 内容快照隔离 | 注册入参、get、list_all、match 中嵌套 trigger 与 Prompt 分别篡改 → 新匹配及激活仍为原内容/原版本；history 返回值清空不改变内部记录 | E3，首轮通过；辅助安全证据，不独立替代真实入口 |
| HTTP 更新后实际执行版本变化、发布版本兼容 | HTTP create/GET → 第一 Chat 与 Provider 消息及激活日志 → HTTP PATCH/GET → 新 Session 读取提交后的内容和实际技能版本 → 下一 Chat；Prompt 为新内容且旧内容消失，日志版本与选中内容一致；skill_update 审计绑定真实操作者/资源且不含 Prompt | E2，首轮因测试错误中途失败；修正后由主 Agent 的 91 passed 批次复验，独立 Agent 未重跑 |
| 租户/owner 同名隔离 | 三用户（两租户，同租户两 owner）HTTP 创建同名不同内容 → 各自实际 Chat，Provider 只收到自己的内容及不可信围栏，三个执行版本不同 | E2，首轮通过 |
| global/private 同名最终选择与权限 | 系统管理员创建 global，成员创建同名 private；跨租户 PATCH private 被 404 拒绝，成员 PATCH global 被 403 拒绝；创建者 Chat 选 private，另两用户 Chat 选 global；新 Session 最终内容不变，两 global 版本一致且与 private 不同 | E2，首轮通过；依据真实 Service 明确的内置→全局→私有顺序，无改权限行为 |
| 内置技能正式回归可执行 | 两真实内置 YAML → HTTP Chat → CapturingProvider 收到真实 Pipeline system 消息，审查的安全/严重性/评分/无代码询问约束、翻译的忠实/风格/术语/输出约束均完整传递；日志含对应实际 effective_version | E2，首轮两例通过。合成 Provider 只证明约束传递，不证明模型实际翻译质量或真的追问代码 |
| 未命中不注入 | 实际 HTTP Chat “今天晴天” → 无技能名称，无两个内置角色指令进入 Provider 消息 | E2，首轮通过 |
| Skill 不授予 L2 写权限 | tool 模式真实 Manager 激活并 collect_tools → 实际 ToolExecutor 调用需要鉴权的 L2 工具 → denied、attempts=0、执行函数零调用 | E2，首轮通过；使用隔离合成写函数，无生产副作用 |

## 版本绑定

下列为修正后恢复验证角色时实际读取的 SHA256（不是首轮运行时指纹，也不单独证明最终审查之后没有改动）：

| 文件 | SHA256 |
|---|---|
| tests/test_skill_version_entry.py | 12C3586D8E76B27AE021D4A48D252AFD46052CA044CB447E65B32C09606C68B4 |
| app/agents/skills/base.py | 54C3E59A6458F226E5AB32823504C148BD306960372D3D3924DD0C5EA2D0ABD4 |
| app/agents/skills/loader.py | 7BA8B1327BF9819441425A91C5052176059CF4121078CD6076649DD2B0623454 |
| app/agents/skills/manager.py | 706382FD196B4076C05425E7B8ED2A2A6D579A92A31498BC25E555CAC03E0E84 |
| app/agents/skills/versioning.py | EAFB73AA81638DA987D11D3029CE5F72148905794EAFAF34BBBA33CF14D43E03 |

## 未验证项与交付结论

本角色未执行发布账本升级演练、全局 history 100 条溢出、全部输入数值/重复键反例、全量 pytest、ruff/mypy；这些由其他实施/审查角色负责，不以本报告宣称通过。未验证真实模型生成质量、DB 持久历史链、生产并发或全局 Trace 持久化；用户批准方案保持 DB/API 发布版本兼容且不承诺这些能力。

首轮没有发现业务缺陷，发现一个测试契约假设错误并明确隔离。关键真实入口反例已通过；HTTP 更新最终链的修正后通过证据归主 Agent，本角色无独立最终全绿声明。本报告不代行用户业务验收，不将任务改为 done，也不替代后续未参与实现者的反例审查及最终人工审核。
