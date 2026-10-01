# ROUTE-001～ROUTE-002：按任务类型路由、兜底链、无模型引导

> 状态：已实现
> 来源：`docs/plans/plan_remaining_delivery.md` 的 ROUTE-001、ROUTE-002；`tasks.yaml`
> 日期：2026-09-27
> 截止：2026-12-22
> 依赖：TEN-003 已完成。ROUTE-002 依赖 ROUTE-001。

本文件写完并完成三轮自查。允许路径之外的文件经确认后才改业务代码。

## 目标

1. 对话和意图可以走不同的模型配置。主配置失败时按顺序试下一家。两家都失败时，用户看到明确错误，日志和错误里没有密钥。
2. 只配现有这一套变量时，行为和现在一样。
3. 没有可用的真实模型时，对话页说明：改环境变量里的密钥，然后重启。不把 Mock 说成已经接入的生产模型。配好并重启后，这段引导消失。

## 现状

- `get_llm_provider()` 只看 `LLM_PROVIDER`、`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_DEFAULT_MODEL`。`openai` 和 `ollama` 都是同一个 OpenAI 兼容客户端。显式 `mock`，或开发环境没有密钥，返回 Mock。生产环境没有密钥时，第一次取客户端就抛 `RuntimeError`。
- 管线只有一个 `self.llm`。理解、意图分流、规划、行动、质量门、反思和最终回复都走它。意图分流是 `_needs_plan`，系统提示是 `SYSTEM_PREFLOW`，阶段名是「意图分流」。
- `ChatService` 把这一个客户端交给自研管线和 Supervisor。记忆压缩和蒸馏也调用 `get_llm_provider()`，没有任务类型参数。
- 测试用 `set_llm_provider_override`，或直接把 Mock 传进管线。覆盖存在时，工厂不再读配置。
- `/api/health` 只报告进程、数据库和向量库。不报告模型。对话页不读健康检查。顶栏已经用查询键 `['health']` 轮询它，类型 `HealthInfo` 还没有 `checks`。
- 失败时，兼容客户端把 httpx 异常原样抛出。管线吞掉异常，用户看到「抱歉，处理你的请求时出现问题，请稍后重试。」异常文本可能进日志。

## 和产品方案的差异

产品方案 P0-8 和 §5.1.6 还要求：请求里指定 `model_profile` 并校验租户、embedding 与 rerank 分开配置、YAML、同一家先指数退避两次、熔断和半开。

`tasks.yaml` 把本批收成：至少两个提供商、对话和意图不同配置、失败后走兜底链、密钥只来自环境变量。非目标写明不做控制台录入、密钥不落库、不更换 Embedding。

本批按任务契约做，不按 P0-8 的全文做。上面那些留下一批。若要纳入，先改任务范围和允许路径。

## 节点范围

2026-09-27 确认：本批不给理解、规划、行动、质量门、反思、响应各配一个模型。这些节点共用对话链。能单独配置的只有意图分流。检索继续用现有嵌入配置，不进入这套路由。

MVP 要证明的是能接两家，并且第一家失败时换到第二家。逐节点分模型要回答的是哪一步该用更便宜或更强的模型。现在没有按节点测过耗时或回答质量，一次改完无法判断哪一步真的该换。本批同时还有沙箱要交付，不再扩大范围。

路由器按用途取一条链。用途和链的对照放在一处，本批只有 `chat` 和 `intent`。兜底顺序、换家条件和固定错误不按节点各写一遍。MVP 之后若要拆开某个节点，先有该节点的耗时或质量证据，再一次只加一个用途名和它的环境变量。

每次调用从自己这条链的第一家重新开始。理解若换成了第二家，下一步规划仍先问对话链的第一家。

## 已定的取舍

两个提供商是两套地址、密钥和模型名，不是再写一个 SDK。两套都用现有的 OpenAI 兼容客户端。Mock 不算第二个生产提供商。

配置只加环境变量，不新增 YAML，不入库。现有四个主变量仍是对话主配置，名字叫 `chat`。未填写的新变量等于没配，单密钥安装和现在相同。

意图只指 `_needs_plan`。理解、规划、行动、质量门、反思、最终回复，以及记忆压缩和蒸馏，都算对话。Supervisor 仍只用对话这条链。它是可选编排，本批不拆它的两次调用。

