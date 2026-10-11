# PAGT-001 实现说明

2026-10-11；分支 `codex/domestic-search-file-read`；Git 基点 `cb25b8053b8ee958cf9ff19d7dbd872e61323608`。状态：技术实现、独立审查及用户委托独立 Agent 执行的业务操作验收通过，tasks.yaml 登记 done。用户后续授权提交全部新增和修改文件，本次创建本地提交；未推送、部署或生产启用。

## 实际完成的功能

获准使用现有对话入口的用户，在运维配置国内搜索服务与密钥后，可通过 Agent 调用 `web_search`，获得有限的标题、来源 URL 和摘要。提供 DashScope/Qwen、智谱 web-search-pro、博查 Web Search、百度千帆 AI Search 四个国内适配器；没有国外供应商或国外故障兜底，不改变主对话 LLM。请求仅发送显式查询，固定国内端点，不二次抓取来源，不携带会话历史/租户知识库。响应作为不可信 JSON 数据，来源不是执行授权。

默认 provider 为 dashscope，无密钥时不注册，不阻塞服务启动。未知服务或不在白名单的模型、非法预算拒绝注册。健康检查 `checks.tools` 显示脱敏配置状态；available 仅表示本地配置就绪，不表示密钥已经远端认证。启动加载健康路由时本地校验、只记录状态和原因，不请求收费 API。数据库/向量库连通性总体状态保持原规则。

`file_ops` 默认关闭。运维通过 JSON 根别名→绝对路径配置明确允许所有对话用户读取的公开共享文本目录；工具仅接收别名与相对路径。没有租户私有文件管理、写入、删除或列目录接口。仅允许有限 UTF-8 普通文件；拒绝绝对/穿越路径、私密名/租户上传目录、设备、二进制、硬链接、符号链接/junction。打开文件后核对实际句柄路径和对象身份，拒绝检查/打开之间的对象替换；重复读取不修改源文件。

搜索超时/429/5xx/无有效来源/超大或异常响应安全返回，不重试、不改用国外服务；取消传播。关闭配置后，新工具清单恢复旧四工具，已持有的新工具对象也在执行时检查开关。新增工具观测不展示原始参数；意外异常只记录类型、返回固定提示，避免拒绝参数中的密钥/绝对路径回显。旧四工具签名与行为未修改。

## 文件与任务映射

| 文件 | 实际职责 |
|---|---|
| app/agents/tools/web_search.py | 四国内协议、模型/来源/响应预算、脱敏、取消 |
| app/agents/tools/file_ops.py | 公开共享根、有限只读、文件对象绑定与安全拒绝 |
| app/agents/tools/builtin.py、base.py | 可选注册；仅新增两工具观测和异常脱敏 |
| app/core/config.py、.env.example | 搜索独立配置、文件默认关闭与有限预算 |
| app/api/routes/health.py、docs/swagger.json | 启动本地状态校验和 additive health 契约说明 |
| tests/test_pagt001_tools.py | 正式工具边界回归 |
| tests/test_pagt001_integration.py | 独立注册/Agent/ChatService/API持久化/health/磁盘 E2 |
| tests/test_pagt001_review.py | 独立协议与Windows对象绑定反例 |
| README.md、docs/product/项目产品需求方案.md | 用户批准国内替代、配置、共享公开范围及回滚 |
| docs/ai-prompts/tools-pagt-001.md | 新工具十要素规格，不把统一治理写成完成 |
| docs/ai-prompts/prompt-model-registry.json、.md | 追加 prompt-model-v2，旧记录不变；新版本操作说明 |
| docs/evaluations/pagt001-tools-v1.md | Smoke/Adversarial/Observed Regression版本与证据边界 |
| docs/plans/plan_pagt_001_20261011.md、plan_pagt_001_review_fixes_20261011.md | 先于业务实现的计划和审查修复计划 |
| docs/reviews/2026-10-11-PAGT-001-review.md、-verification.md | 独立审查与验收实跑、历史和最终指纹 |
| docs/reviews/2026-10-11-PAGT-001-business-acceptance.md | 后续用户授权的独立业务操作验收、独立自建场景及当前源绑定 |
| docs/plans/closure_pagt_001_20261011.md、tasks.yaml | 原始契约逐项对账，保留人工未验收状态 |

