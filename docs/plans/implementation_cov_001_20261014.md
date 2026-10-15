# COV-001 覆盖率报告实现说明

基点80629c4cd5ce00d59b26116c7419edce00e6a113，分支codex/cov-001；用户明确授权实施并提供task-card-delivery v1.3.0，另明确批准新增pytest-cov7.1.0/coverage7.10.6开发依赖及安装指定conda环境。原任务仅交付一次可复现数字/命令、70%另记，不修业务测试或凑比例。

## 实际完成与口径

pyproject.toml的dev增加两个固定工具包，既有包版本未改，运行依赖、pytest默认testpaths、app/tests/CI/前端源码不变。指定环境Python3.12.0/pytest8.4.2（声明8.3.4，差异保留），pip check通过。coverage插件现可识别原卡--cov入口；普通项目启动不增加运行时路径。

测量使用真实完整pytest、隔离SQLite/合成provider，证据E2；不代表检索/LLM质量。实测app语句覆盖13859/15894=87.19642632439914%（展示87.20%），未覆盖2035行，全部205个app Python文件均在报告；本卡后端语句口径达到70%，不将这项数字等同前端/分支覆盖或PRD“CI全绿”整项达成。不额外收集子进程执行。没有新omit/exclude规则；coverage7.10.6默认排除pragma:no cover、省略号函数体、TYPE_CHECKING，共52排除行，具体有效配置及excluded_lines保留，不把排除行称作已覆盖。

测试失败保留pytest非零退出码，--cov-fail-under=0仅表示本卡不新增70%发布门禁。报告不抹去失败/中断，完整与合成反例分开。重跑用新目录、独立COVERAGE_FILE，不cov-append或覆盖旧证据；用户可从JSON分子/分母独立复算70%判定。

## 可复现命令

在仓库根PowerShell、已按批准版本安装开发工具后执行；将r1换成未存在的新目录，禁止覆盖之前报告。

```powershell
$python = 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:LLM_API_KEY = ''
$env:LLM_INTENT_API_KEY = ''
$env:LLM_FALLBACK_API_KEY = ''
$env:OPENAI_API_KEY = ''
$env:EMBEDDING_API_KEY = ''
$env:DATABASE_URL = 'sqlite:///data/cov001-r1/test_ai_assistant.db'
$env:COVERAGE_FILE = 'E:/culture/SmartCustomerServiceSystem/ai-assistant/data/cov001-r1/.coverage'
if (Test-Path -LiteralPath data/cov001-r1) { throw 'Refuse to overwrite coverage evidence' }
New-Item -ItemType Directory -Path data/cov001-r1 | Out-Null
& $python -m pytest --cov=app --cov-report=term-missing `
  --cov-report=json:data/cov001-r1/coverage.json `
  --cov-report=xml:data/cov001-r1/coverage.xml --cov-fail-under=0 `
  -q --tb=short --basetemp=data/pytest-tmp/cov001-r1 `
  --junitxml=data/cov001-r1/pytest.xml
$coverageExitCode = $LASTEXITCODE
```

本轮执行另重定向完整终端到data/cov001-r1/pytest.log并保存exit-code.txt；命令本身无-k/--lf/筛选，完整输入清单与有效coverage配置在input-manifest.json。读取/复制报告不修改测量输入，不以旧数字冒充新轮。

## 验证与角色

- /root：提取任务、固定开发依赖、真实全量测量、环境/指纹与文档整合。
- /root/cov_plan_review：等价工程计划审查，无P1/P2方向问题；默认exclude与逐文件核对建议已采纳。专用plan-eng-review宿主交互接口不可用，未声称执行完整工作流。
- /root/cov_verify_review：按code-review-quality原始契约独立入口/自动验收，使用真实pytest--cov/coverage API与独立临时模块；回归脚本docs/evaluations/cov001-cli-boundary-cases-v1.py，12边界/7CLI过程通过，数据与代码指纹在docs/reviews/cov001_boundary_review_20261014.json。实际Coverage.analysis2逐205文件复算与JSON/XML一致，完整测试/JUnit/真实退出码及423输入/依赖零漂移验证通过；正式结论cov001_entry_review_20261014.md、可重建验收脚本与实际命令/exit0在cov001_independent_report_review_20261014.json，无P1/P2。

新工具基线：安装前原pytest --cov入口不识别参数exit4，仅作为工具缺失，不作业务红测。安装后只新增两包，原包版本不变，pipcheck exit0。历史2329passed报告423输入在开工前匹配，明确为指纹匹配历史证据；当前完整测量提供新证据，不凭历史数字关闭本卡。

Ruff --no-cache、mypy app/205源、pipcheck、源码登记v7均exit0；未改业务源码，不另登记模型版本。最终完整测量2329passed/0failed/0error/0skipped，3项既有warning，716.04秒，exit0；正文JSON/XML/完整终端及JUnit保留在docs/reviews/cov001_coverage_20261014.json、cov001_coverage_20261014.xml、cov001_terminal_20261014.txt、cov001_pytest_20261014.xml。证据cov001_delivery_evidence_20261014.json记录完整命令/环境/有效排除规则、全包版本及423项测量输入SHA256；汇总d9ef4888c0175c81ca38f9fc1a5b1a698b11f6c063c42b9fc1c5ca482acb85c2。独立回归脚本另绑定原字节SHA256=65eeccd2c8b88f63e2cfa55beb65eb65235395a545b47d173b40a2874ddce2a3。验证后仅新增报告/验收说明及任务记录，无测量输入漂移。

依赖依据为官方[pytest-cov7.1.0元数据](https://pypi.org/project/pytest-cov/7.1.0/)与[coverage7.10.6元数据](https://pypi.org/project/coverage/7.10.6/)，用于选择Python3.12兼容组合；安装日志和pipcheck/原包零升级才是本环境实际事实。coverage配置保持现有默认；已有pyproject工作树CRLF在编辑后恢复Git原LF，规范化测量输入未变，Git业务配置diff仅两行dev条目。

## 依赖、未做项与状态

NFR-001的当前CI语法/后端和前端检查结构复核，含push/PR/失败可判定；同Git文件指纹与旧GitHub运行是历史证据，不宣称新提交CI。QA-005仍in_review，最终新提交Ubuntu Backend/Frontend/Agent official CI及人工业务验收待完成；不能借COV-001关闭它，也不修改旧NFR-001 done结论。

当前交付无页面/API契约改动，前端/截图不适用。Linux上的本卡完整覆盖率命令及同最终提交CI未执行；本地安装/覆盖率不能替代Ubuntu CI。仅调用交付技能未委托Agent代行人工业务验收；在前置和关闭对账未通过前不启动整卡最终人工审核，COV-001保持in_review，不done。引用技能关闭要求：“前置卡关闭证据未在当前HEAD复核通过时，本卡禁止done”；“用户未明确委托人工业务验收时，结论交用户决策并保留未完成状态”。

未新增CI覆盖率强制阈值、报告服务或封装器；未改业务/测试/生产配置/密钥，不做模型调用、Git提交推送合并部署。回滚只撤两个新增dev条目及匹配本卡文档；原包不降级，旧报告/重要数据保持。独立边界脚本属于可复现验收回归，不是生产报告程序。
