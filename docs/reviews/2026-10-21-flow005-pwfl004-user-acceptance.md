# 工作流双卡用户请求验收

日期：2026-10-21（Asia/Shanghai）。执行者：本会话/root，未参与这两卡的实现或已有批次审查。用户当前授权为“帮我对FLOW-005，PWFL-004做验收”；本轮只验收、记录反例，不修改业务代码、测试、任务状态或既有说明，不提交/推送。未把前序卡完成当作本次证据。

候选HEAD：3acd233d370ad50339c0cce616bf0243fd25c58d。开始时有三个其他任务的未跟踪文件：proposal_cross_project_reuse_20261021.md、proposal_milvus_default_20261021.md、cross_project_reuse_inventory_20261021.json，均原样保留。最终16项关键源码/契约/依赖输入SHA256见[输入与日志指纹](2026-10-21-flow005-pwfl004-user-acceptance-inputs.json)，复核变化0。

## 验收结论

| 卡 | 业务验收结果 | 关闭结论 |
|---|---|---|
| FLOW-005 | 停用告警、同租户tenant_admin查询、跨租户隔离与重复触发主路径通过；API契约文档同步失败，页面实际验收未完成 | 不通过完整交付验收，保持in_review |
| PWFL-004 | 一次失败一条告警、trace_id关联、成功无告警与重试归并主路径通过；新增告警/日志持久化未脱敏错误原文，安全反例失败 | 不通过，存在P1，保持in_review |

本次结论不同于此前批次报告的“可交付验收”：新增独立反例已证明安全与文档缺口。不能用155项专项全绿覆盖这些失败，也不据本报告关闭任一卡。

## 必须修复的发现

### P1：执行失败异常原文进入新增日志和告警（PWFL-004）

真实入口WorkflowEngine.execute，独立SQLite，合成Bridge抛出带“Authorization: Bearer <合成标记>”的RuntimeError。执行终态保存后，关闭原Session，以新Session读取WorkflowExecutionAlert与WorkflowExecutionLog：合成标记分别出现1次，预期均为0。没有读取、打印或使用真实凭据，这不是实际凭据泄露事故报告。

根因：app/workflow/engine.py:281将row.error直接拼入结束日志；:287-288把row.error传入告警；:401仅将message截断500字符，:422也仅截断error_message，并未脱敏。原有WorkflowExecution.error保存原文属于既有行为，但本次把它复制到新的持久告警/日志表，扩大了存储面。AGENTS.md §8与09-observability规则明确禁止日志/Trace保存凭据和未脱敏数据；截断不能代替脱敏。

修复要求：告警与日志只保存稳定安全错误码/受控摘要，或使用既有脱敏能力处理不可信异常；不得拼接原始异常内容。覆盖Bearer、API Key、含凭据URL、用户输入回显等合成反例，同时保留trace_id与安全原因分类。若需治理原始执行历史error的既有问题，按其文件边界单独确认；本次新表问题不能因既有error字段而豁免。

### P2：新增创建者告警接口未同步README与Swagger导出（FLOW-005）

当前运行时OpenAPI包含GET /api/v1/workflows/alerts及WorkflowAlertOut；docs/swagger.json没有该路径，README也没有该端点。诊断脚本实际比较runtime app.openapi()与导出JSON，前者true、后者false；全文查找README同样false。

这违反AGENTS.md §11“API/Pydantic Schema必须同步README、Swagger导出和契约测试”。修复应同步两份文档并补契约对账断言，保留原有鉴权与租户隔离。README不在FLOW-005原allowed_paths，实施此修复前需按§12取得该文件的最小扩围；本次未擅自修改。

## 原始契约对账

| 原始要求 | 实际操作链/证据 | 类别 | 结果 |
|---|---|---|---|
| FLOW：owner禁用/移出租户导致suspended_owner并有告警 | 原正式owner_alert测试，经真实Engine持久化、新API会话回读 | E2隔离集成，合成身份/Bridge | 通过 |
| FLOW：tenant_admin可见工作流、原创建者、原因 | 同租户HTTP200字段断言；另一租户查询不含原租户告警 | E2真实API/SQLite | 通过 |
| FLOW：重复不重复告警、scheduler删除owner | 原幂等与scheduler用例，纳入69/155项复验 | E2 | 通过 |
| FLOW：页面展示 | 阅读AlertsStrip源码不能代替页面验收；尝试获取当前Chrome标签时cua_repl超时并重置，无页面截图/交互证据 | 未验证 | 不得标通过 |
| PWFL：一次失败一条可查询告警 | 失败Bridge、有限重试、独立新Session查询；本轮额外超时实际终态复核1条alert | E2 | 通过正常持久化路径 |
| PWFL：日志按trace_id查询并关联告警 | 原get_logs_by_trace_id租户过滤测试；独立反例实际2条log与1条alert同trace_id | E2 | 通过终态日志关联 |
| PWFL：权限/隔离 | 原查询函数显式tenant_id过滤，别租户查trace为空 | E2数据层 | 通过显式过滤路径；内部函数默认None不代表已鉴权API |
| PWFL：日志及告警脱敏 | 实际Engine抛合成Bearer异常，新Session回读命中合成凭据 | E2反例 | 失败/P1 |
| API变更文档同步 | runtime与Swagger JSON实际比较、README全文检索 | 确定性契约核对 | 失败/P2 |
| 非目标：不外发邮件/Webhook告警、不做规则界面 | 原始卡、实现与调用方审阅，没有外部模型/通知请求 | 范围核对 | 未扩张 |

