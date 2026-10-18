#!/usr/bin/env python3
"""Correlate GoW Ragnarök guest Wwise audio inactivity with host scheduling.

Uses read-only /proc sampling and current emulator log capture. If the game is
not running, launches the checksum-pinned proven graphics + UFFD baseline.
Never edits the emulator, host scheduling policy, PipeWire, Sunshine or X11,
and never stops/kills an existing game.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time

ROOT = Path.home() / "projects/shadps4"
GAME = "CUSA34384"
BASE = ROOT / "GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
GOOD = (ROOT / "GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build" /
        "shadps4.fullgfx-uffd-batched-read-gow-uffd-batched-read-20261017-231412")
GOOD_SHA256 = "8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef"
CATEGORIES = (
    ("wwise", ("AK::AudioOut", "AK::Audio", "AK_Audio")),
    ("game_main", ("Game:Main",)),
    ("game_workers", ("TaskManager",)),
    ("gpu_command", ("shadPS4:GpuComm", "GpuComm")),
    ("userfaultfd", ("shadPS4:Uffd", "Uffd")),
    ("audio_output", ("shadPS4:AudioOu",)),
    ("sdl_output", ("SDLAudio", "SDL_Audio", "SDLAudioP")),
)

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def proc_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except (PermissionError, FileNotFoundError, ProcessLookupError, OSError):
        return ""


def classify(name: str) -> str | None:
    for group, parts in CATEGORIES:
        if any(part in name for part in parts):
            return group
    return None


def active_emulators() -> list[dict]:
    matches = []
    for path in Path("/proc").glob("[0-9]*"):
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
            exe = os.readlink(path / "exe")
            if not Path(exe).name.lower().startswith("shadps4"):
                continue
            argv = (path / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "replace")
            matches.append({"pid": int(path.name), "binary": exe, "command": argv})
        except (PermissionError, ProcessLookupError, FileNotFoundError, OSError):
            pass
    return matches


def read_sched(tid: str, pid: int) -> dict | None:
    directory = Path(f"/proc/{pid}/task/{tid}")
    name = proc_text(directory / "comm")
    if not name:
        return None
    group = classify(name)
    if group is None:
        return None
    stat = proc_text(directory / "schedstat").split()
    try:
        cpu_ns, queue_ns, slices = (int(stat[i]) for i in range(3))
    except (IndexError, ValueError):
        cpu_ns, queue_ns, slices = 0, 0, 0
    # Kernel schedstat accounting may be disabled. The per-TID process stat
    # still exposes user+system CPU ticks without changing host sysctls.
    if cpu_ns == 0:
        raw = proc_text(directory / "stat")
        rest = raw.rsplit(")", 1)[-1].split()
        if len(rest) > 12:
            try:
                cpu_ns = ((int(rest[11]) + int(rest[12])) *
                          1_000_000_000 // os.sysconf("SC_CLK_TCK"))
            except (ValueError, OSError):
                pass
    # Read state/wchan only for selected threads; the regex excludes
    # changing task comm names and avoids relying on /proc stat parentheses.
    try:
        status = proc_text(directory / "status")
        state = re.search(r"(?m)^State:\s+([A-Z])", status)
        voluntary = re.search(r"(?m)^voluntary_ctxt_switches:\s+(\d+)", status)
        involuntary = re.search(r"(?m)^nonvoluntary_ctxt_switches:\s+(\d+)", status)
        result = {
            "tid": int(tid), "name": name, "group": group,
            "cpu_ns": cpu_ns, "queue_ns": queue_ns, "slices": slices,
            "state": state.group(1) if state else "?",
            "wchan": proc_text(directory / "wchan"),
            "voluntary": int(voluntary.group(1)) if voluntary else 0,
            "involuntary": int(involuntary.group(1)) if involuntary else 0,
        }
        return result
    except OSError:
        return None


def snapshot(pid: int) -> list[dict]:
    directory = Path(f"/proc/{pid}/task")
    try:
        tids = [x.name for x in directory.iterdir() if x.name.isdigit()]
    except (OSError, ProcessLookupError):
        return []
    ret = []
    for tid in tids:
        item = read_sched(tid, pid)
        if item:
            ret.append(item)
    return ret


def summarize(records: list[dict]) -> dict:
    windows = {}
    for rec in records:
        window = f"{int(rec['elapsed_s'] // 15) * 15}-{int(rec['elapsed_s'] // 15) * 15 + 15}s"
        key = (window, rec["group"])
        stats = windows.setdefault(key, {
            "cpu_ms": 0., "runqueue_wait_ms": 0., "samples": 0,
            "state_counts": Counter(), "wchan_counts": Counter(),
            "switches": 0, "nonvoluntary_switches": 0,
            "tids": set()
        })
        stats["cpu_ms"] += rec.get("delta_cpu_ns", 0) / 1e6
        stats["runqueue_wait_ms"] += rec.get("delta_queue_ns", 0) / 1e6
        stats["switches"] += rec.get("delta_voluntary", 0)
        stats["nonvoluntary_switches"] += rec.get("delta_involuntary", 0)
        stats["samples"] += 1
        stats["state_counts"][rec["state"]] += 1
        stats["wchan_counts"][rec["wchan"]] += 1
        stats["tids"].add(rec["tid"])
    return {
        w: {
            g: {
                **{k: round(v, 2) for k, v in details.items()
                   if k in ("cpu_ms", "runqueue_wait_ms")},
                "samples": details["samples"],
                "tids": sorted(details["tids"]),
                "states": dict(details["state_counts"]),
                "wchan_top": details["wchan_counts"].most_common(4),
                "voluntary_switches": details["switches"],
                "nonvoluntary_switches": details["nonvoluntary_switches"],
            } for (window, g), details in windows.items() if window == w
        } for w, _ in windows
    }


def audio_log_summary(logpath: Path) -> dict:
    counters = defaultdict(list)
    if not logpath.is_file():
        return {}
    matcher = re.compile(r"AUDIO_PRODUCER_TIMING port=(\d+) .*?ready=(\d+) no_guest_buffer=(\d+)")
    with logpath.open("rt", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            match = matcher.search(line)
            if match:
                port, ready, empty = match.groups()
                counters[port].append((int(ready), int(empty)))
    return {port: {
        "windows": len(data),
        "last_ten_ready": [x[0] for x in data[-10:]],
        "last_ten_missing": [x[1] for x in data[-10:]],
        "total_ready": sum(x[0] for x in data),
        "total_no_guest_buffer": sum(x[1] for x in data),
    } for port, data in counters.items()}


def run(duration: int, period: float) -> int:
    (ROOT / "logs").mkdir(parents=True, exist_ok=True)
    tag = "gow-guest-audio-wake-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / "logs" / tag
    out.mkdir(parents=True, exist_ok=False)
    running = active_emulators()
    if len(running) > 1 or (running and GAME not in running[0]["command"]):
        print("ACTIVE_OTHER_EMULATOR_DETECTED; NO_GAME_PROCESS_INTERRUPTED", flush=True)
        (out / "active-processes.json").write_text(json.dumps(running, indent=2) + "\n")
        return 2
    was_running = bool(running)
    if was_running:
        pid = running[0]["pid"]
        print(f"ATTACH_EXISTING_GOW=YES PID={pid}", flush=True)
        game_log = None
    else:
        if not GOOD.is_file() or GOOD.is_symlink():
            print("PINNED_PROVEN_GOW_BINARY_NOT_FOUND; NO_LAUNCH", flush=True)
            return 2
        if sha256(GOOD) != GOOD_SHA256:
            print("PINNED_PROVEN_GOW_BINARY_SHA256_MISMATCH; NO_LAUNCH", flush=True)
            return 2
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS",
                       f"unix:path={env['XDG_RUNTIME_DIR']}/bus")
        env["SHADPS4_RUNTIME_LOG_FILTER"] = "*:warn Lib.AudioOut:info Lib.Audio3d:info"
        env["SHADPS4_SDL_OUTER_PACING"] = "1"
        env["SHADPS4_AUDIO_TIMING_PROBE"] = "1"
        env["SHADPS4_UFFD_BATCH_READ"] = "1"
        env["SHADPS4_UFFD_PERF_PROBE"] = "0"
        argv = [str(GOOD), "--cpu-id-mode", "builtin", "--userfaultfd",
                "--game", GAME, "--fullscreen", "true", "--show-fps"]
        game_log = out / "full-console.log"
        handle = game_log.open("wb", buffering=0)
        try:
            child = subprocess.Popen(argv, cwd=BASE, env=env,
                                     stdin=subprocess.DEVNULL,
                                     stdout=handle, stderr=subprocess.STDOUT,
                                     start_new_session=True)
            pid = child.pid
        finally:
            handle.close()
        print(f"PROVEN_GOW_LAUNCHED=YES PID={pid}; AUTOMATIC_GAME_EXIT=NO", flush=True)

    (out / "launch.json").write_text(json.dumps({
        "pid": pid, "attached_existing": was_running,
        "proven_binary": str(GOOD),
        "proven_binary_sha256": GOOD_SHA256,
        "game": GAME, "read_only_probe": True,
        "duration_seconds": duration, "period_seconds": period,
    }, indent=2) + "\n")
    start = time.monotonic()
    prev = {}
    items = []
    with (out / "scheduler-samples.jsonl").open("wt", encoding="utf-8") as dest:
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= duration:
                break
            rows = snapshot(pid)
            if not rows:
                print(f"GOW_PROCESS_NO_LONGER_ALIVE_AT={elapsed:.1f}s", flush=True)
                break
            for sample in rows:
                key = sample["tid"]
                old = prev.get(key)
                if old:
                    for field, dfield in (
                        ("cpu_ns", "delta_cpu_ns"),
                        ("queue_ns", "delta_queue_ns"),
                        ("voluntary", "delta_voluntary"),
                        ("involuntary", "delta_involuntary")
                    ):
                        sample[dfield] = max(0, sample[field] - old[field])
                sample["elapsed_s"] = round(elapsed, 3)
                dest.write(json.dumps(sample, separators=(",", ":")) + "\n")
                items.append(sample)
                prev[key] = sample.copy()
            if int(elapsed // 15) != int((elapsed + period) // 15):
                print(f"HOST_WAKE_SAMPLING_T={elapsed:.1f}s", flush=True)
            time.sleep(period)
    results = summarize(items)
    result = {
        "threads": results, "samples": len(items),
        "process_alive": Path(f"/proc/{pid}").exists(),
        "attached_existing": was_running,
        "recorded_seconds": round(time.monotonic() - start, 2),
        "game_stopped_by_probe": False,
        "schedstat_may_be_disabled": not any(
            x.get("queue_ns", 0) > 0 for x in items),
        "cpu_ticks_fallback_supported": True,
        "audio_buffer_output": audio_log_summary(game_log) if game_log else {},
    }
    (out / "guest-wake-summary.json").write_text(json.dumps(result, indent=2) + "\n")
    (out / "summary-short.txt").write_text(
        "ATTACHED=" + str(was_running) + "\n" +
        "GOW_ALIVE=" + str(result["process_alive"]) + "\n" +
        "SCHEDSTAT_SUPPORTED=" + str(not result["schedstat_may_be_disabled"]) + "\n" +
        "WINDOWS=" + str(len(results)) + "\n" +
        "AUDIO_WINDOWS=" + str(list(result["audio_buffer_output"])) + "\n")
    tar = out.with_suffix(".tar.gz")
    with tarfile.open(tar, "w:gz") as archive:
        for file in out.rglob("*"):
            if not file.is_file() or file.name == "full-console.log":
                continue
            archive.add(file, arcname=f"{tag}/{file.name}")
        if game_log and game_log.is_file():
            with game_log.open("rb") as stream:
                text = stream.read(320 * 1024)
            (out / "console-start-excerpt.log").write_bytes(text)
            archive.add(out / "console-start-excerpt.log",
                        arcname=f"{tag}/console-start-excerpt.log")
    print(f"GOW_AUDIO_WAKE_RESULT=CAPTURED; PROCESS_ALIVE={result['process_alive']}")
    print(f"ARCHIVE={tar}")
    print("EMULATOR_BINARY=UNCHANGED; SYSCTL=UNCHANGED; SSH=OPEN", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=135)
    parser.add_argument("--sample-ms", type=int, default=250)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        assert classify("AK::AudioOutThr") == "wwise"
        assert classify("TaskManager04") == "game_workers"
        assert classify("shadPS4:GpuComm") == "gpu_command"
        assert classify("shadPS4:AudioOu") == "audio_output"
        assert classify("shadPS4:Uffd") == "userfaultfd"
        assert isinstance(snapshot(os.getpid()), list)
        print("GOW_AUDIO_WAKE_PROBE_SELFTEST=PASS")
        return 0
    if not 30 <= args.seconds <= 240 or not 100 <= args.sample_ms <= 2000:
        parser.error("expected 30-240 seconds and 100-2000 ms sampling")
    return run(args.seconds, args.sample_ms / 1000)


if __name__ == "__main__":
    raise SystemExit(main())
