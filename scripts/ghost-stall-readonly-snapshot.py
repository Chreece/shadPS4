#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only 2-point Ghost job-queue/GDB snapshot after intro stalls.

Capture only the shadPS4 PID supplied by our unattended runner, never mutate
guest memory or CPU affinity, never continue, step or set a breakpoint.
Use normal GDB attach -> frozen registers, guest queue memory, selected thread
stacks -> detach, to distinguish busy guest main from waiting job workers.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

MAX_SNAPSHOT_SECONDS = 21
GDB_COMMANDS = r"""set pagination off
set confirm off
set print thread-events off
set auto-load safe-path /dev/null
set debuginfod enabled off
set may-call-functions off
python
import gdb
import traceback
try:
    inf = gdb.selected_inferior()
    print("GHOST_STALL_SNAPSHOT_BEGIN", inf.pid, flush=True)
    def run(command):
        try:
            print("READ", command, flush=True)
            print(gdb.execute(command, to_string=True), flush=True)
        except Exception as exc:
            print("READ_FAILED", command, repr(exc), flush=True)
    run("x/20gx 0x3fb6500")
    run("x/16gx 0x3fb6580")
    run("x/16gx 0x2c7fe20")
    run("x/32i 0xc03560")
    run("x/30i 0xc03bb0")
    active = [t for t in inf.threads() if t.is_valid()]
    print("THREADS_TOTAL", len(active), flush=True)
    selected = []
    for t in active:
        nm = t.name or ""
        if nm == "Game:Main":
            selected.append((0, t, nm))
        elif nm.startswith("JobWorker"):
            selected.append((1, t, nm))
        elif "MovieDecoder" in nm:
            selected.append((2, t, nm))
        elif "GpuComm" in nm or "GpuSche" in nm:
            selected.append((3, t, nm))
    selected.sort(key=lambda entry: (entry[0], entry[1].ptid[1]))
    counts = {}
    for priority, t, nm in selected:
        if counts.get(priority, 0) >= (2 if priority == 0 else 5 if priority == 1 else 1):
            continue
        counts[priority] = counts.get(priority, 0) + 1
        t.switch()
        print("=== THREAD", nm, "ptid", t.ptid, "===", flush=True)
        run("info registers rip rax rbx rcx rdx rsi rdi r8 r9 r10 r11 r12 r13 r14 r15 rsp rbp")
        run("x/20i $pc-32")
        run("bt 8")
        print("=== THREAD_END", flush=True)
    print("GHOST_STALL_SNAPSHOT_END", flush=True)
except BaseException:
    print("GHOST_STALL_SNAPSHOT_PYTHON_EXCEPTION", traceback.format_exc(), flush=True)
end
detach
quit
"""


def owned_emulator(pid: int) -> tuple[str, str]:
    if pid < 1:
        raise RuntimeError("PID must be positive")
    proc = Path("/proc") / str(pid)
    if proc.stat().st_uid != os.getuid():
        raise RuntimeError("PID belongs to another Unix user")
    exe = os.readlink(proc / "exe").removesuffix(" (deleted)")
    cmdline = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    if Path(exe).name.lower() != "shadps4":
        raise RuntimeError("Target PID is not the shadPS4 executable")
    if "Ghost of Tsushima.ps4" not in cmdline and "CUSA11456" not in cmdline:
        raise RuntimeError("Target PID is not the pinned Ghost game")
    return exe, cmdline


def snapshot(pid: int, workdir: Path, label: str) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    output = workdir / ("gdb-guest-stall-" + label + ".log")
    commands = workdir / ("gdb-guest-stall-" + label + ".gdb")
    state = workdir / ("gdb-guest-stall-" + label + ".status")
    if shutil_which_gdb() is None:
        state.write_text("GDB_UNAVAILABLE\n")
        return
    try:
        exe, cmdline = owned_emulator(pid)
        (workdir / ("gdb-target-" + label + ".txt")).write_text(
            "pid=" + str(pid) + "\nexe=" + exe + "\ncmdline=" + cmdline + "\n"
        )
    except (OSError, ValueError, RuntimeError) as exc:
        state.write_text("TARGET_REJECTED " + repr(exc) + "\n")
        return
    commands.write_text(GDB_COMMANDS)
    argv = ["gdb", "-q", "-nx", "-batch", "-p", str(pid), "-x", str(commands)]
    started = time.monotonic()
    with output.open("w") as sink:
        proc = subprocess.Popen(argv, stdout=sink, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        timeout = False
        try:
            proc.wait(timeout=14)
        except subprocess.TimeoutExpired:
            timeout = True
            # Ask GDB itself to detach/quit first, allowing the guest to resume.
            os.kill(proc.pid, signal.SIGINT)
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=3)
        state.write_text(
            f"gdb_pid={proc.pid}\nreturncode={proc.returncode}\n"
            f"timed_out={timeout}\nseconds={time.monotonic()-started:.2f}\n"
            f"begin_marker={'GHOST_STALL_SNAPSHOT_BEGIN' in output.read_text(errors='replace')}\n"
            f"end_marker={'GHOST_STALL_SNAPSHOT_END' in output.read_text(errors='replace')}\n"
        )


def shutil_which_gdb():
    from shutil import which
    return which("gdb")


def selftest() -> None:
    assert "0x3fb6550" not in GDB_COMMANDS or GDB_COMMANDS.count("x/") >= 2
    assert "x/20gx 0x3fb6500" in GDB_COMMANDS
    assert "x/16gx 0x3fb6580" in GDB_COMMANDS
    assert 'nm == "Game:Main"' in GDB_COMMANDS
    assert 'nm.startswith("JobWorker")' in GDB_COMMANDS
    assert "run(\"bt 8\")" in GDB_COMMANDS
    assert "\ndetach\nquit\n" in GDB_COMMANDS
    # A Python loop inside GDB_COMMANDS legitimately uses "continue". Only
    # reject a top-level GDB resume command, not Python flow control.
    assert not re.search(r"(?m)^continue[ ]*$", GDB_COMMANDS)
    assert 'run("continue")' not in GDB_COMMANDS
    assert "set may-call-functions off" in GDB_COMMANDS
    assert "thread apply all" not in GDB_COMMANDS
    # A debugger must not delay the enclosing 180-second child trial.
    assert MAX_SNAPSHOT_SECONDS <= 22
    print("SELFTEST PASS: read-only pending queue, guest-hotloop disassembly, "
          "bounded worker stacks, no continue/step/call; debugger detaches")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--label", choices=("A", "B"), required=True)
    args = ap.parse_args()
    snapshot(args.pid, args.outdir, args.label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
