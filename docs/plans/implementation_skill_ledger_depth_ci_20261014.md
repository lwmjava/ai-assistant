# Skill 账本深度 CI 修复说明

关联 tasks.yaml PAGT-005，计划见 plan_skill_ledger_depth_ci_20261014.md。

validate_release_ledger 在 JSON 解码后迭代检查 dict/list 容器深度，超过 64 层返回固定 skill_ledger_read_failed；JSON 解码本身发生 RecursionError 时仍映射到同一错误。正常账本及既有格式、大小、重复键、历史完整性校验保持。代码位于 app/agents/skills/versioning.py；未改 Python 全局递归上限、Prompt 或模型，无生产数据操作。

新增 Observed Regression 用例：模拟解码器成功返回 2000 层容器，修前 1 failed / 2 passed，错误与用户提供的 Linux Python 3.12 CI 日志一致；修后纳入全部 Skill 回归。另验证解码器递归异常的敏感详情不会进入外部错误。原极深输入测试及错误码断言保留。

实际环境 D:\DepTooL\anaconda3\envs\ai-assistant\python.exe，PYTHONUTF8=1、PYTHONIOENCODING=utf-8。

- python -m pytest -k skill -q --basetemp=data/pytest-tmp/skill-depth-green-20261014 --junitxml=docs/reviews/skill_depth_ci_20261014.xml：107 passed / 2123 deselected，退出 0，33.48 秒，1 条既有 ast.NameConstant 警告。
- python -m ruff check --no-cache app/agents/skills/versioning.py tests/test_skill_versioning.py：退出 0。
- python -m mypy app/agents/skills/versioning.py：退出 0，1 source file。

首次默认 pytest basetemp 因旧 run 目录权限失败，3 errors，未执行业务断言；改用新的仓库内临时目录。首次 Ruff 缓存写入权限失败，改用 --no-cache；随后发现 UP038，修成 dict | list 后通过。没有更换环境或修改检查配置。

未重新运行全量测试、真实 LLM 或 Linux CI；此次业务修改局限账本深度校验，GitHub CI 结论需推送后核验。回滚本修复提交即可；无持久化数据变更。
