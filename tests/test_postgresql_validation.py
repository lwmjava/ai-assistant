"""Safety and honest result contracts for isolated PostgreSQL validation."""

import argparse
import secrets

import pytest
from sqlalchemy.engine import URL

from app.core.config import settings
from app.core.migration import _get_alembic_config
from scripts import validate_postgresql as validator
from scripts.validate_postgresql import ValidationError, junit_result, official_image, require_local_daemon


@pytest.mark.parametrize("image", ["postgres:14-alpine", "postgres:16-alpine", "postgres:16.15"])
def test_only_explicit_supported_official_database_images_are_accepted(image):
    assert official_image(image) == image


@pytest.mark.parametrize("image", ["postgres:latest", "postgres:13", "postgres:18-alpine",
                                   "other/postgres:16", "postgres:16;echo x"])
def test_unbounded_or_untrusted_image_names_are_rejected(image):
    with pytest.raises(argparse.ArgumentTypeError):
        official_image(image)


def test_empty_junit_or_missing_report_never_proves_postgresql_passed(tmp_path):
    path = tmp_path / "report.xml"
    with pytest.raises(ValidationError, match="pytest_report_missing"):
        junit_result(path)
    path.write_text('<testsuites><testsuite tests="0" /></testsuites>', encoding="utf-8")
    with pytest.raises(ValidationError, match="no_postgresql_cases_collected"):
        junit_result(path)


def test_skipped_or_failed_postgresql_cases_remain_visible(tmp_path):
    path = tmp_path / "report.xml"
    path.write_text('<testsuite><testcase><skipped /></testcase><testcase><failure /></testcase>'
                    '<testcase><error /></testcase><testcase /></testsuite>', encoding="utf-8")
    assert junit_result(path) == {"tests": 4, "failures": 1, "errors": 1, "skipped": 1}


def test_alembic_config_preserves_percent_encoded_connection_values(monkeypatch):
    value = URL.create("postgresql+psycopg2", username="placeholder", password=secrets.token_hex(8) + "@%/",
                       host="127.0.0.1", database="not_a_connection_target").render_as_string(hide_password=False)
    monkeypatch.setattr(settings, "DATABASE_URL", value)
    config = _get_alembic_config()
    assert config.get_main_option("sqlalchemy.url") == value


@pytest.mark.parametrize("endpoint", ["ssh://remote", "tcp://db.example:2375", "tcp://127.0.0.1.evil:2375",
                                      "tcp://localhost:2375@db.example:2375", "npipe://remote/pipe/docker_engine"])
def test_remote_daemon_is_rejected_before_resource_creation(endpoint):
    with pytest.raises(ValidationError, match="remote_docker_daemon_not_allowed"):
        require_local_daemon(endpoint)


@pytest.mark.parametrize("endpoint", ["tcp://127.0.0.1:2375", "tcp://localhost:2375", "tcp://[::1]:2375",
                                      "unix:///var/run/docker.sock", "npipe:////./pipe/docker_engine"])
def test_only_local_daemon_endpoints_are_accepted(endpoint):
    require_local_daemon(endpoint)


def test_timeout_cleanup_only_removes_the_owned_container(monkeypatch):
    calls = []

    def command(*args, **kwargs):
        calls.append(args)
        return "owned" if args[0] == "ps" else ""

    monkeypatch.setattr(validator, "docker", command)
    validator.cleanup_container("owned", "unique-run")
    assert calls[-1] == ("rm", "--force", "owned")
    with pytest.raises(ValidationError, match="container_ownership_mismatch"):
        validator.cleanup_container("unrelated", "unique-run")
    assert sum(args[0] == "rm" for args in calls) == 1
