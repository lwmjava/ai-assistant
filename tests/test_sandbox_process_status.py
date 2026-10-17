"""Process state checks distinguish exited children from executing processes."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.tools.sandbox import proc


@pytest.mark.parametrize("state,expected", [("R", True), ("S", True), ("Z", False), ("X", False)])
def test_linux_process_status_with_parentheses_in_name(state, expected):
    observed = []
    fake_os = SimpleNamespace(name="posix", kill=lambda pid, sig: observed.append((pid, sig)))
    with patch.object(proc, "os", fake_os), patch.object(proc.sys, "platform", "linux"):
        with patch("pathlib.Path.read_text", return_value=f"42 (worker (child)) {state} 1 2 3"):
            assert proc.process_alive(42) is expected
    assert observed == [(42, 0)]


def test_linux_process_disappears_after_signal_probe():
    fake_os = SimpleNamespace(name="posix", kill=lambda _pid, _sig: None)
    with patch.object(proc, "os", fake_os), patch.object(proc.sys, "platform", "linux"):
        with patch("pathlib.Path.read_text", side_effect=FileNotFoundError):
            assert proc.process_alive(42) is False


@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_linux_unreadable_status_does_not_claim_exit(error):
    fake_os = SimpleNamespace(name="posix", kill=lambda _pid, _sig: None)
    with patch.object(proc, "os", fake_os), patch.object(proc.sys, "platform", "linux"):
        with patch("pathlib.Path.read_text", side_effect=error):
            assert proc.process_alive(42) is True


def test_signal_permission_denied_does_not_claim_exit():
    def deny(_pid, _sig):
        raise PermissionError

    with patch.object(proc, "os", SimpleNamespace(name="posix", kill=deny)):
        assert proc.process_alive(42) is True


def test_malformed_linux_status_does_not_claim_exit():
    fake_os = SimpleNamespace(name="posix", kill=lambda _pid, _sig: None)
    with patch.object(proc, "os", fake_os), patch.object(proc.sys, "platform", "linux"):
        with patch("pathlib.Path.read_text", return_value="malformed"):
            assert proc.process_alive(42) is True
