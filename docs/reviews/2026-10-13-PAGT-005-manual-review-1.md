# PAGT-005 最终独立人工审核 第 1 轮

日期：2026-10-13。实际独立 Agent 身份：`/root/pagt005_manual_review_1`，由 root 在自动验收与关闭对账后新建；未参与本卡实现、工程计划审查、入口验证或既有反例审查。本轮只写本报告，未修改业务源码、测试或任务状态。

**结论：批准设计范围内的工程验收通过，未发现未决 P1/P2。用户业务验收仍未验证，PAGT-005 应保持 in_review，不自动 done。** 此结论来自当前文件读取、实际串行重跑和指纹核对，不以实现者摘要、多 Agent 一致或历史 done 字段替代验收。

## 依据、批准与审查范围

实际读取 AGENTS.md、AI 辅助开发迭代指导、Harness 规范、适用 Cursor 规则（00/01/02/04/07/08）、task-card-delivery 1.3.0、原始 tasks.yaml PAGT-005、批准计划、Skill 规格、Evaluation v1、实现说明、关闭对账、依赖报告及独立入口反例/修复回路。直接读取最终 base/loader/manager/versioning 完整源码、内置账本和正式版本/Manager/HTTP 入口用例，以及 SkillService Manifest/可见集和 ChatService `_match_skills` 实际调用链。

用户已批准 docs/ 范围及具体语义：“内置发布 version 手动升号并追加持久账本；实际内容 effective_version 自动随内容变化；DB/API 声明 version 不自动递增”，同时批准隔离快照、原子重载与有界历史。此次审核按该批准对照原卡，不把未自动递增 API 声明号误写成已经实现，也不将内存历史称为数据库持久版本链。

Git HEAD 为 `a0d02eb411a53c3191e81f0fc99528ea60d0d3c4`。工作区还有既有 PAGT-004/006 改动；未提交候选以工作区文件字节 SHA256 绑定，不能只用 HEAD。范围内版本代码不直连数据库，不改变 Service 权限过滤，不新增市场/在线编辑 UI、API 契约、配置或生产副作用。

## 机器证据独立核对

使用指定解释器执行只读 Python：逐个 `hashlib.sha256(Path(path).read_bytes())` 比较机器证据；从前置报告提取 11 个路径/哈希重新计算；实际 XML 解析逐字段核对 JUnit 属性及 testcase 数；对 YAML 本卡执行 `json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(',', ':'))` 后计算 SHA256。退出码 0。

结果：`pagt005_delivery_evidence_20261013.json` 的 **20/20 文件字节全部匹配**；前置报告 **11/11 文件字节全部匹配**；两 JUnit 的 SHA256、全部 summary 字段、testcase 数 **103/142 均匹配**，failures/errors/skipped 均 0；本卡规范化 SHA256 为 `c992e027e058af45fb593a41d2cb4155c938fd0da3047f6028915b989f09cf4e`，与登记一致。两内置 YAML、SkillService/ChatService 开工至交付未改的事实也由机器证据及开工指纹交叉核对。

关键当前指纹：

| 文件 | SHA256 |
|---|---|
| app/agents/skills/base.py | 762ad5c9e0560e639f2e2cc1ab29dd0f0f384806817ec61264ef6bbcb2dcf5ce |
| app/agents/skills/loader.py | c347a53a270bd1ecd0cce7c5f06525b1007d64d2fc64c491c0128ce95a72d0aa |
| app/agents/skills/manager.py | 29c8ee266842dafcbbad12813525b0b4aa10232a453f9a37554115a84272a809 |
| app/agents/skills/versioning.py | e87082e90da844e5ee26f61624783f469240df7a84c49f0ebf28280a40b998b0 |
| app/agents/skills/builtin/releases.json | 5095600808f22c11ddafb3c1fd3cf5a6adfa1b39ee5bd1b9a97ff19d246a9a15 |

两 JUnit 字节哈希：Skill `52c1ee79ef3a68f99fdfa55433f92cc94a141c763f7f85902e7be152e8579bcc`；调用方 `41584769ba887f1b664421cc1aee9256bb4ff0ee3cb3d21b8b519b51940cf619`。本轮未重写这两文件，它们保留 root 原运行证据。

## 本 Agent 实际运行

pytest 独占时隙由 root 授予，两个批次串行。解释器 `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`；环境 `RAG_BACKEND=native`、`DATABASE_URL=sqlite:///./data/test_ai_assistant.db`。没有默认 base Python、真实外网、收费模型或生产数据库操作。

