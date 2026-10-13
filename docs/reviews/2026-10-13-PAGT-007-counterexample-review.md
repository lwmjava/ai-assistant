# PAGT-007 独立入口反例审查

日期：2026-10-13。独立 Agent 身份：`/root/pagt007_counterexample_review_v2`。本角色未参与 runner、gate、probe、CI 或正式测试实现；不是最终人工审核 Agent。

## 依据与范围

读取 AGENTS.md、task-card-delivery 1.3.0、code-review-quality、项目迭代指导、Harness 工程规范，适用规则 00/01/02/03/07/08/09/10/11；直接核对 PAGT-007 原始卡、已批准实施计划、独立计划审查和 review_fixes。阅读 runner/gate/probe/CI 最终候选完整代码、runner/CI diff、三个正式测试文件及既有 LLM provider 调用方。分块阅读，不以实现者摘要替代审查。

按 task-card-delivery 的明确规则，不凑发现数量。审查仅写本报告及 `pagt007_review_probe_20261013.py`。不直接读取 .env，不调用模型、不输出凭证、不执行 pytest 或访问测试数据库。临时反例数据位于自动清理的系统临时目录，全部合成，不能证明模型质量或 GitHub 实际运行。

## 实跑反例

命令（指定 conda 解释器）：

```powershell
$env:RAG_BACKEND='native'
$env:LLM_PROVIDER='mock'
$env:LLM_API_KEY=''
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' docs/reviews/pagt007_review_probe_20261013.py
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' -m ruff check --no-cache docs/reviews/pagt007_review_probe_20261013.py
```

首次入口探针总 exit 0，30 项观察全部符合断言；其后因 F06 修改，未沿用首次结论关闭最终代码。最终完整重跑总 exit 0：31 项真实 gate/probe CLI 观察（E2）和 6 项 Git blob/LF/CRLF 内容摘要对照（E3），合计 37 项全部符合断言；探针 ruff exit 0。启动时有既有 PyMuPDF API 弃用提示，不涉及业务断言失败。探针自行构造报告并从 raw 独立计数，不使用 probe 的 recompute；通过真实 gate/probe 子进程观察退出码及稳定 reason code。构造真实 provider 类型的对象仅用于报告结构，没有调用其 chat；真实模型请求数为 0。

| 尝试的反例或边界 | 实际入口及观察 | 证据级别 |
|---|---|---|
| 完整合成报告 | gate CLI exit 0 / AGENT_GATE_PASS | E2，仅报告机制 |
| route=0.9 正常边界 | gate CLI exit 0，阈值保持0.9 | E2，仅报告机制 |
| route=0.8、followup=0.5、tool=0.5 | 三份独立受控报告分别 exit 1，原因分别为对应 metric | E2，退化拦截机制 |
| 完美基线和正常0.9基线衍生三类探针 | 两轮 probe CLI exit 0，内部实际 gate 三类均 exit 1/正确原因 | E2，非质量证据 |
| 重复同一 probe 输出目录 | 两轮均 exit 2，已有 manifest 保留 | E2，幂等/证据保护 |
| smoke/fallback/错误模型/错误端点 | gate CLI exit 2 / invalid_input_or_identity | E2，身份绑定 |
| 错误 commit/Prompt/源码/数据集摘要 | gate CLI exit 2 | E2，当前版本绑定 |
| raw_reply 与预测不一致、tool_reply 与工具名不一致 | gate CLI exit 2，不能自洽伪造评分 | E2，真实解析接线 |
| bool 请求数、bool 温度、bool 指标、NaN/Infinity/负指标 | gate CLI exit 2 | E2，数值边界 |
| 重复JSON键、非UTF8、深层结构、超过1MiB | gate CLI exit 2，无内部 Traceback | E2，失败边界 |
| smoke作为probe来源 | probe exit 2，输出目录未创建 | E2，禁止冒充official |
| 错误 data_hash_algorithm | gate CLI exit 2，不能自述另一套摘要规则 | E2，报告绑定 |
| cases/index 的 Git LF blob 与模拟 LF/CRLF 两种输入 | 各自得到相同的原 Git blob SHA；追加普通空格仍改变摘要 | E3，换行兼容单元对照 |

## 缺陷与复核

