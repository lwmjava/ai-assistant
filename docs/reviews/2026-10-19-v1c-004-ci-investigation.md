# Agent official baseline 失败调查

调查对象：提交 `47dbf69a75ceb8ba0c664066e6afe4fb5bef470b`，GitHub run [39239816644](https://github.com/lwmjava/ai-assistant/actions/runs/39239816644)。Backend、Frontend 成功；Agent official baseline job [118017619340](https://github.com/lwmjava/ai-assistant/actions/runs/39239816644/job/118017619340) 失败。V1C-004 保持 in_review，不能关闭。

## 直接失败原因

已下载 artifact `agent-chain-39239816644-1`（id 12180333485），读取其 official.json。路由准确率和追问召回均为 1.0；工具选择召回和合法调用召回均为 0.5，未达到 1.0 门槛。

唯一失败工具样例是 agent-003：模型文本以 `<｜｜DSML｜｜ calls>` 开头，内部有名为 code_sandbox 的 invoke。当前运行时和评测共用 inspect_tool_call，只认可 `<tool_call>JSON</tool_call>`。结果为 intent=none、error_code=no_tool_call、recorded_calls=0。agent-004 返回标准信封且通过。fitz 弃用警告不是本次退出原因。

上一轮成功 run [39202782711](https://github.com/lwmjava/ai-assistant/actions/runs/39202782711)，提交 e005b4d08b5139080423f780db8c60f92d5d219e，artifact 12163874964：上述四项指标均为 1.0。两轮 prompt_hash 相同（6b6cde3a13a32c3674e908157eb7190376e4e9ad9217d4b16609c3dd0e935ce5），model 均为 deepseek-flash，endpoint_hash 均为 12b8deaccc34b32757dbb1497e029da0c2e7b26ffa86b9c926c08cb4692f4508。上一轮 agent-003 返回标准信封。本轮直接原因是相同提示输入下的输出协议偏离；不能据此认定供应商更换模型或 Supervisor 委派实现存在回归。

官方脚本直接构造路由和行动提示并校验工具提议，不执行 Supervisor；recording Tool 不执行真实沙箱。也不能将本报告当作 Supervisor 真实业务验收。

## 本地核查

平台 Windows；解释器 D:\DepTooL\anaconda3\envs\ai-assistant\python.exe；隔离 DATABASE_URL=sqlite:///./data/v1c004-regression-test.db。业务源码与测试未修改。

实际执行：`python -m pytest tests/eval/test_agent_eval_runner.py tests/eval/test_agent_recording_tools.py -q --basetemp=data/pytest-tmp/v1c004-ci-investigation`，67 passed，8.75 秒，退出码 0。只证明评测器离线契约有效，不证明真实模型已恢复。

GitHub artifact 的首次 PowerShell Invoke-WebRequest 下载因 TLS 身份验证失败，未取得文件；随后使用指定 Python 的 urllib.request 成功下载。该下载工具问题与 CI 失败无关。

## 处理边界

不降低指标、不修改冻结数据、不把 DSML 直接当成合法调用、不添加失败后反复采样。拟修复与待批准路径见 docs/plans/plan_v1c_004_ci_protocol_20261019.md。真实模型复验尚未执行；原始失败保留。历史本地全量与业务接受结论不等于当前必要 CI 已通过。
