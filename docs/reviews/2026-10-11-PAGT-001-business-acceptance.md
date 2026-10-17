# PAGT-001 独立业务操作验收

2026-10-11；执行 Agent `/root/pagt001_business_acceptance`，未参与本卡实现、原审查或原验证。依据用户本轮明确指令“启动单独Agent做人工验收”，按 task-card-delivery v1.2.0 的授权条款执行可复现的业务操作验收。仅修改本报告，不修改业务代码、任务状态、既有测试，不提交、推送、部署或开启生产工具。

## 结论与证据范围

**本卡原始功能契约的可复现业务操作验收通过，未发现新阻断问题。** 当前国内搜索四适配器、无 key 启动与健康提示、公开共享文件只读、越界/写动作拒绝和旧工具兼容均有本 Agent 亲自运行的 E2 证据；F01/F03 原失败正式回归重新通过。此结论支持关闭“Agent 代执行的人工操作验收”缺口，任务状态由主 Agent 根据完整门禁登记。

E2 使用合成 HTTP 返回、本地确定性模型驱动、真实 FastAPI/ChatService/Agent/ToolRegistry、测试 SQLite 和磁盘文件。HTTP 请求走 httpx MockTransport，不访问收费端点。模型驱动只提出预定动作并从真实观测生成答案；不能证明真实模型自主工具选择能力、搜索质量、费用或远端凭据有效性。未读取实际 .env、实际凭据或生产数据。新增自编操作明确禁用 dotenv 源，并强制测试库，不使用真实收费模型。

公开根属于运维明确批准的**所有获准对话用户均可读的公开资料**，本验收使用自建公开临时告示；不推导实际生产目录已经获得业务授权。没有前端改动，页面操作和截图不适用。真实远端密钥认证、真实收费检索质量、生产目录授权/启用、生产回滚仍未验证，不由本报告代替。

## 当前版本与原契约复核

实际 `git branch --show-current`：`codex/domestic-search-file-read`；工作树包含本卡尚未提交的源码、配置、文档及测试。Git 基点 `cb25b8053b8ee958cf9ff19d7dbd872e61323608`。已读取 AGENTS、基础指导/Harness、相关 Cursor 规则、原 tasks.yaml 卡、实施计划、实现说明、关闭对账及前两份独立报告；没有仅凭旧报告认定通过。

| 原始要求 / 失败边界 | 本 Agent 亲自执行的业务链与反例 | 结果 |
|---|---|---|
| 无 key 仍能启动，health 不可用提示 | 实际 TestClient lifespan、`/api/health` HTTP200、`checks.tools` unavailable、默认旧四工具清单 | E2 通过 |
| 配置 key 后对话调用搜索并返回来源 | 四国内 provider 分别经真实 `/api/chat`→ChatService→Agent→隔离 HTTP→带 URL 回复→独立 GET 会话确认持久化 | E2 通过；真实认证/质量未验证 |
| 四 provider 国内限制与精确协议 | 通义/智谱/博查/百度请求主机、国内模型与实际不同原始响应字段；国外/未知 provider/model 拒绝 | E2 通过，无国外 fallback |
| 文件读开关与白名单 | 正式 Agent 文件读取；另自编 `/api/chat` 读取 `bulletin.md`，回复及新 GET 均出现唯一告示标识 | E2 通过 |
| 越界与身份拒绝 | 正式绝对/穿越/未授权根/秘密文件/租户伪造拒绝；自编穿越、额外 `user_id` 与匿名 HTTP 请求拒绝 | E2 通过；匿名 401/403 门禁 |
| 无写能力，原文件不变 | 额外 action=write 拒绝；自编原文件 SHA256 读取前后完全相同，Schema 无写路径 | E2 通过 |
| 身份绑定 | 真实 Windows 文件句柄、junction、硬链接、stat/open 对象替换反例；来源 file/localIP/含凭据/javascript URL 拒绝 | E2 通过，0 skipped |
| 数值 | 正式 bool/NaN/Infinity/负数与预算超限输入反例，不产生文件变化或外部请求 | E2/E3 通过 |
| 上游失败 | timeout、429、500、非法 JSON、超大响应、缺来源；单次请求、取消传播，错误脱敏且旧工具可用 | E2 通过 |
| 回滚/重复读取 | 正式关闭回滚及旧对象拒绝；自编持有 file_ops 的旧 Registry 关闭后拒绝，清单恢复四工具，calculator 12*12=144 | E2 通过；未做生产回滚 |
| F01/F03 正式回归 | 参数/意外异常不回显；深 JSON 配置不阻断注册/health、远端深 JSON 分类拒绝 | E2 通过 |
| 前置与版本追踪 | workflow 当前 9 项包含在本次实跑；v2 CLI check、基点 v1 全字典逐项相等 | 通过；不推导工作流到点执行完整 |

## 本 Agent 实际执行

