# 工作流超时与取消：独立入口级反例审查

日期：2026-10-20。审查身份：`/root/execution_review`，未参与业务实现。采用 task-card-delivery 1.3.0 第7步及等价 code-review-quality 检查维度；没有把实现者摘要当作验收结论。本报告不是最终人工审核，也不代替用户业务接受。

## 范围与契约

读取 AGENTS.md、推进与Harness治理入口、适用Cursor全局/架构/运行时/测试/安全/可靠性规则、tasks.yaml原始PWFL-002、批准计划、ADR-0017、当前完整业务diff、control新增文件、迁移、现有测试及WorkflowBridge/ChatService调用与scheduler调用方。

原始要求：每任务可配置超时，超时后中止并标记、释放占用；通过界面或接口取消运行任务。非目标：不跨进程强杀，不改执行历史结构。批准计划补充合作式收敛、重复取消保护、终态新Session回读、活动删除409、owner身份与租户复核。实现复用原Bridge及ChatService，没有新增Agent Runtime。

## 实际尝试与结果

独立新增7项 `tests/test_workflow_timeout_review.py`，不是复用实现者helper断言。测试经实际WorkflowEngine和ASGI HTTP路由，使用隔离SQLite；合成bridge只作为可控长任务/失败注入，属E2隔离集成，不是生产LLM或硬实时性能证据。

| 入口反例 | 实际结果 |
|---|---|
| HTTP /run长任务，两次并发HTTP取消，下游finally等待闸门 | 两个取消请求均等待；活动注册保留；实际释放后才返回failed/CANCELLED，未重复打断清理 |
| 上述清理过程中HTTP DELETE，然后完成清理再删除 | 清理期间409，完成后200；资源释放与注册移除可观察 |
| 真实总时限触发清理，清理中连续三次取消父任务 | 清理前不结束、不移除注册；清理后父CancelledError继续传播，历史保持最先发生的TIMEOUT，并有结束时间与耗时 |
| 第一次bridge失败，backoff期间禁用owner | 再次尝试前拒绝，bridge只调用一次，有failed持久化终态 |
| backoff期间owner移出租户 | 不映射为触发者、不继续执行，bridge只调用一次，终态failed |
| backoff期间禁用tenant | 再次调用前拒绝，WORKFLOW_TENANT_UNAVAILABLE，bridge只调用一次 |
| 真实engine所有者Session的最终commit抛OperationalError | finally资源仍释放；HTTP取消503且不泄漏内部异常；执行父任务抛真实持久化错误；数据库原running保持，未伪造success或已保存终态 |
| 两个URL均为sqlite://的独立内存数据库，workflow/tenant/execution ID全部相同 | 第二库不能取消第一库实际执行；第一库绑定可以取消且清理注册；第二库历史仍为running |
| 创建/更新分别发送原始JSON的NaN、Infinity、-Infinity，共6项 | 全部422；无新增记录，原timeout_seconds仍300，没有500或部分写入 |

另实际运行实现者13项engine测试和19项HTTP测试及5项原重试测试，复核：总超时、退避超时/取消、重复/错绑取消、失败事务rollback、缺失owner、真实WorkflowBridge→ChatService→AgentPipeline取消传播、cron _trigger取消不结束调用方、吞CancelledError也不误记success、匿名401/只读403/跨租户404/系统管理员兼容、字段严格边界、终态幂等及取消审计内容。

## 命令与环境

Windows；Python 3.12.0；指定解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。`DATABASE_URL=sqlite:///./data/pwfl002-review.db`、`PYTHONUTF8=1`，独立basetemp，未接触开发或生产库。

```powershell
$env:DATABASE_URL='sqlite:///./data/pwfl002-review.db'
$env:PYTHONUTF8='1'
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m pytest tests/test_workflow_timeout_review.py tests/test_workflow_timeout_cancel.py tests/test_workflow_cancel_api.py tests/test_workflow_retry.py -q --basetemp=data/pytest-tmp/pwfl002-review-03
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m ruff check tests/test_workflow_timeout_review.py --no-cache
```

