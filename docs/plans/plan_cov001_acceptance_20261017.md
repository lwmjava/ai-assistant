# COV-001 当前版本验收计划

用户要求开始验收；技术基线d5311557c07273137104105f961cc20fe0e281b1，开工工作区干净。NFR-001及QA-005已done，QA技术输入提交ea7b288的Ubuntu Backend/Frontend/Agent official已通过；d531155仅为关闭文档追加，不改变技术输入。

沿用plan_cov_001_20261014.md测量方案：指定conda解释器，新测试库、basetemp、COVERAGE_FILE；全量pytest --cov=app --cov-report=term-missing并输出JSON/XML/JUnit，--cov-fail-under=0仅用于测量，不掩盖pytest断言失败。不修改源码、测试、依赖、配置、CI或默认exclude/omit，不提高虚构覆盖率；百分比与70%判定独立记录，非分支或前端覆盖率。

验收：命令实际结束且退出码可判定；coverage报告全部app Python文件，分子分母独立复算，与同轮JSON/XML/终端一致；记录配置、默认排除、平台、依赖与最终输入指纹。复跑已有CLI边界用例并由未参与本轮执行的Agent独立审查。只更新本卡记录和证据，不关闭其他任务。

旧计划所述Linux完整覆盖率是历史未执行项，不把它默认为已通过。本轮没有新增平台分支/进程/文件系统实现；其是否为本报告关闭必要条件，应由原卡验收契约、AGENTS第10节触发条件及独立审核共同核对，若属于必要门禁且不可执行则保持in_review。WSL枚举当前访问被拒绝，不声称可用Linux；Ubuntu既有应用CI不是Linux覆盖率测量。

只读验收不新增实施；结果写docs/reviews和对应关闭/验收说明，所有记录保留历史事实，不自动提交推送或部署。