主配置没有密钥时，不把整条链静默换成 Mock。开发环境只有在对话链上一个真实配置都没有时，才使用 Mock。链上另有一把密钥，就用那一家。生产环境一条真实配置都没有时，不在导入阶段崩溃；调用时返回固定错误。健康检查只读配置，不拨外网。

同一家不重试，不做熔断。传输失败、超时，以及 HTTP 401、403、408、429、5xx，才试下一家。HTTP 400 不换家，换一家也修不好我们自己的请求体。流式在吐出任何字之前失败，可以换下一家。已经吐出字之后失败，不再拼接另一家的输出。

测试里的 `set_llm_provider_override` 对对话和意图都生效。直接传进管线的客户端不变：不另传意图客户端时，意图分流用同一个。`ChatService` 构造时传入的客户端同样盖住两条链。评测里的脚本模型是这样注入的，意图分流不能再去读环境变量。

无模型引导不禁止发送。开发环境的 Mock 仍能演示。引导只说明这不是可用的真实模型。健康检查失败时不显示这段引导，避免和顶栏的「后端离线」重复成两句错话。模型未就绪不把 `/api/health` 打成 503，否则没配密钥时顶栏会显示后端离线。

## 方案

### ROUTE-001 路由与兜底

新增环境变量，写进 `app/core/config.py` 和 `.env.example`：

- `LLM_FALLBACK_PROVIDER`、`LLM_FALLBACK_BASE_URL`、`LLM_FALLBACK_API_KEY`、`LLM_FALLBACK_MODEL`。密钥为空表示没有第二家。
- `LLM_INTENT_PROVIDER`、`LLM_INTENT_BASE_URL`、`LLM_INTENT_API_KEY`、`LLM_INTENT_MODEL`。密钥为空表示意图复用对话主配置。
- `LLM_CHAT_FALLBACK_CHAIN`，默认 `chat,fallback`。
- `LLM_INTENT_FALLBACK_CHAIN`，默认 `intent,chat,fallback`。

链上只认这三个名字。未知名字和重复名字跳过。某一跳没有密钥就跳过。某一跳的提供商是 `mock` 时也跳过，除非整条对话链上没有任何带密钥的真实配置，这时才整段换成 Mock。因此 `LLM_PROVIDER=mock` 且兜底密钥已填时，调用走兜底那一家，不在 Mock 上停住。

`app/llm/factory.py`：

- `get_llm_provider(task="chat")`。用途到链的对照放在一处表里，本批只登记 `chat` 和 `intent`。未知用途直接拒绝，不悄悄当成对话。有覆盖时，两个任务都返回覆盖对象。
- 按任务组装链。链上每一跳仍是 `OpenAICompatibleProvider`，超时沿用 `LLM_TIMEOUT`。换家逻辑只写这一处，不散落到理解、规划、行动、反思或响应里。
- `llm_availability()` 只读配置，返回 `real`、`mock` 或 `unavailable`。不返回密钥、地址、模型名。对话链上至少有一跳非 mock 且密钥非空，为 `real`。否则开发环境为 `mock`，生产环境为 `unavailable`。

`app/llm/` 里的兜底链实现 `LLMProvider`。`chat` 和 `stream_chat` 按顺序调用。失败时只记录任务名、配置名、异常类型和 HTTP 状态码。不记录异常原文、请求体、`Authorization` 或密钥。全部失败时抛出固定错误，消息为「当前没有可用的模型，请检查环境变量中的模型密钥后重试。」不把 httpx 异常挂在 cause 上。

调用点：

- `AgentPipeline` 增加可选的意图客户端。缺省时意图分流用原来的 `self.llm`。`_needs_plan` 用意图客户端。其余阶段不动。
- `ChatService._build_pipeline` 把对话链和意图链分别传入。全局覆盖存在，或构造时已经传入客户端时，两链是同一个对象。Supervisor 仍只收对话链。
- 管线捕获到上述固定错误时，用户可见文字用这句，不用笼统的「请稍后重试」。其它异常仍用原来的笼统句子。流式错误事件同样处理。

`/api/health` 增加 `checks.llm.mode`，取值来自 `llm_availability()`。不参与整体 `status`，不改变 200 或 503 的现有条件。

### ROUTE-002 对话页引导

