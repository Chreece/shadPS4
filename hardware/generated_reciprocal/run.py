#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Validate generated reciprocals, then compare two guarded PES runs."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tarfile
import tempfile

import affinity_game_profile as profile
import affinity_game_guard as games
from build_candidate import build, command, digest

HERE = Path(__file__).resolve().parent
REFERENCE = "1bb40d5466221ef17dd3c0e364718c4845158d3b45d4570d30bb94f73128eb8f"


def cancel(signum, frame):
    raise KeyboardInterrupt


def prepare_runtime(runtime):
    user = runtime / "user"
    user.mkdir(parents=True)
    users = []
    for index in range(4):
        uid = 1000 + index
        for name in ("savedata", "trophy", "inputs"):
            (user / "home" / str(uid) / name).mkdir(parents=True)
        users.append({"user_id": uid, "user_name": "Reciprocal Test", "user_color": index + 1,
                      "player_index": index + 1, "shadnet_enabled": False})
    (user / "users.json").write_text(json.dumps({"Users": {"user": users}}))
    (user / "config.json").write_text(json.dumps({
        "GPU": {"null_gpu": True, "full_screen": False, "window_width": 640, "window_height": 360},
        "Log": {"filter": "*:Info", "flush_level": "info", "sync": True},
        "General": {"show_splash": False, "home_dir": str(user / "home")}}))


def display_environment():
    env = dict(os.environ)
    wanted = {b"DISPLAY", b"XAUTHORITY", b"WAYLAND_DISPLAY", b"XDG_RUNTIME_DIR"}
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        for proc in Path("/proc").iterdir():
            try:
                if not proc.name.isdigit() or proc.stat().st_uid != os.getuid():
                    continue
                if (proc / "comm").read_text().strip() not in {
                        "sunshine", "gnome-shell", "kwin_wayland", "xfce4-session"}:
                    continue
                for entry in (proc / "environ").read_bytes().split(b"\0"):
                    key, sep, value = entry.partition(b"=")
                    if sep and key in wanted:
                        env[key.decode()] = value.decode()
                if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
                    break
            except (OSError, UnicodeError):
                continue
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        raise RuntimeError("No desktop display found for the automatic homebrew run")
    env.update(ALSOFT_DRIVERS="null", SDL_AUDIODRIVER="dummy", SHADPS4_ENABLE_IPC="false")
    return env


def validate_probe(path):
    numeric = hashlib.sha256()
    rows = 0
    memory = []
    end = None
    with path.open() as stream:
        for line in stream:
            if line.startswith("RAW "):
                d = dict(x.split("=", 1) for x in line.split()[1:])
                numeric.update((" ".join(d[k] for k in (
                    "op", "in", "mxcsr_in", "mxcsr_out", "out", "errors")) + "\n").encode())
                rows += 1
            elif line.startswith("RECIPROCAL_MEMORY "):
                memory.append(dict(x.split("=", 1) for x in line.split()[1:]))
            elif line.startswith("RECIPROCAL_END "):
                end = dict(x.split("=", 1) for x in line.split()[1:])
    if rows != 121856 or numeric.hexdigest() != REFERENCE:
        raise RuntimeError("Generated reciprocal output differs from the PS4 reference")
    if not end or any(end.get(k) != v for k, v in {
            "rows": "121856", "state_errors": "0", "scalar_packed_differences": "0"}.items()):
        raise RuntimeError("Generated reciprocal state checks failed or the log is incomplete")
    names = ["rcpss_mem", "vrcpss_mem", "rsqrtss_mem", "vrsqrtss_mem"]
    if [x["op"] for x in memory] != names:
        raise RuntimeError("Missing or duplicate memory checks")
    for i, row in enumerate(memory):
        expected = "3eaaa800" if i < 2 else "3f13c800"
        if (row["boundary"] != expected or row["value"] != expected or
                row["boundary_faults"] != "0" or row["failures"] != "0" or
                int(row["recovered"]) != i + 1 or
                len(set(row["flags"].split("/"))) != 1):
            raise RuntimeError("Generated reciprocal memory recovery check failed")
    return {"rows": rows, "hardware_differences": 0, "memory_cases": memory,
            "log_sha256": digest(path)}


def homebrew(binary, work, evidence):
    runtime = work / "homebrew-runtime"
    prepare_runtime(runtime)
    game = work / "homebrew-game"
    (game / "sce_sys").mkdir(parents=True)
    shutil.copyfile(HERE / "eboot-generated.bin", game / "eboot.bin")
    shutil.copyfile(HERE / "param.sfo", game / "sce_sys/param.sfo")
    output = runtime / "user/data/reciprocal-hardware.txt"
    try:
        command([binary, "--cpu-id-mode", "translated", "--ignore-game-patch", game / "eboot.bin"],
                evidence / "homebrew-launch.log", runtime, timeout=180, env=display_environment())
        result = validate_probe(output)
        print("HOMEBREW=121856 numerical rows, 4 boundaries and 4 recovered faults passed", flush=True)
        return result
    finally:
        if output.exists():
            shutil.copyfile(output, evidence / output.name)
        for path in (runtime / "user/log").glob("*"):
            if path.is_file():
                shutil.copyfile(path, evidence / ("homebrew-" + path.name))


