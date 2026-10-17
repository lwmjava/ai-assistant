# Skill 版本回归 v1

日期：2026-10-13。版本：skill-version-regression-v1；内容身份算法：skill-content-canonical-v1。Owner：项目维护者。数据为工程人员/AI 构造的 Smoke、Adversarial 与 Observed Regression，非人工 Gold。

## 输入、执行与可观察期望

| 类型 | 案例与真实入口 | 期望与证据类别 |
|---|---|---|
| Smoke | 两真实内置 YAML → HTTP Chat → 捕获 Provider 系统消息 | code_review/translator 原约束到达 Pipeline；实际内容版本出现在安全激活日志；E2 |
| Smoke | 复制真实 translator YAML/账本 → 修改内容 → 发布门禁 → 升号追加 → 新 Manager 匹配/注入 | 未同步拒绝；合法版本包含旧历史，执行新内容与新版本；E2 |
| Observed Regression | HTTP 创建/更新私有技能 → 提交 → 新 Session → 下一次真实 Chat | API 声明发布号仍 1.0；内容与 effective_version 改变，旧正文不进入新消息；写入审计绑定实际主体/资源；E2 |
| Adversarial | 同名跨租户/同租户不同 owner、global/private 覆盖与越权 PATCH | 仅注入授权最终内容，越权失败且数据库内容保持；E2 |
| Adversarial | 注册入参/get/list/match 的嵌套外部修改；register 后旧 match 激活 | 当前集合隔离；旧快照允许执行且版本与实际正文一致；E2 |
| Adversarial | 坏/部分坏 YAML、目录不可用、重复键、NaN/Infinity/超大值、无效正则、错误类型/编码 | 安全拒绝；失败后技能、history 和 reload 目录保留；合法删除与损坏区分；E2 文件/Manager，E3 纯验证补充 |
| Adversarial | 账本同版多内容、倒退/重复、末条不一致、历史重写、错误目录绑定 | 门禁拒绝；历史前缀与旧可信账本比较；E2 |
| Adversarial | 日志/历史敏感正文与路径、超过 100 名称、相同注册/回退/移除 | 不记录正文/路径；全局容量 100、返回副本、重试幂等、回退有记录；E2 |
| Adversarial | Skill tool 模式绑定 L2 工具 → collect_tools → 实际 ToolExecutor | 未经许可拒绝且函数调用数 0；E2 |

## 验证方式与限制

使用 D:/DepTooL/anaconda3/envs/ai-assistant/python.exe，RAG_BACKEND=native，DATABASE_URL=sqlite:///./data/test_ai_assistant.db。共享测试库串行运行，临时文件在 data/ 下独立 basetemp；真实 HTTP/SQLite/Manager/文件，Provider 是合成 Capturing Mock，只检查约束与版本传递。

pytest 用例和断言即版本化评估器：tests/test_skill_versioning.py、tests/test_skill_manager_versions.py、tests/test_skill_version_entry.py。标准：所有适用断言通过、未发生越权副作用、无秘密泄漏、零 skipped；正式发布门禁同时读取内置 YAML 与 releases.json。结果与最终文件指纹以本卡实现说明/关闭对账记录为准。

没有真实付费 LLM 质量评估；不报告译文准确率或代码审查召回。无前端交互变更，无页面验收；不证明数据库持久用户版本链、跨进程历史、生产并发或性能。账本受 Git/评审管理，不防恶意篡改；单独内容 hash 不能证明操作者和修改时间。
