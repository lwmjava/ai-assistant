# QA-005 既有测试与静态检查整改实现说明

日期：2026-10-10。依据用户本轮明确要求修复上轮2失败、4跳过、6条Ruff，并防止新增回归。沿用QA-005的允许范围，实施前计划为 `plan_qa_005_followup_20261010.md`。不实施NFR-001 CI，保留此前RAG-039的全部修改与人工验收状态。

## 实际修复

| 原问题 | 根因 | 实际行为与代码 |
|---|---|---|
| 历史报告日期测试跨日失败 | 20261007历史文件与运行当天命名比较 | `tests/test_rag_033_parse_quality.py` 将文件名绑定自身generated_at和锁定门禁；3个冻结时钟仍严格验证当天默认命名，保留指定日期命名断言；不改历史报告 |
| Milvus清理日志例提前TypeError | 旧_connect替身不接身份/index | `tests/test_rag_log_minimization.py` 接受实际参数，补连接/查询调用断言，保留RuntimeError诊断和源LogRecord/格式化输出无正文断言 |
| 6条全仓Ruff | 长字符串、3无用import、无用局部变量、lambda赋值 | `scripts/milvus_switch_check.py` 等价字符串分行；`tests/test_rag_030_dialog_parent_assembly.py`、`test_rag_031_llm_boundaries.py` 仅去无用代码/改def；不放宽规则、不删断言 |
| cloud OCR构造/绑定skip | 对不发请求的构造测试要求本机真实凭据 | 原测试走真实工厂/from_settings、临时合成专用配置，封锁同步/异步HTTP客户端，所有原断言保留且追加factory_provider_is_real；非真实OCR质量 |
| MCP collection skip | 未安装已声明extra；旧fixture还强制2.x | 安装mcp1.12.4；`tests/fixtures/echo_mcp_server.py` 按真实SDK接口注册1.x装饰器或保留2.x注册分支，`tests/test_mcp.py` 移除不必要版本skip；实际运行stdio连接、列举、调用及manager闭环 |
| LlamaIndex collection/adapter skip | 未安装已声明extra | 安装llama-index-core0.12.42；实际切分、检索、身份隔离和禁止绕写用例恢复执行 |

恢复原来模块级importorskip隐藏的测试后，LangChain切分两例暴露缺少另一个已声明extra组件，补齐langchain-text-splitters1.1.3。新传递包Deprecated3.0.0含Python3.12语法，与项目mypy的Python3.11目标不兼容，收敛本轮新增包为Deprecated1.2.18/wrapt1.17.3；没有放宽mypy、忽略整文件或切换解释器。

真实SDK使mypy能够发现两个旧接口类型问题：`app/mcp/client.py` 改以模块getattr选择新旧HTTP函数名，保留原优先级和缺失时异常，不改变传输/权限；新增两函数名的连接/断开契约。`app/rag/backend/llamaindex_backend.py` add参数list改Sequence匹配SDK父类，仍拒绝写入并指引RAGService；`tests/test_rag_backend.py` 保留list拒写并补tuple拒写。以上修改在计划补记后实施。

安装新增tiktoken0.14.0自动启用官方计数，第一次完整回归还捕获两项：旧预算夹具将重复x字符数当token数；官方encode会静默替换孤立代理项。前者按生效计数方法使用单token填充并新增实际计数等于room+overflow断言，0/+1阈值保持不变。后者在 `app/llm/counters.py` 官方encode前复用严格UTF8校验，维持原payload_uncountable/零请求语义，未把官方计数降级为字符估算；`tests/test_rag_028_model_capability.py` 将非法文本反例扩大str/list/dict三种形状。版本化Evaluation为 `docs/evaluations/qa005-token-counter-regression-v1.md`。计划先补记、再修复，原失败和最终重验均保留。

## 依赖和数据保护

