#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ghost intro-to-stall guest worker evidence, no source/build/config changes.

Collects LWP scheduler states and bounded GDB snapshots of Game:Main, JobWorker,
GpuInterrupt, Wwise and GPU worker threads before/after the frame plateau.
Identifies an existing Ghost process by argv plus executable basename even
when a previous trial's ELF has become '(deleted)' after rollback.
Never kills the game, invokes guest functions or changes its memory.
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
EXE = HOME / "Applications/shadps4/shadps4"
STATE = HOME / ".local/state/shadps4-playtest-logs"
BASELINE_SHA = "f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f"
STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = HOME / f"ghost-worker-transition-{STAMP}.tar.gz"
WORK = HOME / ".cache" / f"ghost-worker-transition-{STAMP}"
TRACE = re.compile(r"GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)")
STATUS = []
TARGET_NAMES = ("Game:Main", "JobWorker", "GpuInterrupt", "AK::Event", "shadPS4:Gpu", "MovieDecod")

def say(msg):
    msg = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    STATUS.append(msg)
    print(msg, flush=True)

def ghost_identity(exe_name: str, argv: bytes) -> bool:
    # This also matches /proc/PID/exe pointing to a previous candidate (deleted).
    return Path(exe_name.removesuffix(" (deleted)")).name.lower() == "shadps4" and (
        b"CUSA11456" in argv or b"Ghost of Tsushima.ps4" in argv
    )

def live_ghosts():
    result = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            exe = os.readlink(proc / "exe")
            argv = (proc / "cmdline").read_bytes()
            if ghost_identity(exe, argv):
                result.append(int(proc.name))
        except (OSError, PermissionError, ValueError):
            continue
    return result

def find_session(pid: int):
    matches = []
    for meta in STATE.glob("*Ghost_of_Tsushima.ps4/session.meta"):
        try:
            contents = meta.read_text(errors="replace")
            m = re.search(r"(?m)^launcher_pid=(\d+)$", contents)
            if m and int(m.group(1)) == pid:
                matches.append((meta.stat().st_mtime, meta.parent))
        except (OSError, ValueError):
            continue
    return max(matches, default=(0, None))[1]

def read_runtime(path: Path, limit: int = 12000000):
    try:
        with path.open("rb") as f:
            size = path.stat().st_size
            if size > limit:
                f.seek(-limit, os.SEEK_END)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""

def frame_state(path: Path):
    matches = TRACE.findall(read_runtime(path))
    return tuple(map(int, matches[-1])) if matches else None

def proc_task_stats(pid: int):
    result = {}
    for task in Path(f"/proc/{pid}/task").iterdir():
        if not task.name.isdigit():
            continue
        try:
            name = (task / "comm").read_text().strip()
            raw = (task / "stat").read_text().rsplit(") ", 1)[1].split()
            # Linux /proc PID stat fields 14/15 are user/system time.
            ticks = int(raw[11]) + int(raw[12])
            state = raw[0]
            wchan = (task / "wchan").read_text().strip()
            result[int(task.name)] = (name, ticks, state, wchan)
        except (OSError, ValueError, IndexError):
            pass
    return result

def capture_scheduler(pid: int, label: str, runtime: Path):
    before = proc_task_stats(pid)
    time.sleep(1.0)
    after = proc_task_stats(pid)
    lines = [f"snapshot={label}", f"frames={frame_state(runtime)}",
             "tid cpu_ticks_per_second state wchan name"]
    rows = []
    for tid, (name, ticks, state, wchan) in after.items():
        old = before.get(tid)
        delta = ticks - old[1] if old else 0
        rows.append((delta,tid,name,state,wchan))
    rows.sort(reverse=True)
    for delta, tid, name, state, wchan in rows:
        lines.append(f"{tid} {delta} {state} {wchan} {name}")
    (WORK / f"{label}-thread-cpu.txt").write_text("\n".join(lines) + "\n")
    busy = [(name, tid, delta) for delta,tid,name,_,_ in rows
            if delta > 15 and any(name.startswith(n) for n in TARGET_NAMES)]
    say(f"{label}: snapshot {len(rows)} threads, busy targets={busy[:12]}")
    return after