def translated_prefix(binary):
    runtime = binary.parent / "cpu-id-runtime"
    return [str(runtime / "bin64/drrun"), "-root", str(runtime), "-use_dll",
            str(runtime / "lib64/release/libdynamorio.so"), "-disable_rseq", "-no_follow_children",
            "-vm_base", "0x710020000000", "-no_vm_base_near_app", "-c",
            str(runtime / "libshadps4_cpu_id.so"), "--", str(binary), "--cpu-id-mode", "translated"]


def main():
    if len(sys.argv) != 1:
        raise RuntimeError("This runner takes no arguments")
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-generated-rcp-", dir=Path.home()))
    cache = Path.home() / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    installed = Path.home() / "Applications/shadps4/shadps4"
    summary = {"capture_complete": False, "stages": [], "performance": "awaiting gameplay captures"}
    work = lock = before = None
    handlers = {s: signal.signal(s, cancel) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        lock = (cache / "homebrew.lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name, expected in json.loads((HERE / "manifest.json").read_text()).items():
            if digest(HERE / name) != expected:
                raise RuntimeError("Test package checksum mismatch: " + name)
        before = digest(installed)
        if profile.emulators():
            raise RuntimeError("Close the current emulator normally before running this test")
        wrapper = Path.home() / ".local/bin/shadps4-esde"
        if wrapper.is_symlink():
            raise RuntimeError("Launcher is a symlink; left untouched")
        original = wrapper.read_text()
        (evidence / "launcher-before.sh").write_text(original)
        games.hook(original, wrapper, evidence / "preflight.json", "CUSA18676")
        work = Path(tempfile.mkdtemp(prefix="generated-rcp-", dir=cache))
        binary, summary["build"] = build(cache, evidence)
        if profile.emulators() or digest(installed) != before:
            raise RuntimeError("An emulator started or the installed build changed; no game test launched")
        summary["homebrew"] = homebrew(binary, work, evidence)
        missing = [name for name in ("ffmpeg", "xprop", "xwininfo") if not shutil.which(name)]
        summary["missing_capture_tools"] = missing
        if missing:
            print("FPS=Automatic screenshots unavailable (missing " + ", ".join(missing) +
                  "); please note the on-screen FPS during each match", flush=True)
        preservation = None
        for mode in ("native", "translated"):
            if digest(installed) != before:
                raise RuntimeError("Installed build changed between stages")
            print("PLAY=Use the same teams, stadium and settings in both runs. Play a day match for about two minutes, then a night match. Exit PES normally.", flush=True)
            print("Keep Moonlight connected until this stage finishes. The existing session guard controls the launch.", flush=True)
            prefix = [str(binary), "--cpu-id-mode", "native"] if mode == "native" else translated_prefix(binary)
            result = games.run_stage("CUSA18676", "PES reciprocal comparison", mode, binary, prefix,
                                     work, evidence, expected_preservation=preservation)
            summary["stages"].append(result)
            if not result["capture_complete"] or not result["clean_exit_verified"]:
                raise RuntimeError("Stage did not exit cleanly; collecting evidence before another launch")
            preservation = json.loads((work / ("CUSA18676-" + mode) / "preservation.json").read_text())
        summary["capture_complete"] = True
        summary["performance_frames_captured"] = all(x.get("fps_screenshots", 0) > 0 for x in summary["stages"])
        summary["performance"] = "Awaiting comparison of matching gameplay screenshots or user FPS readings; exit status alone is not a performance result."
    except BlockingIOError:
        summary["error"] = "Another CPU test holds the test lock; nothing launched"
        print("TEST_ERROR=" + summary["error"], flush=True)
    except KeyboardInterrupt:
        summary["error"] = "Interrupted; stopping only this test and collecting evidence"
        print("INTERRUPTED=Collecting evidence", flush=True)
    except Exception as error:
        summary["error"] = str(error)
        print("TEST_ERROR=" + str(error), flush=True)
    finally:
        for s in handlers:
            signal.signal(s, signal.SIG_IGN)
        errors = []
        active = False
        if work:
            for path in work.glob("*/job.json"):
                try:
                    job = json.loads(path.read_text())
                    for action in (games.restore, games.stop_owned):
                        try:
                            action(job)
                        except Exception as error:
                            errors.append(str(error))
                    active |= bool(games.owned(job))
                    status_file = path.parent / "status.json"
                    if status_file.exists():
                        status = json.loads(status_file.read_text())
                        active |= bool(status.get("bridge") and games.alive(status["bridge"]))
                except Exception as error:
                    errors.append(str(error))
        try:
            summary["installed_binary_unchanged"] = before is not None and digest(installed) == before
        except OSError:
            summary["installed_binary_unchanged"] = False
        summary["cleanup_errors"] = errors
        summary["owned_processes_active"] = active
        summary["capture_complete"] &= not errors and not active and summary["installed_binary_unchanged"]
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        archive_path = evidence.with_suffix(".tar.gz")
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(evidence, arcname=evidence.name)
        if work and not errors and not active:
            shutil.rmtree(work)
        if lock:
            lock.close()
        for s, handler in handlers.items():
            signal.signal(s, handler)
        print("UPLOAD_ONLY=" + str(archive_path), flush=True)
        print("SSH stays open. The installed emulator, original saves and session guard were not replaced.", flush=True)
    return 0 if summary["capture_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
