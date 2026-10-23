"""临时库上的配额与会话生命周期列降级。由 tests/test_quota.py 以子进程调用。"""

import os
import sqlite3

from sqlmodel import Session

from app.core.database import engine, init_db
from app.core.migration import downgrade
from app.models.conversation import Conversation, Message
from app.models.rag import Document
from app.models.user import Tenant

init_db()
with Session(engine) as session:
    session.add(
        Tenant(
            id="keep-tenant",
            name="keep",
            message_limit=3,
            token_limit=9,
            tokens_used_period=2,
            quota_period_ym="2026-10",
        )
    )
    session.add(
        Conversation(
            id="keep-conv",
            tenant_id="keep-tenant",
            user_id="keep-user",
            title="留下",
        )
    )
    session.add(Message(conversation_id="keep-conv", role="user", content="原话"))
    session.add(Document(id="keep-doc", tenant_id="keep-tenant", user_id="keep-user", title="文档"))
    session.commit()

# 回退到 Token/会话生命周期迁移之前；不越过反馈表迁移（其降级要求显式数据处理）。
downgrade("d7e0f3a21b44")

connection = sqlite3.connect(os.environ["QUOTA_DB_PATH"])
tenant_columns = {row[1] for row in connection.execute("PRAGMA table_info(tenants)")}
conversation_columns = {row[1] for row in connection.execute("PRAGMA table_info(conversations)")}
if {"token_limit", "tokens_used_period", "quota_period_ym"} & tenant_columns:
    raise SystemExit("token quota columns remain on tenants")
if {"deleted_at", "archived_at"} & conversation_columns:
    raise SystemExit("conversation lifecycle columns remain")
if "message_limit" not in tenant_columns:
    raise SystemExit("message_limit should remain after partial downgrade")
title = connection.execute("select title from conversations where id='keep-conv'").fetchone()
content = connection.execute(
    "select content from messages where conversation_id='keep-conv'"
).fetchone()
document = connection.execute("select title from rag_documents where id='keep-doc'").fetchone()
if title != ("留下",) or content != ("原话",) or document != ("文档",):
    raise SystemExit(f"rows missing: {title} {content} {document}")
print("DOWNGRADE_OK")
