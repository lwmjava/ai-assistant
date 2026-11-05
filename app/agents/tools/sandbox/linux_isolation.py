"""Linux-only kernel setup, embedded into the disposable pre-exec wrapper.

This restricts privilege and network access; it is not a filesystem sandbox.
Only native x86-64 and AArch64 ABIs are supported. Unknown ABIs fail closed.
"""

import ctypes
import errno
import os
import platform

ISOLATION_SETUP_FAILURE = "sandbox_isolation_setup_failed"


def network_filter(machine: str) -> list[tuple[int, int, int, int]]:
    """Build classic BPF: validate ABI, reject network and alternate entry points."""
    if machine == "x86_64":
        arch = 0xC000003E
        denied = (*range(41, 56), 56, 57, 58, 101, 165, 166, 272, 288,
                  299, 307, 308, 310, 311, 321, 323, 425, 426, 427, 435, 438)
    elif machine == "aarch64":
        arch = 0xC00000B7
        denied = (39, 40, 97, 117, *range(198, 213), 220, 242, 243,
                  268, 269, 270, 271, 280, 282, 425, 426, 427, 435, 438)
    else:
        raise RuntimeError(ISOLATION_SETUP_FAILURE)
    # seccomp_data: nr at offset 0; arch at offset 4. A mismatched ABI is fatal.
    instructions = [
        (0x20, 0, 0, 4), (0x15, 1, 0, arch), (0x06, 0, 0, 0x80000000),
        (0x20, 0, 0, 0),
        # Also rejects x32 and negative syscall numbers, and unknown future ABIs.
        (0x35, 0, 1, 512), (0x06, 0, 0, 0x00050000 | errno.EPERM),
    ]
    for number in sorted(set(denied)):
        instructions.extend([(0x15, 0, 1, number), (0x06, 0, 0, 0x00050000 | errno.EPERM)])
    instructions.append((0x06, 0, 0, 0x7FFF0000))
    return instructions


def install_linux_isolation() -> None:
    """Reject privileged callers and install a filter inherited across execve."""
    if 0 in getattr(os, "getresuid")():
        raise RuntimeError(ISOLATION_SETUP_FAILURE)

    class CapHeader(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class CapData(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
                    ("inheritable", ctypes.c_uint32)]

    class SockFilter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                    ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]

    class SockFprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(SockFilter))]

    libc = ctypes.CDLL(None, use_errno=True)
    header = CapHeader(0x20080522, 0)
    capabilities = (CapData * 2)()
    if libc.capget(ctypes.byref(header), ctypes.byref(capabilities)) != 0:
        raise RuntimeError(ISOLATION_SETUP_FAILURE)
    if any(cap.effective or cap.permitted or cap.inheritable for cap in capabilities):
        raise RuntimeError(ISOLATION_SETUP_FAILURE)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                          ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise RuntimeError(ISOLATION_SETUP_FAILURE)
    instructions = network_filter(platform.machine())
    filters = (SockFilter * len(instructions))(*(SockFilter(*row) for row in instructions))
    program = SockFprog(len(instructions), filters)
    pointer = ctypes.cast(ctypes.pointer(program), ctypes.c_void_p).value
    if libc.prctl(22, 2, pointer, 0, 0) != 0:  # PR_SET_SECCOMP / SECCOMP_MODE_FILTER
        raise RuntimeError(ISOLATION_SETUP_FAILURE)
