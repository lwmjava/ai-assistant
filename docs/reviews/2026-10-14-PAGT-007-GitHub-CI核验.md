# GitHub CI 核验

用户授权只读核对当前真实模型 official、故障探针和 required check。当前提交 ddaa1cca93d17640ed7373b9cd2284505dfe4e8e，与 run 38507665586 attempt 1 一致。三项 job 全部 success；Agent official evaluation、report gate and regression probes、证据上传步骤均实际 success，未跳过。

已下载 artifact 11868083895，ZIP SHA256 ca0cda2cab37669d797f7a3c41a865e776f73af11f3579454ab6446622f76979 与 CI 上传日志一致；读取 official 和探针 manifest，核对 official 的 37 个源码指纹全部匹配当前提交 Git blob。工作树逐文件原始或 CRLF-to-LF 字节也匹配：初次原始比较有 12 个换行差异；plan_runtime.py 原始混合换行匹配 Git blob，无需规范化。比较记录保留，不是源码漂移。其余四份合成报告已在 ZIP 中确认存在，实际目标失败原因读取 manifest 并由 CI gate/probe 成功日志佐证。

- official：请求 deepseek-flash，12 次真实模型请求，mode official；route_accuracy、followup_recall、tool_selection_recall、tool_call_validity_recall 均 1.0。dataset 0.1.1、evaluator 0.3.0、report/gate 2.0.0。CI 日志 AGENT_GATE_PASS。
- 四类合成退化 route/followup/tool/arguments：各自 gate 退出码 1，原因包含对应目标指标，非身份/解析错误。CI 日志 AGENT_PROBE_PASS:route,followup,tool,arguments。合成报告仅证明拦截机制，不表示额外真实模型质量证据。
- required check：GitHub main 分支读取结果 protected=false，required_status_checks enforcement_level=off、contexts=[]、checks=[]；仓库 rulesets=[]。详细 protection endpoint 因 integration 权限不足返回 403，不能读取更细管理员配置；公开分支响应已明确当前未启用 required checks。检查通过不等于强制合并保护。

结论：真实 CI official 和四类探针缺口闭合；required-check 强制保护仍缺，PAGT-007 保持 in_review。本次没有修改远程配置、请求新模型、触发 CI、提交、推送、合并或部署。

机器证据：pagt007_ci_verification_20261014.json。运行 https://github.com/lwmjava/ai-assistant/actions/runs/38507665586 ，Agent job https://github.com/lwmjava/ai-assistant/actions/runs/38507665586/job/115668771140 。下一步为管理员在 main 设置 Agent official baseline 为 required status check；必要时同时要求 Backend 和 Frontend，并明确管理员/其他 bypass 策略，核验配置后才可声称强制保护。


## 用户配置后的再次核验

main-protection ruleset25052747已active，目标默认main，PR、三个required checks与strict策略生效，bypass为空且当前用户never。先前配置缺口闭合，见pagt007_required_checks_20261014.json。最终关闭以独立收尾验收结论为准。
