# 工作流告警验收缺陷修复独立审查

日期2026-10-21。独立Agent migration_review按task-card-delivery第7步及Review驱动修复回路复核；未参与本轮源码实现，未修改源码或正式测试，只新增本文与data隔离探针。不是最终人工审核或用户业务接受。

## 结论与边界

原验收P1（异常原文扩散至新增告警/日志表）与P2（创建者告警API文档契约缺失）在本轮核验范围内已修复，未发现新增未修复P1/P2。不据此关闭FLOW-005/PWFL-004。

已读取项目基础规则、适用00/01/07/09/11规则、原始任务与批准修复计划、上轮用户验收报告，审查相对3acd233d370ad50339c0cce616bf0243fd25c58d的engine/audit模型/README/Swagger变更及新privacy测试。README扩围依据用户明确批准。实现以稳定受控reason生成摘要，删除告警写入器原文参数，结束日志不再拼接row.error。精确分类避免异常内容中TIMEOUT/CANCELLED标记覆盖重试耗尽分类。原重试、超时、取消与trace关联未修改控制路径。模型说明已改成终态后开始/结束摘要，不再声称每次尝试或实时进度。

原WorkflowExecution.error仍可包含未脱敏异常；本次仅阻止向新表扩散，不代表全系统脱敏。未清理历史数据、未操作真实库、未增加依赖或权限模型。告警仍可保存用户提供的workflow名称等既有业务属性，本报告的“不扩散”特指异常/Prompt原文传播路径，不能扩大为任意字段均无敏感信息保证。

## 实际反例与验证

证据类别E2，合成Smoke/Adversarial，真实Engine/HTTP契约/隔离SQLite，不是生产数据或Gold。正式privacy用例从Bridge异常触发，关闭原Session后读取告警与日志，涵盖Bearer、API Key、URL凭据、私有输入、伪造超时/取消文本；新表无合成标记，1告警2日志共享trace。已有execution/owner告警、旧重试、timeout/cancel及admission用例均实际复跑。

独立stdin探针另建UUID命名SQLite，配置retry=1，真正调用Engine并新Session回读，四项载荷为：TIMEOUT加换行Bearer、嵌套重试前缀加API Key、700字符后URL凭据、CANCELLED加私有输入；同时Prompt含独立私密标记。各项均观察到Bridge调用2次、1告警2日志、reason=retry_exhausted、error_message=WORKFLOW_RETRY_EXHAUSTED，告警及日志所有model_dump字段均无SYNTHETIC_标记且trace一致。证明安全摘要不依赖500字符截断、不因异常控制码或私有Prompt回显失效。该探针保留旧执行error行为，没有将其误判为新表泄露。

API独立核对完整GET /api/v1/workflows/alerts操作及WorkflowAlertOut与运行时OpenAPI完全相等，涵盖鉴权声明、响应引用；正式契约用例也验证README端点存在。原owner告警HTTP租户隔离用例仍通过。未新增或改变接口权限。

## 命令、结果与日志

Windows11；指定D:/DepTooL/anaconda3/envs/ai-assistant/python.exe（Python3.12.0）；依赖无变化。DATABASE_URL=sqlite:///./data/alert-fixes-independent-20261021.db、PYTHONUTF8=1；conftest保持离线网络与配置隔离。

```powershell
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest tests/test_workflow_alert_privacy.py tests/test_workflow_execution_alert.py tests/test_workflow_owner_alert.py tests/test_workflow_retry.py tests/test_workflow_timeout_cancel.py tests/test_workflow_admission_review.py -q --basetemp=data/pytest-tmp/alert-fixes-independent-20261021
```

48 passed，11.04秒，exit0；1条预期SAWarning源于测试故意制造重复ORM身份的失败事务。日志data/alert-fixes-independent-20261021.txt。独立stdin探针用相同解释器python -，先runpy执行tests/conftest.py隔离，再为每项创建Tenant/User/Workflow，经自定义Failure Bridge与no_delay sleeper调用Engine；四项及完整API契约断言exit0，日志data/alert-fixes-independent-probe-20261021.txt。仅探针不作为正式永久回归套件；未更改冻结测试输入。

已读data/alert-review-fixes-red-20261021.txt：正式7项先前确实7 failed（六项安全传播、一项契约缺失），属于有效业务红测；本Agent未假称亲自重跑历史红版本。当前实际48项覆盖这7项修后结果。

## 最终版本绑定与未执行项

HEAD/基线3acd233d370ad50339c0cce616bf0243fd25c58d加本轮工作区，关键输入SHA256：

| 文件 | SHA256 |
|---|---|
| app/workflow/engine.py | 5BAE2E960B198E6F26786F6BB3C5123E1E1E1A51992F001B07D4F26F68F496CA |
| app/audit/models.py | DD3BBC5793A02BE9DB19E70C32F9DED3A9DA63B85B078D364314B9F0DE74B390 |
| tests/test_workflow_alert_privacy.py | 88C58CA0B25D0047D388EE09B6994045B394A188245D1C492F84408BAA2A6230 |
| README.md | 97B6BD061435D94DCFC42ECD14138AFF7C406CE15C33AEDA0B3EEFE752EA960C |
| docs/swagger.json | 9555B7654F947EB016FE24C3149CF86B10E7D92C342C41C396F05A1AA26D7A09 |

全部输入最终指纹由主流程docs/reviews/2026-10-21-workflow-alert-fixes-inputs.json承接；本报告新增不改变测试输入。相关输入变化应重验。未执行全量pytest、Ruff/mypy、前端四门、浏览器、Linux/最终Ubuntu CI，本子任务明确委托只做专项独立复核；不得把主执行者其他结果当本Agent已运行。最终门禁、页面验收、最终独立人工审核及用户业务接受仍需主流程处理。无提交、推送、合并或部署；无历史/真实库数据清理。回滚须排空活动执行后回退修复，不以回退掩盖原敏感扩散缺陷。

## 最终成功摘要修复复核（替代前轮输入证据）

主执行者新增真实Engine成功反例后发现旧摘要将成功分类为bridge_error，已通过有效红测后修复。独立读取最终两行：仅failed调用失败分类，success使用受控none；新测试实际创建成功执行并在新Session断言两条info日志且无bridge_error。此改动不改变失败reason、超时/取消、重试或告警生成条件。上表两项指纹已更新；其余输入保持。

最终使用同一pytest文件列表，DATABASE_URL=sqlite:///./data/alert-fixes-independent-final-20261021.db、basetemp=data/pytest-tmp/alert-fixes-independent-final-20261021，实际49 passed、10.37秒、exit0，1条相同预期事务SAWarning。日志data/alert-fixes-independent-final-20261021.txt。此前48项及旧指纹只作历史，不充当最终候选通过证据。

独立stdin探针在最终Engine上重跑前述四载荷，并新增成功Bridge返回私密标记场景；五项均exit0。失败保持2次尝试/1告警/2日志/受控retry_exhausted，成功1次调用/0告警/2条info日志且结束reason=none；新表全部字段无异常、Prompt或成功输出中的合成私密标记，trace一致。最终探针日志data/alert-fixes-independent-probe-final-20261021.txt。未修改源码或正式测试；本次最终专项与独立反例未发现新增未修复P1/P2。主流程旧fullretry全量证据不再适用，最终全量/其他门禁仍以重新冻结候选执行结果为准，本Agent未运行或代认。
