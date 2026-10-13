# PAGT-005 独立入口反例审查

日期：2026-10-13。独立 Agent 会话身份：`/root/pagt005_counterexample_review`。未参与业务实现与工程计划审查；只写本报告和 `tests/test_skill_version_counterexamples.py`。采用实际读取的 `C:/Users/123/.agents/skills/code-review-quality/SKILL.md`，以逻辑、安全、兼容和可测试性审查，不代行用户业务验收，不提交或发布 Git。

依据：原始 tasks.yaml PAGT-005（版本与变更留痕、内置回归、内容改后版本变化可追溯），用户批准的 `docs/plans/plan_pagt_005_20261013.md`，工程计划审查，task-card-delivery v1.3.0、AGENTS.md、04-skills.mdc。读取最终 base/loader/manager/versioning 全部业务源码、对应有效 diff（排除行尾变化），只读 SkillService 的 Manifest/版本映射及 ChatService `_match_skills`。ChatService 仍先经 Service 的可见集再至最多一个技能匹配，非 builtin 仍经过不可信围栏；未新增权限授权。执行版本不被用作权限凭据；DB/API 声明版本不自动递增是用户批准的兼容语义。

## 实际尝试与初轮结果

自行编写并运行正式反例，均从实际 Manager/真实临时文件与目录进入，不以实现者 helper 摘要作为结论。

| 反例 | 最终业务断言 | 初轮结果与证据类别 |
|---|---|---|
| 注册内容更新后仍持有旧 match | 旧注入正文仍是 original，并携带旧有效版本；新 match 注入 replacement 且有效版本不同 | 通过，E2 真实文件 load/reload/match/activate/inject |
| list_all 嵌套对象与 history 外部篡改 | registered 正文与触发条件及历史保持不变 | 通过，E2 真实文件加载后执行 |
| 部分文件坏 YAML | reload 明确失败，旧 original 继续执行，历史不变 | 通过，E2 |
| 实际目录被移走 | reload 明确失败，旧 original 继续执行，历史不变 | 通过，E2 |
| 合成私有正文及绝对来源路径 | 真激活的正文含合成标记，日志与历史不含标记/来源路径 | 通过，E3 直接注册与真实激活，无真实敏感信息 |
| 同发布号追加另一内容、历史前缀重写 | 从实际文件/账本门禁进入，分别以 version_order/history_rewritten 明确拒绝 | 通过，E2 |
| 单例首次加载坏 YAML，修好后再次调用 | 首次失败后允许再次加载已修复目录并实际注入 original | **失败，P2，E2** |

初轮命令：设置 `RAG_BACKEND=native`、`DATABASE_URL=sqlite:///./data/test_ai_assistant.db`；`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/test_skill_version_counterexamples.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt005-counterexamples`。退出码 1，6 passed / 1 failed，0 skipped，2.47s（命令墙钟 6.54s）。pytest 时隙由 root 明确授予，串行运行；未接触生产数据、外部网络或收费模型。

## 发现与修复回路

**[P2] 首次严格加载失败污染进程单例，修好文件后不能自动重试。** `get_skill_manager` 在 `load()` 成功前即赋 `_skill_manager = SkillManager()`。严格文件加载抛出安全 ValueError 后，留下空 Manager；下一次调用直接返回空对象，不重新加载已修好的目录。反例 `test_independent_singleton_failed_initial_load_can_retry_real_directory` 行 134 实际拿到空 prompt_injection，而不是 original。该情况由本卡从宽松跳过转为严格 load 激活，应纳入首次失败重入/原子状态保证，不能以入口暂不用于 ChatService 为由忽略导出的公共入口。

建议先构造局部 candidate，candidate.load 成功后再发布为全局单例。已向 root 发送正式失败证据；审查者不改业务代码。**初轮结论：存在未修复 P2，当前不可关闭，待主实现修复及本独立 Agent 重验。**

初轮执行源码 SHA256：base `54c3e59a6458f226e5ab32823504c148bd306960372d3d3924dd0c5ea2d0abd4`；loader `7ba8b1327bf9819441425a91c5052176059cf4121078cd6076649dd2b0623454`；manager `706382fd196b4076c05425e7b8ed2a2a6d579a92a31498bc25e555cac03e0e84`；versioning `eafb73aa81638da987d11d3029ce5f72148905794eafaf34bbba33cf14d43e03`；独立反例 `88df10df16859a1fbc7769cae7b00c27d258894e8589a34f03a81b99135fa22c`。工作区基线 HEAD `a0d02eb411a53c3191e81f0fc99528ea60d0d3c4`，存在未提交修改，故以文件指纹而非 HEAD 单独绑定证据。

