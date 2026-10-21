# 工作流告警验收缺陷修复说明

日期2026-10-21。基线3acd233d370ad50339c0cce616bf0243fd25c58d。用户要求修复FLOW-005/PWFL-004验收问题，并明确批准README.md最小扩围。原三个无关未跟踪文件保留；本次未操作生产数据或外部服务。

## 实际行为

工作流最终失败时仍保存一条告警、两条终态摘要及共同trace_id。告警error_message现在由内部reason映射为稳定代码（如WORKFLOW_RETRY_EXHAUSTED），结束摘要只含受控status/reason（成功时reason=none），不再复制下游异常原文。含Bearer、API Key、URL凭据或私有用户输入的异常无法经此路径进入新增日志/告警表；异常文字伪造WORKFLOW_TIMEOUT/WORKFLOW_CANCELLED不改变内部重试耗尽分类。成功、超时、取消、创建者失效与有限重试原行为保持。

日志仍在终态后统一写入，模型与实现说明已澄清这不是实时或逐次attempt完整轨迹。原WorkflowExecution.error既有存储及业务名称等快照不在异常复制修复范围；历史告警未自动清理，不宣称全系统所有字段已脱敏。

FLOW-005的GET /api/v1/workflows/alerts与WorkflowAlertOut已同步Swagger，README新增权限口径。正式契约测试比对运行时operation/Schema与导出，并检查README端点存在。用户已批准README追加路径，tasks.yaml只登记该最小范围及当前进度，不改变原验收或非目标。

## 修改位置

app/workflow/engine.py：安全摘要/内部原因码、严格错误分类；app/audit/models.py：摘要粒度注释。tests/test_workflow_alert_privacy.py：六种合成不可信异常加API文档对账。README.md、docs/swagger.json：新增告警契约；tasks.yaml：批准范围与in_review进度。原实现说明补记安全修复；本文件及docs/reviews记录实际交付，不替代原始验收契约。

## 验证

先补有效红测7 failed（真实Engine的新Session回读中出现合成凭据、导出缺路由），再实现后18项相关测试通过。原验收脚本不改断言复跑：privacy.pass=true，新增告警/日志合成凭据命中均为0，失败一条告警两条log且trace关联、超时分类正确，runtime/export/README端点均存在。

独立审查/migration_review最终49项回归及5个独立探针通过；其未参与实现，报告见docs/reviews/2026-10-21-workflow-alert-fixes-independent.md。Ruff全仓、mypy app/与前端typecheck/test/lint/build均exit0。737项检查输入已冻结；无筛选最终全量pytest3213pass/3skip/4warnings/724.71秒/exit0，最终统计与独立终审以交付报告为准。

## 页面实际验收

127.0.0.1:8769运行真实FastAPI+本次frontend/dist，独立SQLite、合成用户与任务、真实密码哈希和JWT登录，不覆盖鉴权依赖。conftest先隔离.env/密钥，uvicorn lifespan off避免启用真实调度或模型调用；仅绑定loopback。原始合成owner禁用，经真实Engine生成告警。

IAB中ui-admin-a登录→工作流页，显示任务A、原创建者synthetic-owner-a、已禁用原因；刷新后仍显示。退出后ui-admin-b登录，仅显示任务B与owner-b，未显示A。截图docs/reviews/2026-10-21-workflow-alert-ui-tenant-a.png及tenant-b.png；当前截图584×668，信息无截断。页面与真实API返回均通过，但没有单独采集浏览器console日志，不声称完成全套跨浏览器/多尺寸视觉QA。服务端GET工作流/告警和登录均200。测试过程中停用任务的运行返回failed，未调用真实模型；仅修改隔离测试数据。

## 未验证与回滚

最终同提交Ubuntu CI未取得；用户业务接受仍需明确决定，不自行done。原验收报告保留失败历史，新的修复证据与其区分。无新依赖、数据库迁移、生产数据读取/清理、推送、合并或部署。回滚先排空执行再回退本次代码与文档；不清理旧告警或回放任务。


终态摘要最终复核补记：曾发现success误用失败分类，已先红测再修正success为none；相关19项通过，最终审核独立37项通过。此前全量输入失效并已停止，最终源码以重冻结737输入与workflow-alert-fixes-final-20261021全量结果为准。


最终本地检查已齐：737输入重算变化0，完整3213pass/3skip/4warnings，Ruff/mypy与前端四门退出0；最终独立报告及CI/业务接受状态见修复交付对账。此前中断/过期全量不当作通过证据。


关闭登记（2026-10-21）：用户明确接受并要求关闭；同业务候选11f36aa的Ubuntu Backend/Frontend/Agent official CI均success，当前737输入与最终完整回归匹配，关闭前独立163项通过。对应任务已done，详情见docs/reviews/2026-10-21-workflow-three-cards-close.md。上文待验收内容保留为历史时点记录，不代表当前仍有该阻断；原非目标与残余边界保持。
