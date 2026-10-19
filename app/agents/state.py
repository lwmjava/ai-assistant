"""编排状态管理（PAGT-006，ADR-0010 决定2）。

``StateManager`` 包装一次执行的 ``AgentState``，负责可序列化子集的快照组装与恢复；
**本模块不 import ``app.core.database``、不 import ``sqlmodel``、不 import ``app.models``**
（遵守 AGENTS.md §5：agents 层不直接操作数据库）。持久化通过注入的
``AgentStateStore`` Protocol 由服务层（持 Session）实现。

快照只含 AgentState 的可序列化子集；``executed_tool_fingerprints`` 由 set 序列化为
排序后的 list。快照禁止落密钥/连接串（工具观测已在 Executor 层脱敏）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

from app.agents.plan import validate_plan, validate_progress
from app.agents.skills.base import valid_skill_binding

if TYPE_CHECKING:
    from app.agents.pipeline import AgentState


class AgentStateStore(Protocol):
    """持久化协议：由 app/services/agent_state_service.py 实现（持 Session）。"""

    def save_checkpoint(
        self,
        *,
        conversation_id: str,
        message_id: str | None,
        tenant_id: str,
        user_id: str,
        stage: str,
        round_no: int,
        tool_budget_exhausted: bool,
        state_json: str,
        status: str,
        error: str | None,
    ) -> None:
        """按 (conversation_id, message_id) upsert 一行 checkpoint。"""
        ...

    def load_active(self, conversation_id: str, message_id: str | None) -> dict | None:
        """返回活动 checkpoint 行（dict 形式）；无行/过期/损坏→None（按全新 run）。"""
        ...

    def discard(self, conversation_id: str, message_id: str | None) -> None:
        """丢弃/标记某行不再活动（损坏或冲突时调用）。"""
        ...


class StateManager:
    """包装 AgentState，提供快照组装与恢复（不碰 DB）。"""

    # 允许序列化进 checkpoint 的字段（AgentState 子集）。
    _SNAPSHOT_FIELDS = (
        "skill_binding",
        "understanding",
        "plan",
        "context",
        "retrieval_status",
        "retrieval_notice",
        "retrieval_disclosure",
        "tool_results",
        "tool_budget_exhausted",
        "tool_rounds_used",
        "needs_full_pipeline",
        "code_results",
        "structured_plan",
        "plan_progress",
        "plan_cursor",
        "failed_step_id",
    )

    def __init__(self, state: AgentState) -> None:
        self.state = state

    def snapshot(self) -> dict:
        """组装可序列化子集；fingerprints 排序为 list。"""
        data: dict = {}
        for name in self._SNAPSHOT_FIELDS:
            data[name] = getattr(self.state, name, None)
        fingerprints = getattr(self.state, "executed_tool_fingerprints", set()) or set()
        data["executed_tool_fingerprints"] = sorted(fingerprints)
        return data

    def to_json(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False)

    @classmethod
    def valid_snapshot(cls, data: dict) -> bool:
        """Validate all known fields before changing any runtime state."""
        for name in cls._SNAPSHOT_FIELDS:
            if name not in data:
                continue
            value = data[name]
            if name == "skill_binding":
                if not valid_skill_binding(value):
                    return False
                continue
            if name in {"structured_plan", "plan_progress", "plan_cursor", "failed_step_id"}:
                continue
            if name in {"tool_budget_exhausted", "needs_full_pipeline"}:
                if not isinstance(value, bool):
                    return False
            elif name == "tool_rounds_used":
                if type(value) is not int or value < 0:
                    return False
            elif name == "tool_results":
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    return False
            elif name == "code_results":
                if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                    return False
            elif not isinstance(value, str):
                return False
        fps = data.get("executed_tool_fingerprints", [])
        if (not isinstance(fps, list) or not all(isinstance(item, str) for item in fps)
                or ("_round_no" in data and (type(data["_round_no"]) is not int or data["_round_no"] < 0))):
            return False
        plan = data.get("structured_plan")
        progress = data.get("plan_progress", [])
        cursor = data.get("plan_cursor", 0)
        failed = data.get("failed_step_id")
        if plan is None:
            if progress != [] or type(cursor) is not int or cursor != 0 or failed is not None:
                return False
        else:
            if not validate_plan(plan) or not validate_progress(plan, progress, cursor):
                return False
            expected_failed = next((p["id"] for p in progress if p["status"] == "failed"), None)
            if failed != expected_failed:
                return False
            # Persisted success must agree with the already consumed tool budget.
            # Import lazily: Pipeline itself depends on StateManager at module load.
            from app.agents.pipeline import tool_fingerprint

            rounds = data.get("tool_rounds_used")
            completed = [tool_fingerprint(step["tool"], step["arguments"])
                         for step in plan["steps"][:cursor] if step["kind"] == "tool"]
            if (type(rounds) is not int or rounds < len(completed)
                    or len(set(completed)) != len(completed)
                    or len(set(fps)) != len(fps) or rounds < len(fps)
                    or not set(completed).issubset(fps)
                    or ("_round_no" in data and data["_round_no"] != rounds)):
                return False
        return True

    def apply_snapshot(self, data: dict) -> bool:
        """Restore a validated snapshot atomically; reject malformed fields."""
        if not self.valid_snapshot(data):
            return False
        for name in self._SNAPSHOT_FIELDS:
            if name in data:
                setattr(self.state, name, data[name])
        fps = data.get("executed_tool_fingerprints") or []
        self.state.executed_tool_fingerprints = set(fps)
        self.state.tool_rounds_used = data.get("_round_no", data.get("tool_rounds_used", len(fps)))
        return True

    @classmethod
    def restore_from_json(cls, state: AgentState, text: str | None) -> StateManager | None:
        """从 JSON 文本恢复；损坏/空文本返回 None（调用方按全新 run 处理）。"""
        if not text:
            return None
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        manager = cls(state)
        if not manager.apply_snapshot(data):
            return None
        return manager
