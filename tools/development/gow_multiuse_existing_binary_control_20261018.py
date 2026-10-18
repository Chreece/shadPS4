#!/usr/bin/env python3
"""GoW Ragnarök: isolate a 16:52 startup crash without compiling anything.

Checksum-protected known-good baseline first, then checksum-protected already
compiled SRT multiuse candidate with the feature off and on. Preserve ALL
source trees, build outputs and Sunshine. Quit only own game process group.
Unload Ollama only when free GPU VRAM is below 8 GiB, and restore it on exit.
"""
from __future__ import annotations
import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time

FPS_RE = re.compile(r"VIDEOOUT_FLIP_TIMING\b[^\n]*game_flips=(\d+)\s+fps_x1000=(\d+)")
AUDIO_RE = re.compile(r"AUDIO_PRODUCER_TIMING port=126 [^\n]*ready=(\d+)\s+no_guest_buffer=(\d+)")
MULTI_RE = re.compile(r"SRT_MULTIUSE_RESULT shader=(0x[0-9a-fA-F]+)\s+collisions=(\d+)\s+retained=(\d+)")
FATALS = ("Unhandled access violation", "Executed from address 0x0",
          "SignalHandler: Unreachable code", "VK_ERROR_DEVICE_LOST",
          "terminate called after throwing")


def save(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + ".new")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")
    temp.replace(path)


