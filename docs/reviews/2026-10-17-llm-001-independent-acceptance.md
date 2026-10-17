# LLM-001 独立验收审查

日期：2026-10-17。审查者未参与 LLM 路由实现，也未发起本次实机请求。只读既有证据，未改业务代码、测试、依赖、`.env`、`tasks.yaml`，未提交或推送。

## 范围

核对 `tasks.yaml` 中 `LLM-001`（当前状态 `in_review`）四条验收，以及计划 `docs/plans/plan_llm_001_20261015.md` 验收节、有界实机计划 `docs/plans/plan_llm001_two_vendor_live_20261017.md`、实现说明 `docs/plans/implementation_llm_001_20261015.md`。

四条验收：

1. 未知任务拒绝。
2. 熔断后走兜底链。
3. 规划、执行、质量门用 chat。
4. 至少两个模型提供商可切换，切换后对话走被选中那一家。

门禁：全量 pytest 与 GitHub Actions 只沿用已提交 `d5311557c07273137104105f961cc20fe0e281b1`，且当前工作区未改业务、测试、依赖或运行配置。历史千问报告 `docs/reviews/2026-10-16-llm-qwen-live.md` 只作背景，不计入本次两家切换。

当前 `HEAD` 即为该 SHA。`556a9dd04e349e519e8f3d4e58ecdddaaeed647c..HEAD` 在 `app/llm`、`app/services/chat_service.py`、`app/agents`、`app/core/config.py` 上无差异，上述路径的当前版本与该基线提交相同。

## 逐条判定

### 1. 未知任务拒绝：满足

实现说明记载离线红测后定向回归通过：同一组用例最终 120 passed、退出码 0（Windows，指定 conda Python 3.12.0）。其中 `tests/test_llm_route.py` 对未知用途 `plan` 拒绝，`tests/test_llm_profiles_review.py` 对空串、`unknown`、`fallback` 等用途在配置接口与生成工厂两侧拒绝。当前 SHA 的全量 pytest 为零失败，见下方门禁。未重跑 pytest。

### 2. 熔断后走兜底链：满足

实现说明列出的离线用例包含跨工厂开路后改走兜底：`tests/test_llm_circuit.py` 在主模型连续瞬时失败后，后续新工厂实例跳过主模型并返回兜底响应；`tests/test_llm_circuit_review.py` 覆盖半开、取消、凭据错误仍兜底但不计瞬时熔断。定向结果为 120 passed、退出码 0。当前 SHA 全量 pytest 为零失败。未重跑 pytest。

### 3. 规划、执行、质量门用 chat：满足

实现说明写明规划、执行和质量门继续沿用 chat，且未知用途拒绝，不为五阶段单独配模型。离线用例 `tests/test_llm_route.py` 把对话客户端注入管线后，阶段调用记在该 chat 客户端上；意图客户端分开注入时，是否需要规划走意图客户端，理解阶段走 chat 客户端。管线中规划、行动与质量门自纠错都经阶段入口调用注入的对话客户端。这些文件包含在实现说明的 120 passed 定向集内，并包含在当前 SHA 全量 pytest 内。未重跑 pytest。

### 4. 两家切换：满足

只采用 2026-10-17 两份机器记录。脚本在写入 `passed` 前要求：助手消息角色为 assistant、状态为 complete、正文与当次回复一致且模型标识等于被选模型；用户消息已保存；回复含「你好」；记录中的 HTTP 状态均为 200。

| 项目 | 千问 | DeepSeek |
|---|---|---|
| 文件 | `docs/reviews/llm001_qwen_live_20261017.json` | `docs/reviews/llm001_deepseek_live_20261017.json` |
| status | passed | passed |
| endpoint | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `https://api.deepseek.com` |
| model | qwen-plus | deepseek-flash |
| 非流式 HTTP | 200，返回模型 qwen-plus | 200，返回模型 deepseek-flash |
| 流式 HTTP | 200，返回模型 qwen-plus | 200，报告未解析到返回模型 |
| persisted_user | true | true |
| persisted_assistant | true | true |
| reply_contains_hello | true | true |
| 回复长度 / 摘要 | 8 / `510340ada2dd43de1323c5e974878bdd5035745232ee63e6fd4bef643b43576d` | 同左 |

