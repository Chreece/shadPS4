#!/usr/bin/env python3
"""Run the verified GoT generic candidate without historical build launchers.

This runner does not build, patch game shaders, install an emulator, modify
ES-DE, inject input, or touch the ordinary shadPS4 user profile. It packages
the CPU-ID translation runtime produced by the same successful build, runs
with a persistent independent test profile, and captures bounded evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
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
import traceback
import urllib.request

ROOT = Path.home() / "projects/shadps4"
BIN_SHA = "5efed1a93796331556016548da2710f94f2efe00d8cb9ecbb1e4aef4f1d9fcc6"
REV = "79dc2126ac8ab88eacaa0f36a0e0ae71471efc55"
CANDIDATE = ROOT / "GhostOfTsushima/candidates" / (REV + "-" + BIN_SHA) / "shadps4"
BUILD = ROOT / "GhostOfTsushima/fresh-builds/ca69b6509427-3967624"
REPORT = ROOT / "logs/got-generic-srt-resume-20261024-124735-cwbxipfj/info/build-result.json"
GAME = Path("/mnt/roms-all/ps4-games/CUSA11456/eboot.bin")
SESSION = ROOT / "test-profiles/got-generic-world-20261024"
PROFILE = SESSION / "user"
EXCLUDED = {"shader", "cache", "log", "logs", "screenshots", "captures",
            "temp", "custom_configs"}
MAX_LOG_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_FILE = 12 * 1024 * 1024


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("VERIFIED_REGULAR_FILE_REQUIRED=" + str(path))
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify():
    if digest(CANDIDATE) != BIN_SHA:
        raise RuntimeError("CANDIDATE_BINARY_HASH_MISMATCH")
    if not os.access(CANDIDATE, os.X_OK):
        raise RuntimeError("CANDIDATE_NOT_EXECUTABLE")
    if not GAME.is_file():
        raise RuntimeError("GOT_EBOOT_MISSING=" + str(GAME))
    if not REPORT.is_file():
        raise RuntimeError("RECOVERED_BUILD_REPORT_NOT_FOUND")
    r = json.loads(REPORT.read_text())
    checks = {"status": "GENERIC_LARGE_INDEXED_RESOURCE_BUILD_PASS",
              "revision": REV, "candidate_sha256": BIN_SHA,
              "full_build": "PASS", "upstream_main":
              "396c2a09a38d22eaa02aab32c1967263dceab95f",
              "pr_5492_head": "9f55474ad45778f8ac08e923574f214d492997f2"}
    for key, expected in checks.items():
        if r.get(key) != expected:
            raise RuntimeError("VERIFIED_BUILD_REPORT_MISMATCH=" + key)
    if Path(r["candidate"]) != CANDIDATE:
        raise RuntimeError("VERIFIED_CANDIDATE_PATH_MISMATCH")
    return r


def ensure_cpu_id_bundle():
    """The composer copied only the executable, but CPU-ID auto needs runtime."""
    dest = CANDIDATE.parent / "cpu-id-runtime"
    required = ("bin64/drrun", "libshadps4_cpu_id.so",
                "lib64/release/libdynamorio.so")
    if dest.is_symlink():
        raise RuntimeError("CANDIDATE_CPU_ID_BUNDLE_SYMLINK_DISALLOWED")
    if not dest.is_dir():
        src = BUILD / "cpu-id-runtime"
        if src.is_symlink() or not src.is_dir():
            raise RuntimeError("BUILT_CPU_ID_RUNTIME_NOT_FOUND=" + str(src))
        for name in required:
            if not (src / name).is_file():
                raise RuntimeError("BUILT_CPU_ID_RUNTIME_INCOMPLETE=" + name)
        staging = CANDIDATE.parent / (".cpu-id-runtime-" + str(os.getpid()))
        if staging.exists() or staging.is_symlink():
            raise RuntimeError("CPU_ID_STAGING_PATH_OCCUPIED")
        try:
            shutil.copytree(src, staging, symlinks=True)
            staging.rename(dest)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    for name in required:
        if not (dest / name).is_file():
            raise RuntimeError("CANDIDATE_CPU_ID_RUNTIME_MISSING=" + name)
    if not os.access(dest / "bin64/drrun", os.X_OK):
        raise RuntimeError("CANDIDATE_DYNAMORIO_NOT_EXECUTABLE")
    print("CPU_ID_TRANSLATION_RUNTIME=COMPLETE", flush=True)


def prepare_profile():
    if SESSION.is_symlink():
        raise RuntimeError("TEST_PROFILE_SESSION_SYMLINK")
    if PROFILE.is_dir():
        if not (SESSION / "session.json").is_file():
            raise RuntimeError("TEST_PROFILE_EXISTS_WITHOUT_SESSION_MARKER")
        marker = json.loads((SESSION / "session.json").read_text())
        if marker.get("purpose") != "independent_generic_got_runtime":
            raise RuntimeError("TEST_PROFILE_DIFFERENT_OWNER")
        return
    if SESSION.exists():
        raise RuntimeError("TEST_PROFILE_SESSION_ALREADY_OCCUPIED")
    seeds = (
        ROOT / "test-profiles/got-reference-progress/user",
        ROOT / "test-profiles/got-automatic-coherence-progress/user",
        Path.home() / ".local/share/shadPS4",
    )
    seed = next((p for p in seeds if p.is_dir() and not p.is_symlink()
                 and (p / "config.json").is_file()), None)
    if seed is None:
        raise RuntimeError("NO_VALID_REFERENCE_PROFILE")
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(SESSION.parent).free < 2 * 1024**3:
        raise RuntimeError("INSUFFICIENT_SPACE_FOR_ISOLATED_SAVES")
    stage = Path(tempfile.mkdtemp(prefix=".got-generic-profile-", dir=SESSION.parent))
    try:
        shutil.copytree(seed, stage / "user", symlinks=False,
                        ignore=lambda p, names:
                        set(names).intersection(EXCLUDED) if Path(p) == seed
                        else {name for name in names if (Path(p) / name).is_symlink()})
        config_file = stage / "user/config.json"
        settings = json.loads(config_file.read_text())
        if not isinstance(settings, dict):
            raise RuntimeError("TEST_PROFILE_CONFIG_NOT_A_DICTIONARY")
        general = settings.setdefault("General", {})
        logging = settings.setdefault("Log", {})
        gpu = settings.setdefault("GPU", {})
        if not all(isinstance(v, dict) for v in (general, logging, gpu)):
            raise RuntimeError("TEST_PROFILE_CONFIG_GROUP_MALFORMED")
        # No external saves. No per-title settings or shader instrumentation.
        general["home_dir"] = ""
        gpu["dump_shaders"] = False
        gpu["patch_shaders"] = False
        logging["enable"] = True
        logging["filter"] = "*:warning Render:info Render_Vulkan:info Render_Recompiler:info Debug:info"
        config_file.write_text(json.dumps(settings, indent=2) + "\n")
        (stage / "session.json").write_text(json.dumps({
            "purpose": "independent_generic_got_runtime",
            "initial_candidate_sha256": BIN_SHA,
            "seed_type": "independent_reference_copy",
            "normal_profile_modified": False
        }, indent=2) + "\n")
        stage.rename(SESSION)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print("INDEPENDENT_TEST_PROFILE=" + str(PROFILE), flush=True)


def display_environment():
    env = os.environ.copy()
    if not env.get("DISPLAY"):
        try:
            p = subprocess.run(["systemctl", "--user", "show-environment"],
                               capture_output=True, text=True, timeout=6)
            if p.returncode == 0:
                for line in p.stdout.splitlines():
                    if "=" in line:
                        key, value = line.split("=", 1)
                        if key in {"DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
                                   "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY"}:
                            env.setdefault(key, value)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        sockets = sorted(Path("/tmp/.X11-unix").glob("X[0-9]*"))
        for socket in sockets:
            testenv = dict(env, DISPLAY=":" + socket.name[1:])
            try:
                p = subprocess.run(["xset", "q"], env=testenv,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=3)
                if p.returncode == 0:
                    env["DISPLAY"] = testenv["DISPLAY"]
                    break
            except (OSError, subprocess.TimeoutExpired):
                pass
    return env


def free_vram():
    for path in Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"):
        used = path.with_name("mem_info_vram_used")
        if used.is_file():
            try:
                return max(0, int(path.read_text()) - int(used.read_text()))
            except (OSError, ValueError):
                pass
    return None


def ollama(method, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request("http://127.0.0.1:7869" + method, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=12) as response:
        return json.loads(response.read())


def prepare_vram():
    free = free_vram()
    print("VRAM_FREE_BEFORE_GIB=" + (f"{free / 1024**3:.2f}" if free is not None
                                     else "UNAVAILABLE"), flush=True)
    unloaded = []
    if free is None or free >= 8 * 1024**3:
        return unloaded
    try:
        models = ollama("/api/ps").get("models", [])
        for model in models:
            name = model.get("name") or model.get("model")
            if name:
                ollama("/api/generate", {"model": name, "keep_alive": 0})
                unloaded.append(name)
        print("OLLAMA_MODELS_UNLOADED=" + str(len(unloaded)), flush=True)
    except Exception as e:
        print("OLLAMA_VRAM_RELEASE_WARNING=" + str(e), flush=True)
    return unloaded


def restore_ollama(unloaded):
    for name in unloaded:
        try:
            ollama("/api/generate", {"model": name, "prompt": "",
                                     "keep_alive": "20m"})
            print("OLLAMA_RELOADED=" + name, flush=True)
        except Exception as e:
            print("OLLAMA_RELOAD_WARNING=" + name + ":" + str(e), flush=True)


def screenshot(folder, env, ordinal):
    output = folder / f"frame-{ordinal:02d}.png"
    commands = []
    if shutil.which("scrot"):
        commands.append(["scrot", "-o", str(output)])
    if shutil.which("import"):
        commands.append(["import", "-window", "root", str(output)])
    for command in commands:
        try:
            p = subprocess.run(command, cwd=folder, env=env,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15)
            if p.returncode == 0 and output.is_file() and output.stat().st_size > 1024:
                return output
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        from PIL import ImageGrab
        ImageGrab.grab().save(output)
        if output.is_file() and output.stat().st_size > 1024:
            return output
    except Exception:
        pass
    return None


def preserve_tail(source, destination, limit=MAX_LOG_BYTES):
    if not source.is_file():
        return
    with source.open("rb") as handle:
        size = source.stat().st_size
        if size > limit:
            handle.seek(size - limit)
        data = handle.read(limit)
    destination.write_bytes(data)


def summarize_log(info, files):
    categories = Counter()
    samples = {}
    pattern = re.compile(r"error|warn|missing|unresolved|descriptor|resource|srt|fault|"
                         r"pipeline|image|shader|vk_|gpu readback", re.I)
    for name in files:
        if not name.is_file():
            continue
        for line in name.read_text(errors="replace").splitlines():
            if not pattern.search(line):
                continue
            cleaned = re.sub(r"0x[0-9a-fA-F]+", "0xHEX", line.strip())
            cleaned = re.sub(r"\b[0-9]{4,}\b", "N", cleaned)
            category = "error" if re.search("error|failed|unresolved", line, re.I) else (
                "warning" if re.search("warn|missing", line, re.I) else "gpu-shader")
            categories[category] += 1
            if cleaned not in samples and len(samples) < 180:
                samples[cleaned] = category
    result = {"categories": dict(categories),
              "representative_lines": list(samples)[:180]}
    (info / "render-summary.json").write_text(json.dumps(result, indent=2) + "\n")


def stop_process(proc):
    if proc.poll() is not None:
        return
    for sig, pause in ((signal.SIGINT, 7), (signal.SIGTERM, 7),
                       (signal.SIGKILL, 3)):
        try:
            os.killpg(proc.pid, sig)
            proc.wait(timeout=pause)
            break
        except ProcessLookupError:
            break
        except subprocess.TimeoutExpired:
            continue


def archive(run):
    out = Path(str(run) + ".tar.gz")
    with tarfile.open(out, "w:gz") as tf:
        for file in sorted(run.rglob("*")):
            if file.is_file() and not file.is_symlink():
                rel = file.relative_to(run)
                if file.stat().st_size <= MAX_ARCHIVE_FILE:
                    tf.add(file, arcname=str(rel))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=420)
    args = parser.parse_args()
    if not 60 <= args.seconds <= 1200:
        parser.error("--seconds must be between 60 and 1200")
    (ROOT / "logs").mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="got-generic-runtime-20261024-",
                                dir=ROOT / "logs"))
    info = run / "info"
    info.mkdir()
    captures = run / "screenshots"
    captures.mkdir()
    proc = None
    unloaded = []
    start = time.monotonic()
    rc = 125
    try:
        verify()
        ensure_cpu_id_bundle()
        prepare_profile()
        env = display_environment()
        print("RUNTIME_GAME=" + str(GAME), flush=True)
        print("DISPLAY=" + env.get("DISPLAY", "UNSET"), flush=True)
        print("WAYLAND_DISPLAY=" + env.get("WAYLAND_DISPLAY", "UNSET"), flush=True)
        print("CANDIDATE_SHA256=" + BIN_SHA, flush=True)
        unloaded = prepare_vram()
        command = [str(CANDIDATE), "--game", str(GAME),
                   "--fullscreen", "true", "--show-fps"]
        (info / "runtime-input.json").write_text(json.dumps({
            "candidate": str(CANDIDATE), "binary_sha256": BIN_SHA,
            "source_revision": REV, "game": str(GAME),
            "duration_limit_seconds": args.seconds,
            "profile": str(PROFILE), "candidate_verified": True,
            "cpu_id_bundle": str(CANDIDATE.parent / "cpu-id-runtime"),
            "automatic_gameplay_input": False,
            "experimental_shader_flags": False, "es_de_modified": False
        }, indent=2) + "\n")
        console = run / "console.full.log"
        print("GAME_TEST_START=YES", flush=True)
        print("At the GoT menu press controller X (keyboard N) to enter the world.", flush=True)
        print("Press Ctrl+C once to stop early; your SSH session remains open.", flush=True)
        with console.open("wb") as log:
            proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                    cwd=SESSION, env=env, start_new_session=True)
            captures_done = 0
            next_capture = 70
            next_status = 20
            while proc.poll() is None:
                elapsed = time.monotonic() - start
                if elapsed >= args.seconds:
                    print("RUNTIME_LIMIT_REACHED=YES", flush=True)
                    break
                if elapsed >= next_status:
                    print(f"GAME_RUNNING_SECONDS={int(elapsed)}", flush=True)
                    next_status += 30
                if elapsed >= next_capture and captures_done < 6:
                    shot = screenshot(captures, env, captures_done + 1)
                    print("SCREENSHOT=" + (str(shot) if shot else "UNAVAILABLE"), flush=True)
                    captures_done += 1
                    next_capture += 65
                time.sleep(2)
            stop_process(proc)
            rc = proc.returncode if proc.returncode is not None else 125
        print("GAME_PROCESS_RETURN_CODE=" + str(rc), flush=True)
    except KeyboardInterrupt:
        print("USER_STOP_REQUESTED=YES", flush=True)
        if proc is not None:
            stop_process(proc)
        rc = 130
    except BaseException as e:
        print("GAME_TEST_ERROR=" + str(e), flush=True)
        (info / "failure.txt").write_text(traceback.format_exc())
        if proc is not None:
            stop_process(proc)
        rc = 125
    finally:
        try:
            preserve_tail(run / "console.full.log", run / "console.tail.log")
            # shadPS4 uses a portable 'user' folder under the test session.
            native_log = PROFILE / "log" / "shadps4.log"
            if not native_log.exists():
                native_log = PROFILE / "log" / "shad_log.txt"
            preserve_tail(native_log, run / "game.tail.log")
            summarize_log(info, [run / "console.tail.log", run / "game.tail.log"])
            (info / "runtime-result.json").write_text(json.dumps({
                "return_code": rc, "elapsed_seconds": round(time.monotonic() - start),
                "binary_sha256_after": digest(CANDIDATE),
                "gameplay_graphics_verified": False,
                "runtime_logs_collected": True,
                "es_de_modified": False
            }, indent=2) + "\n")
        except Exception as e:
            (info / "collection-error.txt").write_text(str(e) + "\n")
        restore_ollama(unloaded)
        output = archive(run)
        print("UPLOAD_THIS=" + str(output), flush=True)
        print("COMPLETE_RUNTIME_LOG=" + str(run / "console.full.log"), flush=True)
    return 0 if rc == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