`frontend/src/types/api.ts` 的 `HealthInfo` 补上 `checks.llm.mode`。对话页读取 `/api/health`，查询键与顶栏相同，为 `['health']`。`tests/test_health.py` 断言该字段存在，且不改变原来的 200 或 503。`checks.llm.mode` 为 `mock` 或 `unavailable` 时，在消息区域上方显示引导：

「当前没有接入可用的真实模型。请在环境变量中填写模型密钥，保存后重启服务。」

不出现「已接入」「生产模型可用」或把 Mock 描述成可用模型。`mode` 为 `real` 时不显示。健康检查请求失败时不显示。有历史消息时也显示，不只在空会话里显示。发送框保持可用。

## 非目标

不做控制台模型表单，密钥不落库，不改 Embedding，不改 Reranker。不做请求级 profile，不校验租户是否买了某个模型。不做 YAML。同一家不重试，不做熔断。不改 Supervisor 的两次调用分别走哪条链。不因为没配模型而禁止发送，也不把健康检查打成失败。

不给理解、规划、行动、质量门、反思、响应各自加一套环境变量，也不在本批预留这些空配置。嵌入模型留在现有检索配置里。逐节点拆分放到 MVP 之后，一次只加一个有证据的用途。

## 验收

- 配了不同的意图变量时，意图分流打到意图那一家，其它阶段打到对话那一家。意图变量留空时，意图分流与对话同一家。
- 对话主配置抛出超时或 5xx 时，下一家被调用并返回其结果。主配置返回 400 时，不调用下一家。
- 流式在第一个字之前失败时换下一家。已经吐字之后失败时，结果里不会出现下一家的文字。
- 全部失败时，返回文字是上面那句固定错误。用一把形如 `sk-test-secret` 的密钥构造失败，响应和日志都不含这把密钥。
- 只填现有主变量时，仍得到一个兼容客户端。开发环境全空时得到 Mock，`llm_availability()` 为 `mock`。生产环境全空时为 `unavailable`，取客户端不会在导入时崩溃。主变量为空但兜底密钥已填时，可用性为 `real`，调用走兜底那一家。
- `set_llm_provider_override` 之后，对话和意图都是覆盖对象。现有对话测试不用改调用方式。
- 对话页在 `mock` 和 `unavailable` 时能看到引导原文。`real` 时引导不在。健康检查失败时引导不在。
- `/api/health` 在模型为 mock 时仍按数据库和向量库决定 200 或 503。

## 允许路径之外的必要改动

任务允许路径盖不住「任务类型真正被用上」和「页面知道有没有真实模型」。不改这些文件，验收不成立。

- `app/agents/pipeline.py`：意图分流使用单独的客户端；全部失败时采用固定错误文案。
- `app/services/chat_service.py`：构造管线时传入对话链和意图链。
- `app/api/routes/health.py`：增加 `checks.llm.mode`。不改管理员系统状态的响应模型。
- `README.md`：配置表补上新变量，并写明缺密钥时的引导。项目规则要求配置变更同步 README。

`frontend/src/` 在 ROUTE-002 的允许路径内。`app/llm/`、`app/core/config.py`、`.env.example`、`tests/` 在 ROUTE-001 的允许路径内。

## 验证

实现时执行，不在本计划里宣称已经通过：

```powershell
pytest tests/test_llm.py tests/test_llm_route.py tests/test_health.py -v
pytest tests/ -k "llm or route" -v
ruff check app/
```

```powershell
cd frontend
npm run typecheck
npm run build
```

路由是确定性分支，用单元测试证明。不新增检索评测集，也不用 Mock 声称回答质量变了。对话页引导在浏览器里核对：无密钥时可见，模式为真实时不可见，健康检查失败时不误报。

## 自查

### 第一轮：和现有代码、任务原文是否冲突

对照了工厂、管线的单一客户端、`_needs_plan`、健康检查、对话页和允许路径。

- 产品方案比任务更宽。本批按 `tasks.yaml` 的交付和非目标，不实现 profile 表、熔断、Embedding 路由和 YAML。
- 不改管线的话，工厂无法知道这一次调用是意图还是对话。靠提示词猜任务类型会和提示文案绑死，不采用。
- 页面读不到环境变量。不改健康检查，引导只能猜，验收「配好并重启后消失」无法成立。
- 现有覆盖和「构造时传入一个客户端」必须保持，否则对话测试会被路由拆开。
- 管理员状态接口是另一份模型。本批不加模型字段，避免做成管理端模型页。

本轮的待确认项是上面四份允许路径之外的文件。确认前不改业务代码。