未修改前端、ChatService、主Agent运行时、Tool Executor、依赖清单或数据Schema。未实现 file_ops 写能力、资源ACL、独立审计账本或调用预算统一治理。

## 实际分工与审查修复

主 Agent `/root` 整合配置、注册、health、文档、登记与扩大回归；`/root/pagt001_implement` 实现工具及边界测试；`/root/pagt001_verify` 独立真实入口验收；`/root/pagt001_contract` 独立计划、原契约及最终入口反例审查。四角色不重叠拥有文件，数据库验证串行。使用 task-card-delivery；计划按已读取 plan-eng-review 和 brainstorming 的设计/取舍维度审查，最终按 code-review-quality 的安全/逻辑/兼容维度审查。

- 初始有效 Red：真实 default_tools 未注册已配置搜索/文件，2 failed，exit1，不是导入失败。
- F01 P2：拒绝参数仍进入注册器观测；独立正式回归 1 failed 后先落盘计划，再限制新增两工具参数展示和异常输出，30项入口复验通过。
- F02：明确 Accept-Encoding identity，消除客户端默认压缩协商与有界响应检查冲突，协议头正式回归通过。
- F03 P2：3001字符深 JSON 可触发递归异常，破坏工具注册与health；独立正式3 failed后先落盘计划，在解析边界转换为已有分类拒绝，完整反例复验通过。
- 百度官方参数表与 curl 的非必要追问字段存在拼写歧义，删去该可选字段，沿用默认 false；独立四provider协议重验通过。

最终独立审查无未解决P1/P2。验证和审查报告保留修改前失败及历史SHA，最终结果绑定当前源，不以历史通过覆盖新源。

## 实际验证命令与结果

全部 Python 命令使用 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，不是 base。每次 pytest 独立 basetemp，使用测试库/临时文件和合成 HTTP，不接触生产数据。

| 命令（python 指上述解释器） | 实际结果 |
|---|---|
| python -m pytest tests/test_workflow.py tests/test_tools.py tests/test_health.py tests/test_agent_chain.py tests/test_fast_route.py -q --basetemp=data/pagt001-baseline-20261011 | 基线76 passed，exit0 |
| python -m pytest tests/test_pagt001_tools.py tests/test_pagt001_integration.py -q --basetemp=data/pytest-tmp/pagt001-implement-boundaries | 实现者90 passed，exit0，2.83s（F03前历史专项） |
| python -m pytest -k 'tool' -q --basetemp=data/pagt001-root-tool-regression-20261011 | 最终源129 passed、1448 deselected、1既有弃用警告，exit0，8.54s |
| python -m pytest tests/test_tools.py tests/test_agent.py tests/test_agent_chain.py tests/test_fast_route.py tests/test_supervisor.py tests/test_health.py tests/test_workflow.py tests/test_pagt001_tools.py tests/test_pagt001_integration.py tests/test_pagt001_review.py -q --basetemp=data/pagt001-root-regression-20261011 | 最终源200 passed、0 skipped、1既有弃用警告，exit0，5.23s；新增四个独立协议反例前 |
| 独立 python -m pytest tests/test_pagt001_review.py -q（完整实际命令见review） | 19 passed、0 skipped，exit0，1.80s，含后补四provider协议 |
| 独立 python -m pytest tests/test_pagt001_integration.py -q --basetemp=data/pagt001-verify-20261011-final | 最终源30 passed、0 skipped，exit0，2.02s |
| 独立业务 Agent：python -m pytest tests/test_pagt001_tools.py tests/test_pagt001_integration.py tests/test_pagt001_review.py tests/test_tools.py tests/test_health.py tests/test_workflow.py -q --basetemp=data/pagt001-business-acceptance-20261011 | 135 passed、0 skipped，exit0，5.78s；另独立stdin实际业务操作全部通过exit0，步骤与输出见business-acceptance报告 |
| python -m pytest docs/ai-prompts/tests/ -q --basetemp=data/pagt001-prompt-regression-20261011 | 59 passed、0 skipped，exit0，16.80s |
| python -m ruff check . --no-cache | 全仓 All checks passed，exit0；默认缓存目录权限错误曾exit1，后改无缓存实跑，不降低规则 |
| python -m mypy app/ | 191 source files，无问题，exit0 |
| python app/agents/version_registry.py check | prompt-model-v2 status ok，exit0 |
| git -c core.whitespace=cr-at-eol diff --check | exit0；沿用既有CRLF允许规则，未修改Git配置 |

