# 受权评测报告查询与结果页面

## 开工与边界

2026-10-22用户授权开始EVAL-001；基线0bba10c，分支codex/eval-001-report-query，开工前工作区干净。直接依赖RAG-017、RAG-035、PAGT-007均done。当前实现已落盘，技术验证进行中，独立审查、Linux与最终CI待补，不宣称业务验收通过。

沿用共享计划中的只读报告API、列表/详情/趋势/失败切片范围。不运行模型、不改评测器、不开放任意路径下载、不改变冻结基线。允许路径沿用tasks.yaml。

## 已核对事实

app/api/routes/evaluation.py与app/services/evaluation_reports.py尚不存在，卡片facts中的文件名为目标落点，不是已实现证据。现有权限矩阵没有evaluation资源。检索基线含run_config/overall/failing_cases/cases；Agent报告含report_schema_version、版本哈希、模型和cases。三类输入需分别适配，不能套单一字段后把缺测填零。

## 具体实施方案（用户已批准）

建议采用服务端显式白名单目录清单，使用不透明报告ID；每条清单显式指定global或tenant及tenant_id，不从文件名或请求参数推断归属。未登记报告默认拒绝。仓库受版本控制的历史基线仅经显式登记后视为global，不自动扫描并公开整个reports目录。

建议global只允许system_admin/system_viewer读取；tenant报告仅同租户tenant_admin读取。普通member/viewer先不授予；系统角色也不自动跨域读取租户结果。角色与当前租户由既有后端身份依赖取得，前端只控制入口显隐。详情中不存在与无权访问统一404，列表先授权再分页统计，避免泄露存在性。

备选：仅开放系统角色的global页面（租户结果延后，会缩小当前交付）；或开放同租户所有只读角色（权限更宽，目前无批准依据）。建议采用上述显式分域且租户管理员可读方案。

三类适配器把检索、生成、Agent报告映射成稳定DTO；仅导出指标、缺测状态、版本元信息与必要失败标识，不返回原始query、回答、上下文、工具参数、绝对路径或endpoint。未知协议明确拒绝；缺失指标为null；失败保持失败。趋势仅比较同报告族/指标口径，缺测显示缺测，不连成零值。

前端复用现有布局与API客户端：列表按类型选择，详情展示来源版本、模型、缺测与失败切片；趋势以同口径历史指标展示，不给出质量发布结论。

## 失败边界、验证与回滚

先补未认证、停用/撤销身份、跨租户、不存在/未登记ID、未知协议、路径穿越、缺测和失败状态契约测试，再最小实现。检查真实三类报告及其runner的协议后锁定精确适配字段。不修改源报告补权限信息。

业务开发前记录隔离测试库与完整基线：指定conda解释器执行ruff check .、mypy app/、pytest；前端执行npm run typecheck、npm test、npm run lint、npm run build。报告文件读取涉及文件系统，最终需要Linux相关测试及同最终提交Ubuntu Backend CI。新增权限边界须独立于实现者审查，正式结论归docs/reviews；未满足前保持in_review/blocked。

回滚移除本卡路由、页面和权限资源接线，保留原始报告、runner、CI及冻结阈值。没有新增依赖、数据库表或生产操作。

## 当前结果与待办

已完成依赖与三类报告协议核对、后端/前端实现、31条专项测试及页面冒烟检查。最终本地全量回归已通过，独立审查已完成；Linux相关测试及同最终提交Ubuntu CI待补。可验收时间、接受时间、首次接受及7天重开未知。

2026-10-22用户回复“采用”，明确批准上述读取权限及分域方案；随后回复“继续”。范围内常规实现连续推进，不再重复确认。系统角色不跨读租户报告，租户管理员只读同租户，归属未知默认拒绝。

## 基线检查进展

首次检查输出保存于data/validation/eval001-baseline-20261022/：前端四项退出0；Ruff退出2、mypy退出1，均因旧缓存目录拒绝访问；pytest退出1（2109 passed/2 skipped/1165 errors），错误来自旧data/pytest-tmp/run目录拒绝访问，不能作为业务断言失败。保留原日志，改独立cache与basetemp重跑；不改检查规则、测试或全局文件权限。

