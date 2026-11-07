"""工作流定义校验。

当前引擎只执行一条 Prompt 模板。定义里只能保存这一个已批准步骤；
变量名只做声明一致性检查，不替换、不求值。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_BRACE = re.compile(r"\{([^{}]*)\}")
PROMPT_STEP_ID = "prompt"
PROMPT_STEP_TYPE = "prompt"


class WorkflowDefinitionError(Exception):
    """定义不能保存。code 是稳定的拒绝原因。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def revision_token(value: datetime) -> str:
    """把更新时间归一成可往返比较的修订标记。"""
    current = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def same_revision(stored: datetime, sent: str) -> bool:
    try:
        parsed = datetime.fromisoformat(sent)
    except ValueError:
        return False
    return revision_token(stored) == revision_token(parsed)


def declared_variables(template: str) -> list[str]:
    """按出现顺序提取占位名。非法占位直接拒绝。"""
    names: list[str] = []
    for body in _BRACE.findall(template):
        if not _IDENT.fullmatch(body):
            raise WorkflowDefinitionError("invalid_variable")
        if body not in names:
            names.append(body)
    return names


def validate_prompt_template(template: str) -> list[str]:
    if not isinstance(template, str):
        raise WorkflowDefinitionError("invalid_variable")
    return declared_variables(template)


def _has_cycle(graph: Mapping[str, Sequence[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for nxt in graph[node]:
            if walk(nxt):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(walk(node) for node in graph)


def validate_definition(
    steps: Sequence[Mapping[str, object]],
    edges: Sequence[Mapping[str, object]],
) -> str:
    """校验步骤、变量和边。通过时返回要保存的 Prompt 模板原文。"""
    if not steps:
        raise WorkflowDefinitionError("unknown_step")
    ids: list[str] = []
    templates: list[str] = []
    for step in steps:
        step_type = step.get("type")
        step_id = step.get("id")
        template = step.get("template")
        variables = step.get("variables")
        if step_type != PROMPT_STEP_TYPE or not isinstance(step_id, str) or not step_id:
            raise WorkflowDefinitionError("unknown_step")
        if not isinstance(template, str) or not isinstance(variables, list):
            raise WorkflowDefinitionError("invalid_variable")
        if any(not isinstance(name, str) for name in variables):
            raise WorkflowDefinitionError("invalid_variable")
        derived = declared_variables(template)
        if list(variables) != derived:
            raise WorkflowDefinitionError("invalid_variable")
        if step_id in ids:
            raise WorkflowDefinitionError("unknown_step")
        ids.append(step_id)
        templates.append(template)

    graph: dict[str, list[str]] = {step_id: [] for step_id in ids}
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if not isinstance(source, str) or not isinstance(target, str) or source not in graph or target not in graph:
            raise WorkflowDefinitionError("unknown_step")
        graph[source].append(target)
    if _has_cycle(graph):
        raise WorkflowDefinitionError("cycle")
    if len(steps) != 1 or any(graph[step_id] for step_id in ids) or ids[0] != PROMPT_STEP_ID:
        raise WorkflowDefinitionError("unknown_step")
    return templates[0]


def prompt_node(status: str, reason: str | None) -> dict[str, object]:
    """运行图只有引擎实际执行的 Prompt 节点。"""
    failed = status == "failed"
    return {
        "id": PROMPT_STEP_ID,
        "type": PROMPT_STEP_TYPE,
        "status": status,
        "failed": failed,
        "reason": reason if failed else None,
    }
