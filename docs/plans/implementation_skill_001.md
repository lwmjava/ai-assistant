# 实现说明：创建技能并在对话中选用

> 日期：2026-10-01
> 计划：`docs/plans/plan_d2.md` 的 SKILL-001 节，决策见 `docs/adr/0004-skill-scope-and-admin.md`
> 结果：成员可创建私有技能并在自己的对话里选用。系统管理员可跨租户查看、修改、启用、停用、删除，并可新增系统全局技能。

## 完成的行为

1. 成员创建私有技能。名称、说明、关键词、约束、技能说明和示例都要过长度校验。出现「忽略之前的所有指令」这类注入句时返回 422，不入库。
2. 私有技能只出现在创建者的列表和对话匹配里。同一租户的其他成员看不到，对话也不会选用。
3. 系统管理员可以按 `tenant_id` 看到各租户的私有技能，并修改、停用、启用、删除。系统只读角色可以看，不能改。
4. 系统管理员可以把 `scope` 设为 `global` 来新增系统全局技能。启用后其他用户的列表能看到，对话也能选用。其他角色传 `global` 返回 403。成员删除一条已经看得见的系统全局技能返回 403。看不见的私有技能返回 404。
5. 列表和创建、修改的响应不含约束、技能说明和示例。`GET /api/skills/{id}` 才返回这三段。
6. 同一创建者并发提交同名技能时，一条 201、一条 409。与内置技能同名也是 409。
7. 对话最多选用一条。命中的名字写入非流式响应，也写入正常完成和已停止的助手消息。`SKILL_ENABLED=false` 时不选用。
8. 私有技能和系统全局技能进入系统提示前套上不可信围栏。内置 YAML 不套这层。技能清单上的工具名单仍是空的。
9. 创建、修改、启用、停用、删除会写审计。详情只有名称和关键词。重复停用只留一条停用审计。

## 代码位置

- `app/models/skill.py`：技能表。`name_key` 区分私有和系统全局。
- `alembic/versions/d7c2a91e4b18_add_skills_and_message_skill_names.py`：建表，并给 `messages` 增加 `skill_names`。
- `app/services/skill_service.py`：校验、权限和匹配清单。
- `app/api/routes/skills.py`：列表、详情、创建、修改、启用、停用、删除。
- `app/services/chat_service.py`：匹配、围栏，以及两条助手消息写入。
- `app/core/security.py`：资源 `skills`。
- `tests/test_skill_selection.py`、`tests/eval/test_skill_tenant_selection.py`

## 验证

文档里的解释器路径 `D:\install\anaconda3\envs\ai-assistant\Scripts` 在这台机器上不存在。下面用的是同名环境 `D:\DepTooL\anaconda3\envs\ai-assistant\python.exe`。

```text
pytest tests/ -k skill -v
11 passed, 2 skipped, 453 deselected
```

两条 skipped 来自既有用例的跳过条件，不是本次新增失败。

在空库 `sqlite:///./data/test_skill_migrate.db` 上执行升级到 head、降一级、再升级到 head。`skills` 和 `messages.skill_names` 都能建出来、删掉、再加回来。

`ruff check` 对本次新增的技能模块通过。`ruff check app/` 仍有约 60 条既有问题，包括 `chat_service.py` 里原先就存在的前向引用告警。没有为了退出码去改那些无关文件。

## 没做的事

- 技能市场页面、定价、安装和审批。
- 版本史。
- 工具模式、正则触发和始终激活的创建。
- 对话页不展示技能名。
- PostgreSQL 上没有跑这条迁移。
- 用户管理和租户管理页面没有改。
