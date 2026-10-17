# ENV-002 独立入口与代码审查

审查身份 `/root/env002_verify`，未参与模板、README 或服务实现。执行日期2026-10-15，文件名保留任务开工日期。实际读取并使用 task-card-delivery 与 code-review-quality；依据原始卡、批准路径、计划、AGENTS 与适用规则。基线 HEAD 80629c4cd5ce00d59b26116c7419edce00e6a113，分支 codex/cov-001。未读取真实.env，不导入应用，不接触实际数据库或网络。

## 发现与修复复核

P2：README 第3步仍要求“两项都没填”才能选择 /setup，新模板用户名非空/密码为空时无法落入该分支。已交实现者，按 docs/reviews/env002_review_fixes_20261015.md 修复为任一项为空；独立读取最终README确认修复，root另有正式失败断言与复验。此项已解决，未发现剩余 P1/P2。不为满足技能发现数配额虚构缺陷；按 code-review-quality 要求另发只读 meta-review，结论追加于本文。

## 独立实际验收

指定解释器 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，Windows Python3.12.0；沿用已记录的 SQLModel0.0.22、SQLAlchemy2.0.36、bcrypt4.2.1、Pydantic2.10.4 环境。

| 实际命令 | 退出码 | 结果 |
|---|---|---|
| python docs/evaluations/env002_independent_probe.py | 0，两次 | 6项独立断言通过 |
| python docs/evaluations/env001_example_contract.py --self-test | 0 | 181字段14项真实加载与反例通过 |
| python -m ruff check docs/evaluations/env002_independent_probe.py --no-cache | 0 | 独立probe检查通过 |
| git diff -- app/core/config.py app/services/auth_service.py app/core/security.py app/models/user.py app/models/membership.py app/services/membership.py | 0 | 无业务源码差异 |

命令中的python均为上述完整路径。独立probe复用原始源码 AST 提取器，审查已核对提取节点名单与 _TENANT_ROLES 和真实源码一致；自行构造断言，未运行实现者的run()代替独立验收。使用真实 SQLModel/SQLite/bcrypt，跨 Session 提交后读取，不是伪 Session 或SQL自匹配。生成密码只在进程内存中，不写源码、文件或输出。

- 三种缺任一输入组合：新Session中 User/Tenant/Membership 都无记录。
- 已存在default租户：成功引导复用该租户；管理员role为system_admin，email持久化，Membership绑定同一用户/租户且role为member；实际bcrypt正确密码通过、另一个运行时随机密码失败，哈希与明文不同。
- 引导后改为另一个用户名和密码：既有管理员身份/哈希/租户不变，无重复成员关系。
- 实际开启捕获引导logger：输出不含生成口令或其哈希。
- 真实Settings加载新模板：用户名非空而密码空；进程动态注入密码仍可覆盖，未改变部署者自主配置路径。
- README无CLI密码字面量赋值，默认空密码路径说明已补齐。

以上为E2隔离服务入口/配置集成证据。AST提取方式不证明应用全启动、实际.env或生产登录；未将其写成这些结论。全量pytest、前端、Linux新CI不在本卡模板/文档差异范围，未声称执行，亦不覆盖COV-001整体门禁。

## 最终输入绑定与关闭边界

最终指纹：.env.example `bb70330e1a849e71ade0151196580cf4324e025e2432ab528458a42b9c13d498`；README `c4ac81384142d9913af244645e11be80cf61d949704f19acd4b9671cbe5385ba`；root probe `947dca258fbb313a7e32e153e42e04facf9d8a24eb942a7cd7713727471cdd6b`；独立probe `6bc3e999fb22328c037e29b4b64c10616c1e10f5fc822f2a9a7fb25d3bd69961`。配置源码 `74895a3dfa7ddb831fb5cd52968164da5ed87c50a4af07cff1117278353d5432`。其他原始模型/服务/安全源码完整SHA由独立probe输出，并与root报告输入匹配。

技术审查通过；本卡及ENV-001用户业务验收未委托，必须保持in_review，另需不同身份最终审核，不以本审查兼任。旧模板固定值仍可能存在历史提交/历史证据，本卡不清历史或替已部署账号改密。仅改变当前模板口令值与README说明，不修改ENV-001的181覆盖或COV-001依赖/证据，不执行Git操作。

## 反向 meta-review

`/root/env002_verify/meta` 按 code-review-quality 的低发现数反向复核要求独立只读审查。读取原始卡/计划/实现/关闭对账、模板、README 与原始 ensure_initial_admin；自行执行同一指定conda解释器的 `docs/evaluations/env002_independent_probe.py`，exit0、6项通过，README指纹与上述最终值一致。未读取真实.env、导入app、网络或生产DB，未写文件。结论：未发现新增实际P1/P2，默认空密码 /setup 路径修复成立；不增加虚构发现。本meta属于入口审查复核，不替代后续独立最终审核。
