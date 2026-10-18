#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""GoW Ragnarök same-binary UFFD callback A/B with strict baseline protection.

Runs callback OFF then ON, same build and audio clock. Stops ONLY its own test
children after observation; never touches existing emulators, SSH, X11, host
settings, Sunshine, graphics sources or protected executables.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import time
from datetime import datetime

ROOT = Path.home() / "projects/shadps4"
ORIGINAL = ROOT / "GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
BINARY = (ROOT / "GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build" /
          "shadps4.fullgfx-uffd-callback-batch-gow-uffd-callback-batch-20261018-105338")
BIN_SHA = "96648b14d4aea65cc4ddc16a76f23938914f1b715b08e52342f494127b155c88"
GOOD = (ROOT / "GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build" /
        "shadps4.fullgfx-uffd-batched-read-gow-uffd-batched-read-20261017-231412")
GOOD_SHA = "8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef"
AUDIO = re.compile(r"\bAUDIO_(PRODUCER_TIMING|GUEST_SUBMIT|SDL_TIMING)\s+port=(\d+)(.*)")
UFFD = re.compile(r"\bUFFD_PERF\s+(.+)")
KV = re.compile(r"\b([a-z_][a-z_0-9]*)=(\d+)")

def sha(path):
    d = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            d.update(block)
    return d.hexdigest()

def running():
    found = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            exe = os.readlink(proc / "exe")
            state = (proc / "stat").read_text().rsplit(") ", 1)[1][0]
            if Path(exe).name.lower().startswith("shadps4") and state not in ("Z", "X"):
                found.append({"pid": int(proc.name), "exe": exe})
        except (OSError, ValueError, IndexError):
            pass
    return found

def threads(pid, second):
    rows = []
    for task in Path(f"/proc/{pid}/task").glob("[0-9]*"):
        try:
            name = (task / "comm").read_text().strip()
            if name.startswith("AK::Audio"): kind = "wwise"
            elif name.startswith("Game:Main"): kind = "game_main"
            elif name.startswith("TaskManager"): kind = "guest_jobs"
            elif "GpuComm" in name: kind = "gpu"
            elif "Uffd" in name: kind = "uffd"
            elif "AudioOu" in name or "SDLAudio" in name: kind = "output"
            else: continue
            stat = (task / "schedstat").read_text().split()
            status = (task / "status").read_text()
            state = re.search(r"^State:\s+([A-Z])", status, re.M)
            rows.append({"tid": int(task.name), "kind": kind, "name": name,
                         "state": state.group(1) if state else "?",
                         "wchan": (task / "wchan").read_text().strip(),
                         "cpu_ns": int(stat[0]), "runqueue_ns": int(stat[1]),
                         "sched_count": int(stat[2])})
        except (OSError, AttributeError, ValueError, IndexError):
            pass
    return {"second": round(second, 2), "threads": rows}

