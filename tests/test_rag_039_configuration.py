"""导入预算配置契约：批次与并发独立、环境字符串兼容、非法值拒绝。"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("field", ["RAG_IMPORT_BATCH_SIZE", "RAG_IMPORT_MAX_CONCURRENCY"])
@pytest.mark.parametrize("value", [0, -1, True, False, 1.0, float("nan"), float("inf"), 1001])
def test_invalid_import_budget_is_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_environment_import_budgets_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_IMPORT_BATCH_SIZE", "7")
    monkeypatch.setenv("RAG_IMPORT_MAX_CONCURRENCY", "2")
    config = Settings(_env_file=None)
    assert config.RAG_IMPORT_BATCH_SIZE == 7
    assert config.RAG_IMPORT_MAX_CONCURRENCY == 2
