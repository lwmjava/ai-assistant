# 核心借鉴一期任务登记说明

日期：2026-10-21。性质：任务契约/文档治理交付，不是12包业务实现说明。

## 实际完成

- 用户授权先拆任务后，新增9张backlog卡：VDB-001/002/003、EVAL-001/002/003/004/005、TRACE-001。
- MEM-002/003保留原编号和backlog，补充会话快照所需Service/models/alembic等路径和验收；MEM-003列为核心P1治理前置，MEM-002新增依赖MEM-003。
- FEED-001原scope/acceptance/risk/depends_on保持，只补一期映射、计划和准入记录；反馈入集仍由EVAL-002负责。
- 每张新卡登记问题/事实、交付/非目标、允许/禁止路径、依赖、风险/回滚、Evaluation与准入条件、计划链接和估算。
- `plan_core_reuse_task_split_20261021.md`逐卡写明目标、现状、方案、非目标、验收，包含正式依赖图、共享归属、阶段出口和下一项VDB-001准备。
- 评估报告增加登记事实与正式计划链接；旧评估指纹保留为历史，不用旧全绿验证新队列。

任务量从192增至201：141 done / 59 backlog / 1 cancelled；原done和cancelled不变。核心12包成本仍44–79人日，非承诺排期；全部业务卡未开始，不标ready/done。

## 谁可以做什么及成功/失败行为

维护者可按正式ID与depends_on选择一张任务，先完成具体设计/ADR和基线，再进入实现。新范围之外改动、未获批准模型费用/生产/数据删除等不能由任务登记推定授权；必要条件失败时卡保持backlog，实施后的必要检查缺失或失败保持in_review/blocked，不降低门禁。此登记不改变任何终端用户功能。

## 修改与追溯

- `tasks.yaml`：九张新增卡、两张范围扩展和一张计划关联。
- `docs/plans/plan_core_reuse_task_split_20261021.md`：拆分及准入。
- `docs/plans/proposal_cross_project_reuse_20261021.md`：登记事实链接。
- `docs/reviews/core_reuse_task_registration_baseline_20261021.json`：登记前任务对象摘要/原契约/599个保护文件指纹。
- `docs/reviews/core_reuse_registration_spec_20261021.json`：新增卡规范、12包映射及精化依赖。
- `docs/reviews/validate_core_reuse_registration_20261021.py`及对应validation JSON：可复现的文档/任务验证。

## 验证命令与结果

使用 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe docs/reviews/validate_core_reuse_registration_20261021.py`，实际退出码和结果以 `docs/reviews/core_reuse_task_registration_validation_20261021.json` 为准；该文件只在断言全部通过后写入passed。验证YAML无重复键、九张新卡/12包映射、依赖闭环/无环、范围非目标/计划引用、原卡差额、状态/数量和受保护业务输入未变；结果不代表业务测试通过。

本轮未运行pytest、Ruff、mypy、前端四项、真实Milvus/Lite或模型评测：仅修改任务/文档，不触发业务回归执行条件。后续每卡按AGENTS.md第10节实际运行适用门禁，不能把此处不适用当成后续跳过依据。

## 明确没做与回滚

没有修改业务、测试、依赖、配置和CI执行行为；没有修改默认local、清库、安装依赖、调用模型、提交/推送/部署。没有登记其余24包或11个条件包，没有改变MEM-004暂缓。

仅撤销本轮任务登记/计划链接即可回退治理变化，使用登记前摘要和原契约核对；不得用整仓reset覆盖其他人的改动，也不删除数据库或评估报告。下一步为VDB-001具体实施准备，不在本说明中伪写完成。
