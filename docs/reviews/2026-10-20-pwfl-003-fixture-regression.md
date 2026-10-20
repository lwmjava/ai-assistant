# 组合回归夹具归因

2026-10-20，主Agent /root。首次组合回归：155 passed / 1 failed / 3029 deselected / 2 warnings，65.66秒；data/pwfl003-related.txt。

失败为test_cron_trigger_cancel_does_not_cancel_caller等待Bridge进入超时。同文件job夹具用固定timeout-job，在同实际Engine下跨测试删除再重建，前面的cron取消测试已消费当前due。单独执行可通过；生产创建默认新UUID，删除成功会forget。此为新增周期状态暴露的夹具身份复用，不删除断言、不放宽去重、不修改安全设置。

修复计划：job使用Workflow默认新ID，各测试独立身份，保持原成功/失败/取消/租户断言；重跑组合回归并由独立审查核对。原失败不记通过。