def analyze(log):
    out = {"producer": {}, "guest": {}, "sdl": {}, "uffd": {"windows": 0},
           "gpu_device_lost": 0, "audio_backend_errors": 0, "lines": 0}
    totals = ("cpu_faults", "completed", "gpu_faults", "audio_faults",
              "game_main_faults", "slow_21ms", "slow_100ms", "audio_slow_21ms",
              "read_calls", "read_events", "multi_reads", "gpu_callback_submits",
              "callback_faults_batched")
    with log.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            out["lines"] += 1
            out["gpu_device_lost"] += "VK_ERROR_DEVICE_LOST" in line
            out["audio_backend_errors"] += "Failed to output to SDL audio stream" in line
            m = AUDIO.search(line)
            if m:
                category = {"PRODUCER_TIMING": "producer",
                            "GUEST_SUBMIT": "guest", "SDL_TIMING": "sdl"}[m[1]]
                port = m[2]
                if port not in ("0", "22", "126"): continue
                v = {k: int(n) for k, n in KV.findall(m[3])}
                d = out[category].setdefault(port, {"windows": 0, "ready": 0,
                      "missing": 0, "late": 0, "max_gap_us": 0, "max_backend_us": 0,
                      "last10": [], "total_pcm_null": 0})
                d["windows"] += 1
                d["ready"] += v.get("ready", 0)
                d["missing"] += v.get("no_guest_buffer", 0)
                d["late"] += v.get("late_entries", v.get("late_gaps", 0))
                d["max_gap_us"] = max(d["max_gap_us"], v.get(
                    "max_entry_gap_us", v.get("max_gap_us", 0)))
                d["max_backend_us"] = max(d["max_backend_us"], v.get("max_backend_us", 0))
                d["total_pcm_null"] += v.get("null_pcm", 0)
                if "ready" in v and "no_guest_buffer" in v:
                    n = v["ready"] + v["no_guest_buffer"]
                    d["last10"].append(round(v["ready"] / n, 4) if n else 0)
            elif m := UFFD.search(line):
                u = out["uffd"]
                u["windows"] += 1
                v = {k: int(n) for k, n in KV.findall(m[1])}
                for k in totals: u[k] = u.get(k, 0) + v.get(k, 0)
                for k in ("max_cpu_us", "max_audio_us", "max_pending"):
                    u[k] = max(u.get(k, 0), v.get(k, 0))
    for d in out["producer"].values():
        last = d.pop("last10")[-10:]
        d["late_scene_ready_fraction_median"] = round(statistics.median(last), 4) if last else 0
        d["last10_ready_fractions"] = last
    u = out["uffd"]
    u["mean_events_per_read"] = round(u.get("read_events", 0) /
                                      max(1, u.get("read_calls", 0)), 3)
    return out

