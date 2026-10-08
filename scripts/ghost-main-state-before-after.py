#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ghost game-thread scheduler memory and code: intro versus 550-frame stall.

Evidence only: reads process memory and guest instructions with two short,
bounded GDB attachments. No emulator rebuild, changes to memory, signals to
terminate the game, altered settings, privilege escalation or SSH changes.
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
BIN = HOME / "Applications/shadps4/shadps4"
BASE = "f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f"
STATE = HOME / ".local/state/shadps4-playtest-logs"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
WORK = HOME / ".cache" / f"ghost-main-state-{STAMP}"
OUTPUT = HOME / f"ghost-main-state-{STAMP}.tar.gz"
TRACE = re.compile(r"GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)")
STATUS = []
RANGES = {
    "scheduler_code": (0xC03000, 0x1200),
    "guest_job_state": (0x3FB64C0, 0x280),
    "guest_job_object": (0x1000397600, 0x280),
    "guest_time_globals": (0x4517140, 0x80),
}

def say(message):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    STATUS.append(line)

def matches_game(pid):
    process = Path(f"/proc/{pid}")
    try:
        if process.stat().st_uid != os.getuid():
            return False
        name = Path(os.readlink(process / "exe").removesuffix(" (deleted)")).name.lower()
        args = (process / "cmdline").read_bytes()
        return name == "shadps4" and (
            b"CUSA11456" in args or b"Ghost of Tsushima.ps4" in args
        )
    except (OSError, PermissionError, ValueError):
        return False

def game_sessions():
    choices = []
    for meta in STATE.glob("*Ghost_of_Tsushima.ps4/session.meta"):
        try:
            match = re.search(r"(?m)^launcher_pid=(\d+)$", meta.read_text(errors="replace"))
            if match and matches_game(int(match.group(1))):
                choices.append((meta.stat().st_mtime, int(match.group(1)), meta.parent))
        except (OSError, ValueError):
            continue
    return sorted(choices, reverse=True)

def runtime_text(path, cap=12000000):
    try:
        with path.open("rb") as fd:
            if path.stat().st_size > cap:
                fd.seek(-cap, os.SEEK_END)
            return fd.read().decode("utf-8", "replace")
    except OSError:
        return ""

def latest_flip(path):
    matches = TRACE.findall(runtime_text(path))
    return tuple(map(int, matches[-1])) if matches else None

def main_thread(pid):
    found = []
    for task in Path(f"/proc/{pid}/task").iterdir():
        try:
            if (task / "comm").read_text().strip() != "Game:Main":
                continue
            stat = (task / "stat").read_text().rsplit(") ", 1)[1].split()
            found.append((int(stat[11]) + int(stat[12]), int(task.name)))
        except (OSError, ValueError, IndexError):
            continue
    if not found:
        return None
    first = dict((tid, ticks) for ticks, tid in found)
    time.sleep(0.30)
    samples = []
    for _, tid in found:
        try:
            stat = Path(f"/proc/{pid}/task/{tid}/stat").read_text().rsplit(") ", 1)[1].split()
            ticks = int(stat[11]) + int(stat[12])
            samples.append((ticks-first[tid], tid))
        except (OSError, ValueError, IndexError):
            continue
    return max(samples, default=(0, None))[1]