- F03：原 CI 重复 job/env 键。已核对主 Agent 删除重复并保留唯一 Agent job，能力护栏开启，模型默认 deepseek-flash。正式红/绿证据由独立验证角色保存；工作流不依赖 backend，无 pull_request_target、continue-on-error 或缺凭证跳过，secret 仅 official 步骤注入。实际 GitHub 运行仍未验证。
- F04：正常0.9基线已有一例错时，盲目翻转前两例可能反向修好一例。现 probe 只挑原本正确案例；本 Agent 用正常0.9报告的真实 probe CLI 复核，通过三项正确指标退化拦截。
- F05：原 write_report/probe 可绕开 runner 输出范围检查。本 Agent只读识别并即时报告；主 Agent已提升至共用 write_report 检查，probe 在 mkdir 前检查。读最终实现确认先验证再创建，不覆盖旧证据；正式范围反例由主 Agent纳入测试。
- F06（主 Agent 新发现，P1）：Windows CRLF 工作区数据 raw SHA 与 Git LF blob SHA 不同，Ubuntu必经 official 原会永远错误拒绝 frozen dataset。主 Agent先补正式 LF 数据入口红测试，修后报告 runner+gate 67 passed / exit 0（该 pytest 由主 Agent执行，本角色未复跑共享DB）。本角色读取最终修复：两数据文件统一仅 CRLF→LF 的 SHA256，锁原 Git blob SHA，报告明确 `sha256-utf8-crlf-to-lf-v1`；源代码摘要继续使用实际文件字节。独立重新实跑31项CLI及6项换行对照，错误算法声明被拒、非换行修改仍改变摘要。**该缺陷修复复核通过**；本地对照不冒充 Ubuntu GitHub 实际 run。

## 版本、限制及结论

首次实跑指纹（F06 修复前，不能关闭最终实现）：

| 文件 | SHA256 |
|---|---|
| scripts/run_agent_chain_eval.py | 0bdf44eb25e82ef04d38cc0ac98e58cf4a106161b7f873cd9b8fea5af418ede8 |
| scripts/agent_gate_check.py | b9f3c824fb2a291d680dd64d28a8ed59c585bbf6fc6cb992dd1ad5531061f54a |
| scripts/agent_gate_probe.py | 2f867d4845861bd2619358838c1f638f91afab359dd482c93ba390ba5377961d |
| .github/workflows/ci.yml | f5a0c1b22b2e50148b1bc3b529410089c276ebe0be07321fc3f31ca143535d93 |

最终完整重跑对应指纹：

| 文件 | SHA256 |
|---|---|
| scripts/run_agent_chain_eval.py | 0cce4c41abef7887067d4f30d9d2ac02ef95fbf9674c5c92b677c4196dab319d |
| scripts/agent_gate_check.py | e157a355c34e7854989bdb5f1384832f5ed0bc28f46de74d0baa992fdb0288f7 |
| scripts/agent_gate_probe.py | 2f867d4845861bd2619358838c1f638f91afab359dd482c93ba390ba5377961d |
| .github/workflows/ci.yml | f5a0c1b22b2e50148b1bc3b529410089c276ebe0be07321fc3f31ca143535d93 |
| tests/eval/test_agent_gate.py | 90dd37b81e0702d5a7b021637e46edf3ce4f803533d2c3b0d0547cacdca4cb95 |
| tests/eval/test_agent_eval_runner.py | 9ecc0d542691b2ee8920c0820d4b9d2764d2efc9146333f43a703b68b588aa5e |
| tests/eval/test_agent_gate_entry.py | de1b29cc229096028af6095fe7e58ac6ded835e719a3d1a486777e306fb29e80 |

HEAD：`a0d02eb411a53c3191e81f0fc99528ea60d0d3c4`，以上工作区字节摘要用于区分未提交实现。独立探针没有修改语料、业务代码、正式测试、Git 或仓库配置。

换行收尾补验：主 Agent将本卡脚本/测试 CRLF 规范为 LF 后，本角色再次完整重跑 37 项，全部符合断言，总 exit 0；以上表格是规范后的最终指纹。独立将当前 runner 的 LF 字节还原 CRLF 后，其 SHA 精确等于规范前 `90c2362d9c0ed52bdb7688d9fb29875a892284f88364cb96293b061d771c5050`，证明两版文本仅换行不同。标准 `git diff --check -- scripts/run_agent_chain_eval.py .github/workflows/ci.yml` exit 0；没有更改 Git whitespace 配置或忽略检查。