def summarize(console: Path) -> dict:
    data = {"flip_windows": 0, "flips": 0, "positive_fps_windows": 0,
            "late_fps": [], "audio_windows": 0, "late_audio_ready": [],
            "srt_errors": 0, "invalid_sharps": 0,
            "multiuse_collisions": 0, "multiuse_retained": 0,
            "shaders_multiuse": 0, "fatal_signals": [],
            "async_worker_null_pc": False}
    fatal_lines = []
    with console.open(errors="replace") as f:
        for line in f:
            if m := FPS_RE.search(line):
                data["flip_windows"] += 1
                data["flips"] += int(m[1])
                fps = int(m[2]) / 1000
                data["positive_fps_windows"] += fps > 0
                data["late_fps"].append(fps)
            if m := AUDIO_RE.search(line):
                data["audio_windows"] += 1
                ready, missing = int(m[1]), int(m[2])
                data["late_audio_ready"].append(round(ready / max(1, ready+missing), 5))
            if "Failed to compute offset for SRT walker" in line:
                data["srt_errors"] += 1
            if "Sharp source was not flatenned" in line:
                data["invalid_sharps"] += 1
            if m := MULTI_RE.search(line):
                data["multiuse_collisions"] += int(m[2])
                data["multiuse_retained"] += int(m[3])
                data["shaders_multiuse"] += 1
            if any(s in line for s in FATALS):
                fatal_lines.append(line.strip()[:700])
            if "Async MemMapping Worker" in line:
                data["async_worker_null_pc"] = True
    data["fatal_signals"] = fatal_lines[:25]
    fps = data["late_fps"][-8:]
    ready = data["late_audio_ready"][-8:]
    data["late_fps_avg"] = round(sum(fps)/len(fps), 2) if fps else None
    data["late_audio_ready_avg"] = round(sum(ready)/len(ready), 4) if ready else None
    del data["late_fps"]
    del data["late_audio_ready"]
    return data


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ollama-helper", type=Path, required=True)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--baseline-sha", required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--candidate-sha", required=True)
    p.add_argument("--cwd", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--baseline-seconds", type=int, default=75)
    p.add_argument("--candidate-seconds", type=int, default=135)
    opt = p.parse_args()
    for digest in (opt.baseline_sha, opt.candidate_sha):
        if not re.fullmatch("[a-f0-9]{64}", digest):
            raise SystemExit("INVALID_EXECUTABLE_SHA256")
    if not (45 <= opt.baseline_seconds <= 110 and
            90 <= opt.candidate_seconds <= 210):
        raise SystemExit("INVALID_CAPTURE_WINDOWS")
    spec = importlib.util.spec_from_file_location("gow_ollama_safety", str(opt.ollama_helper))
    if not spec or not spec.loader:
        raise SystemExit("MISSING_PINNED_OLLAMA_HELPER")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    # Fail before any GPU or Ollama changes when inputs are unexpected.
    if helper.active_emulators():
        raise SystemExit("OTHER_SHADPS4_ALREADY_RUNNING_NO_CHANGES")
    expected = [(opt.baseline, opt.baseline_sha),
                (opt.candidate, opt.candidate_sha),
                (helper.GOOD, helper.GOOD_SHA)]
    for path, digest in expected:
        if not path.is_file() or path.is_symlink() or helper.sha(path) != digest:
            raise SystemExit("PROTECTED_EXECUTABLE_MISSING_OR_CHANGED=" + str(path))
    if not (opt.cwd / "source/.git").exists():
        raise SystemExit("PROVEN_GAME_CWD_MISSING")
    if shutil.disk_usage(opt.cwd).free < 2*2**30:
        raise SystemExit("PROJECT_DISK_SPACE_UNDER_2_GIB")
    opt.out.mkdir(parents=True, exist_ok=False)
    report = {
        "captured_at": dt.datetime.now().astimezone().isoformat(),
        "mode": "NO_COMPILATION_EXISTING_BINARIES",
        "original_game_data_unchanged": True,
        "graphics_baseline_sha": opt.baseline_sha,
        "candidate_sha": opt.candidate_sha,
        "ollama_free_vram_threshold_gib": 8,
        "stages": {},
        "exceptions": [],
    }
    original_models = []
    owner = None
    attempted_stop = False
    api_only = False
    current_game = None
    contaminated = False
    signals_seen = []
    def on_signal(signum, _):
        signals_seen.append(signum)
        raise InterruptedError("USER_SIGNAL_" + str(signum))
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_signal)

    def run_game(tag: str, binary: Path, digest: str, feature: bool, duration: int) -> dict:
        nonlocal current_game, contaminated
        if helper.active_emulators():
            raise RuntimeError("OTHER_SHADPS4_STARTED_BEFORE_" + tag)
        folder = opt.out / tag
        folder.mkdir()
        # Strip inherited diagnostic settings for a comparable launch.
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("SHADPS4_")}
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS",
                       "unix:path=" + env["XDG_RUNTIME_DIR"] + "/bus")
        flags = {
            "SHADPS4_SRT_PRESERVE_MULTIUSE": "1" if feature else "0",
            "SHADPS4_RUNTIME_LOG_FILTER": "*:warn Lib.AudioOut:info Lib.Audio3d:info",
            "SHADPS4_SDL_OUTER_PACING": "1",
            "SHADPS4_AUDIO_TIMING_PROBE": "1",
            "SHADPS4_UFFD_PERF_PROBE": "1",
            "SHADPS4_UFFD_BATCH_READ": "1",
            "SHADPS4_UFFD_CALLBACK_BATCH": "0",
            "SHADPS4_UFFD_READ_FIRST": "0",
            "SHADPS4_GPU_INVALIDATE_PROBE": "0",
            "SHADPS4_FRAME_AUDIO_PROBE": "1",
        }
        env.update(flags)
        args = [str(binary), "--cpu-id-mode", "builtin", "--userfaultfd",
                "--game", "CUSA34384", "--fullscreen", "true", "--show-fps"]
        save(folder/"start.json", {"args": args, "sha": digest,
                                   "env": flags, "duration": duration})
        start = time.monotonic()
        exit_status = None
        process_pid = None
        print(f"RUN_START={tag} SHA={digest} FEATURE={feature}", flush=True)
        with (folder/"full-console.log").open("wb", buffering=0) as out:
            current_game = subprocess.Popen(
                args, cwd=opt.cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            process_pid = current_game.pid
            screenshot_marks = [30, 60, 90, 120, 150, 180]
            previous_sample = -1
            try:
                while current_game.poll() is None and time.monotonic()-start < duration:
                    elapsed = time.monotonic()-start
                    while screenshot_marks and elapsed >= screenshot_marks[0]:
                        helper.screenshot(folder, screenshot_marks.pop(0), env["DISPLAY"])
                    sample = int(elapsed // 15)
                    if sample != previous_sample:
                        previous_sample = sample
                        vram = helper.vram()
                        save(folder/f"vram-{sample:02d}.json", vram)
                        print(f"RUN={tag} ELAPSED={int(elapsed)}", flush=True)
                    if api_only and sample > 0 and int(elapsed) % 6 == 0:
                        try:
                            contaminated |= bool(helper.loaded())
                        except Exception:
                            contaminated = True
                    time.sleep(0.5)
            finally:
                exit_status = current_game.poll()
                helper.stop_game(current_game)
        elapsed = time.monotonic()-start
        current_game = None
        result = summarize(folder/"full-console.log")
        result.update({"binary_sha256": digest, "feature": feature,
                       "pid": process_pid, "exit_code_before_own_stop": exit_status,
                       "elapsed_seconds": round(elapsed, 2),
                       "early_exit": elapsed < duration-2})
        result["startup_ok"] = (
            not result["early_exit"] and
            result["positive_fps_windows"] > 0 and
            not result["fatal_signals"]
        )
        result["full_cutscene_capture"] = (
            result["startup_ok"] and duration >= 100 and
            result["audio_windows"] >= 12 and result["flip_windows"] >= 12
        )
        if result["early_exit"] and result["async_worker_null_pc"]:
            if shutil.which("coredumpctl"):
                try:
                    capture = subprocess.run(
                        ["coredumpctl", "--no-pager", "info", str(process_pid)],
                        capture_output=True, text=True, timeout=8, check=False)
                    (folder/"core-summary.txt").write_text(
                        capture.stdout[-30000:] + capture.stderr[-2000:])
                except (OSError, subprocess.TimeoutExpired) as e:
                    (folder/"core-summary.txt").write_text("UNAVAILABLE=" + str(e))
        save(folder/"summary.json", result)
        print(f"RUN_RESULT={tag} STARTUP_OK={result['startup_ok']} "
              f"FULL_CUTSCENE={result['full_cutscene_capture']} "
              f"EXIT={exit_status} ELAPSED={result['elapsed_seconds']} "
              f"SRT_RETAINED={result['multiuse_retained']} "
              f"FPS={result['late_fps_avg']}", flush=True)
        report["stages"][tag] = result
        save(opt.out/"report.json", report)
        return result

    error = None
    try:
        report["vram_before"] = helper.vram()
        cards = report["vram_before"]["gpus"]
        if not cards or cards[0]["total"] < 16*2**30:
            raise RuntimeError("TARGET_GPU_NOT_DETECTED_NO_OLLAMA_ACTION")
        card = cards[0]
        free_bytes = max(0, card["total"]-card["used"])
        need_unload = free_bytes < 8*2**30
        report["unload_ollama"] = need_unload
        report["free_vram_before_gib"] = round(free_bytes/2**30, 3)
        print("GPU_FREE_VRAM_GIB=" + str(report["free_vram_before_gib"]), flush=True)
        print("OLLAMA_UNLOAD_REQUIRED=" + str(need_unload), flush=True)
        if need_unload:
            original_models = helper.loaded()
            owner = helper.backend()
            save(opt.out/"ollama-before.json", {"models": original_models,
                                                 "backend": owner})
            if owner:
                attempted_stop = True
                stopped = helper.docker("stop", "--time", "12", owner["id"], timeout=45)
                (opt.out/"ollama-stop.log").write_text(
                    stopped.stdout + stopped.stderr)
                if stopped.returncode or helper.running_container(owner["id"]):
                    raise RuntimeError("OLLAMA_BACKEND_STOP_FAILED")
                print("OLLAMA_BACKEND_STOPPED=YES", flush=True)
            else:
                api_only = True
                for item in original_models:
                    name = item.get("name") or item.get("model")
                    if name:
                        helper.api("/api/generate",
                                   {"model": name, "prompt": "", "stream": False,
                                    "keep_alive": 0}, timeout=40)
                if helper.loaded():
                    raise RuntimeError("OLLAMA_MODELS_NOT_UNLOADED")
            time.sleep(4)
            refreshed = helper.vram()["gpus"]
            matching = [x for x in refreshed
                        if x["card"]==card["card"] and x["total"]==card["total"]]
            if not matching:
                raise RuntimeError("VRAM_CARD_DISAPPEARED")
            report["free_vram_after_unload_gib"] = round(
                (card["total"]-matching[0]["used"])/2**30, 3)
            if card["total"]-matching[0]["used"] < 8*2**30:
                raise RuntimeError("FREE_VRAM_STILL_BELOW_8GIB_NO_GAME_LAUNCH")
        else:
            report["ollama_action"] = "NO_API_NO_DOCKER_NO_MODEL_TOUCH"

        baseline = run_game("01_PROTECTED_BASELINE", opt.baseline,
                            opt.baseline_sha, False, opt.baseline_seconds)
        if not baseline["startup_ok"]:
            report["conclusion"] = "BASELINE_FAILS_TOO_DO_NOT_BLAME_NEW_SRT"
            raise RuntimeError("PROTECTED_BASELINE_COULD_NOT_START")
        a = run_game("02_CANDIDATE_MULTIUSE_OFF", opt.candidate,
                     opt.candidate_sha, False, opt.candidate_seconds)
        if not a["startup_ok"] and a["early_exit"] and a["elapsed_seconds"] < 25:
            print("CANDIDATE_EARLY_EXIT_RETRY_SAME_BINARY_ONCE=YES", flush=True)
            repeat = run_game("02B_CANDIDATE_OFF_RETRY", opt.candidate,
                              opt.candidate_sha, False, opt.candidate_seconds)
            a = repeat
        if not a["full_cutscene_capture"]:
            report["conclusion"] = (
                "CANDIDATE_FAILS_WITH_MULTIUSE_DISABLED_NO_PROMOTION")
            raise RuntimeError("CANDIDATE_MULTIUSE_OFF_FAILED")
        b = run_game("03_CANDIDATE_MULTIUSE_ON", opt.candidate,
                     opt.candidate_sha, True, opt.candidate_seconds)
        if not b["full_cutscene_capture"]:
            report["conclusion"] = "MULTIUSE_ENABLED_FAILED_OR_GRAPHICS_STALLED"
            raise RuntimeError("CANDIDATE_MULTIUSE_ON_FAILED")
        report["comparison"] = {
            "legacy_srt_errors": a["srt_errors"],
            "multiuse_srt_errors": b["srt_errors"],
            "legacy_invalid_sharps": a["invalid_sharps"],
            "multiuse_invalid_sharps": b["invalid_sharps"],
            "multiuse_retained_consumers": b["multiuse_retained"],
            "multiuse_collisions": b["multiuse_collisions"],
            "legacy_late_fps": a["late_fps_avg"],
            "multiuse_late_fps": b["late_fps_avg"],
            "legacy_late_audio_ready": a["late_audio_ready_avg"],
            "multiuse_late_audio_ready": b["late_audio_ready_avg"],
            "feature_proven_to_fix_shader_or_audio": False,
            "note": "Non-scene-matched FPS/audio cannot alone establish benefit; examine screenshots.",
        }
        report["conclusion"] = "EXISTING_EXECUTABLE_AB_COMPLETE_EXAMINE_SHADER_RESULTS"
    except BaseException as ex:
        error = f"{type(ex).__name__}: {ex}"
        report["exceptions"].append(error)
        print("STARTUP_CONTROL_OR_AB_ERROR=" + error, flush=True)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        try:
            helper.stop_game(current_game)
            report["only_own_game_stopped"] = True
        except Exception as ex:
            report["exceptions"].append("OWN_GAME_STOP_ERROR: " + str(ex))
            report["only_own_game_stopped"] = False
        if attempted_stop and owner:
            try:
                if not helper.running_container(owner["id"]):
                    r = helper.docker("start", owner["id"], timeout=45)
                    (opt.out/"ollama-start.log").write_text(r.stdout+r.stderr)
                    if r.returncode:
                        raise RuntimeError("OLLAMA_BACKEND_RESTART_FAILED")
                until = time.monotonic()+100
                while True:
                    try:
                        helper.api("/api/version", timeout=4)
                        break
                    except Exception:
                        if time.monotonic()>until:
                            raise RuntimeError("OLLAMA_API_TIMEOUT_ON_RESTART")
                        time.sleep(2)
                report["ollama_backend_restored"] = helper.running_container(owner["id"])
            except Exception as ex:
                report["ollama_backend_restored"] = False
                report["exceptions"].append("OLLAMA_RESTORE_FAILURE: "+str(ex))
        else:
            report["ollama_backend_restored"] = True
        if not attempted_stop and not api_only:
            report["ollama_models_restored"] = True
            report["ollama_untouched"] = True
        elif report["ollama_backend_restored"]:
            try:
                report["ollama_models_restored"] = helper.reload_models(
                    original_models, opt.out)
            except Exception as ex:
                report["ollama_models_restored"] = False
                report["exceptions"].append("OLLAMA_MODEL_RESTORE_FAILURE: "+str(ex))
        else:
            report["ollama_models_restored"] = False
        report["ollama_reloaded_during_game"] = contaminated
        report["baseline_sha256_unchanged"] = (
            helper.sha(opt.baseline) == opt.baseline_sha)
        report["candidate_sha256_unchanged"] = (
            helper.sha(opt.candidate) == opt.candidate_sha)
        report["protected_2314_sha256_unchanged"] = (
            helper.sha(helper.GOOD) == helper.GOOD_SHA)
        report["no_source_changes"] = True
        report["success"] = (
            not report["exceptions"] and not contaminated and
            report.get("ollama_models_restored", False) and
            report.get("only_own_game_stopped", False) and
            report["baseline_sha256_unchanged"] and
            report["candidate_sha256_unchanged"] and
            report["protected_2314_sha256_unchanged"]
        )
        save(opt.out/"report.json", report)
        archive = opt.out.parent / (opt.out.name+".tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(opt.out, arcname=opt.out.name)
        print("ARCHIVE="+str(archive),flush=True)
        print("NO_REBUILD=YES PROVEN_BINARIES_UNCHANGED=" +
              str(report["baseline_sha256_unchanged"] and
                  report["candidate_sha256_unchanged"]),flush=True)
        print("OLLAMA_MODELS_RESTORED=" +
              str(report["ollama_models_restored"]),flush=True)
        print("SSH_SESSION_UNCHANGED=YES",flush=True)
    return 0 if report["success"] else 2


if __name__ == "__main__":
    sys.exit(main())
