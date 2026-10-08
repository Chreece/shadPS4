#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only hardware watchpoints for Ghost's intro-to-stall state transition.

Captures the actual *writer* of two memory locations that changed across the
three-stage 2026-10-08 evidence: 0x3fb6590 and 0x3fb6550.

Never writes guest memory, patches code, kills shadPS4, or touches saves,
configuration, the build tree, Sunshine, ES-DE or SSH. GDB briefly stops the
process on each hardware watchpoint; every stop is immediately resumed.
"""
from __future__ import annotations

from datetime import datetime
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
EXPECTED_SHA = "f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
ROOT = HOME / ".cache" / f"ghost-state-writers-{STAMP}"
ARCHIVE = HOME / f"ghost-state-writers-{STAMP}.tar.gz"
TRACES = re.compile(
    r"GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)"
)
STATUS = []

def say(message):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    print(line, flush=True)
    STATUS.append(line)

def process_is_ghost(pid):
    proc = Path("/proc") / str(pid)
    try:
        if proc.stat().st_uid != os.getuid():
            return False
        exe = os.readlink(proc / "exe").removesuffix(" (deleted)")
        cmdline = (proc / "cmdline").read_bytes()
        return Path(exe).name.lower() == "shadps4" and (
            b"CUSA11456" in cmdline or b"Ghost of Tsushima.ps4" in cmdline
        )
    except (OSError, PermissionError, ValueError):
        return False

def read_trace(runtime):
    try:
        with runtime.open("rb") as f:
            if runtime.stat().st_size > 8_000_000:
                f.seek(-8_000_000, os.SEEK_END)
            text = f.read().decode("utf-8", "replace")
        matches = TRACES.findall(text)
        return tuple(map(int, matches[-1])) if matches else None
    except OSError:
        return None

def recent_ghost(since):
    matches = []
    for meta in STATE.glob("*Ghost_of_Tsushima.ps4/session.meta"):
        try:
            if meta.stat().st_mtime < since - 15:
                continue
            content = meta.read_text(errors="replace")
            pid_match = re.search(r"(?m)^launcher_pid=(\d+)$", content)
            if not pid_match:
                continue
            pid = int(pid_match.group(1))
            if process_is_ghost(pid):
                matches.append((meta.stat().st_mtime, meta.parent, pid))
        except (OSError, ValueError):
            continue
    return max(matches, default=None)

def gdb_commands():
    # Inferior is paused for the few milliseconds required by each watchpoint
    # callback, not during the surrounding game execution.
    return r'''set pagination off
set confirm off
set print thread-events off
set can-use-hw-watchpoints 1
set debuginfod enabled off
set auto-load safe-path /dev/null
set may-call-functions off
# The guest CPU-ID/TSC and audio code intentionally handle SIGSEGV/SIGILL.
# GDB must forward them to the emulator without stopping the watchpoint run.
# Do not change SIGTRAP handling: hardware watchpoints require it.
handle SIGSEGV nostop noprint pass
handle SIGILL nostop noprint pass
handle SIGBUS nostop noprint pass
handle SIGUSR1 nostop noprint pass
handle SIGUSR2 nostop noprint pass
python
import gdb
hits = 0
MAX_HITS = 18

def read(command):
    try:
        gdb.execute(command)
    except Exception as exc:
        print("READ_ERROR", command, str(exc))

class StateWatch(gdb.Breakpoint):
    def __init__(self, name, expression):
        super().__init__(expression, type=gdb.BP_WATCHPOINT, wp_class=gdb.WP_WRITE)
        self.name = name
        self.silent = True
    def stop(self):
        global hits
        hits += 1
        thread = gdb.selected_thread()
        print("=== GHOST_WATCH_EVENT", hits, self.name, "thread=", thread.ptid,
              "name=", thread.name, "pc=", gdb.parse_and_eval("$pc"), "===", flush=True)
        read("info registers rip rax rbx rcx rdx rsi rdi r8 r9 r10 r11 r12 r13 r14 r15 rsp rbp eflags")
        read("x/20i $pc-28")
        read("bt 12")
        read("x/12wx 0x3fb6548")
        read("x/12wx 0x3fb6588")
        print("=== GHOST_WATCH_EVENT_END", hits, self.name, "===", flush=True)
        # Stop on a bounded event count, then the batch script detaches.
        return hits >= MAX_HITS

StateWatch("state_u32", "*(unsigned int*)0x3fb6590")
StateWatch("transition_ptr_u64", "*(unsigned long long*)0x3fb6550")
print("GHOST_WATCHPOINTS_ARMED", flush=True)
end
python
# "continue" can return on unrelated stops, too. Do not mistake such a stop
# for reaching the requested number of hardware-watchpoint hits.
unexpected_stops = 0
MAX_UNEXPECTED_STOPS = 32
while hits < MAX_HITS and unexpected_stops < MAX_UNEXPECTED_STOPS:
    try:
        gdb.execute("continue")
    except gdb.error as exc:
        print("GHOST_CONTINUE_ERROR", str(exc), flush=True)
        break
    if hits >= MAX_HITS:
        break
    unexpected_stops += 1
    print("GHOST_UNEXPECTED_STOP", unexpected_stops, "watch_events=", hits, flush=True)
    try:
        gdb.execute("info program")
    except gdb.error as exc:
        print("GHOST_INFO_PROGRAM_ERROR", str(exc), flush=True)
    try:
        inferior = gdb.selected_inferior()
        if not inferior.is_valid() or inferior.pid <= 0:
            break
    except Exception:
        break
if hits >= MAX_HITS:
    print("GHOST_WATCHPOINT_LIMIT_REACHED", hits, flush=True)
else:
    print("GHOST_WATCHPOINT_END_WITHOUT_LIMIT", hits,
          "unexpected_stops=", unexpected_stops, flush=True)
end
detach
'''

def capture_game(pid, runtime):
    commands = ROOT / "watchpoints.gdb"
    commands.write_text(gdb_commands())
    transcript = ROOT / "watchpoint-trace.txt"
    args = [
        "gdb", "-q", "-nx", "-nh", "-batch",
        "-iex", "set debuginfod enabled off",
        "-iex", "set auto-load safe-path /dev/null",
        "-p", str(pid), "-x", str(commands)
    ]
    say("Attaching two write-only hardware watchpoints for up to 75 seconds.")
    say("Game may briefly pause at each observed state change; no process is killed.")
    started = time.monotonic()
    timed_out = False
    hardware_confirmed = False
    safe_reject = False
    with transcript.open("w") as out:
        proc = subprocess.Popen(
            args, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
        )
        try:
            while proc.poll() is None:
                elapsed = time.monotonic() - started
                if elapsed >= 8 and not hardware_confirmed:
                    try:
                        out.flush()
                        transcript_tail = transcript.read_text(errors="replace")
                        hardware_count = len(re.findall(
                            r"(?m)^Hardware watchpoint\s+\d+:", transcript_tail))
                        if hardware_count >= 2:
                            hardware_confirmed = True
                            say("Verified two hardware watchpoints; continuing observation.")
                        elif elapsed >= 12:
                            safe_reject = True
                            say("Could not verify two hardware watchpoints; stopping the "
                                "debugger to avoid slow software watchpoints.")
                            proc.terminate()
                            try:
                                proc.wait(timeout=6)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.wait(timeout=5)
                            break
                    except OSError:
                        pass
                if elapsed >= 75:
                    timed_out = True
                    # Terminate the *debugger* at its deadline, never the game.
                    # Linux ptrace releases a tracee when its tracer exits.
                    say("Watchpoint capture reached 75-second deadline; detaching debugger.")
                    proc.terminate()
                    try:
                        proc.wait(timeout=6)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                    break
                if int(elapsed) % 10 == 0 and elapsed > 0:
                    pass
                time.sleep(0.25)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
    # Never send SIGTERM/SIGKILL to the emulator. If a ptrace stop remained,
    # SIGCONT can release it without terminating or changing application data.
    if (timed_out or safe_reject) and process_is_ghost(pid):
        try:
            status = (Path(f"/proc/{pid}/status").read_text(errors="replace"))
            state = re.search(r"(?m)^State:\s+(\w)", status)
            if state and state.group(1).lower() in ("t",):
                os.kill(pid, signal.SIGCONT)
                say("Resumed a remaining ptrace stop with SIGCONT; game not terminated.")
        except (OSError, PermissionError):
            pass
    try:
        size = transcript.stat().st_size
        total = transcript.read_text(errors="replace")
        count = total.count("=== GHOST_WATCH_EVENT ")
        armed = "GHOST_WATCHPOINTS_ARMED" in total
        early_interruptions = total.count("GHOST_UNEXPECTED_STOP")
        if count == 0 and "GHOST_WATCHPOINT_END_WITHOUT_LIMIT" in total:
            say("No hardware write event captured; GDB ended before reaching its "
                "watchpoint-event limit. See watchpoint-trace.txt.")
        say(f"GDB exit={proc.returncode}; unexpected stops={early_interruptions}; "
            f"watchpoints armed={armed}; "
            f"events={count}; output bytes={size}; timeout={timed_out}.")
        (ROOT / "watch-summary.txt").write_text(
            f"gdb_exit={proc.returncode}\nwatchpoints_armed={armed}\n"
            f"events={count}\ntimeout={timed_out}\n"
            f"hardware_confirmed={hardware_confirmed}\nsoftware_rejected={safe_reject}\n"
            f"unexpected_stops={early_interruptions}\n"
            f"guest_alive_after={process_is_ghost(pid)}\n"
            f"final_trace={read_trace(runtime)}\n"
        )
    except OSError as exc:
        say(f"Could not summarize debugger transcript: {exc}")

def selftest():
    assert TRACES.search(
        "GHOST_TRACE vblank=1400 guest_flips=551 pending=0 queued=0"
    ).groups() == ("1400","551","0","0")
    commands = gdb_commands()
    assert "WP_WRITE" in commands and "GHOST_WATCHPOINTS_ARMED" in commands
    assert "0x3fb6590" in commands and "0x3fb6550" in commands
    assert "MAX_HITS = 18" in commands and "detach" in commands
    assert "set can-use-hw-watchpoints 1" in commands
    assert "set may-call-functions off" in commands
    assert "handle SIGSEGV nostop noprint pass" in commands
    assert "handle SIGILL nostop noprint pass" in commands
    assert "handle SIGBUS nostop noprint pass" in commands
    assert "handle SIGTRAP" not in commands
    assert 'gdb.execute("continue")' in commands
    assert "GHOST_WATCHPOINT_END_WITHOUT_LIMIT" in commands
    assert 'print("GHOST_WATCHPOINT_LIMIT_REACHED", hits' in commands
    assert "/proc" not in commands  # debugger only reads guest/process mappings
    print("SELFTEST PASS: exact addresses, write-only watchpoints, 18-event cap, "
          "detach, trace parsing. No debugger was launched.")

def main():
    if "--self-test" in sys.argv:
        selftest()
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    session = None
    started = time.time()
    try:
        for binary in ("gdb",):
            if shutil.which(binary) is None:
                say(f"Missing {binary}; refusing to install packages or request sudo.")
                return
        if not EXE.is_file():
            say("Installed shadPS4 binary missing; refusing to attach.")
            return
        digest = hashlib.sha256(EXE.read_bytes()).hexdigest()
        (ROOT / "installed-sha256.txt").write_text(digest + "\n")
        if digest != EXPECTED_SHA:
            say("Executable differs from the proven baseline; refusing to attach.")
            return
        say("ARMED: launch Ghost through Moonlight -> ES-DE now.")
        say("Waiting until the intro is playing to watch writers of 0x3fb6590 and 0x3fb6550.")
        while time.time() - started < 600:
            selected = recent_ghost(started)
            if selected:
                _, session, pid = selected
                break
            time.sleep(1)
        if session is None:
            say("No Ghost launch detected within ten minutes.")
            return
        runtime = session / "runtime.log"
        (ROOT / "session.meta").write_bytes((session / "session.meta").read_bytes())
        say(f"Game detected: PID={pid}")
        waiting_started = time.monotonic()
        while time.monotonic() - waiting_started < 50 and process_is_ghost(pid):
            trace = read_trace(runtime)
            if trace:
                _, flips, pending, queued = trace
                if 200 <= flips <= 430 and queued <= 16:
                    say(f"INTRO_ACTIVE: flips={flips}; arming writer watchpoints.")
                    capture_game(pid, runtime)
                    break
                if flips > 430:
                    say("Intro progressed too far before watchpoints could arm; "
                        "no late watchpoint will be attached.")
                    break
            time.sleep(0.5)
        else:
            say("Game ended or intro did not reach the watchpoint window.")
        say("CAPTURE COMPLETE. Exit Ghost normally with your gamepad if still running.")
    finally:
        if session is not None:
            runtime = session / "runtime.log"
            if runtime.is_file():
                with runtime.open("rb") as source:
                    if runtime.stat().st_size > 9_000_000:
                        source.seek(-9_000_000, os.SEEK_END)
                    (ROOT / "runtime.log").write_bytes(source.read())
        (ROOT / "collector-status.txt").write_text("\n".join(STATUS) + "\n")
        with tarfile.open(ARCHIVE, "w:gz") as output:
            output.add(ROOT, arcname=ROOT.name)
        say(f"ARCHIVE={ARCHIVE}")
        say("No code, game state, saves, shader cache, source, SSH or emulator executable changed.")

if __name__ == "__main__":
    main()
