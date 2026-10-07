# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import ctypes
import errno
import os
import platform
import sys


class Filter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint),
    ]


class Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]


def main():
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise SystemExit("This faulting-capability test requires Linux x86-64.")
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python3 without_cpuid_faulting.py shadps4 [arguments...]")

    # Return ENODEV only for arch_prctl(ARCH_SET_CPUID, 0).
    filters = (Filter * 8)(
        Filter(0x20, 0, 0, 0),
        Filter(0x15, 0, 4, 158),
        Filter(0x20, 0, 0, 16),
        Filter(0x15, 0, 2, 0x1012),
        Filter(0x20, 0, 0, 24),
        Filter(0x15, 1, 0, 0),
        Filter(0x06, 0, 0, 0x7FFF0000),
        Filter(0x06, 0, 0, 0x00050000 | errno.ENODEV),
    )
    program = Program(len(filters), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_SECCOMP")
    print("CPUID_FAULTING=forced_unavailable", flush=True)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
