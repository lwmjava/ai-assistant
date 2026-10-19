# 预算提示回归的最小允许路径追加

状态：等待用户明确批准，尚未修改目标测试文件。Cursor 后续报告已由用户提供；保留其业务与文档修改，以当前工作区为准。

## 当前核对

- 主 Agent 重新执行当前十文件相关回归：286 passed /1 warning，76.99秒，退出0；隔离 DATABASE_URL=sqlite:///./data/v1c004-regression-test.db，日志 data/v1c004-baseline-20261019/contract-final2.txt。
- 用户提供的 Cursor 全量报告为2881 passed /1 failed /3 skipped，752.19秒，退出1；该统计目前按用户提供的运行报告记录，不冒充本次主 Agent 重跑全量。
- 主 Agent 单独复现 test_supervisor_reports_budget_message_not_retry_hint：1 failed，2.65秒，退出1；日志 data/v1c004-baseline-20261019/budget-confirm.txt。原因是固定预算提示前新增了 not_called 事实前缀，原逐字相等断言不再成立。

## 唯一拟追加路径与补丁

`tests/test_rag_028_model_capability.py`，只改该测试中的回答断言：

```diff
-    assert state.answer == BUDGET_EXCEEDED_MESSAGE
+    assert state.answer.endswith(BUDGET_EXCEEDED_MESSAGE)
+    assert "not_called" in state.answer
     assert "请稍后重试" not in state.answer
```

保留完整预算提示为回答后缀，同时新增确定性未调用说明断言；不只检查宽泛关键词，不降低预算护栏，不改变模型调用、业务执行器或其他用例。

## 批准后的验证

登记该文件的 allowed_paths 扩展，实际运行该测试及整文件、相关契约与无筛选全量 pytest，重新绑定最终输入与静态检查结果，再完成实现说明及新的独立最终审核。之前的49项独立绿报告对应旧指纹，不能直接用于当前源码关闭任务；本轮286已重新包含独立49项，但仍须由独立角色记录当前入口复核。业务接受与最终提交CI分别保留门禁，不提交/推送为完成交付。

依据：AGENTS.md 第12节“需要修改 allowed_paths 之外文件”须先确认；任务交付技能同样禁止范围外实施。一般“继续”或转述工具结论不代替该具体范围批准。

2026-10-19 explicit scope approval: the user allowed adding tests/test_rag_028_model_capability.py solely to replace the answer equality assertion with endswith(BUDGET_EXCEEDED_MESSAGE) and require not_called, preserving the no-retry-hint assertion. No other assertions or business code changed for this scope supplement. Revalidation pending.
