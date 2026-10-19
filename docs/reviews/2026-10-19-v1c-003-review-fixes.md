# 独立入口审查驱动修复

独立Agent /root/v1c003_validation 首轮38项：26通过、12失败，15.47秒。原始输出data/v1c003-baseline-20261019/review.txt，实际退出1。两项失败属于测试把既有error终态误写为fail，按真实契约修正，不改变业务要求。其余为本卡必须修复缺陷：

1. **P2：畸形技能绑定导致旧checkpoint被删除**。AgentStateService在Service的恢复校验前先调用StateManager.valid_snapshot，失效后discard。正式反例覆盖list_mode/bool_version/nan_version/string_tools×普通/流式，旧行消失。修复：在批准路径chat_skills/ChatService的原存储读取前，按相同主体/租户/会话/消息读取活动行，仅预检技能绑定；typed拒绝先于会删除损坏行的兼容读取器。保留原行/JSON/阶段/预算，不修改存储算法或放宽快照校验。
2. **P2：取消无技能终态元数据**。当前取消传播与零成功审计成立，但只记录matched/bound。修复：在两种工具执行await处仅记录cancelled并原样抛出，不新增执行或checkpoint覆盖。
3. **待反例验证的静态边界**：低分拒答早返回应保留技能missing/unsupported确定性说明；结构化reason必须实际消费授权Prompt和离散新鲜度校验。先补正式反例，再在原终态/推理调用点做最小修复；不交给后续卡。

以上均在已批准范围内；原始业务契约不变。修后由独立验证Agent复跑反例，主线程回归、Ruff/mypy及最终全量绑定新输入。

后续正式反例补记：

- fast工具执行元数据误标self：主线32项30pass/2fail，10.07秒；通过独立skill_path保留fast.tools实际路径，禁止仅末尾提示记录正确而执行记录错误。
- 恢复同版本仍重新语义选择及memory await后切Supervisor忽略旧checkpoint：新增7项5fail/2pass，5.23秒；原绑定恢复应从当前授权清单重建精确上下文而不重新选型，并比对mode/tool_names/versions；不支持该checkpoint的编排必须拒绝，保留旧行。
- **P1：结构化工具事件yield后撤销技能仍启动工具**：同组正式反例显示消费者收到tool.call后停用仍产生副作用。修复为yield返回后紧贴实际executor await前再复核。失败拒绝不覆盖成功前缀。
- 空工具绑定与不可信工具名元数据：6项4fail/2pass，4.00秒，empty-metadata-red.txt。空集合要确定性说明不可用；日志只保留已知注册且合法标识的工具名，未知/带换行名称固定unknown，不把模型内容写日志。

最终首轮全量：2 failed / 2795 passed / 3 skipped / 3 warnings，623.82秒，退出1。tests/test_error_model_boundary.py::test_supervisor_preserves_typed_model_failure 与 tests/test_p0_regression.py::test_supervisor_draft_sends_chatmessage_objects 使用SupervisorGraph.__new__绕过构造器，在原有节点调用上新增skill_runtime属性不存在。修复方案：可选技能运行时有类级None默认值，未注入实例保持原节点行为；不修改旧测试、不降低错误模型断言，保留正常构造的实例级运行时。复跑相关两文件及57独立入口，再重新执行同一最终候选的全量门禁。
