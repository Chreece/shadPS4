#!/usr/bin/env python3
"""Isolated GoW compute-shader causality experiment.

Proves whether the exact RADV-hung guest shader 0x04691bd5 is required to
reproduce the GPU hang. This intentionally omits ONE shader dispatch and is
NOT a general emulator fix or a releasable game patch.

No sudo, no modifications to the installed ES-DE binary, saves or drivers.
Restores both original source files BEFORE starting the trial game.
"""
from __future__ import annotations

import datetime
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback

HOME = Path.home()
SRC = HOME / "shadps4-esde-verified-builds/20261009-173836/source"
BUILD = HOME / "shadps4-esde-verified-builds/20261009-174801/build"
LIVE = HOME / "Applications/shadps4/shadps4"
PROCESS = SRC / "src/core/libraries/kernel/process.cpp"
RASTERIZER = SRC / "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
EXPECTED_HEAD = "4eb9fc5f92188abbb30eb2cc55980e922b2a0ec0"
EXPECTED_PROCESS = "05acbce16dc315c5abaccb5190011ceed6ad848a"
EXPECTED_LIVE = "faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834"
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
TRIAL = HOME / ("Applications/shadps4-gow-compute-bypass-" + STAMP)
REPORT = HOME / ("shadps4-gow-compute-bypass-" + STAMP + ".tar.gz")
WORK = Path(tempfile.mkdtemp(prefix=".gow-bypass-", dir=HOME))
SCHEDULE = (0, 3, 6, 9, 10, 11, 12, 13, 14, 15, 17, 20, 24, 29, 35, 43, 54)
BACKUPS = {}
RESULT = "INCOMPLETE"
PHASE = "preflight"
OWN_PROCESS = None
try:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
except (OSError, ValueError):
    pass

def note(msg):
    print(msg, flush=True)

def hashfile(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def command(args, *, logfile=None, timeout=20, env=None):
    if logfile:
        with open(logfile, "wb") as output:
            return subprocess.run(args, stdout=output, stderr=subprocess.STDOUT,
                                  check=False, timeout=timeout, env=env)
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          check=False, timeout=timeout, env=env)

def git(*args):
    return command(["git", "-C", str(SRC), *args], timeout=16)

def blob(path):
    p = git("hash-object", str(path))
    return p.stdout.decode(errors="replace").strip() if p.returncode == 0 else ""

def fail(reason):
    raise RuntimeError("SAFE_STOP: " + reason)

def active_emulators():
    result = []
    for fp in glob.glob("/proc/[0-9]*/comm"):
        try:
            pid = int(fp.split("/")[2])
            if os.stat(fp).st_uid != os.getuid():
                continue
            if Path(fp).read_text().strip().lower() in ("shadps4", "drrun"):
                result.append(pid)
        except (ValueError, OSError):
            pass
    return result

def watched_pids():
    result = {"Xorg": [], "sunshine": [], "shadps4": [], "drrun": []}
    for fp in glob.glob("/proc/[0-9]*/comm"):
        try:
            pid = int(fp.split("/")[2])
            name = Path(fp).read_text().strip().lower()
            for key in result:
                if name == key.lower():
                    result[key].append(pid)
        except (OSError, ValueError):
            pass
    return result

def gpu_state():
    for dev in sorted(Path("/sys/class/drm").glob("card*/device")):
        try:
            if (dev / "vendor").read_text().strip().lower() != "0x1002":
                continue
            values = {}
            for key in ("gpu_busy_percent", "mem_info_vram_used",
                        "mem_info_vram_total", "mem_info_gtt_used"):
                try:
                    values[key] = int((dev / key).read_text().strip())
                except (OSError, ValueError):
                    pass
            return values
        except OSError:
            pass
    return {}