数字不累加为互斥用例总数。未执行全库 pytest 无筛选，也未执行前端构建/页面操作（无前端改动，截图不适用）。既有 ast.NameConstant 弃用警告不属于本卡源改动。

## 登记与最终源指纹

登记 v2 基点仍为上述提交，实际源码包含未提交工作树变化；`source_version=16760dc41df1666c0721909860f21b336dcbc8e54f8ac46231ac886600bb279e`，192登记源。主对话模型默认指纹 `eef69460f8a0996a9793ec2349e3f8ba66a4919d35b31161f45fe876735efaf8` 不变；搜索模型独立配置和白名单由工具源/配置源登记覆盖，观测显示本次声明的provider/model。登记不是实际运行环境配置或真实模型身份认证。与 Git 基点 JSON 比较证实 v1 全记录未变。

| 文件 | SHA256 |
|---|---|
| app/agents/tools/base.py | d295dab0948135ac4baf6ff4b57ec1ed370fc79befec5058e0bd640cc1f93b5b |
| app/agents/tools/builtin.py | 5da0586c310c7527914c74419bef8ccc9fdd3ee53fb852bc3111ba763ef81b3f |
| app/agents/tools/web_search.py | 488c36eca6833cfaf63787af5d82c17fbfd7f5e041a6e587181756c7eab849a2 |
| app/agents/tools/file_ops.py | 39fe09963324f8d6bd35f8cc7ac9a0784ab773bf6172e14db0c6ea1750ba301d |
| app/core/config.py | 08cfa3ee30ded37cc1f621b8d79c916e05034d22bd1e19367db6839bfd029a53 |
| app/api/routes/health.py | 597a4a70b7eb115be60bd351e32d19987c0ce309ed41c295f3f571d224dc78bf |
| tests/test_pagt001_review.py | a1aa740b9f94632940191229eb225cd02b7c116424903a79837e37a1a34fcd84 |
| docs/ai-prompts/prompt-model-registry.json | 6c9637c721bb5fb594aa073f60e26a81ffaf518da7a77c35236d4287121ff0e0 |

FLOW-001前置当前重放通过，扩大回归仍包括9项工作流测试；scheduler SHA256 `0fc0ad61a54327e3148e9eb2268c736f17de54249422943628b3072611109955`，test_workflow.py `e62fe83beaad3e0482cd39184783f24f26e791b211f69a4535e299e6c0a8e2fe`，与开工一致。未发现其未决P1；这不证明到点触发执行完整。

## 失败、回滚与未验证

通过 `SEARCH_PROVIDER=disabled` 和 `FILE_OPS_ENABLED=false` 关闭新工具，不删除任何文件/数据。源码发行回滚需恢复上一完整源码及匹配 v1 登记，不可把基点当成包含本次未提交内容的发行；恢复后运行登记check、health及旧工具回归。实际生产秘密恢复/重启需要对应授权，本次未执行。

后续验收授权：用户明确指令“启动单独Agent做人工验收”。按 task-card-delivery 的“若用户当前指令明确将可自动验证的人工操作验收交由 Agent，记录授权依据并按可复现证据验收”执行；独立 `/root/pagt001_business_acceptance` 未参加本卡实现、原审查或原验证。该 Agent 亲自重放135项，并自行执行真实文件 `/api/chat`→消息新请求回读、匿名/越界/伪造身份/写动作拒绝、文件SHA不变、旧对象关闭、旧四工具可用及旧登记完整保留，全部通过。源指纹与前轮一致、v2check通过，无新阻断；本卡委托的可复现业务操作验收缺口闭合，登记 done。这不是宣称真人亲自操作验收。

真实远端密钥认证、真实收费检索质量、具体生产目录授权、生产启用/回滚仍未验证，也不由 E2 模拟结果替代；这些不在本次授权操作范围。本卡证据支持已实现的功能/协议/安全链路，不支持生产搜索质量；文件可见范围仍取决于运维正确配置公开资料根。不自动推进 PAGT-002。