## 实际实现与验收

### 行为与接口

系统管理员/系统访客可列表与查看已显式登记的global报告；租户管理员只可读取同租户登记项，系统角色没有跨读租户结果的例外。首批四份报告覆盖检索、生成和Agent两个协议版本。租户登记能力由同一服务契约支持、合成夹具验证，首批不伪造真实租户报告，也无上传或登记API。

新增三个GET接口及控制台/evaluations。路由调用Service并复用既有User/Tenant身份依赖；Service在授权后才访问限定reports根目录的白名单JSON，单文件读取上限16MiB。无权和不存在详情统一404；不可读文件503，坏/未知协议422；列表以unsupported/unavailable状态保留明确诊断，先授权再分页计数。列表和详情均不返回报告文件路径或原始payload。

检索适配保留Recall/MRR/nDCG/引用覆盖/安全率/延迟；未定义门禁通过则标unmeasured，已记录失败标failed。生成区分请求调用失败与质量失败，合并原failing_slice及已记录指标gap_cases；任何明确失败优先于矛盾的overall_pass。Agent保留旧协议缺失指标为null，识别路由/工具记录失败；成功用例不冒充官方gate通过。失败切片仅返回case_id/category及固定原因码，最多展示200条并保留总条数。历史表保留协议、数据版本和模型，缺时间不推断，不计算跨口径提升。

不运行模型、改评测器、改阈值或入库真实报告，不修改索引/RAG/Harness执行边界，不引入依赖或迁移。

### 基线、命令与当前结果

Windows、conda Python3.12.0（D:/DepTooL/anaconda3/envs/ai-assistant/python.exe）；FastAPI0.115.6 / SQLModel0.0.22 / Pydantic2.10.4 / pytest8.4.2 / Ruff0.8.4 / mypy1.14.1 / LangGraph1.0.10。使用隔离SQLite与tests/conftest.py的非敏感离线配置：AUTH_ENABLED=true、AGENT_ORCHESTRATION=self、SECURITY_RATE_LIMIT=false、显式local夹具，清空LLM/Embedding密钥、不继承工作区.env。没有关闭产品安全检查来换通过。

原基线HEAD0bba10c，分支codex/eval-001-report-query；已有改动仅本卡tasks.yaml和实施计划。前端基线四项退出0（167条测试）；后端首次因缓存/临时目录权限失败，原日志保留。独立目录补跑Ruff/mypy退出0，pytest 3273 passed / 3 skipped / 4 warnings，953.23秒、退出0。开发期间补跑结果与冻结候选验证分开记录，不将其替代最终全量。

最终日志根目录data/validation/eval001-final-20261022/，各命令退出码单独保存为.exit；一次性日志/指纹不加入版本控制，本文保留必要摘要与源文件指纹。

| 完整命令（解释器均用上述conda路径） | 当前结果与证据 |
|---|---|
| `python.exe -m ruff check .`，RUFF_CACHE_DIR=data/validation/eval001-final-20261022/ruff-cache | 退出0；ruff.log |
| `python.exe -m mypy app/`，MYPY_CACHE_DIR=data/validation/eval001-dev-mypy | 退出0；226个源文件，mypy.log |
| `python.exe -m pytest tests/test_evaluation_reports.py tests/test_evaluation_api.py --basetemp data/validation/eval001-final-20261022/contract-v2-tmp -o cache_dir=data/validation/eval001-final-20261022/contract-v2-cache`，DATABASE_URL=sqlite:///./data/eval001-contract-v2.db | 31 passed，退出0；contract-v2.log |
| `python.exe -m pytest --basetemp data/validation/eval001-final-20261022/full-v3-tmp -o cache_dir=data/validation/eval001-final-20261022/full-v3-cache`，DATABASE_URL=sqlite:///./data/eval001-final-full-v3.db | 3304 passed / 3 skipped / 4 warnings，758.08秒，退出0；pytest-v3.log / pytest-v3.exit |
| `cd frontend; npm run typecheck` | 退出0；frontend-typecheck.log |
| `cd frontend; npm test` | 172 passed / 20文件，退出0；frontend-test.log |
| `cd frontend; npm run lint` | 退出0；frontend-lint.log |
| `cd frontend; npm run build` | 退出0；frontend-build.log |
| `git diff --check` | 退出0；最终交付前再核对文档 |

