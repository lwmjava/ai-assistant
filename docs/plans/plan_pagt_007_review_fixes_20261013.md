# PAGT-007 实施与独立入口核对修复记录

日期：2026-10-13。仅修本卡门禁缺陷，不扩大任务范围，不降低模型护栏或阈值。

## 缺陷与修复计划

| 编号 | 发现者 / 入口 | 问题与影响 | 正式回归及修复 |
|---|---|---|---|
| F01 | 主 Agent / gate.check_report | 若复用 runner scorer，评分错误可由两边共同自证，不能独立复算 | gate 实现 Agent 加坏 scorer 自证反例，改 gate 独立计数；仍复用生产 raw parser |
| F02 | 主 Agent / official 与 gate | 仅比较当前数据指纹，不能拒绝原十例被换成别的十例；官方可能先外发改变的数据 | 锁原 cases/index SHA256；runner 在请求前拒改动，gate 拒指纹漂移 |
| F03 | 独立 entry_validation Agent / CI YAML | agent-baseline job 及 capability env 重复键，可能使 GitHub 拒绝整个工作流；普通 YAML loader 会覆盖键而漏检 | 独立验证 Agent 落严格重复键正式回归，先实跑红；主 Agent 删除重复条目并保留一份完整 job，恢复既有 LF；独立重验 |
| F04 | gate 实现 Agent / 阈值边界探针 | 正常 route=0.9 的报告已有一例错，盲翻前两例可能反向纠正一例，未保证制造退化 | 探针仅选当前正确的非 followup 例翻转；正式边界回归与独立 CLI 复验 |
| F05 | 主 Agent / write_report 与 probe 输出 | scope 仅校验 run 的目标路径，其他写报告入口可绕开报告目录约束 | 正式 test_report_out_of_scope_rejected_before_publication 先落盘并启动红验证；该直接调用进程未返回断言结果、被中断，不能当有效红证据。将 scope 提升至共用写入入口，probe 在创建目录前检查；正式 runner pytest 与独立CLI已证明修复后拒绝 |
| F06 | 主 Agent / Ubuntu CI 的冻结数据入口 | Windows 工作区 CRLF 原字节 SHA 与 Git blob LF 不同，现有冻结常量会使 Ubuntu official 一律失败 | 先正式 isolated official 测试使用 Git blob LF 字节实跑红；统一 cases/index 仅 CRLF→LF 后 SHA256，锁原Gitblob摘要、记录算法，不改语料；重验回归及独立CLI/换行对照 |

F01/F02 为实施初版核对中发现，修复与结果记录在 gate 实现验证和最终实现说明。F03 先落本计划、正式反例和红证据，修复后更新此记录。任何未修复 P1/P2 禁止关闭卡。
