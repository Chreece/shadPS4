#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Evidence-only before/after Ghost intro transition. No emulator rebuild.

Sample the guest Game:Main, GPU command and AK event threads before and after
the video-output stall, plus the guest loop and state words seen at 0xc035d0.
Only reads live process state using bounded, detaching GDB batch commands.
Never sends a quit/kill action to the game, ES-DE, Moonlight, Sunshine or SSH.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time

HOME = Path.home()
STATE = HOME / ".local/state/shadps4-playtest-logs"
EXE = HOME / "Applications/shadps4/shadps4"
EXPECTED_SHA256 = "f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f"
NOW = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
WORK = HOME / ".cache" / ("ghost-before-after-" + NOW)
ARCHIVE = HOME / ("ghost-before-after-" + NOW + ".tar.gz")
TRACE = re.compile(r"GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)")
MESSAGES: list[str] = []

def say(message: str):
    entry = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {message}"
    print(entry, flush=True)
    MESSAGES.append(entry)

def proc_match(pid: int) -> bool:
    proc = Path("/proc") / str(pid)
    try:
        if proc.stat().st_uid != os.getuid():
            return False
        executable = os.readlink(proc / "exe").removesuffix(" (deleted)")
        args = (proc / "cmdline").read_bytes()
        return (Path(executable).name.lower() == "shadps4" and
                (b"CUSA11456" in args or b"Ghost of Tsushima.ps4" in args))
    except (OSError, PermissionError, ValueError):
        return False

def recent_session(started: float):
    choices = []
    for meta in STATE.glob("*Ghost_of_Tsushima.ps4/session.meta"):
        try:
            if meta.stat().st_mtime < started - 10:
                continue
            m = re.search(r"(?m)^launcher_pid=(\d+)$", meta.read_text(errors="replace"))
            if not m:
                continue
            pid = int(m.group(1))
            if proc_match(pid):
                choices.append((meta.stat().st_mtime, meta.parent, pid))
        except (OSError, ValueError):
            continue
    return max(choices, default=None)

def read_log(log: Path, cap: int = 10_000_000) -> str:
    try:
        with log.open("rb") as f:
            if log.stat().st_size > cap:
                f.seek(-cap, 2)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""

def latest_trace(path: Path):
    matches = TRACE.findall(read_log(path))
    return tuple(map(int, matches[-1])) if matches else None

def ticks(pid: int, tid: int):
    try:
        body = Path(f"/proc/{pid}/task/{tid}/stat").read_text().rsplit(") ", 1)[1].split()
        return int(body[11]) + int(body[12])
    except (OSError, ValueError, IndexError):
        return None

def hottest_main(pid: int) -> int | None:
    found = []
    for item in Path(f"/proc/{pid}/task").iterdir():
        try:
            if item.name.isdigit() and (item / "comm").read_text().strip() == "Game:Main":
                found.append(int(item.name))
        except OSError:
            pass
    before = {tid: ticks(pid, tid) for tid in found}
    time.sleep(0.5)
    after = {tid: ticks(pid, tid) for tid in found}
    deltas = {tid: after[tid] - before[tid] for tid in found
              if before[tid] is not None and after[tid] is not None}
    if not deltas:
        say("No live Game:Main CPU samples")
        return None
    tid = max(deltas, key=deltas.get)
    say(f"Guest main candidate TID={tid}, sampled CPU ticks={deltas}")
    return tid

def gdb_capture(pid: int, stage: str):
    target = hottest_main(pid)
    if target is None:
        (WORK / (stage + "-no-main.txt")).write_text("Game:Main not found\n")
        return False
    gdb_file = WORK / (stage + "-commands.gdb")
    capture = WORK / (stage + "-gdb.txt")
    # Every probe is read-only. Always detach before exiting GDB.
    gdb_file.write_text(
        "set pagination off\n"
        "set confirm off\n"
        "set print thread-events off\n"
        "info threads\n"
        "python\n"
        "import gdb\n"
        f"target = {target}\n"
        "def check(expression):\n"
        "    try:\n"
        "        gdb.execute(expression)\n"
        "    except Exception as exc:\n"
        "        print('READ_FAILED', expression, exc)\n"
        "check('x/64i 0xc03570')\n"
        "check('x/36wx 0x3fb6550')\n"
        "check('x/8gx 0x4517168')\n"
        "check('x/8gx 0x4541598')\n"
        "for t in gdb.selected_inferior().threads():\n"
        "    name = t.name or ''\n"
        "    wanted = (t.ptid[1] == target or name.startswith('AK::Event') or\n"
        "              name.startswith('shadPS4:Gpu') or name.startswith('MovieDecod'))\n"
        "    if not wanted:\n"
        "        continue\n"
        "    t.switch()\n"
        "    print('=== THREAD_CAPTURE ===', t.ptid, name)\n"
        "    check('info registers rip rsp rbp rax rbx rcx rdx rsi rdi r12 r13 r14 r15 eflags')\n"
        "    check('x/24i $pc')\n"
        "    check('bt 12')\n"
        "end\n"
        "detach\n"
    )
    cmd = ["gdb", "-q", "-nx", "-nh", "-batch",
           "-iex", "set debuginfod enabled off",
           "-iex", "set auto-load safe-path /dev/null",
           "-p", str(pid), "-x", str(gdb_file)]
    try:
        with capture.open("w") as f:
            result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=18)
        output = capture.read_text(errors="replace")
        passed = result.returncode == 0 and "=== THREAD_CAPTURE ===" in output
        say(f"Captured {stage}: debugger rc={result.returncode}, thread data={passed}")
        return passed
    except subprocess.TimeoutExpired:
        say(f"{stage}: GDB timed out; not killing the emulator.")
        # GDB is killed by Python on timeout; resume any lingering ptrace stop.
        try:
            os.kill(pid, signal.SIGCONT)
        except OSError:
            pass
        return False
    except Exception as exc:
        capture.write_text(f"DEBUGGER_ERROR: {type(exc).__name__}: {exc}\n")
        say(f"{stage}: debugger error {exc}")
        return False

