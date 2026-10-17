# PAGT-005 审查修复计划

日期：2026-10-13。保持已批准范围、版本语义及非目标。

独立入口审查 /root/pagt005_counterexample_review 实测 P2：get_skill_manager 首次加载坏 YAML 时先赋值全局单例，失败后留下空对象；修复文件后再次调用不重试。正式反例 tests/test_skill_version_counterexamples.py，首轮 6 passed / 1 failed（2.47s，退出 1）。修复：局部 candidate 完整 load 成功后才公布单例；失败保持原单例状态。原回归及修复反例必须独立重跑。

主 Agent 补查发现同类数值/安全边界：activate 对极大 int 在区间判断前调用 math.isfinite 可能溢出；JSON 账本极深嵌套可能泄漏非固定 RecursionError；内容 canonical 的 -0.0 与 0.0 应统一以保持等价阈值身份。先补正式失败断言，再分别调整检查顺序、安全错误收口及零规范化；不修改匹配优先级或权限规则。实际结果记录实现说明与审查报告。