1. `python -m pytest -k skill -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt005-manual-1`：退出 **0，103 passed / 1967 deselected / 0 skipped，38.10s**。包含真实 HTTP 创建/更新、提交后新 Session、下一次 Chat Provider 消息/激活日志版本变化，租户/owner/global 同名可见性、L2 Executor 拒绝、两内置约束与未命中、文件发布/升号演练和失败最终状态。
2. `python -m pytest tests/test_pagt001_tools.py tests/test_pagt001_review.py tests/test_pagt001_integration.py tests/test_tools.py -q -p no:cacheprovider --basetemp=data/pytest-tmp-pagt005-manual-dependency-1`：退出 **0，119 passed / 0 skipped，4.05s**。结合 11 当前指纹，前置关键路径仍有效，未发现未决 P1/P2；不是凭前置 done 准入。
3. 单独只读/内存抽查脚本，实际 `validate_release_ledger` 校验正式内置目录，Manager 从两真实 YAML 加载；激活 translator，隔离副本改正文后注册、再激活：声明号仍 1.0，新旧 effective_version 不同且各自绑定真实正文；history 不含合成正文标记；同内容重注册不新增 history；NaN 更新拒绝且最终历史和执行身份不变；未命中不注入。退出 **0**，输出 `manual_actual_builtin_gate_and_snapshot_activation_passed`。该内存补查只作 E3 补充，必交付依靠上述实际文件/HTTP E2 用例。

两 pytest 均有既有 ast.NameConstant DeprecationWarning；独立脚本导入有既有 fitz API 弃用提示。未把警告宣称不存在。未重跑全仓 pytest、ruff/mypy 或调用方 142 条；后者的实际 JUnit/源码指纹已经核对，不冒充本 Agent 亲自执行。前置扩大 health 的原 125 passed/1 failed 及历史 NULL 索引分块隔离记录保留，不将其改写为通过。

## 原契约与关闭对账

| 原卡项目 | 当前可复现结果与证据 | 工程结论 |
|---|---|---|
| 技能带版本号、变更留痕；内容修改后版本变化可追溯 | HTTP 持久更新 → 新 Session → 下一次真实 Chat → 合成 Provider 收到新正文且日志为对应新 effective_version；既有更新审计绑定主体/资源。版本账本与 Manager 有界元数据记录区分清楚。E2，纯 hash 单测只补充 | 通过 |
| 内置技能有可执行回归 | 两实际内置 YAML 经真实 HTTP Chat 传递旧约束及正确版本，未命中不注入；正式门禁读取目录/账本。E2，不证明真实回答质量 | 通过 |
| 修改一个内置技能并跑回归 | 正式用例复制真实 translator/账本，改内容未升号被拒；升至1.1追加历史后新 Manager 实际加载/注入，保留旧前缀。E2，不修改正式 Prompt 来制造演示 | 通过 |
| Non-goals 与依赖 | 没有市场/编辑界面、迁移/新增 API；当前前置119条重跑通过、11指纹匹配 | 通过 |

失败边界按实际回归检查：非有限/极大/负零/错误类型和编码；部分坏文件/目录消失与失败单例重入；跨租户/owner同名与越权修改最终数据；路径/发布标签与实际内容身份、隔离副本/旧 match；回退/移除/重复注册与全局100条容量、日志不泄露正文/来源路径；工具身份不授予 L2 执行权限。原独立 P2 “首次失败污染全局单例”已经由本轮正式反例再次通过，无需解除或降低门禁。

关闭对账的必交付项工程判断与当前证据一致，没有将 E3/E4 推导作为唯一闭合依据。证据 E2 使用真实代码、临时文件及测试 SQLite，Provider 为合成 Capturing Mock；它证明约束、内容和版本传递，不能证明真实翻译/代码审查质量。

## 未验证与状态

用户未明确委托 Agent 代行最终业务判定，本次“人工审核 Agent”完成独立工程审核，不等于用户业务验收。真实 LLM 质量、生产并发/性能、跨进程历史、用户持久全文版本链仍未验证；DB/API 自动递增不属于批准设计。前端页面不适用。103 专项的 1967 deselected 不代表全项目通过。

建议保持 `in_review`，将本结论交给用户验收；不自动开始其它卡。没有修改业务数据、生产迁移、Git 提交/推送/合并/部署。回滚按实现说明恢复本卡运行代码并保持内置 YAML/账本对应，不覆盖既有 PAGT-004/006 工作区变更。业务源码或指纹发生后续变化时，本报告不能直接作为新候选验收证据，应重验受影响路径。
