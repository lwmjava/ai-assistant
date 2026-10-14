# Skill 账本深度 CI 修复计划

关联 tasks.yaml PAGT-005。现状：2000 层数组是否在 JSON 解码时抛 RecursionError 依赖运行环境；CI 解码成功后返回 skill_invalid_ledger，本地解码失败返回 skill_ledger_read_failed。

目标：超过 64 层的账本统一返回 skill_ledger_read_failed。方案：解码后用迭代遍历检查容器深度，避免检查本身递归；解码时的原有异常映射保持。合法账本最大结构深度远小于 64。

范围：app/agents/skills/versioning.py、tests/test_skill_versioning.py、docs/、tasks.yaml。非目标：修改 JSON 解码器、全局递归上限、发布账本内容、Prompt 或模型登记。

验收：保留原失败测试；增加解码成功情况下的过深拒绝用例、解码递归异常映射用例；Skill 回归、受影响 Ruff 和类型检查通过。无生产数据操作；回滚代码提交即可。真实 GitHub CI 结果需推送后另行核验。