pytest：**44 passed，exit0，7.61秒**；1条预期SAWarning来自既有失败事务注入，duplicate Workflow identity，不是断言失败。新增独立测试Ruff：exit0。未声称本审查运行了全量pytest、全量Ruff/mypy、前端四门或Ubuntu CI；这些由主执行者最终证据另行记录。

## 版本绑定

基线业务提交：8e81bb289b9ed10b175041faf212f7c916074b9f。当前HEAD：8cc3b060e45d0f6686d99e94ce1ee71bf10c2f3e，加未提交源码/测试。下列SHA256绑定本次审查输入；相关输入更改后须重验受影响范围。

| 文件 | SHA256 |
|---|---|
| app/workflow/engine.py | 24D43DB0E7FB715049B12DB1E04729BE1BBAE2056FFEB42E9D69D3BA79C40FED |
| app/workflow/control.py | 8372C1B44FF90940A35A48E9A68AFF5DB970D7990806662652C3B8BACAACA85F |
| app/api/routes/workflow.py | 80F1284EFD172339CB685B9BE6F627D73FC023314E84B26B3D7B20D9325CE7E0 |
| app/models/workflow.py | E98F58DBAFD169AA7A84CE425E08773E30A617CD98EFB6DE0CB7A968C82EE62D |
| tests/test_workflow_timeout_review.py | E7B07933E6D7EB5F74A2CB603F73E4D6BB0DC1AB67481D2E9933D915E9119ED1 |
| tests/test_workflow_cancel_api.py | B1F506A38E558F81C1C7FAADEB53AFF36D27B7901A9398E6F8F5E3600CC2E6AE |
| tests/test_workflow_timeout_cancel.py | B450C34AE0BAE9EC5D6E45B4BD2B110D2D9C70AB0F485C1D3BA86090AAEB9C3F |
| tests/test_workflow_retry.py | 5A33E6991925206D04A5AB01962D9A83214C2B7759985D78DFCD394811A3485F |

## 结论和未闭合项

本次反例未发现新增未修复P1/P2业务缺陷。取消/超时的合作清理、父取消传播、身份重验及HTTP反馈在上述E2范围通过。未修改业务代码；只新增本报告与独立测试。

主执行者自查发现URL字符串不能区分两个独立内存数据库，已改用实际Engine实例身份；本审查独立构造上述双内存数据库入口反例并实际复验通过。最终44项复验也覆盖API调用方统一使用该绑定，不依赖数据库连接串。字段更新schema修复后的API也已参与此次复验。

追加修复复核：API实现者运行原始HTTP非法非有限数发现6项500，根因是严格整数拒绝后的validation error input携带非有限float，默认JSON异常响应不能序列化。批准计划末节已记录修复方案，业务修复局限于超时字段before校验：先将非有限float变为字符串，再由原strict int拒绝。实际读取修复及6项反例，未更改全局异常处理，未放宽合法输入；本审查独立重跑6项及全部入口反例/旧重试共44项通过。该已修复缺陷不再列为未决P2。

真实终态数据库写入不可用时，API明确503，注册在子任务确实释放后删除，数据库原running记录可能保留；以后取消返回409，不能声称自动恢复或终态可靠补偿。本卡未引入持久化恢复队列。此证据证明失败被如实暴露，不证明数据库不可用时仍能完成持久化。

主执行者已发现旧库auto_migrate可能直接stamp而遗漏timeout_seconds；追加app/core/migration.py范围尚待确认时，本审查不重复列为新发现，**旧库启动迁移在修复与实际重验前仍未通过，不允许关闭任务**。后续须核对最终修复及报告输入指纹。

README相邻原有“到点是否执行尚未完成”与已关闭前置任务存在文档漂移，已告知主执行者修正。最终全量回归、前端契约四门、最终Ubuntu CI、关闭对账与另一独立人工审核/用户业务接受仍由主流程完成；本报告不能单独支持done。无推送、合并、部署或真实数据迁移。