最早专项红灯为模块不存在（pytest collection退出2），随后15、22、30、31条分层反例通过。开发中首次lint指出异常类缺少Error后缀，已改名，未放宽规则。曾启动的一轮最终全量因补充“单项失败优先”回归被中止，不作为最终证据。README/OpenAPI旧CRLF保留未变行、新增行使用LF，避免全文件换行diff或新增CR空白错误；未改Git配置。

### 页面实际检查与自审

本次AI执行自动操作验收属于实现范围的技术检查，不冒充人工业务接受。127.0.0.1:18891、独立data/eval001-ui-smoke.db、合成system_viewer/member账户，以已构建前端和真实身份/API提供页面；测试宿主lifespan=off，不证明Milvus启动或调度器可用。未调用模型。已观察：系统访客导航到检索列表；切换生成报告显示failed、83.87%答案点和6条失败切片；Agent旧报告tool_call_validity_recall显示缺测，两个协议/数据版本保留；退出后成员入口隐藏，直接/evaluations重定向/chat。临时服务和临时浏览器标签已停止/关闭。截图已归档[生成报告页面](../reviews/2026-10-22-eval-001-generation-page.png)；不作生产性能或质量证据。

自审最终Service/API/前端调用方，确认未知归属拒绝、系统角色不跨读租户报告、授权前不读取文件、无query/answer/Prompt/工具参数/原始错误外发、空值不填零。31条后端反例覆盖真实User/Tenant、降权、停用/删除、越权、错误协议、路径、坏数值和矛盾通过；前端测试覆盖状态/权限镜像及页面错误/空态。这是实现者自审，不能替代独立审查。

### 未完成与关闭边界

Codex独立审查普通调用因网络超时未完成；随后申请只读外部调用被自动审批拒绝，理由为未明确授权将私有源码/测试/权限实现发送给外部Codex目的地。未绕过拒绝或改用另一渠道发送；向用户请求具体授权，当前未取得独立审查结论。

Docker守护进程不可用，Linux相关测试未执行；未提交/推送本卡以触发最终Ubuntu CI，最终SHA CI待补。本卡保持in_review，不写done/已接受/可发布。回滚按本卡新增路由/Service/schema/页面/导航/权限镜像及文档逐项回退，保留原报告和runner；不删除重要数据。

效能观察：开工日期2026-10-22，具体时分未知；可验收/接受时点与首次接受未知；首次权限故障全量2481.46秒，基线补跑953.23秒，其他等待与重复验证总耗时尚未知；不据此声称提速，7天缺陷/重开待观察。

### 实际修改文件与候选绑定

候选为HEAD0bba10c加以下源码/测试文件；没有配置/依赖/评测输入变更。完整565个检查输入指纹见本地manifest.json；下列哈希按原始字节计算。相关源码/测试再变更须重验，后续只补交付说明不使行为证据失效。

