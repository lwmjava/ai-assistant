# PROMPT-001 独立代码审查

日期：2026-10-11；审查身份：`/root/prompt_contract_review`，独立于业务实现者。依据原始任务卡与 task-card-delivery 的入口反例、失败边界、E2 证据要求；使用 code-review-quality 的逻辑、安全、测试和维护维度，遵单卡技能不凑发现数量。审查分段阅读完整 CLI、正式测试、登记与实现说明，未编辑业务代码或任务状态。

## 当前结论

第一轮发现 2 项 P2，均已修复并补正式回归，最终入口复核通过。没有未决 P1/P2；本结论限于代码与隔离集成验收，不替代原技能要求的人工业务判断。

## 已实跑入口反例

解释器：`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`。通过 Python subprocess 在独立进程直接执行 `app/agents/version_registry.py`，配置均为 data 下临时目录中合成 JSON；未读取真实 .env、调用模型、访问业务数据库或执行写入部署。

| 编号 | 操作 | 初轮结果 | 影响与修复建议 |
|---|---|---|---|
| R1 | `snapshot --config` 输入 `{"LLM_DEFAULT_MODLE":"changed"}`；其余参数给合法版本、UTC 时间、40 位来源和回滚说明 | 退出码 0，输出默认模型登记，错拼字段被静默忽略 | P2：操作者以为登记了部署模型，实际绑定默认值。应拒绝未知字段；若允许全配置脱敏，仅显式允许并丢弃已知秘密/无关字段，不能容忍错拼模型字段。 |
| R2 | `check --config` 分别输入 JSON `[]`、`1`、字符串 | 均退出码 1，但 stderr 含 Python Traceback 与 `AttributeError: ... has no attribute get` | P2：snapshot 校验对象类型而 check 没有，共享入口输入校验缺失。应在 model_snapshot 校验 Mapping/字典，并为三个 check 反例补正式回归；保持通用安全错误。 |
| N1 | snapshot 的 LLM_MAX_TOKENS 输入 -100 | 退出码 0 | 有限数字与类型校验不能证明运行配置合法；在本卡不改变 Settings 验证契约前提下，至少说明只登记配置声明、不宣称模型接受该值。是否作为必拒绝值以正式计划明确边界为准。 |

两段实际执行编排脚本均退出码 0；表内各 CLI 返回码来自子进程。初轮源码已阅读并定位 `model_snapshot` 的 `.get` 与未知字段遍历丢弃逻辑。后续代码指纹与修复后入口结果将在本报告末尾补记，不能使用本轮输出支撑修复后版本。

## 非缺陷但必须准确表述的限制

source_commit 最终在存在 .git 的树中进一步用 Git cat-file 校验 commit 对象存在；无 Git 的隔离副本只有声明引用，不代表工具已经确认全部工作树内容等于该 Git 对象。登记的内容摘要才是实际内容身份。默认配置登记不等于当前部署 provider，也不证明生产模型真实调用、费用或质量。endpoint_identities 不能恢复秘密端点，回滚仍需秘密管理材料和授权。

动态租户 Skill/Workflow 内容不在代码模板登记范围，本卡不能据此声称完成动态内容版本治理。全应用源码登记是保守覆盖策略，普通业务代码变更也会造成漂移；维护文档须写明登记粒度和更新步骤。

## 最终复核

已重新阅读完整最终 CLI（分段）、正式测试、登记维护文档及 Smoke/Adversarial Evaluation。最终源码 SHA-256：`bc70bfa04dca6e7453fe30940ecd021eede9edc293648f6e652d0ada06cd081c`；登记 SHA-256：`19fde5cdb111935818f2dfa4d1d9c7608117f4482b1b0bfd352395577a9c261c`。

独立构造的 15 个新进程 E2 入口断言全部符合预期：坏 .env 目录与不同 cwd 下基线通过；[]、整数、字符串 check 配置均安全拒绝；错拼配置、负 token 上限、NaN snapshot 均拒绝；修改 Memory 源后失败且恢复后通过；新增 evolution 源后失败且撤销后通过；模型导出成功、改模型后失败、恢复旧导出后通过；合成 key、带凭据端点及 capability 来源不出现在输出。上述外层编排退出码 0；每个拒绝子进程退出码 1，所有 stderr 均无 Traceback。没有业务数据库、外网模型或真实配置副作用。

真实 Git 来源独立复核：对登记中的 **190** 个源，逐个执行 `git show 8d96ab3e8a3d6ef3b8011804ae00ba3828eaebc4:<path>`，LF 规范化后比对登记 SHA-256；缺失 **0**、不匹配 **0**，编排退出码 0。这是本次基线实际来源证据，不从 CLI 元数据格式正确推导来源可信。

初次正式回归 58 passed / 16.48s / 退出码 0。后续 F06 路径前置守卫与缺默认值守卫修改后，重新读取最终 defaults，并重跑 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest docs/ai-prompts/tests/ -q --basetemp=data/prompt001-review-final-20261011`，**59 passed / 19.51s / 退出码 0**。又重新在真实仓库直接执行 R1 错拼和 R2 的 []/整数/字符串 check，四项均退出码 1 且无 Traceback；真实仓库 `check` 退出码 0，输出 prompt-model-v1、source_version `32d7a3a3fa2ff976d6c23b5d23c46d4472c78d97c7ad1525ccf1474b38068192`、model_version `eef69460f8a0996a9793ec2349e3f8ba66a4919d35b31161f45fe876735efaf8`，依据 source-defaults。

F06 路径逃逸的真实 E2 反例：普通文件 symlink 创建受 Windows 权限限制（WinError 1314），改用临时 Windows directory junction 将隔离 `repo/app/core` 绑定到同一临时目录下的 `outside`，创建成功。直接 snapshot 在 AST 读取前的源码路径守卫拒绝，退出码 1、无 Traceback、外部合成配置原文未改。源码已确认 resolve 后的项目根校验位于 read_text 前；正式回归也验证越界时不调用读取函数。临时 junction 和副本均已清理；没有更改真实项目源或 .env。该证据不把 symlink 不可创建伪装为 symlink 成功。

R1 已由显式允许字段与秘密字段集合解决，R2 已由共享 Mapping 校验解决；N1 的负/零数值边界也已收紧，新增 UTC 格式、能力来源摘要与 Git 对象存在性测试。正式测试中有旧模型快照导出与新进程恢复，不仅是 helper 相等断言。原始 Prompt 来源/版本/时间/回滚登记、模型配置追踪/恢复材料与登记维护文档均有当前 E2 证据；无未决 P1/P2。全仓 Ruff、mypy 与既有生成回归以独立验证 Agent 最终实跑报告为准，本审查不代报未执行命令。

人工业务验收按原技能要求单独保留，不由本报告替代。无收费真实模型质量、生产部署或秘密管理回滚证据；不将其写为已验收。