def gdb_script(tids: set[int]) -> str:
    ids = sorted(tids)
    return (
        "set pagination off\n"
        "set confirm off\n"
        "set print thread-events off\n"
        "set may-call-functions off\n"
        "handle SIGSEGV nostop noprint pass\n"
        "handle SIGILL nostop noprint pass\n"
        "handle SIGBUS nostop noprint pass\n"
        "python\n"
        "import gdb\n"
        f"target_tids = {ids!r}\n"
        "def check(command):\n"
        "    try:\n"
        "        gdb.execute(command)\n"
        "    except Exception as ex:\n"
        "        print('GDB_READ_ERROR', command, ex)\n"
        "print('GHOST_GDB_BEGIN', flush=True)\n"
        "for thread in gdb.selected_inferior().threads():\n"
        "    name = thread.name or ''\n"
        "    if thread.ptid[1] not in target_tids:\n"
        "        continue\n"
        "    thread.switch()\n"
        "    print('GHOST_WORKER_THREAD', thread.ptid, name, flush=True)\n"
        "    check('info registers rip rsp rbp rax rbx rcx rdx rsi rdi r12 r13 r14 r15 eflags')\n"
        "    check('x/18i $pc-24')\n"
        "    check('bt 12')\n"
        "print('GHOST_GDB_END', flush=True)\n"
        "end\n"
        "detach\n"
    )

def capture_gdb(pid: int, label: str, task_info: dict):
    if not shutil.which("gdb"):
        say(f"{label}: no GDB found; only /proc evidence collected")
        return
    relevant = [tid for tid,(name,_,_,_) in task_info.items()
                if any(name.startswith(n) for n in TARGET_NAMES)]
    (WORK / f"{label}-target-tids.txt").write_text(str(sorted(relevant)) + "\n")
    # Keep capture lightweight even if a process has hundreds of unrelated LWPs.
    relevant = sorted(relevant)[:48]
    if not relevant:
        say(f"{label}: no game/job/GPU/audio targets present")
        return
    script = WORK / f"{label}.gdb"
    script.write_text(gdb_script(set(relevant)))
    outfile = WORK / f"{label}-gdb.txt"
    command = ["gdb", "-q", "-nx", "-nh", "-batch",
               "-iex", "set debuginfod enabled off",
               "-iex", "set auto-load safe-path /dev/null",
               "-p", str(pid), "-x", str(script)]
    say(f"{label}: bounded read-only GDB snapshot of {len(relevant)} threads")
    try:
        with outfile.open("w") as stream:
            proc = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                  timeout=25)
        completed = "GHOST_GDB_END" in outfile.read_text(errors="replace")
        say(f"{label}: GDB rc={proc.returncode}, completed={completed}")
    except subprocess.TimeoutExpired:
        say(f"{label}: debugger exceeded 25 seconds, no emulator kill attempted")
        # A ptrace stop may remain if the debugger was force-terminated.
        try:
            status = Path(f"/proc/{pid}/status").read_text(errors="replace")
            if re.search(r"(?m)^State:\s+[tT]\b", status):
                os.kill(pid, signal.SIGCONT)
        except (OSError, PermissionError):
            pass

def collect(pid: int, label: str, runtime: Path):
    say(f"Capturing {label}; current frame state={frame_state(runtime)}")
    info = capture_scheduler(pid, label, runtime)
    if pid in live_ghosts():
        capture_gdb(pid, label, info)
    (WORK / f"{label}-trace.txt").write_text(str(frame_state(runtime)) + "\n")

def selftest():
    assert ghost_identity("/home/chreece/Applications/shadps4/shadps4 (deleted)",
                          b"shadps4\0-g\0CUSA11456\0")
    assert ghost_identity("/home/chreece/Applications/shadps4/shadps4",
                          b"/mnt/roms-all/ps4/Ghost of Tsushima.ps4")
    assert not ghost_identity("/bin/firefox", b"CUSA11456")
    assert not ghost_identity("/a/shadps4", b"Red Dead Redemption.ps4")
    assert TRACE.search("GHOST_TRACE vblank=2520 guest_flips=551 pending=0 queued=0"
                       ).groups() == ("2520","551","0","0")
    commands = gdb_script({100,200})
    assert "GHOST_GDB_END" in commands and "detach" in commands
    assert "thread.ptid[1] not in target_tids" in commands
    assert "set may-call-functions off" in commands
    print("SELFTEST PASS: process identity, trace counters, GDB read-only commands; "
          "no process attached")

