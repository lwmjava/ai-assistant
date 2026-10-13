# Agent 基线门禁运行规格

状态：本地实现；GitHub 运行证据以实际 Actions run 为准。

## 运行契约

沿用 agent-chain-v0.1 的十条 Silver 合成案例，非 Gold；不扩容、不改标准答案。评分版本 agent-chain-eval-0.2.0，报告协议 agent-chain-report-1.0.0，门禁 agent-chain-gate-1.0.0。

route_accuracy ≥0.9，followup_recall=1.0，tool_selection_recall=1.0。仅覆盖短路/完整分流、两条关键续问和两个 code_sandbox 工具选择正例，不代表通用任务成功率或工具执行安全。

official 使用真实单一 OpenAICompatibleProvider、温度0、关闭响应缓存、能力护栏开启且配置获核验；不支持把 fallback 的多模型结果混为一个模型。12次串行逻辑请求，单次60秒、总780秒，不加重试；CI job20分钟。只解析工具意图，不调用 Tool/MCP/RAG/DB。side_effects 的兼容零值是设计声明，不是动态计数；真实模型 HTTP 请求不能理解为零网络访问。

报告保存请求模型与端点哈希；当前 provider 只返回内容字符串，未验证服务端响应模型身份。模型回复≤16KiB、报告≤1MiB，超限拒绝。模型密钥不得进入报告，回复包含实际密钥时拒绝写入；错误仅打印安全分类。

cases/index 指纹算法为 sha256-utf8-crlf-to-lf-v1，只把 CRLF 换为 LF，再 SHA256；其余字节变化均不兼容。锁定原 Git blob 的两份摘要；历史 Windows 报告的原字节 SHA 算法不同，不能冒充新报告。Prompt 指纹覆盖实际构造的两阶段送模消息，源码指纹覆盖实际运行的 Agent/LLM/评测脚本与 CI 文件，工作区改动也可追踪。

## GitHub 配置

Secret：LLM_API_KEY（仅 official 步骤注入）。非秘密 Variables：LLM_BASE_URL、LLM_DEFAULT_MODEL；默认官方 DeepSeek / deepseek-flash。第三方部署或未内置模型还须提供有效的 LLM_CAPABILITY_DECLARED 与 LLM_CAPABILITY_DECLARED_SOURCE。CI固定 chat/intent 都走 chat profile，关闭缓存、开启能力护栏。

push / pull_request 的独立 Agent job 不依赖 backend、frontend 或 RAG 成功。缺凭证（含 fork/Dependabot）明确失败，不自动用 smoke、跳过或提高事件权限。当前没有配置仓库分支保护；工作流提供检查不等于已强制禁止合并。

只上传 evals/reports/agent-ci/，保留14天；合成故障副本单独放 synthetic-probes/ 并附 manifest。不上传 .env、数据库或整个工作区。

## 本地入口

使用项目规定解释器；环境变量只在当前进程覆盖，不修改 .env。输出路径仅限仓库 evals/、data/ 或系统临时目录，既有证据不覆盖。

```powershell
$env:RAG_BACKEND='native'
$env:LLM_CHAT_FALLBACK_CHAIN='chat'
$env:LLM_INTENT_FALLBACK_CHAIN='chat'
$env:LLM_RESPONSE_CACHE_ENABLED='false'
$env:LLM_CAPABILITY_GUARD_ENABLED='true'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' scripts/run_agent_chain_eval.py --mode official --out evals/reports/agent-ci-local/official.json
```

gate / probe 分别接受 --report、--expected-model、--expected-endpoint-hash；probe 另需 --out-dir。期待身份必须来自独立运行配置，不能读取报告身份当作期待值。端点哈希通过 run_agent_chain_eval.endpoint_hash 构造，拒 URL 凭据、query 和 fragment；不要把密钥放命令行。

gate 从原始回复经生产 parser 重建预测、独立计数，对照完整十例、标准答案、汇总、当前输入和源码，再判指标。退出码0通过、1指标退化、2输入/身份/完整性错误；稳定日志 AGENT_GATE_PASS、AGENT_GATE_METRIC:<metric>、AGENT_GATE_ERROR:invalid_input_or_identity。

probe 仅从已过 gate 的完整 official 结构报告派生三类受控故障，真实子进程 gate 必须 exit1且命中对应指标；exit2/异常不能算成功。此工具检验机制，不认证报告来源，调用者必须保留真实 official 的独立 E1 证据，合成测试构造的 official 结构不能当模型质量报告。

报告原子排他发布：先完整写本次临时文件，再创建硬链接，不覆盖目标；失败只清理本次临时文件。probe输出目录也不可重入覆盖，失败后换新目录运行，保留旧证据。硬链接不可用的文件系统会明确失败，不降级成覆盖写。

回滚：撤销独立 Agent job，恢复手工评测；无业务数据/数据库迁移。不要删除既有模型或审查证据，不自动执行 Git 回滚/发布。