def display_env():
    env = os.environ.copy()
    picks = []
    for path in glob.glob("/proc/[0-9]*/comm"):
        try:
            pid = int(path.split("/")[2])
            if os.stat(path).st_uid != os.getuid():
                continue
            name = Path(path).read_text().strip().lower()
            if not ("sunshine" in name or "es-de" in name):
                continue
            vals = {}
            for entry in Path("/proc/%d/environ" % pid).read_bytes().split(b"\0"):
                if b"=" in entry:
                    k, v = entry.split(b"=", 1)
                    vals[k.decode(errors="replace")] = v.decode(errors="surrogateescape")
            if vals.get("DISPLAY"):
                picks.append((0 if "es-de" in name else 1, pid, name, vals))
        except (OSError, UnicodeError, ValueError):
            continue
    picks.sort()
    if picks:
        _, pid, name, vals = picks[0]
        allowed = re.compile(
            r"^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|XDG_DATA_HOME|"
            r"XDG_SESSION_TYPE|DBUS_SESSION_BUS_ADDRESS|PATH|LD_LIBRARY_PATH|"
            r"PULSE_SERVER|VK_.*|MESA_.*|AMD_.*|RADV_.*|SDL_.*)$")
        for k, v in vals.items():
            if allowed.fullmatch(k):
                env[k] = v
        origin = "%s:%d" % (name, pid)
    else:
        origin = "SSH_FALLBACK"
        if not env.get("DISPLAY") and Path("/tmp/.X11-unix/X0").exists():
            env["DISPLAY"] = ":0"
        env.setdefault("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    if not env.get("DISPLAY"):
        fail("No X11 display: Moonlight is not required, but Sunshine's X11 session must exist")
    # Unlike the last RADV trace, we want the normal driver execution mode.
    env.pop("RADV_DEBUG", None)
    env.pop("SHADPS4_CPU_ID_RESTART", None)
    env["SHADPS4_CPU_ID_MODE"] = "auto"
    (WORK / "display-context.txt").write_text(
        "SOURCE=%s\nDISPLAY=%s\nRADV_DEBUG=unset\n" % (origin, env["DISPLAY"]))
    return env

def photo(file, env):
    binary = shutil.which("ffmpeg")
    if not binary:
        return False
    args = [binary, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-f", "x11grab", "-framerate", "1", "-i", env["DISPLAY"],
            "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "6",
            "-y", str(file)]
    try:
        p = command(args, env=env, timeout=3)
        if p.returncode == 0 and file.is_file() and file.stat().st_size > 1000:
            return True
        (WORK / "screenshot-errors.txt").open("a").write(
            "CAPTURE_FAILED=%r\n" % p.stdout[-800:])
    except (OSError, subprocess.TimeoutExpired) as error:
        (WORK / "screenshot-errors.txt").open("a").write(repr(error) + "\n")
    file.unlink(missing_ok=True)
    return False

def contact_sheet(path):
    try:
        from PIL import Image, ImageDraw
        frames = sorted(path.glob("*.jpg"))
        if not frames:
            return
        cols, width, height = 3, 400, 256
        canvas = Image.new("RGB",
            (cols * width, ((len(frames) + cols - 1) // cols) * height),
            (15, 15, 18))
        draw = ImageDraw.Draw(canvas)
        for i, frame in enumerate(frames):
            with Image.open(frame) as opened:
                thumbnail = opened.convert("RGB")
                thumbnail.thumbnail((width - 10, height - 26))
            x, y = (i % cols) * width, (i // cols) * height
            canvas.paste(thumbnail, (x + 4, y + 24))
            draw.text((x + 6, y + 5), frame.stem, fill="white")
        canvas.save(WORK / "contact-sheet.jpg", quality=83)
    except (ImportError, OSError, ValueError) as error:
        (WORK / "contact-sheet-error.txt").write_text(str(error))

def source_patch():
    process_text = PROCESS.read_text()
    rasterizer_text = RASTERIZER.read_text()
    process_anchor = (
        "s32 PS4_SYSV_ABI sceKernelLoadStartModule("
        "const char* moduleFileName, u64 args, const void* argp,\n"
        "                                          u32 flags, const void* pOpt, s32* pRes) {")
    dispatch_anchor = (
        "    const ComputePipeline* pipeline = pipeline_cache.GetComputePipeline();\n"
        "    if (!pipeline) {\n"
        "        return;\n"
        "    }\n")
    if process_text.count(process_anchor) != 1:
        fail("Unexpected guest module loader source signature")
    if rasterizer_text.count(dispatch_anchor) != 2:
        fail("Expected exactly two Vulkan compute dispatch entrypoints")
    if rasterizer_text.count("namespace Vulkan {") != 1:
        fail("Unexpected Vulkan source namespace layout")
    if rasterizer_text.count('#include "common/debug.h"') != 1:
        fail("Unexpected Vulkan include layout")
    cpp_helper = r'''
static bool ShouldSkipKnownHangingGowCompute(const ComputePipeline* pipeline,
                                              const AmdGpu::ComputeProgram& cs_program) {
    if (std::getenv("SHADPS4_GOW_SKIP_COMPUTE_04691BD5") == nullptr) {
        return false;
    }
    const auto& cs = pipeline->GetStage(Shader::SwStage::Compute);
    if (cs.pgm_hash != 0x04691bd5ULL) {
        return false;
    }
    static std::atomic<u64> skipped{0};
    const u64 count = skipped.fetch_add(1, std::memory_order_relaxed) + 1;
    if (count <= 3 || (count & (count - 1)) == 0) {
        LOG_WARNING(Render_Vulkan,
                    "GOW_COMPUTE_04691BD5_SKIPPED count={} grid={}x{}x{}",
                    count, cs_program.dim_x, cs_program.dim_y, cs_program.dim_z);
    }
    return true;
}

'''
    BACKUPS[str(PROCESS)] = PROCESS.read_bytes()
    BACKUPS[str(RASTERIZER)] = RASTERIZER.read_bytes()
    (WORK / "process.cpp.original").write_bytes(BACKUPS[str(PROCESS)])
    (WORK / "vk_rasterizer.cpp.original").write_bytes(BACKUPS[str(RASTERIZER)])
    PROCESS.write_text(process_text.replace(
        process_anchor,
        "// Preserve the correct host SysV stack alignment for this guest entrypoint.\n"
        "__attribute__((force_align_arg_pointer))\n" + process_anchor))
    RASTERIZER.write_text(
        rasterizer_text
        .replace('#include "common/debug.h"',
                 '#include <atomic>\n#include <cstdlib>\n#include "common/debug.h"\n'
                 '#include "common/logging/log.h"')
        .replace("namespace Vulkan {\n", "namespace Vulkan {\n\n" + cpp_helper)
        .replace(dispatch_anchor,
                 dispatch_anchor +
                 "    if (ShouldSkipKnownHangingGowCompute(pipeline, cs_program)) {\n"
                 "        return;\n"
                 "    }\n"))
    diff = git("diff", "--", "src/core/libraries/kernel/process.cpp",
               "src/video_core/renderer_vulkan/vk_rasterizer.cpp")
    (WORK / "experiment.patch").write_bytes(diff.stdout)
    if git("diff", "--check").returncode != 0:
        fail("Invalid source diff generated")
    note("TEMPORARY_SHADER_BYPASS=BUILT_ONLY_IF_EXPLICIT_ENV")
    
def restore_sources():
    status = {}
    for name, content in BACKUPS.items():
        try:
            Path(name).write_bytes(content)
            os.utime(name, None)
            status[name] = "restored"
        except OSError as error:
            status[name] = "RESTORATION_FAILED: %s" % error
    BACKUPS.clear()
    status["source_clean"] = (
        git("status", "--porcelain", "--untracked-files=no").stdout.strip() == b"")
    status["process_blob"] = blob(PROCESS)
    (WORK / "restoration.json").write_text(json.dumps(status, indent=2))
    if not status["source_clean"]:
        fail("Source restoration did not produce a clean tree")

def stop_trial():
    global OWN_PROCESS
    process = OWN_PROCESS
    if process is None or process.poll() is not None:
        return
    for signum, delay in ((signal.SIGINT, 5), (signal.SIGTERM, 4),
                          (signal.SIGKILL, 3)):
        if process.poll() is not None:
            break
        try:
            if os.getpgid(process.pid) != process.pid:
                break
            os.killpg(process.pid, signum)
            process.wait(timeout=delay)
        except ProcessLookupError:
            break
        except subprocess.TimeoutExpired:
            pass

def record_logs():
    patterns = re.compile(r"shadps4|sunshine|amdgpu|drm|radv|gpu|xorg|reset|"
                          r"watchdog|abort|segfault|oom|surface.lost", re.I)
    for label, args in [
        ("kernel", ["journalctl", "-k", "--no-pager", "--since", "-6 minutes"]),
        ("system", ["journalctl", "--no-pager", "--since", "-6 minutes"]),
    ]:
        try:
            p = command(args, timeout=12)
            lines = p.stdout.decode(errors="replace").splitlines()
            (WORK / (label + "-gpu.txt")).write_text(
                "\n".join(row for row in lines if patterns.search(row))[-850000:])
        except (OSError, subprocess.TimeoutExpired) as error:
            (WORK / (label + "-gpu-error.txt")).write_text(str(error))
    for root in [HOME / ".local/share/shadPS4/log", TRIAL / "user/log"]:
        for name in ("CUSA34384.log", "shadps4.log"):
            file = root / name
            try:
                if not file.is_file():
                    continue
                with file.open("rb") as stream:
                    if file.stat().st_size > 1_600_000:
                        stream.seek(-1_600_000, os.SEEK_END)
                    chunk = stream.read(1_600_000)
                (WORK / ("game-" + name)).write_bytes(chunk)
            except OSError:
                pass

def test_game(env):
    global OWN_PROCESS
    env = env.copy()
    env["SHADPS4_GOW_SKIP_COMPUTE_04691BD5"] = "1"
    env["SHADPS4_CPU_ID_MODE"] = "auto"
    env.pop("RADV_DEBUG", None)
    shots = WORK / "screenshots"
    shots.mkdir()
    photo_rows = []
    telemetry = []
    stop_photos = threading.Event()
    beginning = time.monotonic()

    def take_pictures():
        for second in SCHEDULE:
            while not stop_photos.is_set() and time.monotonic() - beginning < second:
                time.sleep(0.06)
            if stop_photos.is_set():
                break
            file = shots / ("%02d_t%03d.jpg" % (len(photo_rows), second))
            valid = photo(file, env)
            photo_rows.append({"planned_s": second,
                               "elapsed_s": round(time.monotonic() - beginning, 2),
                               "valid": valid, "file": file.name if valid else None})
            note("SHOT t=%02ds %s" % (second, "OK" if valid else "FAILED"))

    log = WORK / "emulator-stdout.log"
    with log.open("wb") as stdout:
        OWN_PROCESS = subprocess.Popen(
            [str(TRIAL / "shadps4"), "--cpu-id-mode", "auto",
             "--game", "CUSA34384", "--fullscreen", "true"],
            env=env, stdout=stdout, stderr=subprocess.STDOUT,
            start_new_session=True)
        thread = threading.Thread(target=take_pictures, daemon=True)
        thread.start()
        next_sample = 0.0
        while True:
            elapsed = time.monotonic() - beginning
            if elapsed >= next_sample:
                telemetry.append({"elapsed_s": round(elapsed, 2),
                                  "gpu": gpu_state(), "pids": watched_pids(),
                                  "trial_exit_code": OWN_PROCESS.poll()})
                next_sample = elapsed + 1
            if OWN_PROCESS.poll() is not None or elapsed >= 65:
                break
            time.sleep(0.12)
        if OWN_PROCESS.poll() is None:
            stop_trial()
        OWN_PROCESS.wait()
        rc = OWN_PROCESS.returncode
        OWN_PROCESS = None
        stop_photos.set()
        thread.join(timeout=4)
    (WORK / "screenshot-timeline.json").write_text(json.dumps(photo_rows, indent=2))
    (WORK / "gpu-process-timeline.json").write_text(json.dumps(telemetry, indent=2))
    contact_sheet(shots)
    # The first marker confirms that the targeted shader really executed.
    text = log.read_bytes()[-4_000_000:].decode(errors="replace")
    n_skips = len(re.findall("GOW_COMPUTE_04691BD5_SKIPPED", text))
    out = {
        "experiment": "skip only guest compute shader 0x04691bd5",
        "skip_log_occurrences": n_skips,
        "skip_executed": n_skips > 0,
        "exit_code": rc,
        "duration_s": round(time.monotonic() - beginning, 2),
        "device_lost": bool(re.search(
            r"Device lost during submit|GPU hang detected|VK_ERROR_DEVICE_LOST",
            text, re.I)),
        "srt_errors": text.count("Failed to compute offset for SRT walker"),
        "screenshots_captured": sum(1 for row in photo_rows if row["valid"]),
        "menu_confirmed": False,
        "moonlight_needed": False
    }
    (WORK / "trial.json").write_text(json.dumps(out, indent=2))
    (WORK / "emulator-stdout-last.log").write_bytes(log.read_bytes()[-2_200_000:])
    (WORK / "emulator-stdout-first.log").write_bytes(log.read_bytes()[:250_000])
    log.unlink(missing_ok=True)
    note("SKIP_EXECUTED=%s; GPU_LOST=%s; RC=%s" %
         (out["skip_executed"], out["device_lost"], rc))
    return out

def main():
    global RESULT, PHASE
    note("=== GOD OF WAR: ISOLATE EXACT HUNG COMPUTE SHADER 0x04691BD5 ===")
    note("One unattended test. No Moonlight; no changes to ES-DE.")
    PHASE = "validate source revision, binary, X display, and safe worktree"
    for prog in ("git", "cmake", "ffmpeg"):
        if not shutil.which(prog):
            fail("Required program not found: " + prog)
    if not SRC.is_dir() or not BUILD.is_dir() or not PROCESS.is_file() or not RASTERIZER.is_file():
        fail("Expected verified source or build tree missing")
    if not LIVE.is_file() or hashfile(LIVE) != EXPECTED_LIVE:
        fail("Working ES-DE executable differs from protected baseline")
    if git("rev-parse", "HEAD").stdout.decode(errors="replace").strip() != EXPECTED_HEAD:
        fail("Integrated source commit changed")
    if blob(PROCESS) != EXPECTED_PROCESS:
        fail("Guest module loader file changed unexpectedly")
    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        fail("Source worktree has uncommitted changes")
    if active_emulators():
        fail("An emulator is already running; do not interrupt it")
    # Refuse to alter source files while another process builds this same tree.
    builders = command(["ps", "-eo", "args="], timeout=10)
    if builders.returncode == 0:
        concurrent = [line for line in builders.stdout.decode(errors="replace").splitlines()
                      if str(BUILD) in line and
                      ("cmake --build" in line or "ninja" in line or "make -j" in line)]
        if concurrent:
            fail("A concurrent build uses this shadPS4 build directory")
    for part in ("bin64/drrun", "libshadps4_cpu_id.so",
                 "lib64/release/libdynamorio.so"):
        if not (BUILD / "cpu-id-runtime" / part).is_file():
            fail("CPU-ID runtime component missing: " + part)
    env = display_env()
    if not photo(WORK / "preflight-screen.jpg", env):
        fail("X11 screenshot capture unavailable, no game launched")
    if gpu_state().get("gpu_busy_percent", 0) >= 97:
        fail("GPU already nearly fully busy, no new workload launched")
    PHASE = "temporarily modify source for exact shader skip and proven stack alignment"
    source_patch()
    try:
        PHASE = "incremental release compilation (no full test suite)"
        cmd = ["cmake", "--build", str(BUILD), "--target",
               "shadps4", "--parallel", "5"]
        p = command(cmd, logfile=WORK / "build.log", timeout=900)
        if p.returncode != 0:
            fail("Trial build failed; see build.log")
        TRIAL.mkdir(parents=True, exist_ok=False)
        shutil.copy2(BUILD / "shadps4", TRIAL / "shadps4")
        shutil.copytree(BUILD / "cpu-id-runtime",
                        TRIAL / "cpu-id-runtime", symlinks=True)
    finally:
        restore_sources()
    PHASE = "verify source restoration and staged trial"
    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        fail("Source did not restore cleanly; no game launched")
    if hashfile(TRIAL / "shadps4") == EXPECTED_LIVE:
        fail("Trial binary matches unpatched ES-DE binary")
    smoke = command([str(TRIAL / "shadps4"), "--help"],
                    logfile=WORK / "smoke.log", timeout=15)
    if smoke.returncode != 0:
        fail("Trial binary CLI smoke failed")
    PHASE = "run isolated exact-shader bypass with screenshots and telemetry"
    outcome = test_game(env)
    PHASE = "collect game, kernel and Xorg evidence"
    record_logs()
    if hashfile(LIVE) != EXPECTED_LIVE:
        fail("Installed ES-DE executable unexpectedly changed")
    RESULT = ("SKIPPED_EXACT_SHADER" if outcome["skip_executed"]
              else "NO_MATCHING_COMPUTE_DISPATCH")
    note("GAME_INTRO_AND_MENU=NOT_YET_VISUALLY_CONFIRMED")
    
try:
    main()
except KeyboardInterrupt:
    RESULT = "INTERRUPTED_WITH_REPORT"
    stop_trial()
    (WORK / "error.txt").write_text("Interrupted: only our test process was stopped.\n")
except BaseException:
    RESULT = "SAFE_STOP_WITH_REPORT"
    stop_trial()
    (WORK / "error.txt").write_text(traceback.format_exc())
finally:
    if BACKUPS:
        try:
            restore_sources()
        except BaseException:
            (WORK / "source-restore-error.txt").write_text(traceback.format_exc())
    try:
        manifest = {
            "result": RESULT, "phase": PHASE,
            "live_sha": hashfile(LIVE) if LIVE.is_file() else None,
            "live_unchanged": LIVE.is_file() and hashfile(LIVE) == EXPECTED_LIVE,
            "source_clean": (git("status", "--porcelain",
                                 "--untracked-files=no").stdout.strip() == b""
                             if SRC.is_dir() else None),
            "trial_bin": str(TRIAL / "shadps4"),
            "menu_confirmed": False,
            "moonlight_not_required": True
        }
        (WORK / "manifest.json").write_text(json.dumps(manifest, indent=2))
    except BaseException:
        (WORK / "manifest-error.txt").write_text(traceback.format_exc())
    with tarfile.open(REPORT, "w:gz", compresslevel=6) as tar:
        for child in sorted(WORK.iterdir()):
            tar.add(child, arcname=child.name)
    note("RESULT=" + RESULT)
    note("REPORT=" + str(REPORT))
    note("MOONLIGHT=NOT_REQUIRED_YET")
    note("ESDE_INSTALLATION_AND_SSH=UNCHANGED")
    shutil.rmtree(WORK, ignore_errors=True)
sys.exit(0 if RESULT in ("SKIPPED_EXACT_SHADER",
                          "NO_MATCHING_COMPUTE_DISPATCH") else 1)