| 文件 | SHA256 |
|---|---|
| `app/api/router.py` | `75d97b4b0db2a2ac148e385b0d04816f903590830312e13dc19d14751bce51cf` |
| `app/api/routes/evaluation.py` | `0db0ee374c7d5374a723d09dfe32723b34022e2ae7e29e4d6dd4b91639e5291e` |
| `app/core/security.py` | `e1321cd6a2d1d11034c19784d870248e75666a7bb27e89e066eef8f6b45c905e` |
| `app/schemas/evaluation.py` | `a94361e822049b3dd5f1a1ee0309c2d3523adf6bc020751efde5ac9f81f2a91a` |
| `app/services/evaluation_reports.py` | `f39af5bf21a22287121e06ceacf39f73ab92fd872ac8dfc82571e3491b0d8d52` |
| `frontend/src/App.tsx` | `2ca6327517858a4f5740a76eaaedf996ca032b998683e9ecb434059dfdba4b36` |
| `frontend/src/api/evaluations.ts` | `194ed102dd6a80ae21312847cdfefe1b4ebaf034b15c245f25a7e12e3982b54b` |
| `frontend/src/components/layout/Sidebar.tsx` | `8ef5d6384868c529056a477d6f138f8a8a3814393620bbfde34e8d37b3703adc` |
| `frontend/src/lib/evaluations.test.ts` | `d9ccae4c75994fd284429ca803d3fbe3c20b64b0e4862de494dc89c462331c03` |
| `frontend/src/lib/evaluations.ts` | `81f81dc10540c2e0776aaa52a62df2c6532eddf65d1e3ac971f1d1681b1e9b74` |
| `frontend/src/lib/permissions.ts` | `016ad54125ffcdf1a92c8a3b1d7105bd1a4ec878ea86832e4b447f7d5d2220eb` |
| `frontend/src/pages/Evaluations.test.ts` | `d5e32780b27c5255849d104d6e33dbc18bde65d889347cfe9872141a2bfe8bff` |
| `frontend/src/pages/Evaluations.tsx` | `c087f43d7ac863d65aae919c959910ee802c664c2f5cb8b938072f25d1890b36` |
| `frontend/src/types/api.ts` | `2d568a450872f0986af11a4769d28fd04fc7a816f14f6a1209f66a3e1d910eea` |
| `tests/test_evaluation_api.py` | `5af4fdecd51f7b380d054dd7c25db6cdaf6637aa61a3a58ad0956e8adf8c1d20` |
| `tests/test_evaluation_reports.py` | `156f0566e43bbc1965d81cda2c0b32b31634e3ef5efb6b0f0e83b6cff164ea6e` |

文档与台账同步文件：README.md、docs/swagger.json、docs/product/项目产品需求方案.md、tasks.yaml及本文。报告输入沿用REPORT_ENTRIES明确列出的四份既有JSON，未改正文。

2026-10-22会话中断后的补记：上一轮冻结全量日志停在约20%，对应进程已不存在且没有退出码，不作通过证据；保留pytest-v2.log。已用独立隐藏后台runner重启不带筛选的完整pytest，记录于pytest-v3.log/pytest-v3.exit，完成前仍不关闭。源码/测试未因此改变。

### 独立审查数据发送授权

2026-10-22用户在明确列出“相关源码、测试、权限实现 → 外部Codex服务，只读、排除.env和凭据”的问题中选择“授权只读独立审查”。已按此限定范围重启read-only独立审查，日志data/validation/eval001-final-20261022/independent-review.log；待取得输出后记录正式结论。此前自动审批拒绝作为历史保留，不再视为缺少授权。

### 独立审查完成

授权后的read-only审查退出0，最终输出NO_FINDINGS；没有阻断级发现。审查及内存探针摘要见[正式AI独立审查结论](../reviews/2026-10-22-eval-001-independent-review.md)。此前“未取得结论”为历史时点，当前独立审查项已补齐；全量与Linux/最终CI分别核对，不互相替代。

### 最终本地结果

2026-10-22冻结输入完整pytest-v3结束：3304 passed / 3 skipped / 4 warnings，758.08秒，退出0；比基线3273 passed增加31条本卡回归。既有三项skip及四项warning未隐藏，也不据此宣称Linux通过。此后仅补文档/台账/截图，不变更源码/测试/配置/依赖/评测输入。

独立审查NO_FINDINGS、后端Ruff/mypy与专项31条、前端typecheck/Vitest172条/ESLint/build、实际页面smoke、范围/YAML/OpenAPI/565项指纹及diff检查已通过。技术结果分层记录：本地验证通过，AI独立审查通过，Linux/同最终SHA Ubuntu CI未完成，人工业务接受未登记，任务in_review。未执行本卡Git提交/推送/合并/部署。
