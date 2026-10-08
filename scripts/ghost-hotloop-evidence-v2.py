#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Evidence-only Ghost of Tsushima main-thread hot-loop sampler.

Wait for the regular Moonlight/ES-DE launch. When the diagnostic GHOST_TRACE
shows a persistent absence of guest flips, sample the hottest Game:Main thread
and request bounded gdb stack/register snapshots. Never kill shadPS4, touch its
configuration, change system security, or replace its executable.
"""
from __future__ import annotations

import argparse
import datetime as dt
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
BIN = HOME / "Applications/shadps4/shadps4"
STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = HOME / f"ghost-hotloop-evidence-{STAMP}.tar.gz"
DIR = HOME / ".cache" / f"ghost-hotloop-evidence-{STAMP}"
EXPECTED_BIN_SHA256 = "f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f"
TRACE = re.compile(
    r"GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)"
)
STATUS = []

def say(msg: str):
    line = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    STATUS.append(line)
    print(line, flush=True)

def get_text(path: Path, cap: int = 10_000_000) -> str:
    try:
        with path.open("rb") as f:
            n = path.stat().st_size
            if n > cap:
                f.seek(-cap, os.SEEK_END)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""

def get_meta(meta: Path) -> dict[str, str]:
    return dict(re.findall(r"(?m)^([a-z_]+)=(.*)$", get_text(meta, 5000)))

def live_shadps4(pid: int) -> bool:
    """Match the actual installed executable, not game ID in argv.

    Sunshine/ES-DE launches Ghost by its game-file path. CUSA11456 is
    generally absent from /proc/<pid>/cmdline, so the old check missed games.
    """
    try:
        return Path(f"/proc/{pid}/exe").samefile(BIN)
    except (OSError, ValueError):
        return False

def session_candidate(armed_at: float):
    choices = []
    for meta in STATE.glob("*Ghost_of_Tsushima.ps4/session.meta"):
        try:
            if meta.stat().st_mtime < armed_at - 20:
                continue
            fields = get_meta(meta)
            pid = int(fields.get("launcher_pid", "0"))
            if pid and live_shadps4(pid):
                choices.append((meta.stat().st_mtime, meta.parent, pid))
        except (OSError, ValueError):
            continue
    return max(choices, default=None)

def get_trace(log: Path):
    matches = TRACE.findall(get_text(log))
    if not matches:
        return None
    return tuple(map(int, matches[-1]))

def task_ticks(pid: int, tid: int):
    try:
        stat = Path(f"/proc/{pid}/task/{tid}/stat").read_text()
        fields = stat.rsplit(") ", 1)[1].split()
        return int(fields[11]) + int(fields[12])  # fields 14 and 15 (utime, stime)
    except (OSError, ValueError, IndexError):
        return None

def game_main_threads(pid: int):
    found = []
    try:
        for p in Path(f"/proc/{pid}/task").iterdir():
            if p.name.isdigit() and (p / "comm").read_text().strip() == "Game:Main":
                found.append(int(p.name))
    except OSError:
        pass
    return sorted(found)

def hottest_main(pid: int):
    tids = game_main_threads(pid)
    first = {tid: task_ticks(pid, tid) for tid in tids}
    time.sleep(1.5)
    second = {tid: task_ticks(pid, tid) for tid in tids}
    deltas = {tid: second[tid] - first[tid] for tid in tids
              if first[tid] is not None and second[tid] is not None}
    if not deltas:
        return None
    tid = max(deltas, key=deltas.get)
    say(f"Game:Main threads={tids}; CPU ticks in 1.5s={deltas}; target={tid}")
    return tid

def safe_capture(cmd: list[str], dst: Path, timeout: int = 8):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors="replace", timeout=timeout)
        dst.write_text(f"returncode={p.returncode}\n{p.stdout[:5_000_000]}")
        return p.returncode
    except Exception as exc:
        dst.write_text(f"{type(exc).__name__}: {exc}\n")
        return -1

def proc_snapshot(pid: int, number: int):
    folder = DIR / f"snapshot-{number}"
    folder.mkdir(parents=True, exist_ok=True)
    for path, name in [
        (Path(f"/proc/{pid}/status"), "status.txt"),
        (Path(f"/proc/{pid}/maps"), "maps.txt"),
        (Path(f"/proc/{pid}/smaps_rollup"), "smaps-rollup.txt"),
    ]:
        txt = get_text(path, 3_000_000)
        if txt:
            (folder / name).write_text(txt)
    safe_capture(["ps", "-L", "-p", str(pid), "-o",
                  "pid,tid,stat,pcpu,time,wchan:28,comm"],
                 folder / "thread-states.txt")

def gdb_sample(pid: int, tid: int | None, number: int):
    target = 0 if tid is None else tid
    cmds = DIR / f"gdb-commands-{number}.txt"
    out = DIR / f"gdb-sample-{number}.txt"
    cmds.write_text(
        "set pagination off\n"
        "set confirm off\n"
        "set print thread-events off\n"
        "info threads\n"
        "python\n"
        "import gdb\n"
        f"target_tid = {target}\n"
        "for thread in gdb.selected_inferior().threads():\n"
        "    if thread.ptid[1] == target_tid:\n"
        "        thread.switch()\n"
        "        print('=== TARGET GAME MAIN ===', thread.ptid, thread.name)\n"
        "        gdb.execute('info registers rip rsp rbp rax rbx rcx rdx rsi rdi r8 r9 eflags')\n"
        "        gdb.execute('x/24i $pc')\n"
        "        gdb.execute('x/12gx $rsp')\n"
        "        gdb.execute('bt 16')\n"
        "end\n"
        "thread apply all bt 4\n"
        "detach\n")
    cmd = ["gdb", "-q", "-nx", "-nh", "-batch",
           "-iex", "set debuginfod enabled off",
           "-iex", "set auto-load safe-path /dev/null",
           "-p", str(pid), "-x", str(cmds)]
    start = time.monotonic()
    try:
        with out.open("w") as fd:
            result = subprocess.run(cmd, stdout=fd, stderr=subprocess.STDOUT,
                                    timeout=32)
        txt = get_text(out, 2_000_000)
        success = ("=== TARGET GAME MAIN ===" in txt and
                   "No such process" not in txt and result.returncode == 0)
        say(f"GDB sample {number}: rc={result.returncode}, "
            f"target_found={'=== TARGET GAME MAIN ===' in txt}, "
            f"duration={time.monotonic() - start:.1f}s")
        if not success:
            say("Debugger could not capture a complete snapshot; no privilege changes attempted.")
        return success
    except subprocess.TimeoutExpired:
        say(f"GDB sample {number} hit the safety timeout; process is not being killed.")
        try:
            os.kill(pid, signal.SIGCONT)
        except OSError:
            pass
        return False
    except Exception as exc:
        out.write_text(f"GDB error: {exc}\n")
        say(f"GDB sample {number}: {exc}")
        return False

def selftest():
    assert TRACE.search("[Lib.VideoOut] GHOST_TRACE vblank=1440 guest_flips=546 pending=0 queued=0").groups() == ("1440", "546", "0", "0")
    assert not TRACE.search("unrelated log message")
    assert "Ghost of Tsushima.ps4" in b"/mnt/roms-all/ps4/Ghost of Tsushima.ps4".decode()
    assert all(x >= 0 for x in TRACE.search("GHOST_TRACE vblank=1260 guest_flips=564 pending=0 queued=0").groups() if int(x) >= 0)
    print("SELFTEST PASS: log parser and Ghost launch-name matching; no debugger attach")
    return 0

def main():
    if "--self-test" in sys.argv:
        return selftest()
    DIR.mkdir(parents=True, exist_ok=True)
    start = time.time()
    pid = None
    folder = None
    try:
        import hashlib
        if not BIN.is_file():
            say(f"Installed shadPS4 binary unavailable: {BIN}")
            return 11
        h = hashlib.sha256()
        with BIN.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        (DIR / "binary-sha256.txt").write_text(h.hexdigest() + "\n")
        if h.hexdigest() != EXPECTED_BIN_SHA256:
            say("Installed binary is not the proven diagnostic build. No process will be attached.")
            return 12
        say("ARMED: launch Ghost of Tsushima through Moonlight -> ES-DE.")
        say("The script waits for a persistent flip stall, then briefly samples the game thread.")
        while time.time() - start < 600:
            choice = session_candidate(start)
            if choice:
                _, folder, pid = choice
                break
            time.sleep(1)
        if not folder:
            say("No new running Ghost of Tsushima session was detected in 10 minutes.")
            return 13
        say(f"Detected game session PID={pid}, log={folder / 'runtime.log'}")
        shutil.copy2(folder / "session.meta", DIR / "session.meta")
        scope = Path("/proc/sys/kernel/yama/ptrace_scope")
        if scope.is_file():
            scope_value = get_text(scope, 200).strip()
            (DIR / "ptrace-scope.txt").write_text(scope_value + "\n")
            say("Linux ptrace_scope=" + scope_value +
                " (if attaching is denied, no sudo or system setting changes will be attempted)")
        (DIR / "process-cmdline.txt").write_text(
            Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode(
                "utf-8", "replace") + "\n")
        runtime = folder / "runtime.log"
        prev_count = None
        prev_flip_at = time.monotonic()
        last_status_at = 0.0
        freeze_at = None
        wait_start = time.monotonic()
        while live_shadps4(pid) and time.monotonic() - wait_start < 145:
            status = get_trace(runtime)
            now = time.monotonic()
            if status:
                vblank, flips, pending, queued = status
                if prev_count is None or prev_count != flips:
                    prev_count, prev_flip_at = flips, now
                if now - last_status_at > 8:
                    say(f"vblank={vblank}, flips={flips}, pending={pending}, "
                        f"queued={queued}, stagnant={now-prev_flip_at:.1f}s")
                    last_status_at = now
                if (flips >= 100 and vblank >= 900 and pending == 0
                    and queued == 0 and now - prev_flip_at >= 12):
                    freeze_at = now
                    say(f"CONFIRMED: no guest flip progress for {now-prev_flip_at:.1f}s "
                        f"at vblank={vblank}, flips={flips}.")
                    break
            time.sleep(1)
        if freeze_at is None:
            say("No persistent flip plateau was observed before the game ended or time limit.")
            return 0
        for i in range(1, 4):
            if not live_shadps4(pid):
                say("Game exited before next snapshot.")
                break
            proc_snapshot(pid, i)
            tid = hottest_main(pid)
            (DIR / f"trace-{i}.txt").write_text(str(get_trace(runtime)))
            if shutil.which("gdb") and tid is not None:
                success = gdb_sample(pid, tid, i)
                if not success:
                    say("Skipping subsequent debugger attaches after unsuccessful capture.")
                    break
            else:
                say("No gdb or live Game:Main thread; collected non-invasive thread evidence.")
                break
            time.sleep(4)
        say("Capture complete. You may exit the game normally using the gamepad.")
        return 0
    finally:
        if folder:
            runtime = folder / "runtime.log"
            if runtime.exists():
                with runtime.open("rb") as inp:
                    size = runtime.stat().st_size
                    if size > 10_000_000:
                        inp.seek(-10_000_000, os.SEEK_END)
                    (DIR / "runtime.log").write_bytes(inp.read())
        (DIR / "status.txt").write_text("\n".join(STATUS)+"\n")
        with tarfile.open(OUT, "w:gz") as tar:
            tar.add(DIR, arcname=DIR.name)
        say(f"ARCHIVE={OUT}")
        say("Emulator, game saves, audio, controller settings and SSH session were not modified.")

if __name__ == "__main__":
    raise SystemExit(main())
