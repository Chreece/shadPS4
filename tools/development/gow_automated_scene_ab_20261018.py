#!/usr/bin/env python3
"""GoW sparse CPU-dirty upload scanning: one executable legacy/optimized A/B.

Import only the pin-verified Ollama helper routines; do not call its main().
Stop exactly the test-launched shadPS4 process, then restore Ollama in finally.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

AUDIO = re.compile(r"AUDIO_PRODUCER_TIMING port=126 .*\bready=(\d+) no_guest_buffer=(\d+)")
FLIPS = re.compile(r"VIDEOOUT_FLIP_TIMING .*?\bgame_flips=(\d+) fps_x1000=(\d+)")
BIG_BIND = "GOW_BIG_BUFFER_BIND"
BIG_SYNC = "GOW_BIG_BUFFER_SYNC"
BIND = re.compile(r"shader=(0x[0-9a-f]+) calls=(\d+) requested=(\d+) clamped=(\d+) written=(\w+) tracked_write=(\d+) last_us=(\d+) sum_us=(\d+) max_us=(\d+)")
SYNC = re.compile(r"calls=(\d+) size=(\d+) written=(\w+) uploaded_bytes=(\d+) dirty_ranges=(\d+) scan_sum_us=(\d+) all_sum_us=(\d+) max_us=(\d+)")


def atomic_json(file: Path, obj: object) -> None:
    tmp = file.with_suffix(file.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")
    tmp.replace(file)


def log_summary(file: Path) -> dict:
    out = {"flip_windows": [], "audio_windows": [],
           "large_buffer_bind_samples": [],
           "large_buffer_sync_samples": [],
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
            elif BIG_BIND in line:
                if m := BIND.search(line):
                    out["large_buffer_bind_samples"].append({
                        "shader": m[1], "calls": int(m[2]),
                        "requested_size": int(m[3]),
                        "clamped_size": int(m[4]),
                        "written": m[5] == "true",
                        "tracked_write": int(m[6]),
                        "last_us": int(m[7]),
                        "total_us": int(m[8]),
                        "max_us": int(m[9])})
            elif BIG_SYNC in line:
                if m := SYNC.search(line):
                    out["large_buffer_sync_samples"].append({
                        "calls": int(m[1]), "size": int(m[2]),
                        "written": m[3] == "true",
                        "real_uploaded_bytes": int(m[4]),
                        "dirty_ranges": int(m[5]),
                        "scan_sum_us": int(m[6]),
                        "sync_sum_us": int(m[7]),
                        "max_sync_us": int(m[8])})
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
    # The last record for each stream represents a cumulative total,
    # not a separate sample to sum with earlier records.
    bind = out["large_buffer_bind_samples"]
    sync = out["large_buffer_sync_samples"]
    out["buffer_cost"] = {
        "target_shader": "0xdfd7cccc",
        "measured_bind_calls": bind[-1]["calls"] if bind else 0,
        "obtain_cpu_sum_us": bind[-1]["total_us"] if bind else None,
        "obtain_cpu_max_us": bind[-1]["max_us"] if bind else None,
        "measured_large_sync_calls": sync[-1]["calls"] if sync else 0,
        "actual_upload_bytes": sync[-1]["real_uploaded_bytes"] if sync else None,
        "real_dirty_copy_ranges": sync[-1]["dirty_ranges"] if sync else None,
        "dirty_scan_cpu_sum_us": sync[-1]["scan_sum_us"] if sync else None,
        "total_sync_cpu_sum_us": sync[-1]["sync_sum_us"] if sync else None,
    }
    return out



def copy_selected_profile(source: Path, destination: Path):
    """Clone only emulator config and title-specific saves; never link live saves.

    shadPS4 uses XDG_DATA_HOME/shadPS4 unless cwd/user portable profile exists.
    Its General.home_dir can override that, so redirect this field inside the
    CLONE only and verify original path before cloning.
    """
    if not source.is_dir() or source.is_symlink():
        raise RuntimeError("ORIGINAL_SHADPS4_USER_PROFILE_MISSING_OR_SYMLINK")
    config = source / "config.json"
    if not config.is_file() or config.is_symlink():
        raise RuntimeError("GAME_INSTALL_CONFIGURATION_MISSING_NO_SAVES_TOUCHED")
    original = json.loads(config.read_text())
    home_override = original.get("General", {}).get("home_dir", "")
    if not isinstance(home_override, str):
        raise RuntimeError("UNEXPECTED_HOME_DIR_CONFIGURATION_FORMAT")
    original_home = Path(home_override).expanduser() if home_override else source / "home"
    if not original_home.is_dir() or original_home.is_symlink():
        raise RuntimeError("LIVE_SAVE_HOME_UNAVAILABLE_OR_SYMLINK")
    destination.mkdir(parents=True)
    # Entire profile's potentially huge installed games, caches and screenshots
    # are deliberately not copied or linked into this temporary A/B test.
    folders = ("input_config", "custom_configs", "licenses", "patches")
    for item in source.iterdir():
        if item.is_symlink():
            if item.name in ("config.json",) + folders:
                raise RuntimeError("UNSAFE_SYMLINK_IN_REQUIRED_PROFILE_CONTENT")
            continue
        if item.is_file() and item.stat().st_size < 8 * 2**20:
            shutil.copy2(item, destination / item.name)
        if item.name in folders and item.is_dir():
            shutil.copytree(item, destination / item.name, symlinks=True)
    # Fail if a required folder contains a symlink, rather than risk silently
    # writing to original user data through one.
    for folder in folders:
        chosen = destination / folder
        if chosen.exists() and any(p.is_symlink() for p in chosen.rglob("*")):
            raise RuntimeError("CLONED_PROFILE_HAS_SYMLINK_NO_GAME_LAUNCH")
    copied = 0
    for user in original_home.iterdir():
        if not user.is_dir() or user.is_symlink():
            continue
        save_root = user / "savedata"
        game_saves = save_root / "CUSA34384"
        dest_user = destination / "home" / user.name
        dest_user.mkdir(parents=True)
        for small in user.iterdir():
            if small.is_file() and not small.is_symlink() and small.stat().st_size < 8 * 2**20:
                shutil.copy2(small, dest_user / small.name)
        if game_saves.is_dir() and not game_saves.is_symlink():
            # Preflight size and link safety BEFORE copying any private saves.
            for base, folders_found, files_found in os.walk(game_saves, followlinks=False):
                for folder in folders_found:
                    if (Path(base) / folder).is_symlink():
                        raise RuntimeError("SAVE_DIRECTORY_HAS_SYMLINK_FAIL_CLOSED")
                for name in files_found:
                    p = Path(base) / name
                    if p.is_symlink():
                        raise RuntimeError("SAVE_FILE_HAS_SYMLINK_FAIL_CLOSED")
                    copied += p.stat().st_size
                    if copied > 1200 * 2**20:
                        raise RuntimeError("GAME_SAVE_CLONE_EXCEEDS_1200MIB_FAIL_CLOSED")
            (dest_user / "savedata").mkdir()
            shutil.copytree(game_saves, dest_user / "savedata" / "CUSA34384",
                            symlinks=True)
    if copied == 0:
        raise RuntimeError("NO_GOW_SAVEDATA_FOUND_CANNOT_REPLAY_CONTINUE")
    # Force shadPS4 to resolve HomeDir INSIDE the isolated XDG user profile.
    # Do not edit the original config file, save, or any symlink to either.
    altered = json.loads((destination / "config.json").read_text())
    altered.setdefault("General", {})["home_dir"] = ""
    (destination / "config.json").write_text(json.dumps(altered, indent=2) + "\n")
    return copied


def profile_source_fingerprint(base: Path):
    sha = hashlib.sha256()
    for p in sorted((base / "home").glob("*/savedata/CUSA34384/**/*")):
        if p.is_file():
            sha.update(str(p.relative_to(base)).encode())
            with p.open("rb") as file:
                for part in iter(lambda: file.read(2**20), b""):
                    sha.update(part)
    return sha.hexdigest()


def prepare_isolated_profiles(cwd: Path, output: Path):
    if (cwd / "user").exists():
        raise RuntimeError("PORTABLE_USER_PROFILE_WOULD_OVERRIDE_XDG_ISOLATION")
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    original = data_home / "shadPS4"
    if not original.is_dir():
        raise RuntimeError("ORIGINAL_XDG_SHADPS4_PROFILE_MISSING")
    project_tests = cwd.parent
    private = Path(tempfile.mkdtemp(prefix="gow-auto-private-profiles-", dir=project_tests))
    try:
        baseline = private / "baseline" / "shadPS4"
        bytes_cloned = copy_selected_profile(original, baseline)
        for label in ("A_DENSE_CPU_UPLOAD", "B_SPARSE_CPU_UPLOAD"):
            dest = private / label / "shadPS4"
            dest.parent.mkdir(parents=True)
            shutil.copytree(baseline, dest, symlinks=False)
        config = baseline / "config.json"
        # Original user data stays untouched, including HOME and savedata.
        return {"root": private, "source": original,
                "baseline_sha256": profile_source_fingerprint(baseline),
                "cloned_save_bytes": bytes_cloned,
                "paths": {
                    "A_DENSE_CPU_UPLOAD": private / "A_DENSE_CPU_UPLOAD",
                    "B_SPARSE_CPU_UPLOAD": private / "B_SPARSE_CPU_UPLOAD"
                }}
    except BaseException:
        shutil.rmtree(private)
        raise


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ollama-helper", type=Path, required=True)
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--sha256", type=str, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--cwd", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=360)
    p.add_argument("--auto-input", type=Path, required=True)
    a = p.parse_args()

    if not 250 <= a.seconds <= 480 or not re.fullmatch(r"[a-f0-9]{64}", a.sha256):
        raise SystemExit("INVALID_GAME_PARAMETERS")
    spec = importlib.util.spec_from_file_location("pinned_gow_ollama_helper",
                                                   str(a.ollama_helper))
    if spec is None or spec.loader is None:
        raise SystemExit("PINNED_OLLAMA_MODULE_NOT_LOADABLE")
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)

    # IMPORTANT: fail closed before launching either game or touching Ollama.
    if not a.auto_input.is_file() or a.auto_input.is_symlink():
        raise SystemExit("PINNED_AUTOMATED_INPUT_MISSING_NO_CHANGES")
    screen_env = dict(os.environ)
    screen_env.setdefault("DISPLAY", ":0")
    preflight = subprocess.run(
        [sys.executable, "-B", str(a.auto_input), "--preflight",
         "--display", screen_env["DISPLAY"]],
        env=screen_env, timeout=18, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, check=False)
    print(preflight.stdout, flush=True)
    if preflight.returncode:
        raise SystemExit("AUTOMATED_XTEST_PREFLIGHT_FAILED_NO_CHANGES")
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
    report = {"binary_sha256": a.sha256, "max_seconds_per_mode": a.seconds,
              "automation": "X11 verified PID -> Continue Cross -> forward+right -> cutscene candidate -> 95s log-only",
              "no_image_comparison": True,
              "no_scene_matched_perf_claim": True,
              "tested_flags": ["SHADPS4_GOW_BIG_BUFFER_PROBE=1;SHADPS4_SPARSE_CPU_UPLOAD_WORDS=0",
                               "SHADPS4_GOW_BIG_BUFFER_PROBE=1;SHADPS4_SPARSE_CPU_UPLOAD_WORDS=1"],
              "modes": {}}
    owner = None
    profiles = None
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
        if profiles is None:
            raise RuntimeError("ISOLATED_GOW_SAVE_PROFILE_NOT_READY")
        env["XDG_DATA_HOME"] = str(profiles["paths"][label])
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
            "SHADPS4_GOW_BIG_BUFFER_PROBE": "1",
            "SHADPS4_SPARSE_CPU_UPLOAD_WORDS": "1" if enable else "0",
            "SHADPS4_FRAME_AUDIO_PROBE": "1",
        }
        env.update(flags)
        argv = [str(a.binary), "--cpu-id-mode", "builtin", "--userfaultfd",
                "--game", "CUSA34384", "--fullscreen", "true", "--show-fps"]
        atomic_json(directory / "launch.json", {"argv": argv, "env": flags,
                                                "sha256": a.sha256})
        before = time.monotonic()
        print(f"GAME_START={label} SPARSE_CPU_UPLOAD_WORDS={enable} BUFFER_TIMING=ON", flush=True)
        autopilot = None
        autopilot_exit = None
        with (directory / "full-console.log").open("wb", buffering=0) as console:
            candidate = subprocess.Popen(
                argv, cwd=a.cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=console, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                with (directory / "auto-input.log").open("wb", buffering=0) as controller_log:
                    autopilot = subprocess.Popen(
                        [sys.executable, "-B", str(a.auto_input),
                         "--pid", str(candidate.pid), "--display", env["DISPLAY"],
                         "--out", str(directory), "--launch-monotonic", repr(before),
                         "--max-seconds", str(a.seconds - 8)],
                        cwd=a.cwd, env=env, stdin=subprocess.DEVNULL,
                        stdout=controller_log, stderr=subprocess.STDOUT,
                        start_new_session=True)
                    previous_progress = -1
                    while (candidate.poll() is None and autopilot.poll() is None and
                           time.monotonic()-before < a.seconds):
                        seconds = time.monotonic()-before
                        current_progress = int(seconds//15)
                        if current_progress != previous_progress:
                            previous_progress = current_progress
                            print(f"RUN={label} SECOND={int(seconds)} AUTOMATED=YES", flush=True)
                            atomic_json(directory / f"vram-t{int(seconds)}.json", lib.vram())
                        if use_api_only and seconds >= 10 and int(seconds) % 5 == 0:
                            try:
                                contaminated |= bool(lib.loaded())
                            except Exception:
                                contaminated = True
                        time.sleep(.5)
                    if autopilot.poll() is None:
                        autopilot.terminate()
                        try:
                            autopilot.wait(timeout=9)
                        except subprocess.TimeoutExpired:
                            autopilot.kill()
                            autopilot.wait(timeout=8)
                    autopilot_exit = autopilot.returncode
            finally:
                if autopilot is not None and autopilot.poll() is None:
                    autopilot.terminate()
                    try:
                        autopilot.wait(timeout=9)
                    except subprocess.TimeoutExpired:
                        autopilot.kill()
                        autopilot.wait(timeout=8)
                # Own emulator only. Release XTest keys before terminating.
                lib.stop_game(candidate)
        elapsed = time.monotonic()-before
        candidate = None
        result = log_summary(directory / "full-console.log")
        result["elapsed_seconds"] = round(elapsed, 2)
        result["autopilot_exit_code"] = autopilot_exit
        status_path = directory / "auto-progress.json"
        result["auto_input"] = json.loads(status_path.read_text()) if status_path.is_file() else {}
        stage = result["auto_input"]
        print(f"AUTO_STAGE={label} MENU={stage.get('menu_seen_at')} "
              f"CONTINUE={stage.get('cross_at')} FORWARD={stage.get('forward_at')} "
              f"RIGHT={stage.get('right_at')} APPROACH={stage.get('approach_at')} "
              f"CANDIDATE={stage.get('cutscene_at')} "
              f"SUCCESS={stage.get('success')} EXIT={autopilot_exit}", flush=True)
        result["early_exit"] = not (autopilot_exit == 0 and stage.get("success", False))
        result["scene_alignment"] = {
            "navigation_heuristic_candidate_seen": stage.get("cutscene_at") is not None,
            "forward_right_steering_started": stage.get("right_at") is not None,
            "scene_equivalence_proven": False,
            "performance_basis": "console FPS, Audio3D and buffer-sync counters only",
            "image_capture_and_image_comparison": "disabled",
            "manual_controller_interference_not_deterministically_excluded": True,
        }
        result["valid"] = (not result["early_exit"] and
                           stage.get("right_at") is not None and
                           stage.get("menu_seen_at") is not None and
                           len(result["flip_windows"]) >= 12 and
                           len(result["audio_windows"]) >= 12 and
                           not result["gpu_device_losses"] and
                           not result["audio_backend_errors"])
        atomic_json(directory / "summary.json", result)
        print(f"RUN_RESULT={label} VALID={result['valid']} "
              f"LATE_FPS={result['late_fps_avg']} "
              f"LATE_AUDIO_READY={result['late_audio_fraction_avg']} "
              f"BIG_BUFFER_BINDS={len(result['large_buffer_bind_samples'])} "
              f"SYNC_RECORDS={len(result['large_buffer_sync_samples'])}", flush=True)
        if not result["valid"]:
            raise RuntimeError(f"INVALID_AUTOMATED_SCENE_OBSERVATION_{label}__CHECK_AUTO_INPUT_LOG")
        print(f"AUTOMATED_CONTROL_PATH_COMPLETED={label}; "
              "NO_SCENE_EQUIVALENCE_OR_IMAGE_COMPARISON_CLAIM", flush=True)
        return result

    try:
        # Isolate both game modes from the same exact Continue save snapshot.
        # Independent XDG profiles also keep test-created autosaves private.
        profiles = prepare_isolated_profiles(a.cwd, a.out)
        report["save_isolation"] = {
            "mode": "separate_XDG_DATA_HOME_profile_clones_A_B",
            "original_home_user_and_game_saves_untouched": True,
            "cloned_save_bytes": profiles["cloned_save_bytes"],
            "baseline_content_sha256": profiles["baseline_sha256"],
            "source_xdg_profile": str(profiles["source"]),
            "portable_profile_preflight": "PASS",
        }
        print("IDENTICAL_STARTING_SAVE_PROFILES=PASS copied_bytes=" +
              str(profiles["cloned_save_bytes"]), flush=True)
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
        for label, enabled in (("A_DENSE_CPU_UPLOAD", False), ("B_SPARSE_CPU_UPLOAD", True)):
            report["modes"][label] = play(label, enabled)
        legacy = report["modes"]["A_DENSE_CPU_UPLOAD"]
        sparse = report["modes"]["B_SPARSE_CPU_UPLOAD"]
        report["scene_compare"] = {
            "menu_continue_automated_both": all(
                mode["auto_input"].get("cross_at") is not None
                for mode in (legacy, sparse)),
            "forward_right_automated_both": all(
                mode["auto_input"].get("forward_at") is not None and
                mode["auto_input"].get("right_at") is not None
                for mode in (legacy, sparse)),
            "wolf_scene_candidates_both": all(
                mode["auto_input"].get("cutscene_at") is not None
                for mode in (legacy, sparse)),
            "image_comparison": "disabled",
            "scene_equivalence_proven": False,
            "do_not_infer_a_scene_aligned_win_from_late_metrics_alone": True,
        }
        left, right = legacy["buffer_cost"], sparse["buffer_cost"]
        report["comparison"] = {
            "dense_scan_sum_us": left["dirty_scan_cpu_sum_us"],
            "sparse_scan_sum_us": right["dirty_scan_cpu_sum_us"],
            "dense_sync_total_us": left["total_sync_cpu_sum_us"],
            "sparse_sync_total_us": right["total_sync_cpu_sum_us"],
            "dense_large_sync_calls": left["measured_large_sync_calls"],
            "sparse_large_sync_calls": right["measured_large_sync_calls"],
            "dense_uploaded_bytes": left["actual_upload_bytes"],
            "sparse_uploaded_bytes": right["actual_upload_bytes"],
            "dense_dirty_ranges": left["real_dirty_copy_ranges"],
            "sparse_dirty_ranges": right["real_dirty_copy_ranges"],
            "dense_late_fps": legacy["late_fps_avg"],
            "sparse_late_fps": sparse["late_fps_avg"],
            "dense_late_audio_delivery": legacy["late_audio_fraction_avg"],
            "sparse_late_audio_delivery": sparse["late_audio_fraction_avg"],
            "validated_improvement": False,
            "note": ("Navigation candidate is not proof of scene equality. "
                     "Use log-only CPU time per sync and repeated controlled workloads; "
                     "never compare screenshot images or elapsed-from-launch frames."),
        }
        report["fix_note"] = ("Sparse word traversal uses existing CPU dirty "
                              "summary bits; watcher masks, guest uploads, "
                              "GPU coherence and audio are unchanged.")
    except BaseException as error:
        errors.append(f"{type(error).__name__}: {error}")
        print("GOW_SPARSE_CPU_UPLOAD_AB_ERROR=" + errors[-1], flush=True)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        try:
            lib.stop_game(candidate)
            report["own_game_stopped"] = True
        except Exception as exc:
            errors.append("GAME_EXIT_ERROR=" + str(exc))
            report["own_game_stopped"] = False
        if profiles is not None:
            try:
                shutil.rmtree(profiles["root"])
                report["isolated_private_test_profiles_cleaned"] = True
            except Exception as error:
                errors.append("TEMPORARY_USER_PROFILE_CLEANUP_FAILED=" + str(error))
                report["isolated_private_test_profiles_cleaned"] = False
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
                             report.get("isolated_private_test_profiles_cleaned", False) and
                             report.get("own_game_stopped", False) and
                             report.get("ollama_models_restored", False) and
                             report["protected_graphics_sha256_unchanged"] and
                             report["experimental_binary_sha256_unchanged"] and
                             report.get("scene_compare", {}).get("wolf_scene_candidates_both", False) and
                             report.get("scene_compare", {}).get("forward_right_automated_both", False))
        atomic_json(a.out / "report.json", report)
        archive = a.out.parent / f"{a.out.name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(a.out, arcname=a.out.name)
        print("ARCHIVE=" + str(archive), flush=True)
        print("GAME_EXITED_BEFORE_OLLAMA_RELOAD=" + str(report["own_game_stopped"]), flush=True)
        print("OLLAMA_BACKEND_RESTORED=" + str(report["ollama_backend_restored"]), flush=True)
        print("OLLAMA_MODELS_RELOADED=" + str(report["ollama_models_restored"]), flush=True)
        print("GOW_SPARSE_CPU_UPLOAD_AB_RESULT=" + ("PASS" if report["success"] else "CHECK_LOGS"), flush=True)
        print("SSH_SESSION_PRESERVED=YES", flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