两家各有真实 200、回复含「你好」、用户消息与 complete 助手消息持久化，且持久化模型标识为被选模型。DeepSeek 书面说明 `docs/reviews/2026-10-17-llm-deepseek-live.md` 与其 JSON 一致，并写明新 Session 复核模型为 `deepseek-flash`。该页称千问仍只用 2026-10-16 报告、本轮未重发；该句不覆盖随后存在的 `llm001_qwen_live_20261017.json`。本次千问以这份 2026-10-17 JSON 为准。

`docs/reviews/2026-10-16-llm-qwen-live.md` 是早先五次千问请求，回复验收词不同，不能代替本次千问记录。

### 全量与 CI：可沿用

`docs/reviews/cov001_acceptance_evidence_20261017.json` 的 `measurement_sha` 与 `ci.sha` 均为 `d5311557c07273137104105f961cc20fe0e281b1`，与当前 `HEAD` 一致。pytest 字段：退出码 0，passed 2530，skipped 3，failed 0。`docs/reviews/cov001_pytest_20261017.xml` 首行属性：tests=2533，failures=0，errors=0，skipped=3。2530 与 3 之和等于 2533。

GitHub Actions run `38996604616`：`head_sha` 为同一 SHA，run `conclusion` 为 success。Jobs：`Backend (ruff / mypy / pytest)` success，`Frontend (typecheck / vitest / eslint / build)` success，`Agent official baseline` success。

`git status --short` 的未提交内容为 `tasks.yaml`（仅 COV-001 状态与验收注记）、`docs/plans/`、`docs/reviews/` 与 `docs/evaluations/llm001_two_vendor_live_20261017.py`。对 `app/`、`tests/`、前端源码、依赖清单、`.env`、`.env.example`、`app/core/config.py` 的状态过滤为空。未触及业务、测试、依赖或运行配置，故全量与 CI 可以沿用该已提交 SHA。本审查未改 `tasks.yaml`。

## 命令与退出码

| 命令 | 退出码 | 结果 |
|---|---|---|
| `git rev-parse HEAD` | 0 | `d5311557c07273137104105f961cc20fe0e281b1` |
| `git diff --stat 556a9dd..HEAD -- app/llm app/services/chat_service.py app/agents app/core/config.py` | 0 | 无输出；上述路径无差异 |
| `git status --short` | 0 | 见上一节；未触及业务、测试、依赖、运行配置 |
| 读取 `cov001_pytest_20261017.xml` 首行 tests/failures/errors/skipped | 0 | 2533 / 0 / 0 / 3 |
| `GET https://api.github.com/repos/lwmjava/ai-assistant/actions/runs/38996604616` 及其 jobs | 0 | head_sha 匹配；run 与 Backend、Frontend、Agent official 均为 success |

未重跑 pytest，未读取 `.env`。

## 限制

- 两家流式请求都没有 usage。千问流式记录有 HTTP 200 与返回模型，无 usage。DeepSeek 流式记录有 HTTP 200，没有返回模型，也没有 usage。这是记录限制，不算失败，也不补写估算用量。非流式请求有供应商 usage：千问输入 162 / 输出 1 / 合计 163；DeepSeek 输入 184 / 输出 27 / 合计 211。
- 实机使用合成库、空库与 Mock Embedding，只证明产品链选中该提供商并完成一次合成对话，不证明检索质量、生产或多实例。
- 定向 120 passed 是 2026-10-15 实现说明中的离线结果；与当前版本的绑定靠本 SHA 全量 2530 passed、零失败，而不是重跑定向集。
- 熔断状态不跨进程。本审查不把任务写成可发布。

## 最终结论

四条验收与全量、CI 门禁均满足。可以把任务状态改为 done。本审查没有修改 `tasks.yaml`。
