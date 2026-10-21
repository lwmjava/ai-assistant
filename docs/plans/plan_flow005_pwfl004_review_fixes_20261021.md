# 工作流告警验收缺陷修复计划

日期2026-10-21。基线3acd233d370ad50339c0cce616bf0243fd25c58d。用户明确要求修复验收问题，并单独批准将README.md加入FLOW-005 allowed_paths。原三个无关未跟踪文件与上轮验收报告保持不动。

## 目标与范围

先修PWFL-004：新增日志与告警不再接收不可信异常原文，使用受控原因码和摘要；trace_id、失败一条告警、租户隔离及重试行为保持。再修FLOW-005：同步告警API的Swagger、README并增加契约测试。只涉及app/workflow/engine.py、tests/、docs/、README.md、tasks.yaml；不引入依赖，不改模型/权限/历史数据，不推送。

## 方案

日志只保留执行来源、受控status与reason；告警error_message由内部reason映射为稳定错误码，不依赖异常字符串正则是否覆盖。错误分类仅接受内部精确值或既有重试耗尽前缀，避免异常原文伪造timeout/cancel分类。既有WorkflowExecution.error原始存储不在本次新表修复范围，明确残余边界，不声称全系统异常脱敏。

新测试从真实Engine触发合成Bearer/API Key/URL凭据/原始用户输入回显，关闭原Session再读新表，断言无原文及安全reason/trace_id一致；保留成功、timeout、cancel、retry、setup回归。API契约比较runtime与导出的路径、响应模型，README存在新端点。先运行有效红测后实现。

## 验证与验收

沿用上一轮155项专项、Ruff/mypy及前端四门为开发前基线（输入未变且已逐项指纹核验），本轮候选独立冻结最终输入，再执行全量pytest、Ruff/mypy、前端typecheck/test/lint/build。指定conda解释器与隔离SQLite。补真实浏览器验收，工具不可用时准确记录阻断，不伪造截图；最终同SHA Ubuntu CI待远程提供，不绕过门禁。无未修复业务P1/P2且本地回归通过后记录in_review交付，不自行done。

## 回滚

停止新触发并排空执行后回退改动，无迁移或真实数据库清理。若旧告警含真实敏感数据，需要单独授权的清理流程，不在本次代码修复时读取或删除。
