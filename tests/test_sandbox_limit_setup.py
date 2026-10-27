"""Limit setup errors stop execution before the user script is entered."""

import sys
from types import SimpleNamespace

import pytest

from app.agents.tools.sandbox.base import SandboxConfig
from app.agents.tools.sandbox.runtime import unix_wrapper_source


@pytest.mark.parametrize("error", [ValueError, OSError])
def test_resource_setup_error_never_executes_user_code(error):
    import builtins

    def set_limit(*args):
        raise error("private infrastructure detail")

    def forbidden_exec(*args):
        pytest.fail("resource failure must not execute user code")

    modules = {
        "os": SimpleNamespace(execv=forbidden_exec),
        "sys": sys,
        "resource": SimpleNamespace(setrlimit=set_limit, RLIMIT_CPU=0, RLIMIT_AS=1,
                                    RLIMIT_FSIZE=2, RLIMIT_NPROC=3),
    }
    namespace = dict(vars(builtins))
    namespace["__import__"] = lambda name, *args, **kwargs: modules[name]
    with pytest.raises(SystemExit, match="sandbox_resource_limit_setup_failed"):
        exec(unix_wrapper_source("user_script.py", SandboxConfig()), {"__builtins__": namespace})
