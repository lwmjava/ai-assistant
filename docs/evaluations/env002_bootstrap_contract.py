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
from sqlmodel import Field, Relationship, Session, SQLModel, col, create_engine, select


def source_namespace():
    module = types.ModuleType("env002_isolated_models")
    sys.modules[module.__name__] = module
    ns = module.__dict__
    ns.update(dict(UTC=UTC, datetime=datetime, Enum=Enum, bcrypt=bcrypt, uuid=uuid,
                   Field=Field, Relationship=Relationship, SQLModel=SQLModel,
                   Session=Session, select=select, UniqueConstraint=UniqueConstraint,
                   logger=logging.getLogger("env002"), col=col))
    sources = {
        "app/core/security.py": {"Role", "hash_password", "verify_password"},
        "app/models/base.py": {"_utcnow", "TimestampMixin"},
        "app/models/user.py": {"_uuid", "Tenant", "User"},
        "app/models/membership.py": {"Membership"},
        "app/services/membership.py": {"membership_role_for_user", "get_membership",
                                       "ensure_membership", "backfill_memberships"},
        "app/services/auth_service.py": {"ensure_initial_admin", "_default_tenant_for_admin",
                                          "PasswordTooShortError", "DefaultTenantInactiveError"},
    }
    for filename, names in sources.items():
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
        nodes = [n for n in tree.body if isinstance(n, ast.ClassDef | ast.FunctionDef)
                 and n.name in names]
        assert {n.name for n in nodes} == names
        if filename.endswith("services/membership.py"):
            ns["_TENANT_ROLES"] = {ns["Role"].MEMBER.value, ns["Role"].VIEWER.value,
                                    ns["Role"].TENANT_ADMIN.value}
        if filename.endswith("services/auth_service.py"):
            constants = {"MIN_CLI_PASSWORD_LENGTH", "DEFAULT_TENANT_NAME"}
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id in constants:
                            ns[target.id] = ast.literal_eval(node.value)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, "exec",
                     dont_inherit=True), ns)
    return ns, sources


def run():
    report = {"version": "env002-v2", "checks": [], "platform": sys.platform,
              "python": sys.version}
    contract = check_example(EXAMPLE)
    assert contract["passed"], contract
    cls = isolated_settings()
    with patch.dict(os.environ, {}, clear=True):
        default = cls(_env_file=None)
        sample = cls(_env_file=EXAMPLE, _env_file_encoding="utf-8")
    for key in ("INITIAL_ADMIN_PASSWORD", "INITIAL_SYSTEM_PASSWORD", "INITIAL_CULTURE_PASSWORD"):
        assert getattr(default, key) == ""
        assert getattr(sample, key) == "", "template_contains_login_password"
    report["checks"].extend(["all_source_keys_actual_settings", "source_default_empty", "sample_password_empty"])
    readme = (ROOT / "README.md").read_text(encoding="utf-8-sig")
    assert "单管理员模式下任一项为空，而且库里还没有系统管理员" in readme
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
                                               INITIAL_ADMIN_EMAIL="",
                                               INITIAL_SYSTEM_PASSWORD="", INITIAL_CULTURE_PASSWORD="")
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
                                           INITIAL_ADMIN_PASSWORD=secret, INITIAL_ADMIN_EMAIL="",
                                               INITIAL_SYSTEM_PASSWORD="", INITIAL_CULTURE_PASSWORD="")
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
    # Three-account mode: distinct runtime-generated secrets, actual source and bcrypt.
    passwords = {name: secrets.token_urlsafe(24) for name in ("admin", "system", "culture")}
    complete = dict(INITIAL_ADMIN_USERNAME="admin", INITIAL_ADMIN_PASSWORD=passwords["admin"],
                    INITIAL_ADMIN_EMAIL="", INITIAL_SYSTEM_PASSWORD=passwords["system"],
                    INITIAL_CULTURE_PASSWORD=passwords["culture"])
    invalid = [{**complete, key: ""} for key in complete if key != "INITIAL_ADMIN_EMAIL"]
    invalid.append({**complete, "INITIAL_ADMIN_USERNAME": "other"})
    invalid.extend({**complete, key: secrets.token_urlsafe(2)} for key in
                   ("INITIAL_ADMIN_PASSWORD", "INITIAL_SYSTEM_PASSWORD", "INITIAL_CULTURE_PASSWORD"))
    for config in invalid:
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        ns["settings"] = types.SimpleNamespace(**config)
        with Session(engine) as session:
            try:
                ns["ensure_initial_admin"](session)
            except (ValueError, ns["PasswordTooShortError"]):
                pass
            else:
                raise AssertionError("invalid_triple_config_accepted")
            assert all(not session.exec(select(model)).all()
                       for model in (user_model, tenant_model, membership_model))
        engine.dispose()
    report["checks"].append("triple_missing_wrong_username_and_short_password_rejected_without_rows")
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    ns["settings"] = types.SimpleNamespace(**complete)
    with Session(engine) as session:
        ns["ensure_initial_admin"](session)
        before = {u.username: (u.id, u.hashed_password) for u in session.exec(select(user_model)).all()}
        ns["ensure_initial_admin"](session)
    with Session(engine) as session:
        users = session.exec(select(user_model)).all()
        assert len(users) == 3
        assert {u.username: (u.id, u.hashed_password) for u in users} == before
        assert len(session.exec(select(tenant_model)).all()) == 1
        bindings = session.exec(select(membership_model)).all()
        assert len(bindings) == 3
        expected = {"admin": ("system_admin", False, "member"),
                    "system": ("tenant_admin", False, "tenant_admin"),
                    "culture": ("member", True, "member")}
        for u in users:
            role, test_user, binding_role = expected[u.username]
            assert (u.role, u.is_test_user, u.must_change_password) == (role, test_user, True)
            assert ns["verify_password"](passwords[u.username], u.hashed_password)
            assert not ns["verify_password"](secrets.token_urlsafe(24), u.hashed_password)
            binding = next(b for b in bindings if b.user_id == u.id)
            assert binding.tenant_id == u.tenant_id and binding.role == binding_role
    engine.dispose()
    report["checks"].append("triple_accounts_roles_flags_memberships_real_bcrypt_and_idempotency")
    report["sha256"] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                         for p in [*sources, "app/core/config.py", ".env.example", "README.md",
                                   "docs/evaluations/env002_bootstrap_contract.py",
                                   "docs/evaluations/env001_example_contract.py"]}
    report["result"] = "pass"
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
