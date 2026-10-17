# 任务关闭与提交前回归规则计划

关联：`tasks.yaml` 的 GOV-002。用户批准增加回归约束；本卡为文档治理，风险 L0。

## 目标与现状

现有 Ubuntu CI 已执行后端 Ruff、mypy、全量 pytest，前端类型检查、Vitest、ESLint 和构建；但任务级专项验证没有明确要求覆盖公共入口的全部旧调用方。技能语义匹配改动暴露身份 fixture 和模型调用计数兼容缺口。当前 CI 失败不得被本卡文档检查覆盖为通过。

## 方案与交付

以 AGENTS.md 第 10 节为唯一完整规则源，同步全局/测试 Cursor 规则、端到端工作流和发布检查清单；tasks.yaml 登记治理任务及规则入口。要求开发前基线、公共入口全量回归、适用前端检查、Linux 平台证据、最终源码与报告绑定、身份 fixture 和模型请求用途审查，以及未通过时禁止关闭业务任务。文档治理任务只做范围、语法、引用和一致性检查。

允许路径：AGENTS.md、.cursor/rules/00-global.mdc、.cursor/rules/07-testing-evaluation.mdc、docs/workflows/end-to-end-development.md、docs/checklists/review-and-release-checklist.md、tasks.yaml、本计划及 implementation_regression_gate_20261014.md。

## 非目标

不修改业务代码或测试，不修复当前 36 项 CI 失败，不改旧任务状态；不安装 Git hook 或新增自动检查脚本，不修改远程 GitHub 分支保护，不提交、推送或部署。GitHub 必需检查写为合并要求，不能声称本次已经配置。

## 验收与验证

- 公共入口全量回归、Linux 验证、最终版本绑定均使用强制表述。
- 必要检查失败/未运行/证据过期时禁止业务任务 done；明确只读文档治理例外和用户明确要求保存 WIP 的提交边界。
- 命令与现有 CI/frontend/package.json 一致；本地使用指定 conda 解释器、隔离测试 DB。
- tasks.yaml 可解析、任务 ID 唯一、规则引用可访问；diff 空白检查及范围检查通过。
- 实现说明记录实际结果、未做事项及回滚方式；不把规则落盘当作自动门禁生效。

回滚：仅恢复本卡治理文件改动，无数据或运行时副作用。