均使用 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`；独占共享测试数据库窗口，独立 basetemp。

1. `git status --short`、`git branch --show-current`：exit0；Git 有已有用户配置 ignore 路径权限警告，不影响分支/工作树结果。
2. `python -m pytest tests/test_pagt001_tools.py tests/test_pagt001_integration.py tests/test_pagt001_review.py tests/test_tools.py tests/test_health.py tests/test_workflow.py -q --basetemp=data/pagt001-business-acceptance-20261011`：**135 passed，0 skipped，5.78s，exit0**。不是引用别的 Agent 的结果。
3. `python app/agents/version_registry.py check`：`status=ok`、`version=prompt-model-v2`、`source_version=16760dc41df1666c0721909860f21b336dcbc8e54f8ac46231ac886600bb279e`、`config_basis=source-defaults`，exit0。
4. 独立 Python stdin 业务脚本：**exit0**；输出 `file API read + new request persisted reply + anonymous rejection: PASS`、`traversal + write + spoofed user + unchanged file + stale object disabled + old tools: PASS`、`v1 exact preservation and v2 current: PASS`。脚本实际输出本节下列源码 SHA256，和原最终报告一致。

第4项可复现步骤（全部实际执行，不是建议步骤）：导入应用前将 `DotEnvSettingsSource.__call__` 改为返回空字典，并设 ENV=development、DATABASE_URL=sqlite:///./data/test_ai_assistant.db、AUTH_ENABLED=true、初始管理员为空、AGENT_ORCHESTRATION=self、SECURITY_RATE_LIMIT=false；init_db；SEARCH_PROVIDER=disabled、RAG/MCP/MEMORY=false；在上述 basetemp 内 TemporaryDirectory 建 `bulletin.md`，内容 `BA-PUBLIC-20261011`；启用 FILE_OPS 并仅授权 public 根；保留旧 ToolRegistry。使用本地 Driver，在可用工具提示时仅发 file_ops(root=public,path=bulletin.md) 动作，其后只有观测包含告示标识才回复该标识；依赖覆盖为合成 member 用户，POST `/api/chat`，断言200与回复标识；新 GET 会话，断言 assistant 持久化内容含标识；删除本次测试会话。清理身份覆盖，再 POST，断言401/403。分别调用旧 Registry 的 ../private.md、action=write、user_id=spoofed，断言拒绝且不含告示内容；比较源文件前后 SHA256 相等；关闭 FILE_OPS，旧 Registry 再读同文件断言拒绝；default_tools 长度4，calculator 12*12 结果144。finally 清理 LLM/身份覆盖和本次临时文件。最后读取当前 registry 与 `git show <基点>:docs/ai-prompts/prompt-model-registry.json`，断言历史 versions[0] 全字典相等、current=v2，并打印下述文件 SHA256。

stdin 运行出现既有 fitz API 弃用提示及开发默认 JWT 秘钥提示；退出码0，未将提示伪装为生产认证证据。未执行本轮全库无筛选 pytest、全仓静态检查或前端构建；135项明确范围如上，其他既有技术门禁仍属于主 Agent 证据，不计作本人实跑。

## 最终绑定指纹

SHA256 为实际文件字节摘要；业务源码与前报告一致，没有审查后业务源漂移。

| 文件 | SHA256 |
|---|---|
| app/agents/tools/web_search.py | 488c36eca6833cfaf63787af5d82c17fbfd7f5e041a6e587181756c7eab849a2 |
| app/agents/tools/file_ops.py | 39fe09963324f8d6bd35f8cc7ac9a0784ab773bf6172e14db0c6ea1750ba301d |
| app/agents/tools/base.py | d295dab0948135ac4baf6ff4b57ec1ed370fc79befec5058e0bd640cc1f93b5b |
| app/agents/tools/builtin.py | 5da0586c310c7527914c74419bef8ccc9fdd3ee53fb852bc3111ba763ef81b3f |
| app/core/config.py | 08cfa3ee30ded37cc1f621b8d79c916e05034d22bd1e19367db6839bfd029a53 |
| app/api/routes/health.py | 597a4a70b7eb115be60bd351e32d19987c0ce309ed41c295f3f571d224dc78bf |
| tests/test_pagt001_tools.py | e4f127573eaf822a51f180ffcd07e9d488ebca821e24d23a67376ee8c428fcc0 |
| tests/test_pagt001_integration.py | 1c25ec5b7faf9ef333dcb729d097665b6f2ddaf7d42375714bdbc978cbce797c |
| tests/test_pagt001_review.py | a1aa740b9f94632940191229eb225cd02b7c116424903a79837e37a1a34fcd84 |
| docs/ai-prompts/prompt-model-registry.json | 6c9637c721bb5fb594aa073f60e26a81ffaf518da7a77c35236d4287121ff0e0 |

本卡无写路径、无新增依赖、旧四工具签名未改变，未推进下一任务。本报告的通过限于已授权的本地/隔离业务功能验收，真实收费质量与具体生产数据授权仍需后续独立证据。
