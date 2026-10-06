"""在独立进程里运行用户脚本，并在超时或失败时结束整个进程树。

Windows 用 Job Object。子进程由 CreateProcess 挂起创建，绑定后再 ResumeThread。
Popen 的挂起标志会在返回前关掉线程句柄，进程无法恢复。
Unix 用新会话和进程组，资源限制由包装脚本在 execv 之前设置。
"""

from __future__ import annotations

import locale
import logging
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass

from app.agents.tools.sandbox.base import SandboxConfig
from app.agents.tools.sandbox.runtime import unix_wrapper_source

logger = logging.getLogger(__name__)

ISOLATION_FAILURE = "无法建立进程隔离"

_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_STARTF_USESTDHANDLES = 0x00000100
_HANDLE_FLAG_INHERIT = 0x00000001
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9
_WAIT_TIMEOUT = 0x00000102
_STILL_ACTIVE = 259
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass
class ProcessOutcome:
    """一次子进程执行的原始结果。隔离失败时 isolation_error 非空，用户代码没有运行。"""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    timed_out: bool = False
    pid: int = 0
    isolation_error: str = ""


def process_alive(pid: int) -> bool:
    """进程是否仍在运行。pid 无效时视为已结束。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def windows_user_command(script_path: str) -> str:
    """Windows 上启动的是解释器加用户脚本，不是带 import os 的 -c 包装。"""
    return subprocess.list2cmdline([sys.executable, script_path])


def run_script(
    script_path: str,
    *,
    cwd: str,
    env: dict[str, str],
    timeout: float,
    config: SandboxConfig,
) -> ProcessOutcome:
    """运行用户脚本。超时或失败时结束这次启动的进程树。"""
    if os.name == "nt":
        return _run_windows(script_path, cwd=cwd, env=env, timeout=timeout)
    return _run_unix(script_path, cwd=cwd, env=env, timeout=timeout, config=config)


def terminate_contained_tree(parent_command: list[str], *, cwd: str) -> tuple[int, int]:
    """启动一个会再生子进程的父进程，放进与正式执行相同的结束机制，然后结束整棵树。

    返回父进程和它拉起的子进程 pid。调用方断言两者都已结束。
    仅用于测试。用户代码路径不走这里。
    """
    if os.name == "nt":
        return _terminate_windows_tree(parent_command, cwd=cwd)
    return _terminate_unix_tree(parent_command, cwd=cwd)


def _decode(data: bytes) -> str:
    encoding = locale.getencoding()
    return data.decode(encoding, errors="replace")


def _run_unix(
    script_path: str,
    *,
    cwd: str,
    env: dict[str, str],
    timeout: float,
    config: SandboxConfig,
) -> ProcessOutcome:
    wrapper_path = os.path.join(cwd, "_sandbox_limit.py")
    with open(wrapper_path, "w", encoding="utf-8") as handle:
        handle.write(unix_wrapper_source(script_path, config))
    proc = subprocess.Popen(
        [sys.executable, wrapper_path],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(proc.pid)
        stdout, stderr = proc.communicate()
    return ProcessOutcome(
        stdout=_decode(stdout or b""),
        stderr=_decode(stderr or b""),
        exit_code=proc.returncode if proc.returncode is not None else -1,
        timed_out=timed_out,
        pid=proc.pid,
    )


def _kill_process_group(pid: int) -> None:
    try:
        getattr(os, "killpg")(pid, getattr(signal, "SIGKILL"))
    except OSError:
        try:
            os.kill(pid, getattr(signal, "SIGKILL"))
        except OSError:
            logger.warning("结束沙箱进程组失败")


def _terminate_unix_tree(parent_command: list[str], *, cwd: str) -> tuple[int, int]:
    proc = subprocess.Popen(
        parent_command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        child_pid = _wait_pid_file(parent_command)
        _kill_process_group(proc.pid)
        proc.wait(timeout=5)
        return proc.pid, child_pid
    finally:
        if proc.poll() is None:
            _kill_process_group(proc.pid)
            proc.wait(timeout=5)


def _windows_kernel32():
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)


def _run_windows(script_path: str, *, cwd: str, env: dict[str, str], timeout: float) -> ProcessOutcome:
    import ctypes
    from ctypes import wintypes

    kernel32 = _windows_kernel32()
    _bind_win32(kernel32)
    job = _create_job(kernel32)
    if not job:
        logger.warning("沙箱无法创建进程隔离，错误码 %s", ctypes.get_last_error())
        return ProcessOutcome(isolation_error=ISOLATION_FAILURE)

    stdout_read = stdout_write = stderr_read = stderr_write = stdin_handle = None
    process = thread = None
    try:
        stdout_read, stdout_write = _create_pipe(kernel32)
        stderr_read, stderr_write = _create_pipe(kernel32)
        stdin_handle = _open_nul(kernel32)
        command = windows_user_command(script_path)
        process, thread, pid = _create_suspended(
            kernel32,
            command=command,
            cwd=cwd,
            env=env,
            stdin_handle=stdin_handle,
            stdout_handle=stdout_write,
            stderr_handle=stderr_write,
        )
        if not process:
            logger.warning("沙箱无法启动代码进程，错误码 %s", ctypes.get_last_error())
            return ProcessOutcome(isolation_error=ISOLATION_FAILURE, pid=0)
        if not kernel32.AssignProcessToJobObject(job, process):
            logger.warning("沙箱无法绑定进程隔离，错误码 %s", ctypes.get_last_error())
            kernel32.TerminateProcess(process, 1)
            return ProcessOutcome(isolation_error=ISOLATION_FAILURE, pid=pid)
        kernel32.CloseHandle(stdout_write)
        kernel32.CloseHandle(stderr_write)
        stdout_write = stderr_write = None
        if kernel32.ResumeThread(thread) == 0xFFFFFFFF:
            kernel32.TerminateProcess(process, 1)
            return ProcessOutcome(isolation_error=ISOLATION_FAILURE, pid=pid)
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        reader_stdout, stdout_read = stdout_read, None
        reader_stderr, stderr_read = stderr_read, None
        readers = [
            threading.Thread(target=_read_handle, args=(kernel32, reader_stdout, stdout_chunks)),
            threading.Thread(target=_read_handle, args=(kernel32, reader_stderr, stderr_chunks)),
        ]
        for reader in readers:
            reader.start()
        waited = kernel32.WaitForSingleObject(process, int(timeout * 1000))
        timed_out = waited == _WAIT_TIMEOUT
        if timed_out:
            kernel32.TerminateJobObject(job, 1)
            kernel32.WaitForSingleObject(process, 5000)
        for reader in readers:
            reader.join(timeout=5)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(process, ctypes.byref(code))
        exit_code = int(code.value)
        if exit_code == _STILL_ACTIVE:
            exit_code = -1
        return ProcessOutcome(
            stdout=_decode(b"".join(stdout_chunks)),
            stderr=_decode(b"".join(stderr_chunks)),
            exit_code=exit_code,
            timed_out=timed_out,
            pid=pid,
        )
    finally:
        for handle in (stdout_write, stderr_write, stdout_read, stderr_read, stdin_handle, thread, process):
            if handle:
                kernel32.CloseHandle(handle)
        if job:
            kernel32.CloseHandle(job)


def _terminate_windows_tree(parent_command: list[str], *, cwd: str) -> tuple[int, int]:
    kernel32 = _windows_kernel32()
    _bind_win32(kernel32)
    job = _create_job(kernel32)
    if not job:
        raise RuntimeError(ISOLATION_FAILURE)
    process = thread = None
    try:
        command = subprocess.list2cmdline(parent_command)
        process, thread, pid = _create_suspended(
            kernel32,
            command=command,
            cwd=cwd,
            env=None,
            stdin_handle=None,
            stdout_handle=None,
            stderr_handle=None,
        )
        if not process or not kernel32.AssignProcessToJobObject(job, process):
            if process:
                kernel32.TerminateProcess(process, 1)
            raise RuntimeError(ISOLATION_FAILURE)
        if kernel32.ResumeThread(thread) == 0xFFFFFFFF:
            kernel32.TerminateProcess(process, 1)
            raise RuntimeError(ISOLATION_FAILURE)
        child_pid = _wait_pid_file(parent_command)
        kernel32.TerminateJobObject(job, 1)
        kernel32.WaitForSingleObject(process, 5000)
        return pid, child_pid
    finally:
        for handle in (thread, process, job):
            if handle:
                kernel32.CloseHandle(handle)


def _wait_pid_file(parent_command: list[str]) -> int:
    """父进程把子进程 pid 写到命令的最后一个参数。"""
    import time
    from pathlib import Path

    pid_path = Path(parent_command[-1])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if pid_path.exists():
            text = pid_path.read_text(encoding="utf-8").strip()
            if text:
                return int(text)
        time.sleep(0.05)
    raise TimeoutError("沙箱测试进程没有写下子进程编号")


def _windows_process_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = _windows_kernel32()
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return int(code.value) == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _bind_win32(kernel32) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, ctypes.c_uint]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, ctypes.c_uint]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.CreatePipe.restype = wintypes.BOOL
    kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel32.SetHandleInformation.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL


def _create_job(kernel32):
    import ctypes
    from ctypes import wintypes

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("per_process", ctypes.c_int64),
            ("per_job", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("min_ws", ctypes.c_size_t),
            ("max_ws", ctypes.c_size_t),
            ("active", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [("count", ctypes.c_uint64)] * 6

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimit),
            ("io", IoCounters),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process", ctypes.c_size_t),
            ("peak_job", ctypes.c_size_t),
        ]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = ExtendedLimit()
    info.basic.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job,
        _JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        kernel32.CloseHandle(job)
        return None
    return job


def _security_attributes():
    import ctypes
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    attributes = SecurityAttributes()
    attributes.nLength = ctypes.sizeof(attributes)
    attributes.bInheritHandle = True
    return attributes


def _create_pipe(kernel32):
    import ctypes
    from ctypes import wintypes

    read = wintypes.HANDLE()
    write = wintypes.HANDLE()
    attributes = _security_attributes()
    if not kernel32.CreatePipe(ctypes.byref(read), ctypes.byref(write), ctypes.byref(attributes), 0):
        raise OSError(ctypes.get_last_error(), "CreatePipe")
    kernel32.SetHandleInformation(read, _HANDLE_FLAG_INHERIT, 0)
    return read, write


def _open_nul(kernel32):
    import ctypes

    attributes = _security_attributes()
    handle = kernel32.CreateFileW(
        "NUL",
        _GENERIC_READ,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        ctypes.byref(attributes),
        _OPEN_EXISTING,
        0,
        None,
    )
    if not handle or int(handle) == ctypes.c_void_p(-1).value:
        return None
    return handle


def _env_block(env: dict[str, str]):
    import ctypes

    joined = "\0".join(f"{key}={value}" for key, value in env.items()) + "\0"
    return ctypes.create_unicode_buffer(joined)


def _create_suspended(
    kernel32,
    *,
    command: str,
    cwd: str,
    env: dict[str, str] | None,
    stdin_handle,
    stdout_handle,
    stderr_handle,
):
    import ctypes
    from ctypes import wintypes

    class StartupInfo(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    startup = StartupInfo()
    startup.cb = ctypes.sizeof(startup)
    flags = _CREATE_SUSPENDED | _CREATE_NO_WINDOW
    env_block = None
    if env is not None:
        env_block = _env_block(env)
        flags |= _CREATE_UNICODE_ENVIRONMENT
    if stdout_handle and stderr_handle:
        startup.dwFlags = _STARTF_USESTDHANDLES
        startup.hStdInput = stdin_handle or 0
        startup.hStdOutput = stdout_handle
        startup.hStdError = stderr_handle
    process_info = ProcessInformation()
    command_buf = ctypes.create_unicode_buffer(command)
    created = kernel32.CreateProcessW(
        None,
        command_buf,
        None,
        None,
        True if stdout_handle else False,
        flags,
        ctypes.cast(env_block, ctypes.c_void_p) if env_block is not None else None,
        cwd,
        ctypes.byref(startup),
        ctypes.byref(process_info),
    )
    if not created:
        return None, None, 0
    return process_info.hProcess, process_info.hThread, int(process_info.dwProcessId)


def _read_handle(kernel32, handle, chunks: list[bytes]) -> None:
    import ctypes
    from ctypes import wintypes

    try:
        while True:
            buffer = ctypes.create_string_buffer(4096)
            read = wintypes.DWORD()
            ok = kernel32.ReadFile(handle, buffer, 4096, ctypes.byref(read), None)
            if not ok or read.value == 0:
                break
            chunks.append(buffer.raw[: read.value])
    finally:
        kernel32.CloseHandle(handle)
