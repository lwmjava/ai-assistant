"""Configuration secrets remain encrypted at rest and bound to their purpose."""
import pytest
from cryptography.fernet import Fernet

from app.security.secret_storage import API_KEY_FIELDS, SecretStorageError, decrypt_secret, encrypt_secret


def test_roundtrip_and_purpose():
    key = Fernet.generate_key().decode()
    value = Fernet.generate_key().decode()
    token = encrypt_secret("LLM_API_KEY", value, key)
    assert value not in token
    assert decrypt_secret("LLM_API_KEY", token, key) == value
    with pytest.raises(SecretStorageError):
        decrypt_secret("EMBEDDING_API_KEY", token, key)


@pytest.mark.parametrize("mode", ["missing", "wrong", "tampered"])
def test_invalid_secrets_fail_without_values(mode):
    key = Fernet.generate_key().decode()
    token = encrypt_secret("LLM_API_KEY", "synthetic-value", key)
    actual_key = "" if mode == "missing" else Fernet.generate_key().decode() if mode == "wrong" else key
    if mode == "tampered":
        token = token[:-5] + "AAAAA"
    with pytest.raises(SecretStorageError) as caught:
        decrypt_secret("LLM_API_KEY", token, actual_key)
    assert token not in str(caught.value)
    assert key not in str(caught.value)


def test_settings_decrypt_and_hide(monkeypatch):
    from app.core.config import Settings
    key = Fernet.generate_key().decode()
    value = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_SECRET_ENCRYPTION_KEY", key)
    token = encrypt_secret("LLM_API_KEY", value, key)
    settings = Settings(_env_file=None, LLM_API_KEY=token)
    assert settings.LLM_API_KEY == value
    assert value not in repr(settings)
    assert "LLM_API_KEY" not in settings.model_dump()


def test_dotenv_plaintext_rejected_even_when_overridden(tmp_path, monkeypatch):
    from app.core.config import Settings
    path = tmp_path / "synthetic.env"
    path.write_text("LLM_API_KEY=" + "-".join(("synthetic", "value")) + "\n", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "override-value")
    with pytest.raises(SecretStorageError):
        Settings(_env_file=path)


def test_migration_copy_and_failure(tmp_path, monkeypatch):
    from app.core.config import Settings
    from scripts.migrate_api_secrets import migrate
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_SECRET_ENCRYPTION_KEY", key)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    source, target = tmp_path / "input.env", tmp_path / "output.env"
    source.write_text("LLM_API_KEY=" + "-".join(("synthetic", "value")) + "\n", encoding="utf-8")
    migrate(source, target)
    assert "synthetic-value" not in target.read_text()
    assert "synthetic-value" in source.read_text()
    assert Settings(_env_file=target).LLM_API_KEY == "-".join(("synthetic", "value"))
    original = target.read_bytes()
    with pytest.raises(SecretStorageError):
        migrate(source, target)
    assert target.read_bytes() == original


@pytest.mark.parametrize("field", API_KEY_FIELDS)
def test_each_provider_field(field, monkeypatch):
    from app.core.config import Settings
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_SECRET_ENCRYPTION_KEY", key)
    value = Fernet.generate_key().decode()
    settings = Settings(_env_file=None, **{field: encrypt_secret(field, value, key)})
    assert getattr(settings, field) == value
    assert value not in repr(settings)
    assert field not in settings.model_dump()


@pytest.mark.parametrize("content", [
    "LLM_API_KEY=first\nLLM_API_KEY=second\n",
    "APP_SECRET_ENCRYPTION_KEY=forbidden\n",
    "LLM_API_KEY=${OTHER}\n",
    "LLM_API_KEY=enc:unknown:bad\n",
    "LLM_API_KEY=" + "'multi" + "\nline'\n",
    "LLM_API_KEY='unterminated\n",
])
def test_migration_rejects_without_output(content, tmp_path, monkeypatch):
    from scripts.migrate_api_secrets import migrate
    monkeypatch.setenv("APP_SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    source, target = tmp_path / "input.env", tmp_path / "output.env"
    source.write_text(content, encoding="utf-8")
    with pytest.raises(SecretStorageError):
        migrate(source, target)
    assert not target.exists()
    assert source.read_text(encoding="utf-8") == content


def test_dotenv_rejects_master_key(tmp_path):
    from app.core.config import Settings
    source = tmp_path / "input.env"
    source.write_text("APP_SECRET_ENCRYPTION_KEY=forbidden\n", encoding="utf-8")
    with pytest.raises(SecretStorageError):
        Settings(_env_file=source)


def test_file_secrets_require_encryption(tmp_path):
    from app.core.config import Settings
    (tmp_path / "LLM_API_KEY").write_text("synthetic-file-secret", encoding="utf-8")
    with pytest.raises(SecretStorageError):
        Settings(_env_file=None, _secrets_dir=tmp_path)
