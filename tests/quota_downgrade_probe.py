"""临时库上的配额列降级。由 tests/test_quota.py 以子进程调用，不碰开发库。"""

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
    session.add(Tenant(id="keep-tenant", name="keep"))
    session.add(Conversation(id="keep-conv", tenant_id="keep-tenant", user_id="keep-user", title="留下"))
    session.add(Message(conversation_id="keep-conv", role="user", content="原话"))
    session.add(Document(id="keep-doc", tenant_id="keep-tenant", user_id="keep-user", title="文档"))
    session.commit()

# 配额迁移的父修订固定；后续新增迁移不能改变此业务回滚测试的目标。
downgrade("d7c2a91e4b18")

connection = sqlite3.connect(os.environ["QUOTA_DB_PATH"])
tenant_columns = {row[1] for row in connection.execute("PRAGMA table_info(tenants)")}
document_columns = {row[1] for row in connection.execute("PRAGMA table_info(rag_documents)")}
if "message_limit" in tenant_columns or "storage_limit_bytes" in tenant_columns:
    raise SystemExit("quota columns remain on tenants")
if "source_bytes" in document_columns:
    raise SystemExit("source_bytes remains on rag_documents")
title = connection.execute("select title from conversations where id='keep-conv'").fetchone()
content = connection.execute(
    "select content from messages where conversation_id='keep-conv'"
).fetchone()
document = connection.execute("select title from rag_documents where id='keep-doc'").fetchone()
if title != ("留下",) or content != ("原话",) or document != ("文档",):
    raise SystemExit(f"rows missing: {title} {content} {document}")
print("DOWNGRADE_OK")