PWFL-004“可查询”按原卡与本批批准方案解释为数据层查询，不把缺少新API本身判作缺陷。数据层函数不校验管理员身份，文档中的“管理员可查”不等于存在可登录查询端点。运行中实际日志数为0，两条开始/结束日志均在终态后生成；因此不能声称有实时轨迹或每次attempt日志。这不单独阻断原卡最小trace_id关联要求，但与原实施计划§3.3的每次attempt日志及模型docstring有漂移，说明应收敛，不能写成完整执行轨迹。

## 实际命令与结果

平台Windows11；Python3.12.0，指定解释器D:/DepTooL/anaconda3/envs/ai-assistant/python.exe。依赖版本与SHA见inputs.json。PYTHONUTF8=1，conftest屏蔽本机.env及模型密钥；全部隔离SQLite与合成provider，不访问生产库或收费模型。

1. DATABASE_URL=sqlite:///./data/accept-flow005-pwfl004-20261021.db；`python -m pytest tests/test_workflow_owner_alert.py tests/test_workflow_execution_alert.py tests/test_workflow_admission_review.py tests/test_workflow_timeout_cancel.py tests/test_workflow_schedule_contract.py -q --basetemp=data/pytest-tmp/accept-flow005-pwfl004-20261021`：69 passed、1预期事务SAWarning、20.80s、exit0。[归档](2026-10-21-flow005-pwfl004-user-acceptance-69.txt)。
2. DATABASE_URL=sqlite:///./data/accept-alerts-workflow-20261021.db；`python -m pytest -k workflow -q --basetemp=data/pytest-tmp/accept-alerts-workflow-20261021`：155 passed、3053 deselected、2 warnings、43.36s、exit0。[归档](2026-10-21-flow005-pwfl004-user-acceptance-workflow.txt)。两warning为既有AST弃用与事务故障注入SAWarning。
3. `python -m ruff check . --no-cache`：exit0；`python -m mypy app/`：222 source files、exit0。
4. frontend：`npm run typecheck`、`npm run test`、`npm run lint`、`npm run build`分别exit0；Vitest18 files/167tests passed。依赖未变，沿用已有node_modules，未重跑npm ci。
5. DATABASE_URL=sqlite:///./data/accept-probe-bootstrap-20261021.db；`python data/accept_alerts_probe_20261021.py`：脚本执行exit0，但JSON的privacy.pass=false是明确业务失败，不能记为通过。脚本每次建立不同临时SQLite文件，通过tests/conftest.py隔离配置，不删除真实数据。可用`python docs/reviews/2026-10-21-flow005-pwfl004-user-acceptance-probe.py.txt`复跑相同诊断；脚本源码以.txt归档，避免作为项目新增模块影响Ruff输入。[结果JSON](2026-10-21-flow005-pwfl004-user-acceptance-probe.json)。

没有修改业务、测试、依赖、配置或CI；已复现阻断问题，本轮不重跑约千秒的全量pytest来代替缺陷验收。旧实现说明的3205pass是历史执行，修复测试/查询过滤后最终同SHA的全量输入清单与CI证据未在本轮独立核验，不冒称本轮全量通过。

## 外部证据与关闭限制

GitHub connector按候选SHA查询workflow runs返回空数组，该工具明确只筛PR触发第一页；combined status也返回空statuses。因此本轮没有取得同SHA Backend/Frontend/Agent official run/job URL，不能据空数组认定失败、成功或“没有CI”。当前Chrome GitHub Actions标签读取超时，未操作重跑、推送或远程设置。

最终结论：两卡保留in_review，不修改tasks.yaml已有状态。修复顺序建议先阻止异常原文进入新表并增加正式安全回归，再同步API契约文档、补页面实际验收与最终CI/全量版本证据；完成后独立复验。没有提交、推送、合并、部署或生产操作。只新增本次验收报告/指纹/诊断归档，其他工作区文件原样保留。


后续修复追踪：用户已要求修复并批准README最小扩围，新的修复与复验状态见[修复交付对账](2026-10-21-workflow-alert-fixes-delivery.md)。本报告保留原候选失败证据，不把后续结果倒写为原候选通过。
