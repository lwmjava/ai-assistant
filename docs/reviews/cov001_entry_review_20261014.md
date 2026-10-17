# COV-001 独立入口反例审查与自动验收

审查身份：`/root/cov_verify_review`，独立于主要实施者；本轮为自动验收与入口反例审查，不能兼任技能要求的最终人工审核 Agent。实际采用 task-card-delivery 的原始契约核对及 code-review-quality 的逻辑、安全、失败、可复现证据审查；不为发现数量捏造问题。

## 原始契约与范围

`tasks.yaml` COV-001 要求一次可复现覆盖率数字、命令与 70% 达标情况如实另记，不通过补无意义测试提高数字，也不在本卡修业务测试。用户批准的依赖只为 pytest-cov 7.1.0 / coverage 7.10.6；当前 pyproject diff 只有两个 dev 条目，无业务、测试、CI 或前端行为改动。

计划已先于实现落盘。前置 QA-005 的最终远程 CI/人工验收缺口不得被覆盖率数字消除；本轮即使自动验收通过，也不能据此标整卡 done。

## 实际入口反例

入口：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe docs/evaluations/cov001-cli-boundary-cases-v1.py`，退出码 0。脚本只写新的 `data/cov001-independent-*` 隔离目录，命令的 cwd、rootdir、confcutdir、pytest.ini、COVERAGE_FILE 显式隔离；模型密钥进程值置空，pytest fixture 实际校验空值及独立 test_ai_assistant.db 路径。不读取 .env、不访问共享测试库或开发库、不请求模型。

最终完整记录：`docs/reviews/cov001_boundary_review_20261014.json`，同字节副本原件在 `data/cov001-independent-wu5c7hqa/result.json`；脚本 SHA256 以 JSON 中 script_sha256 为准。解释器 Python 3.12.0 / pytest 8.4.2 / pytest-cov 7.1.0 / coverage 7.10.6。每次 subprocess 的参数列表、退出码、日志与临时模块指纹可复现。

| 边界 | 实际反例与结果 | 证据含义 |
|---|---|---|
| 数值 | 空分母、NaN、正负 Infinity、1e308 伪比例被独立断言拒绝；真实无数据 coverage json exit 1，未产生 JSON | 不将伪数字/无数据当覆盖率 |
| 失败路径 | 合成真实断言错误，pytest --cov-fail-under=0 仍 exit 1；插件未加载 exit 4；语法收集错误 exit 2，未产生完整 JSON | 数字/阈值零不能覆盖测试失败或收集失败 |
| 权限与租户 | 子进程测试实际校验模型密钥空值及独立测试库；仅 data 下合成模块，未使用应用权限入口 | 无新增应用权限路径，跨租户能力不变；原完整权限测试应随全量测量验证 |
| 身份与绑定 | 未导入 app/unimported.py 存在 coverage files 且 covered_lines=0；删该文件报告行被清单对比拒绝；源码变更使旧 SHA256 绑定失效 | 不能仅凭文件数/现存旧报告证明新输入有效 |
| 幂等与回滚 | 两次独立 pytest CLI，无 append，各独立 .coverage；第二次 totals 相同且第一轮全部证据字节 SHA256 不变 | 新轮不污染旧证据；无需业务数据回滚 |

这些是 E2 合成 CLI/coverage 机制证据，不替代真实 app 的覆盖率数字、不证明模型质量或生产性能。业务数值算法/新数据库事务/新跨租户入口不在原卡交付范围，不适用；全量现有业务断言不能因此被豁免。

脚本 Ruff 命令：`python -m ruff check --no-cache docs/evaluations/cov001-cli-boundary-cases-v1.py` exit 0。首次使用默认共享 .ruff_cache 遭写权限拒绝，改用无缓存检查；未把那次失败记作通过。反例脚本创作中先发现 Windows JSON 路径为反斜杠、收集失败无 JSON；修正独立断言后新目录重跑完整脚本，旧临时目录/结果保留。

## 实测报告独立验收

完整应用测量 `data/cov001-r1` 已结束，实际 pytest 退出码 0。终端与 JUnit 均为 2329 passed / 0 failures / 0 errors / 0 skipped，3 个既有 warning，716.04 秒。未挑选测试子集，未修改原测试断言。

独立命令：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe data/cov001-independent-verify-app.py`，退出码 0。验证脚本通过 `coverage.Coverage.load()` 与 `analysis2()` 从同轮 `.coverage` 逐文件复算，而非只接受主 Agent 的汇总值。机器结果 `docs/reviews/cov001_independent_report_review_20261014.json` 保留验证命令、脚本源码/指纹、原报告 SHA256 与全部 205 文件计数/排除行，可重建独立复核。

- 分子 13859 / 分母 15894，未覆盖 2035，语句覆盖率 **87.19642632439914%**；按该口径达到 70%。终端四舍五入 87% 不是计算依据。
- 205 个 `app/**/*.py` 与 coverage files 清单逐路径相等，无漏文件或额外路径；每文件总语句、覆盖行、未覆盖行、排除行与 Coverage API 相等，再汇总与 JSON/XML 一致。
- 默认排除 52 行（规则见 input-manifest），排除行在机器结果逐文件保留；无新增 omit/exclude，无分支/额外子进程覆盖声明。
- 423 项源码/测试/配置/评测输入按 CRLF→LF 规范化 SHA256 与当前最终输入完全一致，所有已记录依赖版本与当前环境一致。绑定基点 `80629c4cd5ce00d59b26116c7419edce00e6a113` 与当前 pyproject 两个批准 dev 条目，不把新报告写成旧提交已具备的结果。

这是 E2 真实应用代码 + 隔离数据库/合成 provider 的覆盖率测量。87.1964% 只说明语句被执行，不证明生产模型/RAG 质量、分支覆盖、Linux 环境或发布合格。前端源码/契约/CI 不改，页面和前端覆盖率不在原卡交付范围；不据此宣称前端达标。

## 审查结论

入口反例边界、可复现覆盖率数字与完整输入绑定自动验收通过；未发现 P1/P2 业务缺陷。当前最终 diff 仅批准 dev 依赖及文档/任务证据，没有业务/原测试/CI 执行行为改动，没有为数字新增业务测试。前置 QA-005 的最终远程 CI 与人工验收缺口原样保留，卡应保持 in_review。最终人工审核/业务判断由未参与实施及本轮审查的独立角色或用户按授权处理，不以本轮自动验收冒充。
