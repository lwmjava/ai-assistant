# EVAL-003 独立 AI 受托业务验收（2026-10-26）

结论：**本轮业务接受不通过**。核心生命周期专项65例通过，但新增P2反例表明Agent任务在一次调用后异常或取消，持久任务及其API仍显示调用0次；失败/取消保留了正确终态，却丢失必要的调用追溯。此结论不追溯修改历史done，也不否定历史样本的真实质量结果。未修改业务、测试或任务状态，未提交推送。

验收者为独立子Agent `/root/eval003_business_acceptance`，不是实现者；依据用户明确委托做AI业务场景验收，非真人业务接受。未阅读本轮技术review或主Agent发现。本轮只写本报告和忽略目录探针；保护开工已有NFR-005未提交改动。

## 输入与边界

- 分支 `feat/EVAL-002`，开工/验收HEAD `2549b43cf94e3a2f8ea0be0a8df618432bdd10a3`。历史业务候选 `1c8da78e6316c719f54d34418c8743f53f5165f1`。
- 读取AGENTS.md、匹配全局/评测/安全规则、EVAL-003卡和指定组合验收记录。基线已有修改：alembic/env.py及三份既有迁移、app/core/migration.py、app/models/rag.py、app/models/user.py、app/rag/service.py、tasks.yaml；另有NFR-005迁移/计划/工具/测试未跟踪文件。未改动它们。
- Windows，Python 3.12.0，解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`；pytest8.4.2、SQLModel0.0.22（完整包版本/输入指纹在本次result.json）。隔离SQLite；专项框架清空模型密钥、禁外网、禁读工作区dotenv，显式local只作为测试配置。
- 本轮无外部模型请求、无新增费用、无生产库读写；未运行浏览器。首次独立探针在应用导入时使用默认Settings加载机制，未手工读取或输出dotenv；最终探针已在导入前禁用dotenv源并阻断非loopback网络，最终结果仅依据该版本。

## 本轮实际场景与结果

| 场景/期望 | 实际 | 结论与证据 |
|---|---|---|
| 同key同请求复用，异请求冲突 | API返回202并复用同ID；Service异版本抛Conflict | 通过；test_evaluation_jobs前两例 |
| 未批准预算新增调用0，角色与租户拒绝 | 非零调用预算/非允许模式拒绝；非平台管理员403，禁用/撤销身份拒绝 | 通过；jobs/API/quality专项；均合成数据，无真实调用 |
| 排队任务取消，不运行已取消队列项 | API取消queued→cancelled，重复取消409；Dispatcher有界领取并跳过已取消项 | 通过；jobs专项真实Service/Dispatcher，runner为合成 |
| 运行取消关闭调用，禁止后续调用 | 慢Mock调用被取消，provider清理完成，再次调用拒绝 | 通过；runtime_guards第二例；不是本轮浏览器点击 |
| 成功报告关联与确定终态，外部失败不改成功 | 成功保存report_id/path/SHA256；异常failed，重启interrupted；红色断言报告保持failed | 通过；jobs/orchestrator/quality专项 |
| 用量与任务/报告关联 | 正常Agent合成两次consume后持久external_calls_used=2；quality预占在请求前保存，失败/取消仍保留记录 | 通过（限定正常Agent和quality路径）；对应专项与源码核对 |
| Agent调用后异常仍保存调用次数 | 合成代理consume1次→RuntimeError；真实execute_job终态failed/provider_failed，但持久external_calls_used=0、无report | **不通过，P2**；独立探针exception_after_call |
| Agent调用后取消仍保存调用次数 | 合成代理consume1次→CancelledError；真实execute_job终态cancelled，但持久external_calls_used=0、无report | **不通过，同一P2**；独立探针cancel_after_call |

专项共65 passed/0 failed，15.98秒；这表示已有断言通过，不能覆盖新增反例。API身份检查由真实TestClient/依赖/隔离数据库执行；任务API测试抑制异步submit，Dispatcher另由其专门用例执行。orchestrator专项本身mock `_validate_job`，因此不将其称为完整权限端到端证明。

## P2：异常/取消任务低报调用数

`app/services/evaluation_orchestrator.py` 的 `_ExternalBudget.consume()` 只递增内存used；`_run_agent` 在await `_run_isolated_agent` 返回后才经 `_persist_case` 累加job.external_calls_used。若调用已经开始，随后取消或异常，流程跳过persist；`execute_job`仅写终态，不保存该预算用量。API `app/api/routes/evaluation.py` 直接投影row.external_calls_used，故任务/API都低报。本轮复现合成调用1次、存储0次；没有真实供应商调用或费用。

最小探针路径：`data/validation/eval003-independent-business-20261026/probe.py`；结果 `call-count-probe.json`。它使用内存SQLite、真实execute_job→run_real_evaluation→_run_agent，替换 `_run_isolated_agent` 为consume后异常/取消的合成实现，并替换输入加载/身份检查以隔离计数问题。该探针只证明计数持久化缺口，不用于证明身份/真实模型质量；正常边界另有上述专项。`assert` 确认缺陷现状，退出0不表示业务通过。正式修复需把可复现反例纳入tests/evals并验证API计数、取消、失败与正常路径；本轮未获实现授权，不添加业务/测试修改。

## 历史证据复用与未测

复核 `docs/checklists/evaluation-platform-acceptance.md` 2026-10-26节：历史为隔离真实技术文档、OpenAI兼容text-embedding-v3/1024、deepseek-flash；显式local，三类真实资源、预算拒绝和页面受托接受；保守费用上界0.2037865元。其单样本质量、Milvus未验收、Silver非Gold等限制保留。本轮未重复付费、未亲测这些真实模型样本，也不将历史单样本扩大成总体质量通过。

`git diff 1c8da78... -- app/services/evaluation* app/api/routes/evaluation.py app/schemas/evaluation.py frontend/src/pages/Evaluations.tsx frontend/src/pages/EvaluationReports.tsx app/llm/budget.py app/llm/counters.py` 无差异，直接生命周期/页面实现与历史匹配；但当前未提交app/models/user.py、app/models/rag.py、app/rag/service.py已变化，属于真实资源依赖，不能把旧真实验收和CI当成当前整个工作树端到端证据。本次合成专项绑定实际工作树。历史CI40254924700及3575passed/4skipped只按既有记录引用，本轮未重新查询远程CI，不能声明当前HEAD/工作树全量CI通过。

页面本轮未亲测：只确认直接页面实现无历史候选差异，可引用旧页面接受事实；当前隔离浏览器操作与生产Milvus、真人Gold审核、生产启用均未验收。未重复full或静态检查；此轮是只读验收，不关闭业务卡。

## 命令与原始证据

1. `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe scripts/validate_delivery.py targeted --test tests/test_evaluation_jobs.py --test tests/test_evaluation_api.py --test tests/test_evaluation_orchestrator.py --test tests/test_evaluation_runtime_guards.py --test tests/test_evaluation_quality.py`：收集exit0、pytest exit0、65passed；`data/validation/20261026T130943-092f37566b3e/{result.json,pytest.log,pytest.xml}`。result包含完整实际pytest参数、环境、SHA256输入清单，不能替代全量门禁。
2. `$env:PYTHONPATH=(Get-Location).Path; D:/DepTooL/anaconda3/envs/ai-assistant/python.exe data/validation/eval003-independent-business-20261026/probe.py`：最终exit0，两个反例如上；`call-count-probe.json`。先前误用 `python -m runpy <路径>` exit1，随后网络阻断过宽影响Windows asyncio socketpair而exit1，均为探针启动问题，已改为直接执行并仅允许loopback；不当作产品失败或成功。
3. Git分支/HEAD/status、历史路径diff、Get-FileHash均实际执行exit0；未触发提交/推送。业务输入与历史匹配的限定如上。

## 关键输入SHA256

以下为本轮实际输入；更完整清单见专项result.json。若相关输入改变，应复验受影响证据。

- `app/services/evaluation_jobs.py`: `a4c50bfc06e1a34c16f35a327756538040d079833a92faf91bada51dd73f131f`
- `app/services/evaluation_orchestrator.py`: `881f477c29992ec08c0ad14dde69c9008399271836b67524ff6800f840a04a02`
- `app/services/evaluation_quality.py`: `b0c57e0b336d86bad45fc4eac3656f24bb2ce1583aad08117ca43da881b68b99`
- `app/api/routes/evaluation.py`: `e897d36a8dd6c1320bac7fb835fc55f2e12175610a2bdfd39c0407fddb1d6529`
- `tests/test_evaluation_jobs.py`: `707329b9260904db9c40af00ab662d63596dbbc61569caa9676cc563e49f7804`
- `tests/test_evaluation_api.py`: `5ffff4bd704e673701d85c468792ec70001dfe037eb1362d2f70f0f83e5f57bf`
- `tests/test_evaluation_orchestrator.py`: `2ef3794136c6acc3814f99333530519f874172cc569ed89b123fbd4ec52a057a`
- `tests/test_evaluation_runtime_guards.py`: `fc183bb15dfd7d9761c2872dc2d9ee754d1f9f1fdb9cbddb0d9bce1da584f849`
- `tests/test_evaluation_quality.py`: `33282ead9c1fbdbc9be2253da9f133140f8e42783fffbf8f7e2e9c0cd6ddb6ff`
