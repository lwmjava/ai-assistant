"""沙箱文件隔离、进程树结束和结果清理。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

from app.agents.tools.sandbox.base import KillReason, SandboxConfig
from app.agents.tools.sandbox.proc import (
    ISOLATION_FAILURE,
    process_alive,
    terminate_contained_tree,
    windows_user_command,
)
from app.agents.tools.sandbox.runtime import (
    child_environment,
    effective_timeout,
    sanitize_host_paths,
    unix_wrapper_source,
)
from app.agents.tools.sandbox.sandbox import CodeSandbox


def _run(code: str, config: SandboxConfig | None = None):
    return asyncio.run(CodeSandbox().execute(code, config))


def test_print_one_stdout():
    result = _run("print(1)")
    assert result.stdout.strip() == "1"
    assert result.public_view()["status"] == "ok"
    assert result.public_view()["stdout"].strip() == "1"


def test_fraction_and_url_survive_path_cleanup():
    result = _run('print("1/2")\nprint("https://example.com/a")')
    assert "1/2" in result.stdout
    assert "https://example.com/a" in result.stdout
    assert "<sandbox>" not in result.stdout


def test_open_and_os_open_cannot_read_outside_file(tmp_path: Path):
    nonce = f"sandbox-secret-{uuid.uuid4().hex}"
    secret = tmp_path / "secret.txt"
    secret.write_text(nonce, encoding="utf-8")
    path = str(secret)
    samples = [
        f"print(open({path!r}, encoding='utf-8').read())",
        f"fd = os.open({path!r}, os.O_RDONLY)\nprint(os.read(fd, 200))",
    ]
    for code in samples:
        result = _run(code)
        blob = "\n".join(
            [
                result.stdout,
                result.stderr,
                result.killed_detail,
                result.to_observation(),
                str(result.public_view()),
            ]
        )
        assert nonce not in blob
        assert path not in blob
        assert "user_script.py" not in blob


def test_os_name_is_not_in_user_globals():
    code = "try:\n    os.open\n    print('LEAK')\nexcept Exception:\n    print('NOOS')\n"
    result = _run(code)
    assert "LEAK" not in result.stdout
    assert "NOOS" in result.stdout


def test_child_env_drops_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SANDBOX_SENTINEL_ENV", "nope")
    env = child_environment({"SANDBOX_SENTINEL_ENV": "nope"}, windows=os.name == "nt")
    assert "SANDBOX_SENTINEL_ENV" not in env
    assert "PYTHONDONTWRITEBYTECODE" in env


def test_execute_uses_allowlisted_env(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, dict[str, str]] = {}

    def fake_run(script, *, cwd, env, timeout, config):
        del script, cwd, timeout, config
        seen["env"] = env
        from app.agents.tools.sandbox.proc import ProcessOutcome

        return ProcessOutcome(stdout="1\n", exit_code=0, pid=0)

    monkeypatch.setenv("SANDBOX_SENTINEL_ENV", "nope")
    monkeypatch.setattr("app.agents.tools.sandbox.sandbox.run_script", fake_run)
    _run("print(1)", SandboxConfig(env_vars={"SANDBOX_SENTINEL_ENV": "nope", "PATH": "kept"}))
    assert "SANDBOX_SENTINEL_ENV" not in seen["env"]
    if "PATH" in os.environ or os.name == "nt":
        assert seen["env"].get("PATH") == "kept" or "PATH" not in os.environ


def test_non_positive_timeout_uses_default():
    assert effective_timeout(0) == 30
    assert effective_timeout(-1) == 30
    assert effective_timeout(1) == 1


def test_unix_wrapper_execs_script_without_leaking_os():
    script = r"C:\sandbox\user_script.py" if os.name == "nt" else "/tmp/sandbox/user_script.py"
    config = SandboxConfig()
    source = unix_wrapper_source(script, config)
    assert "os.execv" in source
    assert "exec(open" not in source
    assert "RLIMIT_NPROC" in source
    assert str(config.max_memory_mb * 1024 * 1024) in source
    assert str(config.max_cpu_seconds) in source
    assert str(config.max_disk_mb * 1024 * 1024) in source
    assert repr(script) in source


def test_windows_command_is_the_user_script():
    command = windows_user_command(r"E:\work\user_script.py")
    assert "user_script.py" in command
    assert " -c " not in command
    assert sys.executable in command or Path(sys.executable).name in command


def test_sanitize_absolute_paths_only():
    raw = r"see C:\Users\a\x and C:/Users/a/y and \\server\share and \\?\C:\Users\a and /tmp/sandbox/x"
    cleaned = sanitize_host_paths(raw)
    assert "C:\\Users" not in cleaned
    assert "C:/Users" not in cleaned
    assert "server" not in cleaned or "<sandbox>" in cleaned
    assert "/tmp/sandbox" not in cleaned
    assert cleaned.count("<sandbox>") >= 4
    assert sanitize_host_paths("value 1/2 https://example.com/a") == "value 1/2 https://example.com/a"


def test_timeout_kills_direct_child():
    result = _run("while True:\n    pass\n", SandboxConfig(timeout_seconds=1))
    view = result.public_view()
    assert result.killed_by == KillReason.TIMEOUT
    assert view["status"] == "timeout"
    assert "执行超过" in view["reason"]
    assert result.child_pid > 0
    for _ in range(40):
        if not process_alive(result.child_pid):
            break
        asyncio.run(asyncio.sleep(0.05))
    assert not process_alive(result.child_pid)


def test_process_tree_terminate(tmp_path: Path):
    parent = tmp_path / "parent.py"
    pid_file = tmp_path / "child.pid"
    parent.write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    parent_pid, child_pid = terminate_contained_tree(
        [sys.executable, str(parent), str(pid_file)],
        cwd=str(tmp_path),
    )
    assert parent_pid > 0
    assert child_pid > 0
    assert not process_alive(parent_pid)
    assert not process_alive(child_pid)


def test_rmtree_failure_still_returns_stdout(monkeypatch: pytest.MonkeyPatch):
    def fake_run(script, *, cwd, env, timeout, config):
        del script, cwd, env, timeout, config
        from app.agents.tools.sandbox.proc import ProcessOutcome

        return ProcessOutcome(stdout="1\n", exit_code=0, pid=0)

    def boom(path):
        raise OSError(path)

    monkeypatch.setattr("app.agents.tools.sandbox.sandbox.run_script", fake_run)
    monkeypatch.setattr(shutil, "rmtree", boom)
    result = _run("print(1)")
    assert result.stdout.strip() == "1"


def test_job_failure_does_not_run_user_code(monkeypatch: pytest.MonkeyPatch):
    if os.name != "nt":
        pytest.skip("Job Object 只在 Windows 上创建")
    monkeypatch.setattr("app.agents.tools.sandbox.proc._create_job", lambda kernel: None)
    result = _run("print(987654)")
    view = result.public_view()
    assert view["status"] == "error"
    assert ISOLATION_FAILURE in view["reason"]
    assert "987654" not in result.stdout
    assert result.child_pid == 0


def test_truncation_notice_uses_limit():
    result = _run("print('abcdefghij')", SandboxConfig(max_output_chars=4))
    assert result.truncated
    assert "4" in result.to_observation()
    assert "{max_output_chars}" not in result.to_observation()


def test_code_sandbox_observation_strips_argument_path():
    """交给模型的代码工具观测去掉参数里的绝对路径。其它工具仍保留原文。"""
    import asyncio

    from app.agents.tools.base import Tool, ToolCall, ToolRegistry
    from app.agents.tools.builtin import default_tools

    registry = ToolRegistry(default_tools())
    sentinel = r"C:\Users\secret-host\note.txt"
    observation = asyncio.run(
        registry.run(
            ToolCall(
                name="code_sandbox",
                arguments={"code": f"print(1)\n# {sentinel}"},
            )
        )
    )
    assert "1" in observation
    assert sentinel not in observation
    assert "<sandbox>" in observation

    plain = ToolRegistry(
        [
            Tool(
                name="echo_path",
                description="原样返回",
                parameters={},
                func=lambda arguments: str(arguments.get("path", "")),
            )
        ]
    )
    kept = asyncio.run(
        plain.run(ToolCall(name="echo_path", arguments={"path": sentinel}))
    )
    assert sentinel in kept


def test_empty_code_publishes_error_result():
    import asyncio

    from app.agents.tools.builtin import code_sandbox, take_code_results

    async def run():
        text = await code_sandbox({})
        return text, take_code_results(), take_code_results()

    text, view, again = asyncio.run(run())
    assert "未提供 code" in text
    assert view == [{"status": "error", "stdout": "", "reason": "未提供 code 参数。"}]
    assert again == []


def test_code_result_contexts_do_not_mix():
    import asyncio

    from app.agents.tools.builtin import publish_code_result, take_code_results

    async def worker(label: str) -> list[dict[str, str]]:
        publish_code_result({"status": "ok", "stdout": label, "reason": ""})
        await asyncio.sleep(0.02)
        first = take_code_results()
        second = take_code_results()
        assert second == []
        return first

    async def both() -> None:
        left, right = await asyncio.gather(worker("left"), worker("right"))
        assert left == [{"status": "ok", "stdout": "left", "reason": ""}]
        assert right == [{"status": "ok", "stdout": "right", "reason": ""}]

    asyncio.run(both())


def test_pipeline_stream_emits_code_result():
    """假模型触发代码工具后，流式事件带上标准输出，观测里没有哨兵路径。"""
    import asyncio

    from app.agents.pipeline import AgentPipeline, AgentState
    from app.agents.tools.base import ToolRegistry
    from app.agents.tools.builtin import default_tools

    sentinel = r"C:\Users\secret-host\note.txt"

    class Scripted:
        model = "script"
        used = False

        async def chat(self, messages, options=None):
            content = messages[-1].content
            if "## 可用外部工具" in content and not self.used:
                self.used = True
                return (
                    '<tool_call>{"name": "code_sandbox", "arguments": {"code": '
                    + json.dumps(f"print(1)\n# {sentinel}")
                    + "}}</tool_call>"
                )
            return "完成。"

        async def stream_chat(self, messages, options=None):
            yield "完成。"

    pipeline = AgentPipeline(Scripted(), tools=ToolRegistry(default_tools()))
    state = AgentState(user_input="请执行这段代码并告诉我输出")

    async def collect():
        return [event async for event in pipeline.run_stream(state)]

    events = asyncio.run(collect())
    results = [event for event in events if event.type == "code_result"]
    assert len(results) == 1
    payload = json.loads(results[0].data)
    assert payload["status"] == "ok"
    assert payload["stdout"].strip() == "1"
    assert sentinel not in results[0].data
    assert state.code_results == [payload]
    joined = "\n".join(state.tool_results)
    assert sentinel not in joined
    assert "<sandbox>" in joined


def test_failed_code_result_keeps_stdout():
    import asyncio

    from app.agents.tools.builtin import code_sandbox, take_code_results

    sentinel = r"C:\Users\secret-host\note.txt"

    async def run():
        await code_sandbox({"code": f"print('kept')\nraise RuntimeError({sentinel!r})"})
        return take_code_results()

    view = asyncio.run(run())
    assert len(view) == 1
    assert view[0]["status"] == "error"
    assert "kept" in view[0]["stdout"]
    assert view[0]["reason"]
    assert sentinel not in view[0]["stdout"]
    assert sentinel not in view[0]["reason"]
