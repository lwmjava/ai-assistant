# Python 类型检查约定

> 由 `AGENTS.md` 第10节按需引用；修改 SQLModel 查询、注解、可选导入、Alembic head 或子进程错误处理时适用。

2026-09-29 对 `mypy app/rag/access.py app/rag/import_jobs.py app/rag/service.py` 的复盘见 `docs/reviews/2026-09-29-mypy类型错误复盘.md`。当时 40 条报错、退出码 1。编写或修改 SQLModel 查询、注解和子进程错误处理时遵守：

- 条件、排序、连接使用 `sqlmodel.col`。`is_current`、`deleted_at`、`updated_at`、`created_at`、`id` 的注解是 Python 类型，查询里仍要包 `col()` 再调用 `.is_()`、`.asc()`、`.desc()` 或做 `==` / `<`。新增调用同时补上 `col` 的 import。
- `Session.exec` 只用于 `select`。`delete` 与 `update` 使用 `session.execute`。行数用 `getattr(result, "rowcount", 0)`。不用错误码对不上的 `type: ignore` 去压 `exec(delete(...))`。当前配置 `warn_unused_ignores = false`，忽略是否生效以命令输出里的错误码为准。
- `session.exec(...).all()` 按 `Sequence` 接收。文件没有 `from __future__ import annotations` 时，注解里的 `Sequence`、模型名和其他名字必须有运行时 import。
- 键固定的返回值用 `TypedDict`。需要符合既定值类型的字典写出注解。同一变量在不同分支要赋 `str` 和 `str | None` 时，先声明目标类型。
- `subprocess` 超时异常的 stdout、stderr 按 `bytes | None` 解码后再交给 `str | None`。已经是 `list[str]` 的命令变量不要再赋成拼接后的字符串。
- 数据类按字段构造，不用 `**dict`。未知键抛出带键名的 `ValueError`。
- 可选依赖缺失时，不把 `None` 赋给 `import` 进来的类名。先 `Name: Any = None`，再 `import ... as _Name`。`Any` 要导入。
- 用逐个 `is None` 收窄，不用 `None in (a, b, c)`。
- 把可能为空的 Alembic head 收成 `str` 时，空值要失败。这个异常若发生在启动迁移路径上，必须进入开发环境「迁移失败不阻塞启动」的既有处理，不能留在那段 `try` 之外。
- 不放宽 mypy 配置，不改 `follow_imports`，不用整文件 `type: ignore` 换退出码 0。