def gdb_commands(tid, label):
    output = WORK / label
    # Emit only read operations, and detach. Main code and state are captured
    # both as disassembly in the transcript and as byte-exact memory snapshots.
    command = (
        "set pagination off\n"
        "set confirm off\n"
        "set print thread-events off\n"
        "set may-call-functions off\n"
        "python\n"
        "import gdb\n"
        f"target_tid = {tid}\n"
        f"dest = {str(output)!r}\n"
        f"ranges = {RANGES!r}\n"
        "def safe(command):\n"
        "    try:\n"
        "        gdb.execute(command)\n"
        "    except Exception as exc:\n"
        "        print('READ_ERROR', command, type(exc).__name__, str(exc), flush=True)\n"
        "for thread in gdb.selected_inferior().threads():\n"
        "    if thread.ptid[1] == target_tid:\n"
        "        thread.switch()\n"
        "        print('=== GUEST_MAIN_SELECTED ===', thread.ptid, thread.name, flush=True)\n"
        "        safe('info registers rip rsp rbp rax rbx rcx rdx rsi rdi r8 r9 r10 r11 r12 r13 r14 r15 eflags')\n"
        "        safe('x/95i 0xc03580')\n"
        "        safe('x/96i 0xc03100')\n"
        "        safe('x/36gx 0x3fb6500')\n"
        "        safe('x/20gx 0x4517140')\n"
        "        safe('bt 15')\n"
        "        break\n"
        "for name, (base, size) in ranges.items():\n"
        "    try:\n"
        "        data = gdb.selected_inferior().read_memory(base, size).tobytes()\n"
        "        with open(dest + '-' + name + '.bin', 'wb') as file:\n"
        "            file.write(data)\n"
        "        print('CAPTURED_RANGE', name, hex(base), len(data), flush=True)\n"
        "    except Exception as exc:\n"
        "        print('RANGE_FAILED', name, type(exc).__name__, str(exc), flush=True)\n"
        "print('GUEST_SCHEDULER_SNAPSHOT_COMPLETE', flush=True)\n"
        "end\n"
        "detach\n"
    )
    return command

def capture(pid, label, runtime):
    tid = main_thread(pid)
    say(f"{label}: Game:Main TID={tid}, trace={latest_flip(runtime)}")
    if tid is None:
        return
    cmds = WORK / (label + ".gdb")
    log = WORK / (label + "-gdb.txt")
    cmds.write_text(gdb_commands(tid, label))
    command = [
        "gdb", "-q", "-nx", "-nh", "-batch",
        "-iex", "set debuginfod enabled off",
        "-iex", "set auto-load safe-path /dev/null",
        "-p", str(pid), "-x", str(cmds),
    ]
    try:
        with log.open("w") as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                    timeout=20)
        recorded = "GUEST_SCHEDULER_SNAPSHOT_COMPLETE" in log.read_text(errors="replace")
        say(f"{label}: GDB exit={result.returncode}, byte snapshots={recorded}")
    except subprocess.TimeoutExpired:
        say(f"{label}: debugger 20-second limit, no emulator termination")
        try:
            if re.search(r"(?m)^State:\s+[tT]\b",
                         Path(f"/proc/{pid}/status").read_text(errors="replace")):
                os.kill(pid, signal.SIGCONT)
        except (OSError, PermissionError):
            pass

def compare_snapshots():
    result = []
    for key, (base, size) in RANGES.items():
        a = WORK / f"intro-{key}.bin"
        b = WORK / f"stall-{key}.bin"
        if not a.is_file() or not b.is_file():
            result.append(f"{key}: missing a matching snapshot")
            continue
        before, after = a.read_bytes(), b.read_bytes()
        if len(before) != len(after):
            result.append(f"{key}: inconsistent range sizes")
            continue
        diff_words = []
        for offset in range(0, len(before)-3, 4):
            old = int.from_bytes(before[offset:offset+4], "little")
            new = int.from_bytes(after[offset:offset+4], "little")
            if old != new:
                diff_words.append((base + offset, old, new))
        result.append(f"{key}: changed={len(diff_words)} of {len(before)//4} dwords")
        for addr, old, new in diff_words[:75]:
            result.append(f"  {addr:#x}: {old:#010x} -> {new:#010x}")
        if len(diff_words) > 75:
            result.append(f"  ... {len(diff_words)-75} additional changes")
    (WORK / "memory-comparison.txt").write_text("\n".join(result)+"\n")
    say("Memory comparison written; no guest data was changed")