### 第二轮：企业里会不会这样用

- 本批只把意图分流和其余对话节点分成两类。理解到响应各配一个模型，留到 MVP 之后按证据逐个加。路由器用用途查链，就是为了那时只加一行，不重写换家。
- 对话用一家、意图分类用另一家，是常见的省成本接法。没单独配意图时，继续用对话那一家，避免没填新变量就行为突变。
- 主家超时或 5xx 再换一家，用户仍能得到回答。请求本身不合法时不换家，避免把同一份坏请求打到两家。
- 流式已经出字就不再换家，避免两段回答粘在一起。
- 一把密钥都没有时，开发环境仍能用 Mock 把界面走通，同时写明这不是真实模型。生产环境不假装有模型。
- 引导不挡住输入。本地演示还能发消息。真正的失败发生在调用时，用固定句子说明。

本轮没有待改的设计问题。

### 第三轮：失败时会不会说错，以及能不能被测试抓住

- 错误句子是固定的，不拼接供应商返回。日志只记类型和状态码，测试用哨兵密钥断言它不出现。
- 健康检查不拨模型、不把 mock 算成服务宕机，顶栏不会因此变红。
- 健康检查请求失败时，页面不显示「去改密钥」，因为那时还不知道模型配没配。
- 400 不降级、流式中途不换家，各有一条测试。意图留空、只配主变量、只配兜底密钥，也各有一条。
- 覆盖开关仍让旧测试走 Mock，避免本批把无关对话测试改红。

本轮没有待改的设计问题。

四份路径外文件确认后，按本文件实现。先做 ROUTE-001，再做 ROUTE-002。

## 实现说明

对话和意图分流可以走不同的模型配置。只填原来的主密钥时，仍然使用这一家。主家超时、连不上或返回 401、403、408、429、5xx 时，改问兜底那一家。400 不换家。流式已经吐出文字后不再拼接另一家。两家都不行时，用户看到「当前没有可用的模型，请检查环境变量中的模型密钥后重试。」日志里没有密钥。

理解、规划、行动、质量门、反思和最终回复仍共用对话链。能单独配置的只有意图分流。检索仍用原来的嵌入配置。开发环境一把可用密钥都没有时继续用 Mock，对话页提示去环境变量填写密钥并重启。配上密钥并重启后，这段提示消失。生产环境没有密钥时，取客户端不会在导入时崩溃，调用时返回上面那句固定错误。健康检查会带上 `checks.llm.mode`，但不因此变成 503。

代码位置：

- 路由与兜底：`app/llm/factory.py`，`app/llm/routing.py`
- 配置：`app/core/config.py`，`.env.example`，`README.md`
- 意图分流调用点：`app/agents/pipeline.py`，`app/services/chat_service.py`
- 健康检查：`app/api/routes/health.py`
- 对话页引导：`frontend/src/pages/Chat.tsx`，`frontend/src/types/api.ts`
- 测试：`tests/test_llm_route.py`，`tests/test_health.py`

验证：

```text
python -m pytest tests/test_llm.py tests/test_llm_route.py tests/test_health.py -v --tb=short
25 passed

python -m pytest tests/ -k "llm or route" -q --tb=line
23 passed, 1 skipped, 354 deselected

python -m pytest tests/test_chat.py tests/test_chat_controls.py tests/test_agent.py -q --tb=line
15 passed

python -m ruff check app/llm/routing.py app/llm/factory.py app/core/config.py app/api/routes/health.py tests/test_llm_route.py tests/test_health.py
All checks passed

cd frontend
npm run typecheck
通过
npx vite build
通过，6.89s
```

`ruff check app/` 仍有 7 条 `F821`，是管线和服务里原有的引号前向注解，这次没有新增。

本机当时把服务拉起来后，`/api/health` 返回 `status=ok`，`checks.llm.mode=real`。对话页需要登录。浏览器停在登录页，用户名和密码都是空的，没有进入对话页，因此没有在页面上亲眼看到引导消失。引导在 `mock` 和 `unavailable` 时出现、在 `real` 时不出现，由页面条件和健康检查字段决定，后端测试已覆盖字段本身。

没做的事：不给理解、规划、行动、反思、响应各自配模型。不改嵌入模型。不做熔断，同一家不重试。不做控制台模型表单，密钥不落库。Supervisor 仍只用对话链。
