# 覆盖率报告边界回归 v1

契约为tasks.yaml COV-001（一次可复现数字、命令、70%另记）；非质量调参、非Gold、非业务E2E。原始缺失工具入口exit4仅证明环境缺少pytest-cov，不把导入/环境错误写成业务红测。

真实报告通过指定conda解释器完整pytest --cov=app生成，证据E2（真实应用代码+隔离数据库/合成provider，无外部模型质量宣称）。covered_lines/num_statements为语句覆盖率；coverage7.10.6默认pragma:no cover、省略号函数体和TYPE_CHECKING排除规则及每文件excluded_lines会记录，不新增omit/exclude，不收集额外子进程覆盖或分支覆盖。

独立验证人/root/cov_verify_review从原始pytest --cov CLI与coverage API设计并实际运行合成反例，回归入口docs/evaluations/cov001-cli-boundary-cases-v1.py，详细命令、输出与报告见docs/reviews/cov001入口审查记录。合成反例仅证明测量/拒绝机制，不能代替真实app覆盖率数字。

| 边界 | 反例/期望 |
|---|---|
| 数值 | 无数据不能凭空给出数字；非法非有限/零分母/不一致统计不被独立验收当作有效报告 |
| 失败 | 原pytest --cov入口遇到真实断言失败仍exit1，--cov-fail-under=0不掩盖测试失败；不把中断/部分运行称完整 |
| 权限/隔离 | 合成CLI在独立data目录、根路径/配置和模型密钥覆盖中运行，数据库仅test_ai_assistant.db；不读.env、不接开发库或真实服务。无新增身份/租户路径，既有权限由全量用例验证 |
| 身份绑定/覆盖范围 | 比对全部app/**/*.py、有效coverage配置和最终输入SHA256；旧指纹和未导入文件遗漏必须被辨识 |
| 幂等/回滚 | 两轮独立数据文件不cov-append，旧证据字节不变；回滚仅新增dev依赖与匹配本卡文档，不删除业务数据 |

完整测量命令和同轮统计将在implementation_cov_001_20261014.md及机器证据落盘。独立入口审查/关闭对账须完成；QA-005前置及最终CI/人工验收缺口原样保留，工具全绿不直接使COV-001 done。
