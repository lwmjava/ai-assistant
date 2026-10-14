# 任务关闭与提交前回归规则实现说明

关联 GOV-002；计划：plan_regression_gate_20261014.md。用户批准增加治理规则，本卡只改文档。

## 实际完成

AI Agent 和开发者修改后端公共路径时，必须执行全量 pytest；后端变更执行 Ruff/mypy，适用前端变化执行 typecheck、Vitest、ESLint 和 build。新增身份复核须检查旧成功 fixture 的有效 User/Tenant，新增模型调用须按用途检查缓存、预算、取消与重试断言。平台相关变化须有 Linux 测试和最终提交的 Ubuntu Backend CI。

开发前记录基线；检查报告绑定最终输入指纹或提交 SHA，相关输入再变更必须重跑。适用必要检查失败、缺失或过期时，业务卡不能 done，保持 in_review/blocked；用户明确要求保存 WIP 可以提交，但不能声称验收或发布通过。合并前要求核验最终 SHA 必需检查和远程保护；未核验不得声称自动强制生效。纯文档治理按文档契约验证，不能覆盖已有业务失败，也不追溯更改历史 done。

代码位置（规则文件）：AGENTS.md 第10节为完整规则源；.cursor/rules/00-global.mdc 与 07-testing-evaluation.mdc、docs/workflows/end-to-end-development.md、docs/checklists/review-and-release-checklist.md 引用/同步要求；tasks.yaml 增加 regression_gate_policy 和 GOV-002 单卡登记。

## 验证与边界

环境：D:\DepTooL\anaconda3\envs\ai-assistant\python.exe；基线 HEAD 为 0d900950176e8ae110dbf0b553ce7715e087d8a0，开工工作区干净。既有业务 CI 36 失败为前序调查输入，本卡不修复也不声称业务基线通过。

- 指定解释器执行 YAML解析、任务ID唯一、允许路径引用、强制条款和 frontend/package.json 命令名检查：退出0。
- 首次 git diff --check 因写入工具把原LF变为CRLF而退出2；恢复原文件LF后重新执行，退出0，Diff没有整文件换行噪声。
- 最终文档检查：YAML和任务状态、全部8个允许路径、规则同步引用、Git diff范围、强制条款、既有任务完全未变及空白检查均通过，退出0。

未运行后端全量/前端/Linux/真实模型检查：本卡仅改变文档规则，不改变检查执行行为，适用第10节文档例外；这些检查对先前业务改动的失败不因此消失。没有安装Git hook、新增检查脚本、修改CI、配置远程分支保护、修改业务/测试或旧任务状态；规则要求不等于工具自动阻断已经生效。没有提交、推送或部署。

回滚：恢复本卡8个治理文件的增补及任务登记；无运行时、数据库或权限变化。
