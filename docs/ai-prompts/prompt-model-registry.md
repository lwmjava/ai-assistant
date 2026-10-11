# Prompt / 模型版本登记

版本格式 v1；维护入口 `app/agents/version_registry.py`。当前 `prompt-model-v1` 是源码默认值登记基线，登记生效时间为该基线登记时间，**不是部署启用或模型质量认证**。`source_commit` 是变更基点 Git 引用，`source_version` 是实际工作树 LF 规范化文件摘要的版本；未提交文件不能宣称已存在于基点提交。在 Git 工作树内工具核对基点确为存在的 commit；隔离无 Git 副本仅保留该引用，来源需在原库独立核对。

## 登记范围

保守登记全部 `app/**/*.py`（排除登记工具自身）和内置技能 YAML，因此包括 Agent、Supervisor、快速路径、Memory、Reflect/Distill、RAG 切分与摘要中的提示词及构造逻辑。任何新增、删除、修改源码都会要求新登记，即使非 Prompt 变化也可能触发。覆盖现有静态模板和动态构造代码；数据库用户技能、工作流模板、对话/RAG 内容是运行时输入，不是本文件登记的静态源码版本；动态技能版本治理由独立任务处理。

工具仅使用标准库，不导入应用包、不执行 Settings，不读取 `.env`、不发网络请求、不改源和配置。请直接执行文件；不要用 `python -m app.agents.version_registry`，后者的包初始化会加载应用设置。登记工具不接入对话/审计 Trace、不提供运行时切换平台。

## 日常核对与登记

在声明的 conda 环境执行（可从任意工作目录执行绝对脚本路径）：

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' app/agents/version_registry.py check
```

通过输出版本、源码指纹、模型指纹和配置依据；缺失、坏 JSON、重复键/版本、未知配置键、类型错误、非有限/非法数值、源增删改或模型漂移返回退出码 1。失败消息不复述输入值、地址或异常堆栈，检查不改文件。

变更时先保留旧登记，再生成一个新的候选记录（以下示例 SHA 请换为实际 `git rev-parse HEAD`）：

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' app/agents/version_registry.py snapshot --version prompt-model-v2 --source-commit <40位Git基点SHA> --effective-at 2026-10-11T08:00:00Z --rollback '恢复上一登记源和配置；重启并核对'
```

人工审查输出后，将整个记录追加到 `prompt-model-registry.json` 的 `versions` 数组，并令 `current` 指向新版本，旧记录禁止修改/删除/复用名称。Git 评审负责历史不可变性，CLI 检查结构和指纹但不能阻止人工同时篡改历史及其指纹。先跑 `check` 和本目录正式测试，再提交源码、登记与验证证据。生成候选不会自行写登记或声明发布。

默认模型快照由 AST 读取源码默认值，与环境实际生效配置不同。登记环境覆盖时由授权运维显式提供 JSON `--config <配置快照.json>`，相同参数也用于 `check`；不应复制整个 `.env`。只接受固定模型字段和四个端点及能力来源；已知 API key / DATABASE_URL 字段被排除，其他未知键拒绝。无秘密字段可用 `export-config` 导出；端点与能力来源原文只形成 SHA-256 摘要，不输出原地址，不可从摘要还原。**模型名、provider、能力声明等可输出字段不得放入凭据或敏感正文。** 当前白名单在代码 `FIELDS` 中，以此为契约。

## 隔离/正式回滚

1. 选旧登记版本，保留当前文件备份和差异。源码发行已提交时用 `git restore --source <旧源码发行commit> -- <已登记源文件列表>`，只在获授权环境且确认文件归属后执行；`source_commit` 是基点，若登记包含未提交变更，需要从对应登记发行提交/归档恢复文件，不能直接用基点冒充完整发行。删除该版本中不存在的新源，恢复后源清单和摘要必须完全匹配。工具不会执行恢复或删除。
2. 执行 `python app/agents/version_registry.py export-config --version <旧版本>`，得到可恢复的非敏感参数 JSON。按既有配置途径应用这些参数；显式配置版本需补入秘密管理中的旧端点、能力来源及凭据版本，摘要不能代替秘密备份。API key 值或存在性不参与模型版本，模型 fingerprint 是声明配置身份，不证明实际 fallback/Mock 路由结果。
3. 用旧版本 `check --version <旧版本> --config <恢复配置.json>`；默认版本且源码默认值一致时可省略 `--config`。重复核对得到同版本指纹。发布后重启服务，再做既有业务/健康核对。真实部署秘密恢复、重启和业务验收需授权，当前证据只是在临时源树和新进程中的 E2 演练。

## 正式回归与 Evaluation

```powershell
& 'D:\DepTooL\anaconda3\envs\ai-assistant\python.exe' -m pytest docs/ai-prompts/tests/ -q --basetemp=data/prompt001-registry-tests
```

`prompt-model-registry-evaluation.json` 为 AI 编写的 Smoke/Adversarial v1，不是 Gold 或真实模型生成质量评测。验收包含源/模型改动→校验失败→恢复→新进程通过，秘密不输出，坏输入/JSON、UTC、有限数值、类型、未知键、Git基点与新源防漏登记。既有生成行为保持由 Agent/LLM/Supervisor 回归验证。