## 修复后最终独立复验

主 Agent 已落盘 `docs/plans/plan_pagt_005_review_fixes_20261013.md` 并修复：局部 candidate 完整加载后才设置单例；极大置信度先区间判断；深 JSON RecursionError 固定错误收口；canonical 统一负零/正零。审查者重新直接读取最终相关源码与真实入口测试，未修改业务代码。

实际独立命令（均同上指定解释器及测试库配置，pytest 串行）：

- `python -m pytest tests/test_skill_version_counterexamples.py tests/test_skill_versioning.py tests/test_skill_manager_versions.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt005-counterexamples-final`：退出 0，**82 passed / 0 failed / 0 skipped，2.46s**。原单例反例现实际重新加载修复后的目录并执行 original；极大整数、负零等正式边界回归也通过。
- `python -m pytest tests/test_skill_version_entry.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt005-counterexample-http-final`：退出 0，**10 passed / 0 failed / 0 skipped，17.15s**，1 条既有 ast.NameConstant DeprecationWarning。亲自运行真实 HTTP 创建/更新、提交后新 Session、下一轮 Chat Provider 消息及安全日志的版本变化，租户/owner 同名隔离、global/private 最终选用、内置约束传递/不匹配、实际 L2 Executor 无副作用拒绝，均为 E2；合成 Provider 只证明约束进入，不证明真实回答质量。
- 显式调用 `validate_release_ledger(Path('app/agents/skills/builtin'), Path('app/agents/skills/builtin/releases.json'))`：退出 0，打印 real_builtin_ledger_valid；实际两内置文件与持久账本当前内容绑定有效。它与临时文件正式反例共同支撑发布门禁，不宣称独立防篡改。
- `python -m ruff check tests/test_skill_version_counterexamples.py --no-cache`：退出 0，All checks passed。

最终结论：**原 P2 已独立复验修复，未发现未决 P1/P2，入口反例审查通过。** 该结论仅是工程入口审查，不等于最终人工审核或用户业务验收；本报告后若业务源码再变，应核对指纹并重验受影响项。

最终 SHA256（相对仓库根；指纹变化包含根 Agent 恢复业务文件为 HEAD 的 LF）：

| 文件 | SHA256 |
|---|---|
| app/agents/skills/base.py | 762ad5c9e0560e639f2e2cc1ab29dd0f0f384806817ec61264ef6bbcb2dcf5ce |
| app/agents/skills/loader.py | c347a53a270bd1ecd0cce7c5f06525b1007d64d2fc64c491c0128ce95a72d0aa |
| app/agents/skills/manager.py | 29c8ee266842dafcbbad12813525b0b4aa10232a453f9a37554115a84272a809 |
| app/agents/skills/versioning.py | e87082e90da844e5ee26f61624783f469240df7a84c49f0ebf28280a40b998b0 |
| app/agents/skills/builtin/releases.json | 5095600808f22c11ddafb3c1fd3cf5a6adfa1b39ee5bd1b9a97ff19d246a9a15 |
| app/agents/skills/builtin/code_review.yaml | 393163fa0120c352b40e51f3498d304d92a5ee0929f330fe3d5cc4c2b32500ef |
| app/agents/skills/builtin/translator.yaml | b26ce002d2440433dda2e5ffbb84b5cb18241ff2510d4cf21f0407d39e15e262 |
| app/services/skill_service.py | 5d0050794ac3505ad4834deecb922163c16ceb578c18c376bd10dea3aad250e3 |
| app/services/chat_service.py | 616ad0d7db6d4c371246547a1ccab35b957b02349b1cc91725104d7fbe947868 |
| tests/test_skill_version_counterexamples.py | 88df10df16859a1fbc7769cae7b00c27d258894e8589a34f03a81b99135fa22c |
| tests/test_skill_version_entry.py | 12c3586d8e76b27ae021d4a48d252afd46052ca044cb447e65b32c09606c68b4 |

## 审查边界

未运行真实付费模型或人工翻译/代码审查质量测评；不宣称生产质量。数据库长期版本正文、跨进程历史、页面新增交互不在批准实现内。账本历史完整性以可信前缀比较与 Git/审查共同约束；两个可同时改写文件不构成防篡改审计。最终工程完成仍需主 Agent 的完整自动验证、关闭对账和另一位全新独立人工审核 Agent；本 Agent 不兼任该角色。