def collect_summary(log: Path):
    raw = read_log(log)
    found = [tuple(map(int, x)) for x in TRACE.findall(raw)]
    content = [
        f"video_samples={len(found)}",
        f"peak_flips={max((r[1] for r in found), default=0)}",
        f"last_sample={found[-1] if found else None}",
        f"srt_offset_errors={raw.count('Unexpected instruction for offset computation')}",
        f"stencil_warnings={raw.count('Stencil test requires test_val')}",
        f"buffer_clamps={raw.count('Clamped size from')}",
        f"assertions={raw.count('Assertion Failed!')}",
        f"gamepad_quits={raw.count('HOST_QUIT action=accepted')}",
    ]
    (WORK / "summary.txt").write_text("\n".join(content) + "\n")
    say("; ".join(content[:4]))
    if log.is_file():
        with log.open("rb") as f:
            if log.stat().st_size > 12_000_000:
                f.seek(-12_000_000, 2)
            (WORK / "runtime.log").write_bytes(f.read())

def selftest():
    assert TRACE.search("GHOST_TRACE vblank=1440 guest_flips=551 pending=0 queued=0").groups() == ("1440", "551", "0", "0")
    assert not TRACE.search("no graphics")
    assert os.path.basename("/home/user/shadps4 (deleted)".removesuffix(" (deleted)")) == "shadps4"
    print("SELFTEST PASS: trace decoding, process basename and no debugger attach")

def main():
    if "--self-test" in sys.argv:
        selftest()
        return
    WORK.mkdir(parents=True, exist_ok=True)
    started = time.time()
    folder = None
    pid = None
    try:
        if not EXE.is_file():
            say("Expected installed emulator missing. No game was modified.")
            return
        digest = hashlib.sha256(EXE.read_bytes()).hexdigest()
        (WORK / "installed-sha256.txt").write_text(digest + "\n")
        if digest != EXPECTED_SHA256:
            say("Executable checksum does not match the proven baseline; no attaching.")
            return
        if not shutil.which("gdb"):
            say("GDB is missing; no privileged installation will be attempted.")
            return
        say("ARMED: launch Ghost through Moonlight -> ES-DE.")
        say("Automatic pre-intro, last-intro, and post-stall read-only snapshots.")
        while time.time() - started < 600:
            choice = recent_session(started)
            if choice:
                _, folder, pid = choice
                break
            time.sleep(1)
        if not folder:
            say("No new Ghost session detected within 10 minutes.")
            return
        say(f"Guest PID={pid}; session={folder}")
        (WORK / "session.meta").write_bytes((folder / "session.meta").read_bytes())
        log = folder / "runtime.log"
        milestone = 0
        previous_flips = None
        previous_flip_time = time.monotonic()
        deadline = time.monotonic() + 165
        while time.monotonic() < deadline and proc_match(pid):
            sample = latest_trace(log)
            now = time.monotonic()
            if sample:
                vblank, flips, pending, queued = sample
                if previous_flips is None or flips != previous_flips:
                    previous_flips = flips
                    previous_flip_time = now
                if milestone == 0 and flips >= 250:
                    say(f"Before intro ends: {flips} flips, vblank={vblank}")
                    gdb_capture(pid, "before-intro")
                    milestone = 1
                if milestone == 1 and flips >= 460:
                    say(f"Approaching intro transition: {flips} flips, vblank={vblank}")
                    gdb_capture(pid, "end-intro")
                    milestone = 2
                if (milestone >= 1 and milestone < 3 and flips >= 400
                    and pending == 0 and queued == 0 and
                    now - previous_flip_time >= 12):
                    say(f"Guest flips frozen: {flips}, vblank={vblank}, stagnant={now - previous_flip_time:.1f}s")
                    gdb_capture(pid, "post-stall")
                    milestone = 3
                    break
            time.sleep(1)
        collect_summary(log)
        say(f"CAPTURE COMPLETE: {milestone} stages reached. You may now exit Ghost normally.")
    finally:
        (WORK / "collector-status.txt").write_text("\n".join(MESSAGES) + "\n")
        if folder is not None and not (WORK / "runtime.log").exists():
            collect_summary(folder / "runtime.log")
        with tarfile.open(ARCHIVE, "w:gz") as f:
            f.add(WORK, arcname=WORK.name)
        say(f"ARCHIVE={ARCHIVE}")
        say("No emulator source, game data, processes, SSH, or configuration modified.")

if __name__ == "__main__":
    main()
