"""The default application database must not change with the launch directory."""

from pathlib import Path

from app.core.config import Settings


def test_default_database_is_anchored_to_project_from_frontend_directory(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(root / "frontend")
    configured = Settings(DATABASE_URL="sqlite:///./data/ai_assistant.db")
    assert configured.DATABASE_URL == "sqlite:///" + (root / "data/ai_assistant.db").as_posix()


def test_explicit_test_database_is_not_redirected():
    configured = Settings(DATABASE_URL="sqlite:///./data/test_ai_assistant.db")
    assert configured.DATABASE_URL == "sqlite:///./data/test_ai_assistant.db"
