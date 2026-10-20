# ADR-0017 工作流执行超时与协作取消

日期：2026-10-20。状态：Accepted。用户明确批准 `docs/plans/plan_pwfl_002_20261020.md` 的最小扩围及生命周期方案；批准不等于实现验收。

## 背景与事实

现有手动请求及cron调用共同等待 WorkflowEngine，重试及退避没有总时限，也没有取消入口。HTTP请求和调度器分别拥有数据库Session。WorkflowExecution已有字符串status及error和结束时间，不需要改历史结构。

## 决定

每个Workflow持久化timeout_seconds，严格整数1–3600，默认300。执行开始快照时限，以事件循环单调时钟计时，包含所有bridge尝试和退避。engine拥有执行子任务和终态写入；单进程注册只保存执行/workflow/tenant/数据库绑定、任务及完成信号，不能共享Session或ORM对象给取消请求。HTTP与cron仍等待最终结果，保持/run 201响应。

超时或手动取消只取消当前子任务；等待其finally清理后，唯一所有者rollback失败事务并写failed，error分别为WORKFLOW_TIMEOUT或WORKFLOW_CANCELLED，记录finished_at与duration_ms。重复取消不重复打断清理。父调用取消时完成收尾后继续传播，不吞掉应用停机取消。成功后的webhook有原有10秒HTTP上限，不纳入已完成执行的时限。

取消API沿用workflows/write及原系统管理员跨租户规则，核对数据库记录与运行绑定，越权/错绑404，终态取消幂等返回，未找到本进程运行409。取消路由等待完成后用新Session回读终态。活动执行时删除Workflow返回409，防止级联删除历史与收尾竞争。owner缺失不回退成触发者身份。

取消是合作式行为：截止时发出取消，不保证同步阻塞、吞取消的下游或既有外部副作用能中断。不遗弃仍运行子任务，不移除其注册来伪造释放；清理实际完成才登记终态并释放。不是分布式取消、并发池或重启恢复，也不承诺硬实时资源回收。

## 选项与后果

采用现有engine包裹子任务以复用受控ChatService。只增加/run临时参数无法满足cron持久化配置，否决；改异步立即返回需要新增任务/Session长期所有权协议，超出范围，否决。单进程注册不能跨worker取消；不在本卡引入外部状态存储。历史保持failed/error兼容，调用者用稳定错误标记区分原因。

## 迁移与回滚

仅新增Workflow非空timeout_seconds列及服务端默认300，测试旧行升级及降级。仅操作隔离库，真实数据迁移另行授权。发布/回滚前排空活动执行；旧代码容忍新增列，可先回退代码，保留历史，不重放任务。回退迁移只作用配置列，不删执行历史。

## 验证

覆盖总超时、退避取消、重复/父取消、任务完成竞态、finally资源释放、失败事务rollback、新Session终态回读、跨租户/错绑/匿名拒绝、活动删除409、cron继续、真实Bridge取消与旧行迁移。版本化Smoke/Adversarial场景为workflow-timeout-cancel-v1，隔离集成不代表真实模型质量。必要全量回归、静态检查、前端契约四门及最终CI未齐时不得done。
