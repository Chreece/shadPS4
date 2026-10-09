#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded test-owned Ghost hot CPU-thread RIP samples without ptrace or root.

Called ONLY by a validated automatic Ghost session after guest flips have
stopped but emulated vblank continues. Requires the opt-in Linux SIGUSR2
handler installed by ghost-guest-cpu-rip-probe-20261009.py; requires the
actual shadPS4 guest PID, its own environ GHOST_CPU_RIP_LOG, the matching
new test output file, same UID, Ghost game argv and a hot Game:Main thread.

Uses Linux tgkill to address that one native thread exactly 16 times at
140-ms intervals. The handler simply writes native instruction pointers.
Never calls gdb, perf, sudo, kills, stops or changes another process.
"""
from __future__ import annotations
import collections
import ctypes
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import time

NUM_SAMPLES = 16
INTERVAL = 0.14
CPU_INTERVAL = 0.7
MIN_TICKS = 8
SYSCALL_TGKILL_X86_64 = 234
RECORD = re.compile(r"GHOST_CPU_RIP_SAMPLE tid=(\d+) rip=0x([0-9a-fA-F]{16})")


def thread_stat(stat: str) -> dict:
    """Parse /proc/PID/task/TID/stat, ignoring spaces in the comm field."""
    pos = stat.rfind(") ")
    if pos < 0:
        raise ValueError("Missing stat comm delimiter")
    rest = stat[pos + 2:].split()
    if len(rest) < 20:
        raise ValueError("Short task stat")
    return {
        "state": rest[0],
        "ticks": int(rest[11]) + int(rest[12]),
        "start_ticks": int(rest[19]),
    }


def snapshot(pid: int) -> dict[int, dict]:
    taskdir = Path("/proc") / str(pid) / "task"
    collected = {}
    for tid_path in taskdir.iterdir():
        if not tid_path.name.isdigit():
            continue
        try:
            name = (tid_path / "comm").read_text().strip()
            stat = thread_stat((tid_path / "stat").read_text())
            collected[int(tid_path.name)] = dict(comm=name, **stat)
        except (OSError, ValueError, PermissionError):
            continue
    return collected


def validate_game(pid: int, work: Path) -> Path:
    proc = Path("/proc") / str(pid)
    if proc.stat().st_uid != os.getuid():
        raise RuntimeError("Game PID belongs to another user")
    exe = os.readlink(proc / "exe").removesuffix(" (deleted)")
    if Path(exe).name.lower() != "shadps4":
        raise RuntimeError("PID is not the installed shadPS4 emulator")
    argv = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    if not ("CUSA11456" in argv or "Ghost of Tsushima.ps4" in argv):
        raise RuntimeError("PID is not the requested Ghost game")
    intended = work / "guest-cpu-rip.txt"
    if intended.is_symlink() or not intended.is_file():
        raise RuntimeError("Opt-in native RIP log not created by game")
    contents = (proc / "environ").read_bytes().split(b"\0")
    if b"GHOST_CPU_RIP_LOG=" + os.fsencode(intended) not in contents:
        raise RuntimeError("Opt-in RIP path not verified in actual guest process")
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise RuntimeError("TGKILL syscall is only reviewed on Linux x86_64")
    return intended


def executable_map_entries(pid: int) -> list[dict]:
    entries = []
    try:
        lines = (Path("/proc") / str(pid) / "maps").read_text(errors="replace").splitlines()
    except (OSError, PermissionError):
        return entries
    for line in lines:
        parts = line.split(maxsplit=5)
        if len(parts) < 5 or "x" not in parts[1]:
            continue
        try:
            start, end = (int(v, 16) for v in parts[0].split("-", 1))
            offset = int(parts[2], 16)
        except (ValueError, IndexError):
            continue
        entries.append({
            "start": start, "end": end, "perms": parts[1],
            "file_offset": offset, "name": parts[5] if len(parts) > 5 else "",
        })
    return entries


def match_rip(rip: int, mappings: list[dict]) -> dict | None:
    for area in mappings:
        if area["start"] <= rip < area["end"]:
            return {
                "perms": area["perms"],
                "object": area["name"],
                "mapping_offset_hex": hex(rip - area["start"]),
                "file_offset_hex": hex(area["file_offset"] + rip - area["start"]),
                "range_start_hex": hex(area["start"]),
                "range_end_hex": hex(area["end"]),
            }
    return None


def collect(pid: int, work: Path) -> dict:
    work = work.resolve()
    if not work.is_dir() or not work.name.startswith("ghost-"):
        raise RuntimeError("Unexpected test workspace; refusing signal")
    rip_log = validate_game(pid, work)
    first = snapshot(pid)
    time.sleep(CPU_INTERVAL)
    second = snapshot(pid)
    candidates = []
    for tid, later in second.items():
        before = first.get(tid)
        if before is None or before["start_ticks"] != later["start_ticks"]:
            continue
        delta = later["ticks"] - before["ticks"]
        if delta < 0:
            continue
        candidates.append({
            "tid": tid, "comm": later["comm"], "cpu_ticks_delta": delta,
            "start_ticks": later["start_ticks"], "state": later["state"],
        })
    candidates.sort(key=lambda x: -x["cpu_ticks_delta"])
    hot = next((row for row in candidates
                if row["comm"] == "Game:Main" and
                row["cpu_ticks_delta"] >= MIN_TICKS), None)
    report = {
        "guest_pid": pid, "samples_requested": NUM_SAMPLES, "samples_delivered": 0,
        "elapsed_cpu_seconds": CPU_INTERVAL, "hz": os.sysconf("SC_CLK_TCK"),
        "top_threads": candidates[:20], "selected_hot_tid": hot["tid"] if hot else None,
        "minimum_cpu_ticks": MIN_TICKS,
        "errors": [], "rip_counts": [], "result": "NO_HOT_GUEST_THREAD",
    }
    if hot is None:
        (work / "guest-cpu-hotloop.json").write_text(json.dumps(report, indent=2) + "\n")
        print("GHOST_CPU_HOT_THREAD_NOT_CONFIRMED", flush=True)
        return report

    # The diagnostic source handles SIGUSR2 only when this file exists.
    # Detect a blocked signal on the selected thread and fail closed.
    status = (Path("/proc") / str(pid) / "task" / str(hot["tid"]) / "status"
              ).read_text(errors="replace")
    block_match = re.search(r"(?m)^SigBlk:\s*([0-9a-fA-F]+)", status)
    if not block_match or (int(block_match.group(1), 16) &
                           (1 << (signal.SIGUSR2 - 1))):
        report["errors"].append("SIGUSR2 is blocked or SigBlk unavailable")
        report["result"] = "SIGNAL_BLOCKED"
        (work / "guest-cpu-hotloop.json").write_text(json.dumps(report, indent=2) + "\n")
        return report

    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    for iteration in range(NUM_SAMPLES):
        # Check identity and original TID start tick on EVERY signal.
        if not validate_game(pid, work).is_file():
            break
        try:
            now = thread_stat(
                (Path("/proc") / str(pid) / "task" /
                 str(hot["tid"]) / "stat").read_text())
        except (OSError, ValueError):
            report["errors"].append("Selected thread exited before sample")
            break
        if now["start_ticks"] != hot["start_ticks"]:
            report["errors"].append("Selected TID was reused; stopping")
            break
        result = libc.syscall(
            ctypes.c_long(SYSCALL_TGKILL_X86_64),
            ctypes.c_int(pid), ctypes.c_int(hot["tid"]),
            ctypes.c_int(signal.SIGUSR2))
        if result != 0:
            report["errors"].append(
                "tgkill failed errno=" + str(ctypes.get_errno()))
            break
        report["samples_delivered"] += 1
        time.sleep(INTERVAL)

    # Read-only RIP correlations after bounded sampling; the host game stays up.
    records = RECORD.findall(rip_log.read_text(errors="replace"))
    counts = collections.Counter(
        value for tid, value in records if tid == str(hot["tid"]))
    maps = executable_map_entries(pid)
    report["rip_counts"] = [
        {"rip": "0x" + addr, "samples": hits,
         "executable_mapping": match_rip(int(addr, 16), maps)}
        for addr, hits in counts.most_common(32)
    ]
    report["samples_logged"] = sum(counts.values())
    report["result"] = (
        "GUEST_RIP_SAMPLED" if counts else "HANDLER_OUTPUT_NOT_FOUND"
    )
    (work / "guest-cpu-hotloop.json").write_text(json.dumps(report, indent=2) + "\n")
    (work / "guest-cpu-executable-maps.txt").write_text(
        "\n".join(
            f"{row['start']:016x}-{row['end']:016x} {row['perms']} "
            f"{row['file_offset']:x} {row['name']}"
            for row in maps[:500]
        ) + "\n"
    )
    print("GHOST_CPU_HOT_TID=" + str(hot["tid"]) +
          " cpu_ticks_delta=" + str(hot["cpu_ticks_delta"]), flush=True)
    print("GHOST_CPU_RIP_SAMPLES=" + str(report["samples_logged"]) +
          " unique_pcs=" + str(len(counts)), flush=True)
    print("GHOST_CPU_STALL_RESULT=" + report["result"], flush=True)
    return report


def selftest():
    fields = ["R"] + ["0"] * 50
    fields[11], fields[12], fields[19] = "67", "33", "5420"
    parsed = thread_stat("123 (Game:Main) " + " ".join(fields))
    assert parsed["ticks"] == 100 and parsed["start_ticks"] == 5420
    assert parsed["state"] == "R"
    assert len(RECORD.findall(
        "GHOST_CPU_RIP_SAMPLE tid=123 rip=0x000000001234abcd\n"
        "GHOST_CPU_RIP_SAMPLE tid=123 rip=0x000000001234abce\n")) == 2
    maps = [{"start": 0x1200, "end": 0x1300, "perms": "r-xp",
             "file_offset": 0x200, "name": "/guest/game"}]
    assert match_rip(0x1234, maps)["file_offset_hex"] == "0x234"
    assert match_rip(0x9999, maps) is None
    assert SYSCALL_TGKILL_X86_64 == 234 and NUM_SAMPLES <= 32
    with tempfile.TemporaryDirectory(prefix="ghost-cpu-hotloop-fixture-") as d:
        p = Path(d) / "sample"
        p.mkdir()
        (p / "rip").write_text("GHOST_CPU_RIP_SAMPLE tid=123 rip=0x000000001234abcd\n")
        assert len(RECORD.findall((p / "rip").read_text())) == 1
    print("SELFTEST PASS: CPU tick decoding, bounded test-owned TID signaling "
          "guards, RIP correlation, and executable mapping offsets. "
          "No real process signaled.", flush=True)


def main():
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    if len(sys.argv) != 3:
        raise SystemExit("Usage: collector.py --self-test | PID WORK")
    pid = int(sys.argv[1])
    if pid <= 1:
        raise ValueError("Invalid game PID")
    collect(pid, Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
