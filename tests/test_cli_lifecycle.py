"""部署命令使用显式项目与临时配置，不操作真实容器。"""

import subprocess
from pathlib import Path

import pytest

from app.cli import main


@pytest.fixture
def deployment(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = tmp_path / "deployment.env"
    env_file.write_text("POSTGRES_PASSWORD=sentinel-password\n", encoding="utf-8")
    return ["--project-name", "cli-test", "--compose-file", str(compose), "--env-file", str(env_file)]


@pytest.mark.parametrize("command,expected", [
    ("start", ["up", "--detach", "--wait", "--wait-timeout", "300"]),
    ("stop", ["stop", "--timeout", "30"]),
    ("status", ["ps", "--all"]),
    ("logs", ["logs", "--no-color", "--tail", "100"]),
])
def test_explicit_deployment_commands(monkeypatch, deployment, command, expected, capsys):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "lifecycle-marker\n", "")

    monkeypatch.setenv("COMPOSE_PROFILES", "unrequested")
    monkeypatch.setenv("COMPOSE_FILE", "another.yml")
    monkeypatch.setattr(subprocess, "run", run)
    assert main([command, *deployment]) == 0
    argv, kwargs = calls[0]
    assert argv[:2] == ["docker", "compose"]
    assert argv[-len(expected):] == expected
    assert argv[argv.index("--project-name") + 1] == "cli-test"
    assert Path(argv[argv.index("--env-file") + 1]).is_absolute()
    assert Path(argv[argv.index("--file") + 1]).is_absolute()
    assert "COMPOSE_PROFILES" not in kwargs["env"]
    assert "COMPOSE_FILE" not in kwargs["env"]
    assert kwargs.get("shell", False) is False
    assert kwargs["timeout"] > 0
    if command in ("status", "logs"):
        assert "lifecycle-marker" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["start", "stop", "status", "logs"])
def test_project_and_environment_are_required(command):
    with pytest.raises(SystemExit) as error:
        main([command])
    assert error.value.code == 2


@pytest.mark.parametrize("option,value", [
    ("--project-name", "../other"), ("--project-name", "-other"),
    ("--timeout", "0"), ("--timeout", "-1"), ("--tail", "0"),
])
def test_invalid_options_do_not_execute(monkeypatch, deployment, option, value):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid input launched Docker")

    monkeypatch.setattr(subprocess, "run", unexpected)
    with pytest.raises(SystemExit) as error:
        main(["logs", *deployment, f"{option}={value}"])
    assert error.value.code == 2


def test_missing_file_is_readable_and_does_not_execute(monkeypatch, deployment, capsys):
    Path(deployment[-1]).unlink()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("Docker launched"))
    assert main(["status", *deployment]) == 1
    assert "配置文件" in capsys.readouterr().err


@pytest.mark.parametrize("failure,code,message", [
    (FileNotFoundError("sentinel-private-path"), 1, "Docker"),
    (PermissionError("sentinel-private-path"), 1, "权限"),
    (subprocess.TimeoutExpired("secret-command", 1, output=b"sentinel-password"), 124, "status"),
    (KeyboardInterrupt(), 130, "status"),
])
def test_execution_failure_is_safe(monkeypatch, deployment, capsys, failure, code, message):
    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", run)
    assert main(["start", *deployment]) == code
    output = capsys.readouterr()
    assert message in output.err
    assert "sentinel" not in output.out + output.err
    assert "secret-command" not in output.err


def test_compose_failure_preserves_code_without_leaking(monkeypatch, deployment, capsys):
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 17, "sentinel-password", "private-token"))
    assert main(["start", *deployment]) == 17
    output = capsys.readouterr()
    assert "17" in output.err
    assert "sentinel-password" not in output.out + output.err
    assert "private-token" not in output.out + output.err


def test_successful_logs_hide_credentials_without_settings(monkeypatch, deployment, capsys):
    monkeypatch.setenv("JWT_SECRET_KEY", "another-secret-value")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(
        argv, 0,
        "ready-marker\nsentinel-password\nanother-secret-value\npassword=unknown-value\n"
        "postgresql://user:unknown@db/app\nAuthorization: Bearer unknown\n", "private-stderr"))
    assert main(["logs", *deployment]) == 0
    output = capsys.readouterr()
    assert "ready-marker" in output.out
    for secret in ("sentinel-password", "another-secret-value", "unknown", "private-stderr"):
        assert secret not in output.out + output.err


def test_overrides_are_bounded(monkeypatch, deployment):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert main(["logs", *deployment, "--tail", "7", "--timeout", "9"]) == 0
    assert calls[0][0][-1] == "7"
    assert calls[0][1]["timeout"] == 9


def test_interpolated_and_overridden_secrets_are_hidden(monkeypatch, deployment, capsys):
    Path(deployment[-1]).write_text(
        "BASE=review-synthetic-value\nPOSTGRES_PASSWORD=${BASE}\nJWT_SECRET_KEY=file-secret-value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JWT_SECRET_KEY", "process-secret-value")
    monkeypatch.setenv("BASE", "host-base-value")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(
        argv, 0, "ready-marker\nreview-synthetic-value\nhost-base-value\nfile-secret-value\nprocess-secret-value", ""))
    assert main(["logs", *deployment]) == 0
    output = capsys.readouterr().out
    assert "ready-marker" in output
    for secret in ("review-synthetic-value", "host-base-value", "file-secret-value", "process-secret-value"):
        assert secret not in output


def test_compose_unbraced_interpolation_is_hidden(monkeypatch, deployment, capsys):
    Path(deployment[-1]).write_text("POSTGRES_PASSWORD=$BASE\n", encoding="utf-8")
    monkeypatch.setenv("BASE", "unbraced-review-value")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(
        argv, 0, "ready-marker\nunbraced-review-value", ""))
    assert main(["logs", *deployment]) == 0
    assert "unbraced-review-value" not in capsys.readouterr().out