def run(label, enabled, duration, root):
    existing = running()
    if existing:
        raise RuntimeError(f"OTHER_EMULATOR_RUNNING_NO_PROCESS_TOUCHED: {existing}")
    out = root / label
    out.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("SHADPS4_")}
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={env['XDG_RUNTIME_DIR']}/bus")
    changes = {"SHADPS4_RUNTIME_LOG_FILTER": "*:warn Lib.AudioOut:info Lib.Audio3d:info",
               "SHADPS4_SDL_OUTER_PACING": "1", "SHADPS4_AUDIO_TIMING_PROBE": "1",
               "SHADPS4_UFFD_BATCH_READ": "1", "SHADPS4_UFFD_PERF_PROBE": "1",
               "SHADPS4_UFFD_CALLBACK_BATCH": str(enabled)}
    env.update(changes)
    argv = [str(BINARY), "--cpu-id-mode", "builtin", "--userfaultfd",
            "--game", "CUSA34384", "--fullscreen", "true", "--show-fps"]
    (out / "parameters.json").write_text(json.dumps(
        {"argv": argv, "test_env": changes, "binary_sha256": BIN_SHA}, indent=2) + "\n")
    observations = []
    start = time.monotonic()
    print(f"RUN={label} CALLBACK_BATCH={enabled} TEST_SECONDS={duration}", flush=True)
    with (out / "full-console.log").open("wb", buffering=0) as log:
        child = subprocess.Popen(argv, cwd=ORIGINAL, env=env, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        try:
            while time.monotonic() - start < duration:
                if child.poll() is not None:
                    break
                observations.append(threads(child.pid, time.monotonic() - start))
                time.sleep(1)
        finally:
            if child.poll() is None:
                print(f"TERMINATING_ONLY_OWN_TEST_CHILD={child.pid}", flush=True)
                child.terminate()
                try: child.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    print("STOPPING_ONLY_OWN_UNRESPONSIVE_CHILD", flush=True)
                    child.kill()
                    child.wait(timeout=8)
    (out / "threads.json").write_text(json.dumps(observations, indent=2) + "\n")
    result = analyze(out / "full-console.log")
    result["runtime_seconds"] = round(time.monotonic() - start, 2)
    result["ended_early"] = result["runtime_seconds"] < duration - 2
    result["wwise_thread_seen"] = any(
        any(t["kind"] == "wwise" for t in row["threads"]) for row in observations)
    (out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    if result["ended_early"] or not result["wwise_thread_seen"] or (
        result["producer"].get("126", {}).get("windows", 0) < 8) or (
        result["gpu_device_lost"] or result["audio_backend_errors"]):
        raise RuntimeError(f"INVALID_GAMEPLAY_OR_AUDIO_CAPTURE={label}; see summary.json")
    print(f"RUN_PASS={label} AUDIO3D_READY={result['producer']['126']['ready']} "
          f"UFFD_FAULTS={result['uffd'].get('cpu_faults')} "
          f"GPU_CALLBACKS={result['uffd'].get('gpu_callback_submits')}", flush=True)
    return result

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--analyze":
        print(json.dumps(analyze(Path(sys.argv[2])), indent=2))
        return 0
    duration = int(os.environ.get("GOW_AB_SECONDS", "135"))
    if not 90 <= duration <= 240:
        raise SystemExit("GOW_AB_SECONDS_RANGE_90_TO_240")
    for path, expected in ((BINARY, BIN_SHA), (GOOD, GOOD_SHA)):
        if not path.is_file() or path.is_symlink() or sha(path) != expected:
            raise SystemExit(f"CHECKSUM_PIN_FAIL_NO_LAUNCH={path}")
    if not (ORIGINAL / "source/.git").exists():
        raise SystemExit("ORIGINAL_GAME_SOURCE_TREE_MISSING_NO_LAUNCH")
    if shutil.disk_usage(ROOT).free < 1073741824:
        raise SystemExit("INSUFFICIENT_FREE_SSD_NO_LAUNCH")
    if existing := running():
        raise SystemExit(f"EXISTING_EMULATOR_NO_LAUNCH_OR_TERMINATION={existing}")
    logs = ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    out = logs / ("gow-audio-uffd-ab-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(exist_ok=False)
    error = None
    try:
        a = run("A_CALLBACK_OFF", 0, duration, out)
        b = run("B_CALLBACK_ON", 1, duration, out)
        get = lambda x: x["producer"]["126"]["late_scene_ready_fraction_median"]
        summary = {"A_audio3d_ready_fraction": get(a), "B_audio3d_ready_fraction": get(b),
                   "ready_fraction_delta": round(get(b) - get(a), 4),
                   "A_audio3d_late_submits": a["guest"].get("22", {}).get("late", 0),
                   "B_audio3d_late_submits": b["guest"].get("22", {}).get("late", 0),
                   "A_gpu_callbacks": a["uffd"].get("gpu_callback_submits", 0),
                   "B_gpu_callbacks": b["uffd"].get("gpu_callback_submits", 0),
                   "A_faults": a["uffd"].get("cpu_faults", 0),
                   "B_faults": b["uffd"].get("cpu_faults", 0),
                   "promoted_to_proven": False,
                   "reason": "Scene match and subjective audio must also be checked."}
        (out / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
        print("AUDIO_UFFD_AB_COMPARISON=" + json.dumps(summary), flush=True)
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        print("AUDIO_UFFD_AB_ERROR=" + error, flush=True)
    finally:
        (out / "status.json").write_text(json.dumps(
            {"complete": error is None, "error": error,
             "experimental_binary_unchanged": sha(BINARY) == BIN_SHA,
             "protected_graphics_binary_unchanged": sha(GOOD) == GOOD_SHA,
             "ssh_and_sunshine_untouched": True}, indent=2) + "\n")
        archive = logs / f"{out.name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(out, arcname=out.name)
        print("ARCHIVE=" + str(archive), flush=True)
        print("PROVEN_EXECUTABLE_CHECKSUM_PRESERVED=" + str(sha(GOOD) == GOOD_SHA),
              flush=True)
        print("GOW_TEST_STOPPED_OWN_CHILD_ONLY=YES; SSH_SESSION_OPEN=YES", flush=True)
    return 1 if error else 0

if __name__ == "__main__":
    sys.exit(main())
