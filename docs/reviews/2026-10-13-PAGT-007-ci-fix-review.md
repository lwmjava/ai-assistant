# PAGT-007 真实 CI 失败修复的独立入口审查

日期：2026-10-13。初轮根因审查身份：`/root/pagt007_counterexample_review_v2`。修后独立复验身份：`/root/pagt007_entry_validation_v2`；原 counterexample 角色因模型 capacity 两次失败，由已有独立入口验证角色补做 F07 复验。本报告区分双方实际执行，不声称一个角色执行另一个角色的命令；双方均独立于实现者，不是最终人工业务验收。

本轮只写本报告，不修改实现、正式测试、旧审查报告或发布工作树；不读取 .env，不调用模型、不推送、不触发 CI。依据沿用已读取的 AGENTS.md、task-card-delivery 1.3.0、code-review-quality、原卡和批准方案，追加读取 CI、正式入口测试及修复计划。

## F07 根因的独立复现

主 Agent提供真实 GitHub run `38397622273`、发布提交 `7bf432820c47963bd657b39da8b3987087cc8d1c`、未改发布工作树 `data/pagt007-publish`，以及下载的真实模型产物 `data/pagt007-ci-evidence/official.json`。本角色没有重新取得 GitHub run 或发起模型请求，而是独立从这些实际入口和报告复现失败。

使用 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`，从发布工作树 CI 原文件直接提取 `endpoint_hash=$(python -c ...)` 内的实际 Python code，按独立已知官方端点设置 LLM_BASE_URL，清空模型凭证并设置 mock provider，子进程捕获 stdout。没有调用 chat。

捕获 code 自身 exit 0，但 stdout 为两行：导入期间的既有 PyMuPDF 弃用提示，以及末行正确的64字符端点摘要。CI 将两行全部当作 `--expected-endpoint-hash`，不满足 gate 对独立期待摘要的格式要求。

保持发布源码与报告不变，实际运行同一个 gate CLI：

| 独立输入 | 实际结果 |
|---|---|
| 工作流原 code 的完整 stdout 捕获值 | exit 2 / AGENT_GATE_ERROR:invalid_input_or_identity |
| 独立计算的干净官方端点摘要 | exit 0 / AGENT_GATE_PASS |

干净摘要：`12b8deaccc34b32757dbb1497e029da0c2e7b26ffa86b9c926c08cb4692f4508`。报告 SHA256 独立读取为 `2dcf41385f92ea03c48b403c63119268992db4e059ecb356ef6ec2408d1e7c47`，与主 Agent提交的真实CI产物摘要一致。根因隔离成立：报告及三项指标可以通过正确身份期待的 gate，失败由端点捕获 stdout 污染触发。严格 gate 拒绝错误身份输入的行为正确，不能放宽 gate 去接受污染摘要。

## 修复复核

主 Agent 已落 F07 缺陷计划及正式失败测试，并把工作流改为 heredoc：仅在导入 endpoint_hash 时用 contextlib.redirect_stdout(sys.stderr)，随后 print 真摘要。没有取最后一行、过滤摘要或放宽 gate。修前正式 1failed /exit1 由主 Agent 执行，独立根因复现见上节，修后由 `/root/pagt007_entry_validation_v2` 实际执行以下复验。

```powershell
$env:RAG_BACKEND='native'
$env:DATABASE_URL='sqlite:///./data/test_ai_assistant.db'
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest tests/eval/test_agent_ci_endpoint_capture.py tests/eval/test_agent_gate_entry.py::test_ci_secret_scope_and_fail_closed -q -p no:cacheprovider --basetemp=data/pagt007_f07_independent
```

实际 **2 passed /0 skipped，exit0，3.27s**。新增正式回归直接提取实际 YAML 捕获代码并在导入处注入受控 stdout 警告；stdout 必须精确等于干净摘要，受控警告必须进入 stderr。原工作流 secret scope 回归也通过。

随后独立从当前 YAML 的 `Agent report gate and regression probes` 步骤提取真实 heredoc 内容，使用指定 Python 子进程执行；清空凭证，固定非生产测试环境，不调用 chat。实际 exit0，stdout 精确为64字符hex加单个换行；stderr含真实fitz警告。此结果同时覆盖真实依赖警告而非仅合成注入。

用实际 `D:/DepTooL/Git/bin/bash.exe -n`，经 stdin 输入完整 YAML run 字符串，**exit0**，确认 heredoc 和 command substitution Bash 语法。只做语法验证，未执行 gate/probe/model 或发布命令。最初常用路径未找到 Git Bash；随后按 git.exe 实际安装路径找到上述二进制，未把未执行项标为通过。

最终字节 SHA256：

| 文件 | SHA256 |
|---|---|
| .github/workflows/ci.yml | 0a4c5918b959bb5d752a717df70e970822065943e11c52a143b6a23d15e2b7fd |
| tests/eval/test_agent_ci_endpoint_capture.py | e6553674fefc3e6b9ac49a915322a2470daa84a354ad3e3745cd4dc064613857 |
| tests/eval/test_agent_gate_entry.py | de1b29cc229096028af6095fe7e58ac6ded835e719a3d1a486777e306fb29e80 |

结论：F07 修后独立 E2 捕获接线验证通过，无本次复验发现的未修复 P1/P2；旧源码 CI hash 已失效。失败 run 仍不能作为整卡 CI 验收成功，修后真实 GitHub run 未执行。

## 明确未验证项

修后真实 GitHub CI 尚未重跑，本轮额外12次模型调用授权由主 Agent报告已用尽。两个独立角色均未新增模型调用或重新推送。最终人工审核和仓库 required-check 配置亦未验证，不能 done。真实报告校验为初次提交的 E1 产物复核；修后的 shell 捕获机制属于 E2 隔离验证，不能把旧报告更改源码摘要后冒充最终代码的新真实报告。
