"""Independent ENV-002 persisted-entry assertions; no application imports."""
import hashlib
import io
import json
import logging
import os
import secrets
import types
from unittest.mock import patch

from env002_bootstrap_contract import ROOT, isolated_settings, source_namespace
from sqlmodel import Session, SQLModel, create_engine, select


def run():
    ns, sources = source_namespace()
    user, tenant, member = [ns[name] for name in ("User", "Tenant", "Membership")]
    secret = secrets.token_urlsafe(28)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = ns["logger"]
    logger.disabled = False
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    checks = []
    for username, password in (("", ""), ("entry", ""), ("", secret)):
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        ns["settings"] = types.SimpleNamespace(INITIAL_ADMIN_USERNAME=username,
                                               INITIAL_ADMIN_PASSWORD=password,
                                               INITIAL_ADMIN_EMAIL="")
        with Session(engine) as session:
            ns["ensure_initial_admin"](session)
        with Session(engine) as session:
            assert all(not session.exec(select(model)).all() for model in (user, tenant, member))
        engine.dispose()
    checks.append("three_incomplete_inputs_no_persisted_rows")
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        row = tenant(name="default")
        session.add(row)
        session.commit()
        session.refresh(row)
        initial_tenant_id = row.id
    ns["settings"] = types.SimpleNamespace(INITIAL_ADMIN_USERNAME="independent-entry",
                                           INITIAL_ADMIN_PASSWORD=secret,
                                           INITIAL_ADMIN_EMAIL="probe@example.invalid")
    with Session(engine) as session:
        ns["ensure_initial_admin"](session)
    with Session(engine) as session:
        persisted = session.exec(select(user)).one()
        assert persisted.role == "system_admin" and persisted.tenant_id == initial_tenant_id
        assert persisted.email == "probe@example.invalid"
        assert persisted.hashed_password != secret
        assert ns["verify_password"](secret, persisted.hashed_password)
        assert not ns["verify_password"](secrets.token_urlsafe(28), persisted.hashed_password)
        identity = (persisted.id, persisted.hashed_password, persisted.tenant_id)
        binding = session.exec(select(member)).one()
        assert binding.user_id == persisted.id and binding.tenant_id == initial_tenant_id
        assert binding.role == "member"
        assert len(session.exec(select(tenant)).all()) == 1
    checks.append("reuse_default_tenant_real_hash_email_and_membership")
    ns["settings"].INITIAL_ADMIN_USERNAME = "second-entry"
    ns["settings"].INITIAL_ADMIN_PASSWORD = secrets.token_urlsafe(28)
    with Session(engine) as session:
        ns["ensure_initial_admin"](session)
    with Session(engine) as session:
        persisted = session.exec(select(user)).one()
        assert (persisted.id, persisted.hashed_password, persisted.tenant_id) == identity
        assert len(session.exec(select(member)).all()) == 1
    checks.append("existing_admin_no_replacement_or_duplicate")
    engine.dispose()
    assert secret not in stream.getvalue() and identity[1] not in stream.getvalue()
    checks.append("bootstrap_logs_no_generated_secret_or_hash")
    cls = isolated_settings()
    with patch.dict(os.environ, {}, clear=True):
        loaded = cls(_env_file=ROOT / ".env.example")
        assert loaded.INITIAL_ADMIN_USERNAME and loaded.INITIAL_ADMIN_PASSWORD == ""
    with patch.dict(os.environ, {"INITIAL_ADMIN_PASSWORD": secret}, clear=True):
        overridden = cls(_env_file=ROOT / ".env.example")
        assert overridden.INITIAL_ADMIN_PASSWORD == secret
    checks.append("actual_settings_blank_template_and_process_injection")
    readme = (ROOT / "README.md").read_text(encoding="utf-8-sig")
    assert 'INITIAL_ADMIN_PASSWORD="' not in readme
    assert '$env:INITIAL_ADMIN_PASSWORD = "' not in readme
    assert "任一项为空" in readme
    checks.append("readme_no_literal_password_assignment")
    inputs = [*sources, "app/core/config.py", ".env.example", "README.md",
              "docs/evaluations/env002_bootstrap_contract.py",
              "docs/evaluations/env001_example_contract.py",
              "docs/evaluations/env002_independent_probe.py"]
    print(json.dumps({"checks": checks, "result": "pass", "sha256": {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in inputs
    }}, indent=2))


if __name__ == "__main__":
    run()
