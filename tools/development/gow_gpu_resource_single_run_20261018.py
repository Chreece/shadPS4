#!/usr/bin/env python3
"""Single GoW compute queue/SRT resource diagnosis, 8GiB Ollama VRAM gate.

Import only the pin-verified Ollama helper routines; do not call its main().
Stop exactly the test-launched shadPS4 process, then restore Ollama in finally.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tarfile
import time

AUDIO = re.compile(r"AUDIO_PRODUCER_TIMING port=126 .*\bready=(\d+) no_guest_buffer=(\d+)")
FLIPS = re.compile(r"VIDEOOUT_FLIP_TIMING .*?\bgame_flips=(\d+) fps_x1000=(\d+)")
QUEUE = "GOW_QUEUE_REG_PACKET"
SHARP = "GOW_SHARP_REASON"
SRT = "GOW_SRT_OFFSET_REASON"


def atomic_json(file: Path, obj: object) -> None:
    tmp = file.with_suffix(file.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")
    tmp.replace(file)


def log_summary(file: Path) -> dict:
    out = {"flip_windows": [], "audio_windows": [],
           "queue_reg_diagnostic_lines": 0, "sharp_missing_lines": 0,
           "srt_pointer_failures": 0, "srt_member_failures": 0,
           "srt_zero_flatbuf_total": 0, "srt_unsupported_total": 0,
           "srt_offset_failures": 0,
           "audio_backend_errors": 0, "gpu_device_losses": 0}
    with file.open(errors="replace") as f:
        for line in f:
            if "VIDEOOUT_FLIP_TIMING" in line:
                if m := FLIPS.search(line):
                    out["flip_windows"].append({
                        "fps": int(m[2]) / 1000, "game_flips": int(m[1])})
            elif "AUDIO_PRODUCER_TIMING port=126 " in line:
                if m := AUDIO.search(line):
                    r, missing = int(m[1]), int(m[2])
                    out["audio_windows"].append({
                        "ready": r, "missing": missing,
                        "fraction": round(r/max(1, r+missing), 4)})
            elif QUEUE in line:
                out["queue_reg_diagnostic_lines"] += 1
            elif SHARP in line:
                out["sharp_missing_lines"] += 1
            elif SRT in line:
                if "phase=pointer" in line: out["srt_pointer_failures"] += 1
                if "phase=member" in line: out["srt_member_failures"] += 1
                for key, field in (("zero_flatbuf=", "srt_zero_flatbuf_total"),
                                   ("unsupported=", "srt_unsupported_total")):
                    if m := re.search(r"\b" + re.escape(key) + r"(\d+)", line):
                        out[field] += int(m[1])
            if "Failed to compute offset for SRT walker" in line:
                out["srt_offset_failures"] += 1
            if "VK_ERROR_DEVICE_LOST" in line:
                out["gpu_device_losses"] += 1
            if "Failed to output to SDL audio stream" in line:
                out["audio_backend_errors"] += 1
    # Startup is not scene matched. Keep complete per-window data in archives.
    out["late_fps_avg"] = round(sum(x["fps"] for x in out["flip_windows"][-8:]) /
                               max(1, len(out["flip_windows"][-8:])), 2)
    out["late_audio_fraction_avg"] = round(
        sum(x["fraction"] for x in out["audio_windows"][-8:]) /
        max(1, len(out["audio_windows"][-8:])), 4)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ollama-helper", type=Path, required=True)
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--sha256", type=str, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--cwd", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=135)
    a = p.parse_args()

    if not 100 <= a.seconds <= 240 or not re.fullmatch(r"[a-f0-9]{64}", a.sha256):
        raise SystemExit("INVALID_GAME_PARAMETERS")
    spec = importlib.util.spec_from_file_location("pinned_gow_ollama_helper",
                                                   str(a.ollama_helper))
    if spec is None or spec.loader is None:
        raise SystemExit("PINNED_OLLAMA_MODULE_NOT_LOADABLE")
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)

    # IMPORTANT: fail closed before modifying Ollama.
    if lib.active_emulators():
        raise SystemExit("SHADPS4_ALREADY_RUNNING_NO_OLLAMA_CHANGE")
    for binary, expected in ((a.binary, a.sha256), (lib.GOOD, lib.GOOD_SHA)):
        if not binary.is_file() or binary.is_symlink() or lib.sha(binary) != expected:
            raise SystemExit(f"PINNED_GOW_EXECUTABLE_UNAVAILABLE_OR_CHANGED={binary}")
    if not (a.cwd / "source/.git").exists():
        raise SystemExit("ORIGINAL_GOW_SOURCE_MISSING")
    if lib.shutil.disk_usage(lib.ROOT).free < 2 * 2**30:
        raise SystemExit("DISK_SPACE_BELOW_2GIB_NO_OLLAMA_CHANGE")

    a.out.mkdir(exist_ok=False, parents=True)
    report = {"binary_sha256": a.sha256, "seconds_per_mode": a.seconds,
              "tested_flags": ["KNOWN_GOOD_AUDIO_AND_GPU_RUNTIME_UNCHANGED"],
              "modes": {}}
    owner = None
    models_before = []
    attempted_stop = False
    use_api_only = False
    candidate = None
    contaminated = False
    errors = []

    def interrupt(signum, _frame):
        raise InterruptedError(f"INTERRUPTED_SIGNAL_{signum}")

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupt)

    def play(label: str, enable: bool) -> dict:
        nonlocal candidate, contaminated
        if lib.active_emulators():
            raise RuntimeError("OTHER_EMULATOR_DETECTED_BETWEEN_RUNS")
        directory = a.out / label
        directory.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("SHADPS4_")}
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS",
                       "unix:path=" + env["XDG_RUNTIME_DIR"] + "/bus")
        flags = {
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
        argv = [str(a.binary), "--cpu-id-mode", "builtin", "--userfaultfd",
                "--game", "CUSA34384", "--fullscreen", "true", "--show-fps"]
        atomic_json(directory / "launch.json", {"argv": argv, "env": flags,
                                                "sha256": a.sha256})
        before = time.monotonic()
        print(f"GAME_START={label} PROVEN_GPU_LOGGING_AND_SRT_DIAGNOSTICS=YES", flush=True)
        with (directory / "full-console.log").open("wb", buffering=0) as console:
            candidate = subprocess.Popen(
                argv, cwd=a.cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=console, stderr=subprocess.STDOUT, start_new_session=True)
            marks = [30, 60, 90, 120]
            previous_progress = -1
            try:
                while candidate.poll() is None and time.monotonic()-before < a.seconds:
                    seconds = time.monotonic()-before
                    if marks and seconds >= marks[0]:
                        lib.screenshot(directory, marks.pop(0), env["DISPLAY"])
                    current_progress = int(seconds//15)
                    if current_progress != previous_progress:
                        previous_progress = current_progress
                        print(f"RUN={label} SECOND={int(seconds)}", flush=True)
                        atomic_json(directory / f"vram-t{int(seconds)}.json", lib.vram())
                    if use_api_only and seconds >= 10 and int(seconds) % 5 == 0:
                        try:
                            contaminated |= bool(lib.loaded())
                        except Exception:
                            contaminated = True
                    time.sleep(.5)
            finally:
                # STOP our game *before* the other mode / Ollama restart.
                lib.stop_game(candidate)
        elapsed = time.monotonic()-before
        candidate = None
        result = log_summary(directory / "full-console.log")
        result["elapsed_seconds"] = round(elapsed, 2)
        result["early_exit"] = elapsed < a.seconds-2
        result["valid"] = (not result["early_exit"] and
                           len(result["flip_windows"]) >= 12 and
                           len(result["audio_windows"]) >= 12 and
                           not result["gpu_device_losses"] and
                           not result["audio_backend_errors"])
        atomic_json(directory / "summary.json", result)
        print(f"RUN_RESULT={label} VALID={result['valid']} "
              f"LATE_FPS={result['late_fps_avg']} "
              f"LATE_AUDIO_READY={result['late_audio_fraction_avg']} "
              f"SRT_ERRORS={result['srt_offset_failures']} "
              f"QUEUE_REG_SAMPLES={result['queue_reg_diagnostic_lines']} "
              f"SHARP_FAILURES={result['sharp_missing_lines']}", flush=True)
        if not result["valid"]:
            raise RuntimeError(f"INVALID_GAME_OBSERVATION_{label}")
        return result

    try:
        # Make the free-VRAM decision *before* any Ollama API call, model
        # unload, or Docker stop. Use raw sysfs byte counts on the largest
        # discrete GPU, never a rounded GiB display value.
        report["vram_before"] = lib.vram()
        cards = report["vram_before"]["gpus"]
        if not cards or cards[0]["total"] < 16 * 2**30:
            raise RuntimeError("UNABLE_TO_IDENTIFY_16GIB_PLUS_DISCRETE_GPU_NO_OLLAMA_CHANGE")
        card = cards[0]
        free_bytes = max(0, card["total"] - card["used"])
        threshold_bytes = 8 * 2**30
        report["gpu_card"] = card["card"]
        report["free_vram_before_gib"] = round(free_bytes/2**30, 3)
        report["ollama_unload_threshold_gib"] = 8
        need_ollama_unload = free_bytes < threshold_bytes
        report["ollama_unload_required"] = need_ollama_unload
        print("GPU_FREE_VRAM_BEFORE_GIB=" +
              str(report["free_vram_before_gib"]), flush=True)
        print("OLLAMA_UNLOAD_REQUIRED=" + str(need_ollama_unload), flush=True)
        if need_ollama_unload:
            report["ollama_version_before"] = lib.api(
                "/api/version", timeout=10).get("version")
            models_before = lib.loaded()
            owner = lib.backend()
            atomic_json(a.out / "ollama-before.json", {
                "models": models_before, "backend": owner})
            print("OLLAMA_MODELS_BEFORE=" +
                  str([model.get("name") for model in models_before]), flush=True)
            if owner:
                attempted_stop = True
                r = lib.docker("stop", "--time", "12", owner["id"], timeout=45)
                (a.out / "ollama-stop.log").write_text(r.stdout+r.stderr)
                if r.returncode or lib.running_container(owner["id"]):
                    raise RuntimeError("OLLAMA_BACKEND_CONTAINER_STOP_FAILED")
                report["ollama_action"] = "BACKEND_STOPPED"
                print("OLLAMA_BACKEND_STOPPED=YES", flush=True)
            else:
                use_api_only = True
                for item in models_before:
                    name = item.get("name") or item.get("model")
                    if name:
                        lib.api("/api/generate", {"model": name, "prompt": "",
                                                 "stream": False, "keep_alive": 0},
                                timeout=40)
                if lib.loaded():
                    raise RuntimeError("OLLAMA_MODEL_API_UNLOAD_FAILED")
                report["ollama_action"] = "API_UNLOADED"
                print("OLLAMA_API_UNLOADED; MAY_RELOAD_IF_HA_CALLS_IT", flush=True)
            time.sleep(4)
            report["vram_after_decision"] = lib.vram()
            freed_cards = report["vram_after_decision"]["gpus"]
            freed = None
            for current in freed_cards:
                if current["card"] == card["card"] and current["total"] == card["total"]:
                    freed = (card["used"] - current["used"]) / 2**30
                    report["free_vram_after_unload_gib"] = round(
                        (current["total"] - current["used"]) / 2**30, 3)
                    break
            report["ollama_vram_reclaimed_gib"] = (
                round(freed, 3) if freed is not None else None)
            print("OLLAMA_VRAM_RECLAIMED_GIB=" +
                  str(report["ollama_vram_reclaimed_gib"]), flush=True)
            was_using_vram = sum(item.get("size_vram", 0) or 0
                                 for item in models_before)
            if freed is not None and was_using_vram > 3 * 2**30 and freed < 1.5:
                raise RuntimeError("OLLAMA_VRAM_NOT_RECLAIMED_NO_GAME")
            if owner and lib.running_container(owner["id"]):
                raise RuntimeError("OLLAMA_RESTARTED_PREMATURELY")
            if use_api_only and lib.loaded():
                raise RuntimeError("OLLAMA_RELOADED_BEFORE_GAME")
            # This is a reservation threshold, not permission to kill other
            # GPU workloads. If Ollama cannot release enough, fail closed.
            if freed is None or report["free_vram_after_unload_gib"] < 8:
                raise RuntimeError("LESS_THAN_8GIB_VRAM_AFTER_OLLAMA_UNLOAD_NO_GAME")
        else:
            # No API requests, no Docker operations, no model context changes.
            report["ollama_action"] = "UNCHANGED_FREE_VRAM_AT_LEAST_8GIB"
            report["vram_after_decision"] = lib.vram()
            report["ollama_vram_reclaimed_gib"] = 0.0
            print("OLLAMA_LEFT_RUNNING_AND_MODELS_UNTOUCHED=YES", flush=True)
        if lib.active_emulators():
            raise RuntimeError("ANOTHER_EMULATOR_STARTED_AFTER_VRAM_PREFLIGHT")
        for label, enabled in (("GPU_RESOURCE_TRACE", False),):
            report["modes"][label] = play(label, enabled)
        report["diagnostic_capture"] = report["modes"]["GPU_RESOURCE_TRACE"]
        report["fix_note"] = ("Unimplemented GPU SetQueueReg payload and unresolved "
                              "shader resource origin classified without memory changes.")
    except BaseException as error:
        errors.append(f"{type(error).__name__}: {error}")
        print("GOW_GPU_RESOURCE_TRACE_ERROR=" + errors[-1], flush=True)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        try:
            lib.stop_game(candidate)
            report["own_game_stopped"] = True
        except Exception as exc:
            errors.append("GAME_EXIT_ERROR=" + str(exc))
            report["own_game_stopped"] = False
        if attempted_stop and owner:
            try:
                if not lib.running_container(owner["id"]):
                    r = lib.docker("start", owner["id"], timeout=45)
                    (a.out / "ollama-start.log").write_text(r.stdout+r.stderr)
                    if r.returncode:
                        raise RuntimeError("OLLAMA_BACKEND_RESTART_FAILED")
                until = time.monotonic() + 100
                while True:
                    try:
                        lib.api("/api/version", timeout=4)
                        break
                    except Exception:
                        if time.monotonic() > until:
                            raise RuntimeError("OLLAMA_API_RESTART_TIMEOUT")
                        time.sleep(2)
                report["ollama_backend_restored"] = lib.running_container(owner["id"])
            except Exception as exc:
                errors.append("OLLAMA_BACKEND_RESTORE_ERROR=" + str(exc))
                report["ollama_backend_restored"] = False
        else:
            report["ollama_backend_restored"] = True
        if not attempted_stop and not use_api_only:
            # Ollama was never touched: do not invoke model loading, because
            # even a no-op generate can alter residency or context.
            report["ollama_models_restored"] = True
            report["ollama_untouched"] = True
        elif report["ollama_backend_restored"]:
            try:
                report["ollama_models_restored"] = lib.reload_models(models_before, a.out)
            except Exception as exc:
                errors.append("OLLAMA_MODEL_RELOAD_ERROR=" + str(exc))
                report["ollama_models_restored"] = False
        else:
            report["ollama_models_restored"] = False
        try:
            report["vram_after_ollama_recovery"] = lib.vram()
        except Exception:
            pass
        report["protected_graphics_sha256_unchanged"] = (lib.GOOD.is_file() and
                                                         lib.sha(lib.GOOD) == lib.GOOD_SHA)
        report["experimental_binary_sha256_unchanged"] = (a.binary.is_file() and
                                                          lib.sha(a.binary) == a.sha256)
        report["ollama_model_reloaded_during_game"] = contaminated
        report["errors"] = errors
        report["success"] = (not errors and not contaminated and
                             report.get("own_game_stopped", False) and
                             report.get("ollama_models_restored", False) and
                             report["protected_graphics_sha256_unchanged"])
        atomic_json(a.out / "report.json", report)
        archive = a.out.parent / f"{a.out.name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(a.out, arcname=a.out.name)
        print("ARCHIVE=" + str(archive), flush=True)
        print("GAME_EXITED_BEFORE_OLLAMA_RELOAD=" + str(report["own_game_stopped"]), flush=True)
        print("OLLAMA_BACKEND_RESTORED=" + str(report["ollama_backend_restored"]), flush=True)
        print("OLLAMA_MODELS_RELOADED=" + str(report["ollama_models_restored"]), flush=True)
        print("GOW_GPU_RESOURCE_TRACE_RESULT=" + ("PASS" if report["success"] else "CHECK_LOGS"), flush=True)
        print("SSH_SESSION_PRESERVED=YES", flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
