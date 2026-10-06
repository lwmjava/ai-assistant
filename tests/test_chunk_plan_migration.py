"""Nullable plan migration preserves legacy rows through a reversible schema cycle."""

from sqlalchemy import MetaData, Table, create_engine, inspect, select

from alembic import command
from alembic.config import Config
from app.core.config import settings
from app.models.rag import Document


def test_plan_migration_preserves_legacy_document(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "f9a014c6e001")
    engine = create_engine(url)
    table = Table("rag_documents", MetaData(), autoload_with=engine)
    row = Document(id="legacy", tenant_id="audit", user_id="audit", title="legacy")
    with engine.begin() as connection:
        values = {key: value for key, value in row.model_dump().items() if key in table.c}
        connection.execute(table.insert().values(**values))
    for target, present in [("head", True), ("f9a014c6e001", False), ("head", True)]:
        if target == "head":
            command.upgrade(config, target)
        else:
            command.downgrade(config, target)
        columns = {column["name"] for column in inspect(engine).get_columns("rag_documents")}
        assert ("chunk_plan" in columns) is present
        current = Table("rag_documents", MetaData(), autoload_with=engine)
        with engine.connect() as connection:
            stored = connection.execute(select(current).where(current.c.id == "legacy")).mappings().one()
            assert stored["title"] == "legacy"
            if present:
                assert stored["chunk_plan"] is None
    engine.dispose()
