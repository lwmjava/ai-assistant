"""RuntimeBridge：连接工作流与 Agent 管线。

以任务创建者（owner）身份构造执行上下文，调用 ChatService 执行 Prompt 模板，
返回模型输出文本。复用现有 Agent 五阶段管线（含 RAG / MCP 工具注入），
而非文档假设的 LangGraph —— 本项目使用自定义 ``app/agents/pipeline.py``。
"""

import logging

from sqlmodel import Session

from app.models.user import User
from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)


class WorkflowBridge:
    """将 Workflow 的一次执行桥接到对话管线。"""

    def __init__(self, chat_service: ChatService | None = None) -> None:
        # 允许注入自定义 ChatService（测试 / 运行时覆盖）。
        self._chat = chat_service or ChatService()

    async def execute(self, session: Session, owner: User, prompt: str) -> str:
        """以 owner 身份执行 prompt，返回模型回复文本。

        Args:
            session: 数据库会话（ChatService 会复用并持久化会话 / 消息）。
            owner: 任务创建者（已加载的 User 对象），执行以其身份运行。
            prompt: 实际要执行的 Prompt（已解析模板）。

        Returns:
            模型生成的回复文本。
        """
        # 复用现有 Agent 管线：每次执行新建一个会话（Conversation），
        # 归属字段取 owner 的租户 / 用户，便于审计与回溯；
        # SQLite 不强制外键，owner 为合成主体（测试）时亦可运行。
        _conv, answer = await self._chat.chat(session, owner, prompt)
        return answer
