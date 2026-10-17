# ENV-002 实现说明

`.env.example` 的 INITIAL_ADMIN_PASSWORD 现为空字符串，保留用户名与181个配置键。复制模板不再自动创建带有公开固定密码的管理员；部署者须在本地或秘密管理中填写自主口令，空用户库且用户名、密码都非空时才引导账号。README 同步任一为空均跳过、空用户库条件，CLI 示例不再提供可执行密码字面量，先安全注入再执行创建命令。

app/core/config.py 与 app/services/auth_service.py 均未修改：源码密码默认仍为空，原有引导、角色、租户与成员关系行为保留。未读取真实.env、未创建真实账号、未改变依赖或CI。COV-001/ENV-001已有工作保留；ENV-001旧模板报告是历史指纹，当前181字段加载已另行复核，不把前置卡业务验收写成完成。

## 验证

基线 HEAD 80629c4cd5ce00d59b26116c7419edce00e6a113，分支codex/cov-001。Windows、指定conda Python3.12.0；依赖版本、完整命令、退出码、输出见 docs/reviews/env002_delivery_evidence_20261014.json。候选为HEAD加该报告和 env002_bootstrap_evidence_20261014.json 中列明SHA256的源码、模板、README及验证器。报告不含临时测试密码。

- 正式配置加载红例：修改前 `python docs/evaluations/env002_bootstrap_contract.py` exit1，失败为 template_contains_login_password。此前脚本误用返回键的KeyError已修正，该环境错误不充当业务红例。
- 最终同命令exit0：10项断言，实际Settings加载181键与空默认、空模板；原始 ensure_initial_admin 与 SQLModel/bcrypt 在独立SQLite执行五组组合（含当前模板）、新Session读取，重入幂等；已有普通成员时跳过新管理员且成员回填不重复。证据为E2隔离集成，不代表生产启动验证。
- `python docs/evaluations/env001_example_contract.py --self-test` exit0，14项：当前181/181、零额外/重复/未标注、错误语法/数值/布尔/密钥/真实.env路径拒绝。
- `python -m ruff check . --no-cache` exit0；`python -m mypy app/` exit0（205源文件）；Git whitespace diff check exit0。开发前同环境ruff/mypy也exit0。

本卡只改变示例文本和说明，未改后端公共入口、测试、运行配置源码、依赖、平台行为、前端或API契约。全量pytest、前端检查、Linux与真实服务启动不适用本卡影响范围，未声称执行或生产安全达标。未额外调用收费模型。

## 状态、审查与回滚

独立计划审查为 /root/env002_plan，独立入口验收为 /root/env002_verify，6项自建反例及181字段14项验证通过，另经只读meta反向复核，无未修复P1/P2。最终审查另由未参与以上工作的/root/env002_final实施，报告见docs/reviews/2026-10-15-ENV-002-最终人工审核第1轮.md。实际使用task-card-delivery与code-review-quality，工程计划采用等价维度审查。卡保持in_review，前置ENV-001及本卡用户业务验收未委托。未进行Git提交或推送。

回滚只逆向本卡模板/README/任务记录，保留ENV-001和COV-001的其他内容。恢复旧固定口令会重新暴露已修复风险；优先修正文档而非恢复可用密码。历史Git提交未清除，旧固定值不能当作有效部署密码；本卡不涉及历史清理、已部署账号改密或真实环境验证。

审查修复（2026-10-15）：独立入口审查发现 /setup 分支遗漏用户名非空/密码空情形，正式反例先失败后修复，10项最终验证包含README分支与密码字面量回归。修复记录见docs/reviews/env002_review_fixes_20261015.md，旧9项报告保留为历史，最终README指纹重新绑定。