指定解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`（本机Python3.12，项目mypy检查目标3.11）。变更前用importlib.metadata输出92个已安装分发版本约束；带该约束dry-run，机器检查proposal不会替换旧分发，再安装。原92包逐项核对全部未变，新增40个分发，pip check通过。Deprecated/wrapt调整只涉及本轮新装包。固定验证组合记录于 `tests/requirements-optional.txt`，不改变生产默认依赖/配置。

安装复现：在同一conda环境先 `python -m pip list --format=freeze > data/test-environment-constraints.txt`，再 `python -m pip install --dry-run --constraint data/test-environment-constraints.txt -r tests/requirements-optional.txt`，审核不替换旧包后去掉dry-run；最后 `python -m pip check`。约束必须来自实际目标环境，不复制本机的平台包清单到其他机器。安装缺依赖分支测试仍保留并实际执行。

全部pytest使用development、native/local、Mock Embedding，模型/OCR密钥变量清空、LLM_PROVIDER=openai用于原工厂警告测试；测试自己注入合成provider，不调用真实付费模型。SQLite为独立库，源文件为临时目录；没有读取.env、重要数据或真实客户文档，没有生产写入、重建、部署或Git发布。

## 实际命令与红绿证据

| 命令/报告（均用指定解释器） | 结果 |
|---|---|
| `python -m pytest -q -rs --basetemp=data/t/qa5b1010`；DB `data/test_qa005_baseline1010.db`，`data/qa005_baseline_20261010.log` | 改前exit1：1335 passed/2 failed/4 skipped，231.13s |
| `python -m ruff check . --no-cache` | 改前exit1，6条；修复后exit0，All checks passed |
| 可选依赖dry-run/install（完整日志 `data/qa005_dependency_*_20261010.*`） | exit0；先39新增，再补text-splitters，旧92个版本不变 |
| 第一次focused验证；`data/qa005_focus_20261010.log` | exit1：59 passed/2 failed，10.59s；恢复收集暴露缺text-splitters，不宣称通过 |
| 第一次安装后mypy | exit1：Deprecated3语法错误；收敛新包后又发现MCP符号/LlamaIndex override两条，均归因并修复 |
| 最终focused：MCP、backend、索引适配器、日期、cloud、日志、父块、LLM边界；`python -m pytest … -q -rs --basetemp=data/t/qa5f21010`，`data/qa005_focus2_20261010.log` | exit0：63 passed，9.22s，无skip |
| 第一次完整绿测尝试；`python -m pytest -q -rs --basetemp=data/t/qa5g1010`，`data/qa005_final_20261010.log` | exit1：1358 passed/2 failed/0 skipped，251.35s；两项新tokenizer计数问题已归因修复，此记录不是最终通过 |
| `python -m pytest tests/test_rag_028_model_capability.py -q -rs --basetemp=data/t/qa5c1010` | exit0：44 passed，2.70s，官方计数器生效 |
| `python -m mypy app/`（SDK接口修复后） | exit0，188 source files |
| `python -m pip check` | exit0，No broken requirements found |
| `git diff --check` | exit0 |
| 最终完整pytest：`python -m pytest -q -rs --basetemp=data/t/qa5g21010`，DB `data/test_qa005_final2_1010.db`，报告 `data/qa005_final2_20261010.log` | exit0：1364 passed / 0 failed / 0 skipped，225.63s；1条既有ast.NameConstant弃用警告，非本轮错误 |
| 独立本轮7测试文件（MCP/backend/date/cloud/log/父块/LLM边界） | exit0：215 passed，24.45s |
| 独立最终028官方tokenizer回归与official/fallback入口反例 | exit0：44 passed，2.97s；两路径共12入口反例通过（超限1/精确边界/不可序列化/非法Unicode三形状） |

最终完整pytest覆盖最终代码，所有原失败和跳过已经实际执行通过，RAG-039恢复/幂等回归也包含在全量中；没有发现新增测试回归。独立审查无未修复P1/P2，源码指纹和逐条反例见独立审查报告。完整后端mypy、全仓Ruff、pipcheck、diffcheck均exit0。前一次完整1358/2为中间失败证据，已由最后1364/0/0覆盖；不可误作最终结果。

## 原始问题关闭对账

| 用户指出的问题 | 证据与最终结果 | 验收结论 |
|---|---|---|
| 2个既有失败 | 三个日期时钟/历史generated_at严格绑定、Milvus实际故障与日志断言；独立入口+最终全量exit0 | 通过 |
| 4个跳过 | MCP真实stdio/manager、LlamaIndex切分与检索/身份、cloud真实工厂无网络；最后0 skipped | 通过；构造测试不证明远端OCR质量 |
| 6条Ruff错误 | 全仓 `ruff check . --no-cache` exit0，未改规则/门禁 | 通过 |
| 修复不引发新增问题 | 原92包逐项不变、pipcheck0、mypy188源0、最终1364/0/0、独立反例全部通过，原RAG-039代码保留 | 已验证范围内通过，不承诺未知缺陷不存在 |
| CI建设/整卡人工验收 | NFR-001未实施，人工验收本轮未获替代授权 | 不属于本轮问题修复；QA-005保留in_review |

## 验收映射、未做项与回滚

原2失败与4跳过均转换为实际执行通过的断言，没有改成新skip；6条Ruff全部消除。MCP真实本机stdio及SQLite/LlamaIndex检索属于E2隔离集成；日期/日志/Cloud构造/符号解析为确定性单元/契约证据，不证明OCR和Embedding生产质量。前端无变化，页面验收不适用；MCP2.x分支仅保留，未在本机2.x环境实测。不宣称数学意义上永无缺陷，以最后完整回归、独立审查和版本核对证明未发现新增回归。

独立身份 `/root/rag039_contract` 未修改此次业务/测试，负责原始计划、完整本轮diff、入口/反例和环境版本核对，报告放 `docs/reviews/2026-10-10-QA-005遗留整改独立审查.md`。主Agent修复和验证。使用已调用的task-card-delivery工程门禁及等价根因调查，不声称执行gstack调查技能中的shell/遥测初始化。QA-005的NFR-001依赖与人工验收仍按原项目规则保留，不把本轮检查通过写成CI建设完成。

回滚只回退此次QA-005的最小diff，不覆盖上轮RAG-039修改；测试/fixture回退不会迁移数据。可选包仅安装到指定开发环境，原92包未变；需要恢复包环境时根据变更前清单与本轮新增清单移除新增项，先核对其他会话是否已依赖它们，保留原包。移除extras会恢复可选测试skip，不代表代码错误已修。没有自动执行回滚或删重要数据。
