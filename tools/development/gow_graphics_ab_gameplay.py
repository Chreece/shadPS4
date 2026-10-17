#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run and observe one SHA-verified GoW variant; leave the game running afterwards.

Usage: python3 gow_graphics_ab_gameplay.py --variant pm4
       python3 gow_graphics_ab_gameplay.py --variant observability
       python3 gow_graphics_ab_gameplay.py --variant baseline

The script never modifies game saves, shaders, source trees, system services,
Sunshine, X11, other emulator processes or SSH.
"""
import argparse
from collections import Counter, deque
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time
import traceback

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
BINROOT = ROOT / "build"
LOGS = Path.home() / "projects/shadps4/logs"
VARIANTS = {
    "pm4": (
        "shadps4.native-pm4-registers-gow-pm4-source-matched-20261017-082349",
        "effdac71d173e9b098b88e188f5302977fb103a330d570b4d648f73e35ba9c5e",
    ),
    "observability": (
        "shadps4.native-gfx-observability-gow-graphics-source-matched-20261017-080431",
        "6dfd863b568c93e084bd9290a1d234a3de9c6d00065e0e84b08a967c8d0d91bd",
    ),
    "baseline": (
        "shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543",
        "e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa",
    ),
}
ANSI = re.compile(r"\x1b\[[0-9;]*m")
HEXSHADER = re.compile(r"(?:stage|shader)\s+(?:0x)?([a-fA-F0-9]{6,16})", re.I)
KNOWN = {
    "clamped_buffer": "Clamped size from",
    "unflattened_descriptor": "sharp source was not flattened",
    "unflattened_old": "Sharp source was not flatenned",
    "queue_reg": "SetQueueReg",
    "dma": "Enabling DMA for shader",
    "vk_rejection": "Rejecting invalid T#",
    "pm4_range": "out-of-range",
}
SELECT_NAMES = ("GpuComm", "Uffd", "Game:Main", "TaskManager", "VideoOut", "Render", "Worker")


def cmdline(proc):
    raw = (proc / "cmdline").read_bytes()
    return [x.decode(errors="replace") for x in raw.rstrip(b"\0").split(b"\0") if x]


def running_emulators():
    result = []
    for p in Path("/proc").iterdir():
        if not p.name.isdecimal():
            continue
        try:
            if p.stat().st_uid != os.getuid():
                continue
            args = cmdline(p)
            if not args or not Path(args[0]).name.lower().startswith("shadps4"):
                continue
            status = (p / "stat").read_text().rsplit(") ", 1)[1].split()[0]
            if status not in ("Z", "X"):
                result.append({"pid": int(p.name), "state": status, "argv": args})
        except (OSError, ValueError, IndexError):
            continue
    return result


def threads(pid):
    stats = {}
    taskdir = Path(f"/proc/{pid}/task")
    for t in taskdir.iterdir():
        if not t.name.isdecimal():
            continue
        try:
            name = (t / "comm").read_text().strip()
            if not any(x in name for x in SELECT_NAMES):
                continue
            stat = (t / "stat").read_text().rsplit(") ", 1)[1].split()
            stats[t.name] = {
                "name": name,
                "state": stat[0],
                "cpu_ticks": int(stat[11]) + int(stat[12]),
                "wait_channel": (t / "wchan").read_text().strip(),
            }
        except (OSError, ValueError, IndexError):
            continue
    return stats


def inspect_log(path, directory):
    if not path.is_file():
        return {"file_missing": True}
    categories = Counter()
    shaders = Counter()
    selected_first = []
    selected_tail = deque(maxlen=450)
    total = 0
    malformed = 0
    try:
        with path.open("rb", buffering=1024 * 1024) as f:
            for raw in f:
                total += 1
                line = ANSI.sub("", raw.decode("utf-8", errors="replace")).rstrip()
                hits = [name for name, substring in KNOWN.items() if substring in line]
                if hits or "<Error>" in line or "<Warning>" in line:
                    for name in hits:
                        categories[name] += 1
                    if "<Error>" in line:
                        categories["errors"] += 1
                    if "<Warning>" in line:
                        categories["warnings"] += 1
                    if len(selected_first) < 450:
                        selected_first.append(line[:1400])
                    selected_tail.append(line[:1400])
                match = HEXSHADER.search(line)
                if match and hits:
                    shaders[match.group(1).lower()] += 1
    except OSError:
        malformed += 1
    (directory / "highlighted-log-head-tail.txt").write_text(
        "\n".join(selected_first) + "\n\n... SELECTED WARNING/ERROR LOG TAIL ...\n\n" +
        "\n".join(selected_tail) + "\n", encoding="utf-8"
    )
    summary = {
        "bytes_at_capture": path.stat().st_size,
        "lines": total,
        "counts": dict(categories),
        "top_shader_ids": shaders.most_common(35),
        "read_failures": malformed,
        "full_log_path_on_server": str(path),
    }
    (directory / "log-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=VARIANTS, default="pm4")
    parser.add_argument("--seconds", type=int, default=180)
    options = parser.parse_args()
    if options.seconds < 30 or options.seconds > 360:
        parser.error("--seconds must be 30..360")

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS.mkdir(parents=True, exist_ok=True)
    out = LOGS / f"gow-graphics-ab-{options.variant}-{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    archive = LOGS / f"{out.name}.tar.gz"
    filename, expected_hash = VARIANTS[options.variant]
    exe = BINROOT / filename
    stats = []
    status = {
        "variant": options.variant,
        "expected_sha256": expected_hash,
        "file": str(exe),
        "seconds_requested": options.seconds,
        "game_launch_requested": True,
        "source_files_changed": False,
        "sunshine_x11_changed": False,
        "other_emulators_changed": False,
        "ssh_session_changed": False,
    }
    child = None
    exit_code = 1
    logfile = None
    try:
        if not exe.is_file() or exe.is_symlink():
            raise RuntimeError(f"PINNED_EXECUTABLE_MISSING_OR_SYMLINK={exe}")
        digest = hashlib.sha256(exe.read_bytes()).hexdigest()
        status["measured_sha256"] = digest
        if digest != expected_hash:
            raise RuntimeError(f"BIN_SHA256_MISMATCH measured={digest}")
        running = running_emulators()
        (out / "previous-running-emulators.json").write_text(json.dumps(running, indent=2) + "\n")
        if running:
            for p in running:
                print(f"EXISTING_EMULATOR={p['pid']} {' '.join(p['argv'])[:200]}", flush=True)
            raise RuntimeError("AN_EMULATOR_IS_ALREADY_RUNNING; NO_GAME_LAUNCHED")

        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
        for k in list(env):
            if k.startswith(("SHADPS4_GOW_", "SHADPS4_CPU_ID_")):
                env.pop(k, None)
        command = [str(exe), "--cpu-id-mode", "builtin",
                   "--userfaultfd", "--game", "CUSA34384",
                   "--fullscreen", "true", "--show-fps"]
        (out / "command.txt").write_text(" ".join(command) + "\n")
        logfile = (out / "game-console-full.log").open("wb", buffering=0)
        child = subprocess.Popen(command, env=env, cwd=ROOT, stdin=subprocess.DEVNULL,
                                 stdout=logfile, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        status["game_pid"] = child.pid
        print(f"GAME_LAUNCHED=YES VARIANT={options.variant} PID={child.pid}", flush=True)
        print("PLAY_NORMALLY_WITH_MOONLIGHT; NO_AUTO_INPUT; NO_AUTO_TERMINATION", flush=True)
        previous = threads(child.pid)
        started = time.monotonic()
        hz = os.sysconf("SC_CLK_TCK")
        consecutive_deadlock = 0
        for elapsed in range(15, options.seconds + 1, 15):
            time.sleep(max(0.0, started + elapsed - time.monotonic()))
            if child.poll() is not None:
                status["game_exit_code"] = child.returncode
                print(f"GAME_EXITED_AT={elapsed}s CODE={child.returncode}", flush=True)
                break
            current = threads(child.pid)
            snapshot = []
            gpu_fault = False
            uffd_wait = False
            total_cpu_ms = 0
            for tid, stat in sorted(current.items(), key=lambda x: int(x[0])):
                prior = previous.get(tid)
                cpu_ms = max(0, round((stat["cpu_ticks"] - prior["cpu_ticks"]) * 1000 / hz)) if prior else None
                if cpu_ms is not None:
                    total_cpu_ms += cpu_ms
                row = {"tid": tid, "cpu_delta_ms": cpu_ms, **stat}
                snapshot.append(row)
                gpu_fault |= ("GpuComm" in stat["name"] and "handle_userfault" in stat["wait_channel"] and cpu_ms == 0)
                uffd_wait |= ("Uffd" in stat["name"] and "futex_wait" in stat["wait_channel"] and cpu_ms == 0)
            stalled = gpu_fault and uffd_wait and total_cpu_ms == 0
            consecutive_deadlock = consecutive_deadlock + 1 if stalled else 0
            stats.append({"elapsed_seconds": elapsed, "threads": snapshot,
                          "cpu_delta_ms_total": total_cpu_ms, "deadlock_signature": stalled})
            print(f"T={elapsed}s THREAD_CPU_MS={total_cpu_ms} GPU_FAULT={int(gpu_fault)} UFFD_WAIT={int(uffd_wait)}", flush=True)
            if consecutive_deadlock >= 3:
                status["repeated_old_deadlock_signature"] = True
                print("OLD_UFFD_DEADLOCK_SIGNATURE=REPRODUCED", flush=True)
                break
            previous = current
        status["game_alive_at_capture_end"] = child.poll() is None
        status["observations"] = len(stats)
        status.setdefault("repeated_old_deadlock_signature", False)
        logfile.flush()
        logfile.close()
        logfile = None
        status["log_summary"] = inspect_log(out / "game-console-full.log", out)
        exit_code = 0
    except Exception as e:
        status["error"] = str(e)
        (out / "error.txt").write_text(traceback.format_exc())
        print(f"TEST_BLOCKED={e}", flush=True)
    finally:
        if logfile:
            logfile.close()
        (out / "thread-samples.json").write_text(json.dumps(stats, indent=2) + "\n")
        status["test_result"] = "CAPTURED" if exit_code == 0 else "BLOCKED"
        (out / "result.json").write_text(json.dumps(status, indent=2) + "\n")
        with tarfile.open(archive, "w:gz") as t:
            for f in sorted(out.iterdir()):
                if f.is_file() and f.name != "game-console-full.log":
                    t.add(f, arcname=f.name)
        print(f"ARCHIVE={archive}", flush=True)
        print(f"VARIANT={options.variant}", flush=True)
        print(f"GAME_RUNNING_AT_END={bool(child and child.poll() is None)}", flush=True)
        print("GAME_NOT_TERMINATED_BY_SCRIPT=YES", flush=True)
        print("SUNSHINE_X11=UNCHANGED; SSH_SESSION=UNCHANGED", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