def self_test():
    assert TRACE.search(
        "GHOST_TRACE vblank=2880 guest_flips=554 pending=0 queued=0"
    ).groups() == ("2880", "554", "0", "0")
    commands = gdb_commands(12345, "intro")
    assert "set may-call-functions off" in commands
    assert "GUEST_SCHEDULER_SNAPSHOT_COMPLETE" in commands
    assert "read_memory(base, size)" in commands
    assert commands.endswith("detach\n")
    assert "0xc03580" in commands
    assert len(RANGES) == 4
    assert all(size < 8192 for base, size in RANGES.values())
    assert "kill" not in commands and "set *(unsigned" not in commands
    print("SELFTEST PASS: guest main selection, bounded read-only memory and "
          "disassembly, four exact ranges, detach and trace parsing")

def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    WORK.mkdir(parents=True, exist_ok=True)
    runtime = None
    pid = None
    try:
        if not BIN.is_file():
            say("shadPS4 baseline executable unavailable; not attaching")
            return
        checksum = hashlib.sha256(BIN.read_bytes()).hexdigest()
        (WORK / "installed-sha256.txt").write_text(checksum + "\n")
        if checksum != BASE:
            say("Executable changed since proven baseline; refusing attach")
            return
        if not shutil.which("gdb"):
            say("GDB unavailable; no sudo/install requested")
            return
        sessions = game_sessions()
        if len(sessions) > 1:
            say("Multiple live Ghost sessions found; exit extras normally before capture")
            return
        if sessions:
            _, existing_pid, existing_folder = sessions[0]
            current = latest_flip(existing_folder / "runtime.log")
            if current and current[1] > 495:
                say(f"Ghost PID={existing_pid} is already past the intro at {current[1]} "
                    "frames. Exit it normally with your gamepad, then rerun this collector "
                    "to capture BOTH sides of the transition.")
                return
        if not sessions:
            say("ARMED: launch Ghost through Moonlight -> ES-DE now.")
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                sessions = game_sessions()
                if sessions:
                    break
                time.sleep(0.8)
        if not sessions:
            say("No live Ghost detected within 10 minutes")
            return
        _, pid, session = sessions[0]
        runtime = session / "runtime.log"
        if (session / "session.meta").is_file():
            shutil.copy2(session / "session.meta", WORK / "session.meta")
        say(f"Monitoring PID={pid}, current frames={latest_flip(runtime)}")
        already_intro = False
        previous = None
        flip_at = time.monotonic()
        deadline = time.monotonic() + 160
        while time.monotonic() < deadline and matches_game(pid):
            record = latest_flip(runtime)
            now = time.monotonic()
            if record:
                vblank, flips, pending, queued = record
                if previous != flips:
                    previous, flip_at = flips, now
                if not already_intro and 280 <= flips <= 495:
                    say(f"INTRO snapshot at guest_flips={flips}")
                    capture(pid, "intro", runtime)
                    already_intro = True
                    record = latest_flip(runtime)
                    if record:
                        previous = record[1]
                    flip_at = time.monotonic()
                if flips >= 500 and pending == 0 and queued == 0 and now - flip_at >= 12:
                    say(f"CONFIRMED STALL: {flips} guest flips, vblank={vblank}")
                    capture(pid, "stall", runtime)
                    break
            time.sleep(0.5)
        else:
            say("No qualifying stall within observation, or game ended")
        compare_snapshots()
        say("CAPTURE COMPLETE: exit the game normally if still running")
    finally:
        if runtime is not None:
            (WORK / "runtime.log").write_text(runtime_text(runtime))
        (WORK / "status.txt").write_text("\n".join(STATUS)+"\n")
        with tarfile.open(OUTPUT, "w:gz") as archive:
            archive.add(WORK, arcname=WORK.name)
        say(f"ARCHIVE={OUTPUT}")
        say("No build, game-memory modifications, force-quit, SSH or settings changes")

if __name__ == "__main__":
    main()
