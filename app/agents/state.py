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
        "understanding",
        "plan",
        "context",
        "retrieval_status",
        "retrieval_notice",
        "retrieval_disclosure",
        "tool_results",
        "tool_budget_exhausted",
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

    def apply_snapshot(self, data: dict) -> None:
        """把快照恢复进被包装的 AgentState（幂等恢复，不抛异常给主链）。"""
        for name in self._SNAPSHOT_FIELDS:
            if name in data:
                setattr(self.state, name, data[name])
        fps = data.get("executed_tool_fingerprints") or []
        self.state.executed_tool_fingerprints = set(fps)

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
        manager.apply_snapshot(data)
        return manager
