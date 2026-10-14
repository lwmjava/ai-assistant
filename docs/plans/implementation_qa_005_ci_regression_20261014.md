# CI 回归失败整改实现说明

关联 QA-005；计划 plan_qa_005_ci_regression_20261014.md。用户授权修复此次 CI 失败。此前 GOV-002 治理改动保留，独立于本卡。

## 实际行为

成功测试通过显式 persist_identity fixture 将 User 和 Tenant 写入隔离测试库，结束后只删除该 fixture 新建的身份行；已有租户配额等配置保持，已有失效身份不自动恢复。fixture 不是 autouse，不替换权限复核、不自动为任意请求赋权。适配对话、停止、限流、配额、工作流、共享工具、会话断开及 RAG 预算/检索披露成功用例，原成功、拒绝、来源、配额和落库断言保留。默认 RAG 工厂测试先断言 Settings 声明默认值为 native，再使用该声明值设置测试实例；既隔离本机配置，也保留默认值变更回归。

缓存取消验收继续使用真实本地 HTTP 服务。服务器可针对语义选择返回合法 null，测试分别核对一次有界非流式选择与两次流式回答；取消后的未消费回答不能作为缓存返回，完成回答与未变选择的再次调用不增加对应网络请求。不是把原总次数2简单改为3。

Linux process_alive 先探测 PID，再读取 /proc/PID/stat；用最后一个右括号之后的状态识别包含空格/括号的进程名。Z/X 表示已退出，不算仍在执行；读取前PID消失返回false，权限不足或无法读取时保守视为仍存活。Unix测试终止辅助函数在结束父进程后最多等待5秒确认子进程停止，超过期限明确失败。Windows Job Object执行路径保持。生产沙箱杀进程组机制未改变，不增加进程恢复或高权限能力。

新增 tests/test_sandbox_process_status.py 的进程状态边界回归；scripts/verify_sandbox_linux.py 用标准库加载实际沙箱模块、真实fork制造可复验僵尸并回收，实际进程组终止重复20次。不加载应用配置、不读取凭据、不调用模型。

## 版本与回滚

应用源只改 app/agents/tools/sandbox/proc.py。其余为上述测试fixture/断言、验收工具与文档。源码登记保守覆盖整个app，因此追加prompt-model-v6；v1-v5记录内容不变，默认模型指纹保持，语义选择协议与Prompt正文未改。基点提交0d900950176e8ae110dbf0b553ce7715e087d8a0，不冒充新增源码已经提交。回滚只恢复本卡沙箱/测试并恢复匹配源树的登记；不迁移数据库，不删除重要数据。

## 已执行验证

Windows解释器固定 D:\DepTooL\anaconda3\envs\ai-assistant\python.exe（Python3.12.0、pytest8.4.2），pytest独立测试库和basetemp，UTF8。调用前清空进程模型/Embedding密钥覆盖，不读取.env正文。完整命令与最终指纹见 docs/reviews/ci_regression_20261014.json（最终验证后写入）。

| 检查 | 实际结果 |
|---|---|
| 开工全量pytest | 36 failed / 2247 passed，退出1，517.36秒；data/ci-fix-baseline-20261014.xml与log。35项同用户CI，另1项本机RAG_BACKEND覆盖导致工厂默认测试失败；Windows树终止用例通过 |
| 新进程状态红例 | 4 failed / 5 passed，退出1；僵尸/已退出状态/PID消失/权限不足行为确实被原逻辑破坏 |
| 实际Linux红例 | Python3.12.7，Z状态仍kill-zero可见，但原process_alive返回true；报告passed=false，退出1 |
| 受影响回归 | 15文件265 passed，退出0，46.95秒；包括技能主体撤销、权限变化、默认Pipeline与新Session回读，data/ci-fix-target-20261014.xml |
| 实际Linux修复后 | 同一标准库验收器23检查全通过，退出0：真实僵尸、当前存活进程、回收后PID、20次真实进程树终止；沙箱归一化SHA256与当前源匹配 |
| 全仓Ruff/mypy | ruff check --no-cache .退出0；mypy app/205源退出0 |
| registry check | 当前prompt-model-v6，退出0；初次生成候选的时间写为+00:00被契约拒绝，改为UTC Z后成功，失败未写入登记 |
| 最终全量pytest | 最终final2：2292 passed / 0 failed / 0 skipped，退出0，496.90秒，3条既有warning；docs/reviews/ci_regression_final_20261014.xml |

Linux环境为本机docker-desktop WSL2，临时使用Astral python-build-standalone的CPython3.12.7 musl包，仅运行沙箱标准库验收，不用于替换项目pytest/mypy解释器；官方下载包SHA256核验为5ed4a4078db3cbac563af66403aaa156cd6e48831d90382a1820db2b120627b5。未安装/升级conda或项目依赖。初始Docker daemon不可用、直接WSL shell进程组探针未稳定产生僵尸，最终用真实fork保持父进程未回收子进程取得确定性红例；不将未复现的shell探针当成通过证据。

## 未做与关闭边界

未修改ChatService身份复核、安全开关、LLM协议、CI、分支保护或默认业务配置；没有真实模型调用、质量/费用声明、部署、提交或推送。前端无源码/契约变化，不执行前端检查。最终同提交Ubuntu Backend CI须在用户提交/推送后核验，独立审查已完成且P2闭合，无剩余有证据P1/P2；业务验收与远程CI待完成，QA-005保持in_review。此次本地修复不等于远程CI或任务已关闭。

## 独立审查补记

/root/ci_fix_review只读反例审查发现1项P2：原本显式硬编码native会漏掉源码声明默认值变化。已改为先断言声明默认值，再使用该默认值；独立进程变异default=langchain被拒绝、仅实例覆盖langchain仍通过，审查者确认闭合，无其他有证据P1/P2。报告docs/reviews/ci_regression_review_20261014.md。首次final运行最终2292passed/exit0/456.88秒，但其收集版本早于此测试修订，故不用于最终验收；最终完整重跑改用ci-fix-final2专属DB、basetemp、XML和log。

## 最终验证补记

最终全量final2已完成：2292passed/0failed/0skipped/exit0，496.90秒。3条告警为既有ast.NameConstant弃用和两条故意异常向量回归的numpy告警。最终Ruff/mypy/pipcheck/registry/diff均exit0；diff使用cr-at-eol识别已有CRLF登记文件，不降低空格/空行检查。机器证据ci_regression_20261014.json记录完整命令、基线36项清单、Linux红绿、独立审查、依赖版本与422输入指纹；运行后全部指纹未变。模型默认快照与登记v1-v5内容不变。前端不适用；本机Python3.12.0/pytest8.4.2/已安装extras与GitHub环境不同，完整依赖版本已记录，不将本机通过冒充同SHA远程CI通过。