def main():
    if "--self-test" in sys.argv:
        selftest()
        return
    WORK.mkdir(parents=True, exist_ok=True)
    session = None
    pid = None
    runtime = None
    try:
        if not EXE.is_file():
            say("Installed emulator missing; no attach")
            return
        installed_sha = hashlib.sha256(EXE.read_bytes()).hexdigest()
        (WORK / "installed-sha256.txt").write_text(installed_sha + "\n")
        if installed_sha != BASELINE_SHA:
            say(f"Installed binary hash changed: {installed_sha}; refusing attach")
            return
        choices = live_ghosts()
        if len(choices) > 1:
            say(f"Multiple Ghost processes {choices}; stop extras normally first")
            return
        if not choices:
            say("ARMED: launch Ghost through Moonlight -> ES-DE.")
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                choices = live_ghosts()
                if len(choices) > 1:
                    say("More than one Ghost process detected; refusing to attach")
                    return
                if len(choices) == 1:
                    break
                time.sleep(1)
        if len(choices) != 1:
            say("No running Ghost detected within ten minutes")
            return
        pid = choices[0]
        session = find_session(pid)
        if session is None:
            say(f"Ghost PID={pid}, no matching session log; cannot monitor flips")
            return
        runtime = session / "runtime.log"
        shutil.copy2(session / "session.meta", WORK / "session.meta")
        (WORK / "guest-pid.txt").write_text(str(pid) + "\n")
        say(f"Ghost PID={pid}, current state={frame_state(runtime)}")
        previous_flips = None
        flip_changed = time.monotonic()
        pre_done = False
        deadline = time.monotonic() + 155
        while time.monotonic() < deadline and pid in live_ghosts():
            state = frame_state(runtime)
            now = time.monotonic()
            if state:
                vblank, flips, pending, queued = state
                if previous_flips != flips:
                    previous_flips = flips
                    flip_changed = now
                if not pre_done and 350 <= flips < 550:
                    collect(pid, "late-intro", runtime)
                    pre_done = True
                    # Re-read after debugger pause before assessing a plateau.
                    next_state = frame_state(runtime)
                    if next_state:
                        previous_flips = next_state[1]
                    flip_changed = time.monotonic()
                if flips >= 500 and pending == 0 and queued == 0 and now-flip_changed >= 12:
                    say(f"Confirmed plateau at {flips} flips, vblank={vblank}")
                    collect(pid, "stall", runtime)
                    time.sleep(12)
                    if pid in live_ghosts():
                        collect(pid, "stall-repeat", runtime)
                    break
            time.sleep(0.5)
        else:
            say("Game ended, or no plateau captured within observation window")
        say("CAPTURE COMPLETE. If Ghost is still running, exit normally via gamepad.")
    finally:
        if runtime is not None:
            raw = read_runtime(runtime)
            (WORK / "runtime.log").write_text(raw)
            matches = TRACE.findall(raw)
            (WORK / "summary.txt").write_text(
                f"pid={pid}\ntrace_count={len(matches)}\n"
                f"max_guest_flips={max((int(m[1]) for m in matches), default=0)}\n"
                f"last_trace={matches[-1] if matches else None}\n"
                f"assertions={raw.count('Assertion Failed!')}\n"
                f"mip_copy_executed={raw.count('GHOST_MIP_COPY mips=')}\n"
            )
        (WORK / "status.txt").write_text("\n".join(STATUS) + "\n")
        with tarfile.open(OUT, "w:gz") as t:
            t.add(WORK, arcname=WORK.name)
        say(f"ARCHIVE={OUT}")
        say("Read-only capture; no build, settings edits, emulator termination or SSH changes.")

if __name__ == "__main__":
    main()
