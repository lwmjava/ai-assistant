# SKILL-003 独立入口验证（2026-10-14）

验证身份：`/root/skill003_verification`，未修改业务实现或 tasks.yaml；独立构造 `tests/test_skill_semantic_entry.py`。依据原始任务交付“引入 LLM 语义匹配技能触发”、验收“语义匹配能命中关键词匹配不到的技能”，及已批准确定性优先、权限、安全回退方案。此报告为独立验证，不替代最终人工审核。

证据类别：E2 隔离集成。实际 ChatService 异步技能入口、真实 SQLModel 查询与激活代码、独立内存 SQLite、合成 SelectionProvider。未读取密钥、未调用真实模型，不能证明真实模型语义质量。两对话路径传参测试用固定安全过滤结果隔离安全步骤，验证真实 chat/chat_stream 传递脱敏后的文本；不把此测试视为过滤器质量证据。

| 入口反例/操作 | 可观察结果 |
|---|---|
| 同一请求不含包裹码，原同步匹配无命中，异步入口返回 parcel | 命中且仅有一次合成模型请求；技能正文未发送给选择模型 |
| 私有/global 技能激活 | 对应不可信围栏保留，权限与工具策略不能被技能覆盖 |
| 同租户其他成员、跨租户同名成员、disabled | 候选不可见、无命中、零模型调用 |
| 允许候选存在但模型返回跨租户名字 | 被拒绝，发送候选不含该名字 |
| SKILL_ENABLED=false、确定性关键词已命中 | 零额外模型调用，保留既有关键词命中 |
| 模型异常后再请求 | 首次安全回退，数据库技能仍启用且版本保持；第二次恢复命中 |
| chat 与 chat_stream 的脱敏传参 | 原始输入未传给异步选择入口，均收到固定脱敏文本 |
| chat 与 chat_stream 在真实异步语义请求等待时取消 | CancelledError 传播；真实 claim_execution 锁释放，随后可重新取得同一锁 |
| 完整 chat + 默认真实 AgentPipeline 成功，提交后新 Session 读取 | skill_names 为 parcel，content 与实际回答一致、status=complete；选择后的技能正文进入后续模型消息，激活 ctx 的 name/effective_version 可追溯 |

实际命令：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/test_skill_semantic_entry.py -q --basetemp=data/pytest-tmp/skill003-entry --junitxml=docs/reviews/skill003_entry_20261014.xml`。运行使用 PYTHONUTF8=1、PYTHONIOENCODING=utf-8，显式清空进程 OPENAI_API_KEY 与 LLM_API_KEY；conftest 所需共享测试库只作初始化，业务反例使用独立内存库。退出码 0，13 passed，2.06 秒，1 条既有 ast.NameConstant 弃用警告。无 skipped。

`python -m ruff check --no-cache tests/test_skill_semantic_entry.py` 退出码 0。首次默认 Ruff 缓存目录拒绝写入，改用 --no-cache 后得到真实检查结果；首次检查发现测试局部异常类缺 Error 后缀，已修改并复验通过，没有修改规则。

验证完成时原始字节 SHA256：

追加成功对话后第一次重验遇到主 Agent 同期身份复核修复：原隔离测试中的瞬态 User 不存在于数据库，7 项被新主体校验拒绝、6 项通过。此为测试前置不合格；已将 Tenant/User 实际登记于隔离库，完整对话使用数据库加载身份，并重新运行全部 13 项通过，没有绕过主体校验或放宽断言。其他独立审查 Agent 发现的 await 期间主体失效 P1 及其复核见主交付报告，本报告不宣称自行发现/关闭该缺陷。

- app/services/chat_service.py：6ec002f01defac3b3defeaa8046d5b7e61c387bded9eeeacbfcaa588dbe42b26
- app/agents/skills/manager.py：fbd20580fd1d8cab50db227fc20427b8990c5749c3841bc7fe55650cd052ccbf
- tests/test_skill_semantic_entry.py：ee4fca191a7ef8da72c5a9c525e5edad0f43fec45f28da631014a90abc0f5785

结论：上述权限、回退、兼容、脱敏传参、取消释放路径通过；未发现这些入口的未决业务缺陷。真实模型质量及最终业务授权不是本次合成验证的结论，保留对应主报告缺口，不自行将任务设为 done。未提交、推送、部署或改变任何真实数据。

