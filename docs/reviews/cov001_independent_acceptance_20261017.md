# COV-001 当前版本独立验收

2026-10-17，独立Agent /root/acceptance_closure 只读核验，未执行本轮覆盖率测量、未改文件；用户已授权对本卡代行验收。结论：未发现阻断，建议done。

原始data/cov001-acceptance-20261017/.coverage通过Coverage.analysis2逐文件复算：211/211个app Python文件全部纳入，语句14951覆盖/17075总计，2124缺失，52默认排除行，87.56076134699853%。JSON语句覆盖需将executed_lines与statements相交，执行行可能含非语句行，不能用执行行列表全等误报；修正审核脚本这一口径后，所有文件的语句/缺失/排除统计一致。XML211文件的语句与命中逐文件一致，终端取整88%与精确比例一致。

JUnit2533=2530通过+3跳过，0失败/错误，与终端汇总一致；607个输入raw SHA256全部匹配，HEAD d5311557c07273137104105f961cc20fe0e281b1。12项CLI边界结果已读、脚本hash匹配；无新增omit/exclude_also/分支配置，不使用合成数据代替app报告。

QA-005/NFR-001前置已done；同SHA Ubuntu Backend/Frontend/Agent official实际全通过，见 https://github.com/lwmjava/ai-assistant/actions/runs/38996604616 。本轮仅运行已有测量命令和新增报告，不修改代码、测试、依赖、CI、进程或平台行为，AGENTS第10节Linux实现变更触发条件不成立。原卡没有要求额外Linux覆盖率测量；旧关闭对账中“未执行”的事实保留，但不是新增必过验收标准。

仅验收Windows后端语句覆盖率87.56%、达到70%；不宣称分支、前端、Linux覆盖率或生产质量。关闭元数据不改变技术输入，可沿用同技术版本CI。详细命令、报告hash及配置见cov001_acceptance_evidence_20261017.json、cov001_acceptance_inputs_20261017.json。
