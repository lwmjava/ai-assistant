# ENV-001 / ENV-002 漂移修复实现说明

2026-10-17，基线与候选指纹、解释器、依赖版本、完整命令、退出码及输出见 [当前证据](../reviews/env_drift_evidence_20261017.json)。计划见 [修复计划](plan_env_drift_fix_20261017.md)。

ENV-001：部署者可以按示例配置当前189项Settings；system/culture空口令各有条件必填标注。验收脚本按源码字段集合比对，不再硬编码181；缺失、额外、重复、未标注、坏语法、非空秘密与所有初始口令均拒绝。实际隔离Settings加载通过。README当前快照及tasks验收契约已同步，旧报告保留历史身份。

ENV-002：验收脚本从源码加载引导函数及其当前辅助函数、常量、异常，并补齐三账号配置。运行时随机生成口令，仅在内存SQLite保存bcrypt哈希；未读取真实.env、未操作项目或生产数据库。单管理员完整配置创建成功、任一输入为空跳过；三账号完整配置创建正确角色、首次改密/测试标记及成员关系；缺任一口令、错误管理员名或任一短口令均拒绝且无残留行。重复运行保留账号身份与哈希，既有用户阻止补种。独立probe再次核对默认租户复用、邮箱、真bcrypt、成员关系、幂等和日志无口令/哈希。README明确“任一项为空跳过”仅适用单管理员模式。

最终验证：env001 --self-test 15项、env002 bootstrap 12项、independent probe 6项均exit0；ruff check . --no-cache exit0；mypy app/ 211源文件exit0；tasks YAML解析exit0；git -c core.whitespace=cr-at-eol diff --check exit0。默认Ruff缓存路径被sandbox拒绝，采用--no-cache运行相同规则；README原CRLF保留，普通diff --check将行尾CR报为尾空白，显式识别CR后通过。没有放宽lint/type规则。

代码位置：docs/evaluations/env001_example_contract.py、env002_bootstrap_contract.py、env002_independent_probe.py；配置示例.env.example；说明README.md；任务tasks.yaml。没有修改app、原tests、依赖、CI或运行配置值。原失败三命令均先红后绿。本卡不涉及前端/API契约或应用公共路径，未重跑全量应用pytest/前端检查；Linux、最终提交CI、另一身份独立审核及人工业务验收未执行，不声称生产发布或整卡关闭，两卡仍in_review。机器证据不将独立probe等同另一身份的独立审核。

回滚：仅逆向本轮上述文件差异与新文档，不恢复历史可用口令、不修改真实.env或数据库、不删除历史证据。后续业务配置增加仍须同步示例；动态集合检查会拒绝缺失项。


## 受委托验收后的当前结论（2026-10-17续轮）

上述“独立审核与业务验收未执行、两卡in_review”是修复初轮状态。用户随后明确授权独立审核和主Agent代验；本轮未再修改技术输入。独立主审/root/env_independent_review和未参与其工作的env_meta反向复核均无P1/P2，33项原检查及另建16组合/停用租户拒绝通过。主Agent受委托业务验收独立执行16种配置组合、日志保密及网络拒绝检查通过，Ruff/mypy重新运行通过，当前技术输入SHA256一致。

按ENV-001→ENV-002依赖顺序将两卡更新done，旧计划/实现/报告保留历史日期。当前关闭对账见closure_env_001_002_20261017.md；独立审核与业务验收报告位于docs/reviews/2026-10-17-ENV独立审核.md和2026-10-17-ENV业务验收.md，精确命令/输出及技术输入在env_business_acceptance_evidence_20261017.json，最终任务/文档绑定在env_acceptance_final_manifest_20261017.json。范围、回滚及不适用门禁判断见关闭对账；未提交、推送或部署，不关闭QA-005等其他卡，不宣称整体可发布。
