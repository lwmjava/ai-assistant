# PAGT-004 独立审查驱动修复

日期：2026-10-13。依据：/root/pagt004_plan_review 的真实 Pipeline 内存反例；原始报告 docs/reviews/2026-10-13-PAGT-004-counterexample-review.md。不是关闭证据，以下缺陷必须修复并独立复验后才能交付。

## P1-1 被拒绝的敏感计划仍写入失败 checkpoint

新计划的敏感工具参数在准备阶段被拒绝，但 state.plan 仍保留原始 JSON，异常出口随后保存完整字符串。反例中 synthetic password marker 实际出现在 state_json。

修复：对完整计划实施可持久化数据检查，准备/恢复验证失败时清除原始计划及相关运行计划字段，再保存安全失败状态。保留安全错误码与步骤标识；不向日志、Trace 或 checkpoint 输出原始敏感计划。新增真实 SQLite 失败落库后新 Session 读取断言，工具零调用。

## P1-2 恢复绕过执行参数/敏感参数验证

StateManager 的结构校验能验证 Plan JSON 形状，但没有注册工具 schema。恢复后跳过准备，导致类型错误或敏感参数仍进入工具。反例实际工具调用1次，错误为空。

修复：提取共享执行前完整验证（注册工具、参数 schema、可持久化数据），新计划与恢复进入步骤迭代前都调用，且不得在验证失败时降级成新执行。先验证整计划再任何副作用。新增真实 checkpoint + 新 Session 进入 Pipeline 的类型/敏感参数反例，断言工具零调用、明确失败和安全终态。

## 验证与范围

仅修改本卡已批准 app/agents/、tests/、docs/。不改变权限、服务/API、数据模型或业务数据库。先红失败须对应泄露和工具被执行事实，不能用导入失败替代。重跑新 Plan 专项、受影响恢复/日志/入口验证与领域回归；独立审查者再次运行同反例，旧全绿不能作为修复证据。若额外发现问题，在本页继续追加而不静默转下一卡。

## CE05 成功前缀与工具预算、指纹矛盾

独立反例：v1 快照工具步骤 succeeded、cursor=1，却 tool_rounds_used=0 且没有已执行指纹，仍被 StateManager 接受。恢复会跳过未经证实的成功步骤，还可能刷新剩余工具预算。

修复：仅对具有 structured_plan 的快照增加执行一致性校验。每个成功工具步骤必须具备对应规范指纹；成功工具指纹不能重复，累计轮次不得低于成功工具数。v1 必须携带明确累计轮次；数据库元数据 _round_no 存在时必须与快照累计值一致，且满足成功前缀下限。旧文本快照保留原有兼容逻辑。字段与状态验证均在原子恢复之前执行，拒绝时不部分修改目标状态。

Pipeline 进入结构步骤执行前使用同一完整 StateManager 验证，防止内存构造绕过恢复校验。正式反例包含缺失指纹、错误指纹、低轮次、DB/JSON 冲突、重复成功工具，以及合法成功前缀、reason 前缀和旧文本快照。先运行业务断言红测试，再最小修复并串行验证；新测试不访问项目共享测试库以外的业务数据。

CE05 单元验证证据（不是整卡验收）：声明解释器执行 `-m pytest -q tests/test_plan_checkpoint_consistency.py`，修复前 9 failed / 3 passed、退出1、2.12秒；修复后12 passed、退出0、1.63秒。`ruff check --no-cache app/agents/state.py tests/test_plan_checkpoint_consistency.py` 与 `mypy app/agents/state.py` 均退出0；对应检查没有放宽门禁。数据库行的 `_round_no` 在服务原有注入后校验，因此不改 Service；入口级持久化复验由独立验证角色执行。

## CE-03/04 P2 模型超时与非法草稿

Preflight 和 QualityGate 在默认结构化与 v1 恢复模式增加与阶段一致的 runtime timeout；显式旧链保持兼容。草稿先校验字符串、非空与长度，再解析工具调用；失败返回 draft 安全定位并写入失败快照。先新增同步/流式睡眠与 None 草稿反例，红测试后实施。

## CE06 JSON 业务失败被误判为成功

真实 file_ops_tool 读取不存在的隔离公开共享文件返回 JSON status=error/code=access_denied，Executor 仍返回 ok，导致对应 Plan 步骤 succeeded。排查确认 web_search 与 file_ops_write 使用相同的普通 JSON 字符串错误返回，具有同类缺口。

修复：在各工具原有 JSON 结果产生处附加 ToolOutput 的结构化 status/error_code；不解析任意文本、不修改 JSON 字节内容，不绕过注册、权限或写闸。file_ops 的 error/unavailable、file_ops_write 的 error/unavailable/conflict 以及 web_search 的 error/unavailable 均不能判成功；web_search 的 empty/no_sources 为明确无来源失败。timeout 分类为 timeout，其余业务失败为 error，错误码沿用原有 code。成功文本和原有观测格式保持一致。

正式反例使用真实临时目录、真实 file_ops_tool/写工具和实际 create-only 冲突；搜索配置不可用/无效参数由真实函数拒绝且不外发，不使用 helper 替代实际工具。先红业务断言，再最小适配，重跑工具专项及真实 Pipeline 入口审查。写权限与身份门禁保持原样；测试中的写文件限定 pytest 临时公开根。

## CE-07 P1 恢复文本视图与结构计划分离

独立repair_recheck实际复现合法structured_plan搭配含synthetic password的state.plan，恢复成功执行且3个新checkpoint含该值。修复：恢复v1时先完整验证注册工具/schema/敏感结构，再从唯一结构计划生成文本视图，在RAG或草稿前替换不一致原文本；执行绑定错误不得吞掉变全新执行。旧无结构checkpoint保持文本契约。正式反例：真实SQLite写活动checkpoint，新Session恢复，检查模型请求与终态新Session读回均不含marker且合法结构正常执行。先红后改，再独立同反例复验。
