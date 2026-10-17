# Skill 版本规格 v1

日期：2026-10-13。Owner：项目维护者。实施依据：已批准 PAGT-005 计划。发布号与内容修订用于追溯，不授予权限。

## Purpose / Preconditions

Skill 是匹配后提供 Prompt 或工具名称的声明；已认证主体的可见技能仍由 SkillService 按租户、创建者与全局范围筛选。运行内置技能不需要额外权限，调用工具仍经 ToolExecutor。私有/全局正文保持既有不可信围栏。YAML 和直接 Manifest 必须经过相同内容验证。

## Version / Compatibility

`version` 是兼容旧 `1.0` 的两段或三段数字发布号。DB/API 原字段保持其声明发布号含义，不随编辑自动递增。`content_revision` 是 canonical-v1 JSON 的完整 SHA256：包含名称、描述、模式、触发条件、Prompt、工具/模型列表与 enabled；排除发布标签、机器路径和 origin。数值置信度统一为有限浮点数；列表顺序和正文空白保留其语义。

`effective_version` 为 `version#sha256:<64位内容修订>`。改变执行内容会改变该标识；改 YAML 注释、映射键顺序、文件位置不会。origin 独立记录，内容指纹不是租户资源 ID 或授权凭证。激活 `SkillContext.versions` 和安全 `skill_activated` 日志记录实际注入快照，未激活候选不记录。日志可关联既有请求身份上下文；既有 Skill 写入审计记录操作者与资源，正文不进入新历史。

## Workflow / Tools / RAG

YAML/Service Manifest → 验证 → register 深复制 → match 隔离副本 → activate 校验实际副本 → Prompt 注入或工具名称收集。旧 match 可在新注册后执行旧快照，执行标识准确记录旧内容；不会静默改成最新内容。get/list/match 的外部修改不改变 Manager 当前集合。

Manager load/reload 使用严格发现，整批候选成功才替换技能集合、历史和目录；失败保留旧状态。普通 discover_skills 对单文件失败安全跳过，保留既有 Service 行为。合法删除全部技能产生空集合，与文件损坏失败不同。RAG 不在技能内读取或绕过 ACL；原 Chat 主链保持。

Manager 全局最多保留 100 条旧/新版本及 origin 元数据，重复注册同一内容和来源不增加记录。记录在进程/Manager 生命周期内有效，不是用户技能的持久历史正文；用户技能编辑者由既有 DB 审计追溯。

内置目录 `app/agents/skills/builtin/` 的 `releases.json` 是版本控制的发布账本。发布校验显式绑定目录和同目录账本，读取全部 YAML（含禁用技能）。修改内置行为时提升数字发布号并追加摘要与内容修订；先前条目保留。校验拒绝遗漏/未知技能、同名文件、当前内容不一致、版本倒退/重复；提供旧账本时还检查历史前缀。Git 和审查验证历史完整性，账本不是防篡改审计系统。自定义目录/同名动态注册不自动成为正式内置发布。

## Constraints / Escalation

名称最多 64 字符，发布号最多 32 字节；Prompt 最多 65536 字节，描述/正则最多 8192 字节，每列表最多 128 项、每项最多 256 字节，总 canonical 内容最多 131072 字节。拒绝错误类型、非有限/越界置信度、无效正则、重复 YAML 键、错误编码及过大文件。错误与加载日志使用固定类别，不输出原始正文、正则、秘密或绝对路径。高风险工具仍按既有审批规则停止执行。

## Builtin Examples / Evaluation

`code_review`：关键词激活审查角色，传递安全、性能、风格、最佳实践、严重程度与修复示例要求；缺代码时友好询问的既有指令保持。`translator`：关键词激活翻译角色，传递忠实原文、流畅、风格、术语、文化与输出顺序要求；原 YAML 未定义缺目标语言时自动拒绝，不能声称已经实现该分支。两者均无绑定工具，无独立 RAG；仅匹配时注入，无匹配不注入。

正式文件发布演练和真实 HTTP Chat 回归见 tests/test_skill_versioning.py、tests/test_skill_version_entry.py；失败/回滚/幂等见 tests/test_skill_manager_versions.py。合成 Provider 用于检查真实消息和版本链路，不能证明真实 LLM 的审查或翻译质量。Evaluation：docs/evaluations/skill-version-regression-v1.md。
