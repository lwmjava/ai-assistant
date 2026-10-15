"""Real source bootstrap in isolated SQLite; never imports application Settings."""
import ast
import hashlib
import json
import logging
import os
import secrets
import sys
import types
import uuid
from datetime import UTC, datetime
from enum import Enum
from unittest.mock import patch

import bcrypt
from env001_example_contract import EXAMPLE, ROOT, check_example, isolated_settings
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Relationship, Session, SQLModel, create_engine, select


def source_namespace():
    module = types.ModuleType("env002_isolated_models")
    sys.modules[module.__name__] = module
    ns = module.__dict__
    ns.update(dict(UTC=UTC, datetime=datetime, Enum=Enum, bcrypt=bcrypt, uuid=uuid,
                   Field=Field, Relationship=Relationship, SQLModel=SQLModel,
                   Session=Session, select=select, UniqueConstraint=UniqueConstraint,
                   logger=logging.getLogger("env002")))
    sources = {
        "app/core/security.py": {"Role", "hash_password", "verify_password"},
        "app/models/base.py": {"_utcnow", "TimestampMixin"},
        "app/models/user.py": {"_uuid", "Tenant", "User"},
        "app/models/membership.py": {"Membership"},
        "app/services/membership.py": {"membership_role_for_user", "get_membership",
                                       "ensure_membership", "backfill_memberships"},
        "app/services/auth_service.py": {"ensure_initial_admin"},
    }
    for filename, names in sources.items():
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
        nodes = [n for n in tree.body if isinstance(n, ast.ClassDef | ast.FunctionDef)
                 and n.name in names]
        assert {n.name for n in nodes} == names
        if filename.endswith("services/membership.py"):
            ns["_TENANT_ROLES"] = {ns["Role"].MEMBER.value, ns["Role"].VIEWER.value,
                                    ns["Role"].TENANT_ADMIN.value}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, "exec",
                     dont_inherit=True), ns)
    return ns, sources


def run():
    report = {"version": "env002-v1", "checks": [], "platform": sys.platform,
              "python": sys.version, "head": "80629c4cd5ce00d59b26116c7419edce00e6a113"}
    contract = check_example(EXAMPLE)
    assert contract["passed"], contract
    cls = isolated_settings()
    with patch.dict(os.environ, {}, clear=True):
        default = cls(_env_file=None)
        sample = cls(_env_file=EXAMPLE, _env_file_encoding="utf-8")
    assert default.INITIAL_ADMIN_PASSWORD == ""
    assert sample.INITIAL_ADMIN_PASSWORD == "", "template_contains_login_password"
    report["checks"].extend(["181_keys_actual_settings", "source_default_empty", "sample_password_empty"])
    readme = (ROOT / "README.md").read_text(encoding="utf-8-sig")
    assert "- 任一项为空，而且库里还没有系统管理员" in readme
    assert 'INITIAL_ADMIN_PASSWORD="' not in readme
    assert '$env:INITIAL_ADMIN_PASSWORD = "' not in readme
    report["checks"].append("readme_partial_empty_setup_no_password_literal")
    ns, sources = source_namespace()
    secret = secrets.token_urlsafe(24)
    user_model, tenant_model, membership_model = (ns[n] for n in ("User", "Tenant", "Membership"))
    logging.getLogger("env002").disabled = True
    for index, (username, password) in enumerate((("", ""), ("probe", ""), ("", secret), ("probe", secret),
                                               (sample.INITIAL_ADMIN_USERNAME, sample.INITIAL_ADMIN_PASSWORD))):
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        ns["settings"] = types.SimpleNamespace(INITIAL_ADMIN_USERNAME=username,
                                               INITIAL_ADMIN_PASSWORD=password,
                                               INITIAL_ADMIN_EMAIL="")
        with Session(engine) as session:
            ns["ensure_initial_admin"](session)
            ns["ensure_initial_admin"](session)
        with Session(engine) as session:
            users = session.exec(select(user_model)).all()
            tenants = session.exec(select(tenant_model)).all()
            memberships = session.exec(select(membership_model)).all()
            if index != 3:
                assert not users and not tenants and not memberships
            else:
                assert len(users) == len(tenants) == len(memberships) == 1
                assert users[0].role == "system_admin" and tenants[0].name == "default"
                assert memberships[0].user_id == users[0].id
                assert memberships[0].tenant_id == users[0].tenant_id == tenants[0].id
                assert ns["verify_password"](secret, users[0].hashed_password)
        report["checks"].append(f"bootstrap_combination_{index}_persisted_idempotent")
        engine.dispose()
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        tenant = tenant_model(name="existing")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        session.add(user_model(tenant_id=tenant.id, username="existing-member",
                               hashed_password=ns["hash_password"](secret), role="member"))
        session.commit()
    ns["settings"] = types.SimpleNamespace(INITIAL_ADMIN_USERNAME="new-admin",
                                           INITIAL_ADMIN_PASSWORD=secret, INITIAL_ADMIN_EMAIL="")
    with Session(engine) as session:
        ns["ensure_initial_admin"](session)
        ns["ensure_initial_admin"](session)
    with Session(engine) as session:
        users = session.exec(select(user_model)).all()
        assert len(users) == 1 and users[0].role == "member"
        assert len(session.exec(select(tenant_model)).all()) == 1
        assert len(session.exec(select(membership_model)).all()) == 1
    engine.dispose()
    report["checks"].append("existing_member_prevents_admin_backfill_idempotent")
    report["sha256"] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                         for p in [*sources, "app/core/config.py", ".env.example", "README.md",
                                   "docs/evaluations/env002_bootstrap_contract.py",
                                   "docs/evaluations/env001_example_contract.py"]}
    report["result"] = "pass"
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