## 新鲜真实模型报告的独立补核

主 Agent按唯一已批准本轮12次调用完成 `evals/reports/agent-chain-v0.1-official-pagt007-20261013.json`，向本角色提交新鲜报告及真实调用结果；本角色没有重新调用模型。本地模型报告属于 E1；由该报告衍生受控退化副本的拦截属于 E2，不能将副本当模型质量通过证据。

独立只读加载报告，明确期待模型 `deepseek-flash`、独立计算官方端点身份摘要，通过真实 `gate.check_report` 重新核对 frozen cases/index、当前 Prompt、源码、Git、数据 hash 算法、原始回复与评分。结果通过：requested_model=deepseek-flash、provider=openai-compatible、mode=official、model_requests=12；route_accuracy/followup_recall/tool_selection_recall 均1.0。报告所绑定 runner/gate/probe/CI 字节摘要与上表最终版本精确相等。

另从真实 gate CLI 再读取该 official 及三份衍生副本：official exit 0 / AGENT_GATE_PASS；route、followup、tool 副本分别 exit 1，只返回各自目标 metric 原因。独立核 manifest 的 evidence 声明为 synthetic-controlled-faults-mechanism-only-not-model-quality、source_model 为 deepseek-flash，三类 exit/reason 与 CLI 实际复核相符。

实际子进程使用下列入口及独立期待值，可从项目根复现（不会调用模型）：

```powershell
& 'D:/DepTooL/anaconda3/envs/ai-assistant/python.exe' scripts/agent_gate_check.py --report evals/reports/agent-chain-v0.1-official-pagt007-20261013.json --expected-model deepseek-flash --expected-endpoint-hash 12b8deaccc34b32757dbb1497e029da0c2e7b26ffa86b9c926c08cb4692f4508
```

三类副本使用相同命令，将 --report 分别替换为 `evals/reports/agent-chain-v0.1-synthetic-probes-pagt007-20261013/synthetic-route.json`、`synthetic-followup.json`、`synthetic-tool.json` 对应完整路径；各返回1表示成功观测退化拦截。

本角色两次只读核对命令均使用指定 conda 解释器；身份及 manifest 检查 exit 0，四个 CLI 复核最终总 exit 0；新增模型调用 0。首次临时 CLI 断言使用整个 stdout.strip 精确比对，因既有 PyMuPDF 警告占了一行而退出1；调整为解析 AGENT_GATE_ 原因行后重跑上述四入口通过。这是审查断言修正，未改业务代码、未忽略 gate 错误或指标失败。

报告字段集合精确匹配 runner 生成协议；不含独立 credential 字段，常见 Bearer/sk-/私钥文本模式扫描未命中。审查未输出原始模型回复、工具 code 或任何凭证，未读 .env；没有取得项目密钥做逐字比较，因此该扫描不声称覆盖所有秘密格式。runner 自身仍在写报前拒绝回复包含所用 api_key。

| 新鲜证据 | SHA256 |
|---|---|
| evals/reports/agent-chain-v0.1-official-pagt007-20261013.json | f99b1e4dd7f52024765885534fd0254c211bb749e81a17bb1456b63adb5e895f |
| evals/reports/agent-chain-v0.1-synthetic-probes-pagt007-20261013/manifest.json | a09f6d76b7993e9b931b5a297e4ce1c8f3a92be90ed8ed0f45cd6cc3bcf55f35 |

最终结论：**本轮独立入口反例审查通过，没有未修复 P1/P2 业务缺陷；本地新鲜 E1 模型报告及 E2 退化拦截亦已独立核对通过。** GitHub CI、实际 required-check 仓库设置以及最终人工审核仍未验证，不能据本地结果关闭整卡，不建议 done。冻结十例只能拦截覆盖范围内的硬退化；两个工具正例仅 selection recall，不表示实际工具执行安全。报告身份为请求模型，不声称已核验服务端响应模型。固定 side_effects 字典明确为选择阶段设计声明，不用作动态零副作用证据。
