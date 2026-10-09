#!/usr/bin/env python3
"""Unattended GoW Ragnarök startup, visual, and RADV hang evidence collection.

Only launches the already proven, separate shadPS4 trial binary. Never modifies
ES-DE, source, saves, drivers, or the current SSH session. No sudo required.
"""
import collections
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
import time
import traceback

HOME = Path.home()
TRIAL = HOME / "Applications/shadps4-gow-stackalign-trial-20261009-203151/shadps4"
INSTALLED = HOME / "Applications/shadps4/shadps4"
EXPECTED_TRIAL = "2e49283a3ef69d300c3a89ccc51e24f255ed777036125b1fc5e4b13440513839"
EXPECTED_INSTALLED = "faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834"
SHOT_TIMES = [0, 3, 6, 9, 11, 12, 14, 16, 19, 22, 26, 31, 38, 46, 55, 66, 78, 93, 108]
SAVED_DUMP_BUDGET = 28 * 1024 * 1024
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
REPORT = HOME / ("shadps4-gow-unattended-" + STAMP + ".tar.gz")
WORK = Path(tempfile.mkdtemp(prefix=".gow-unattended-", dir=HOME))
START_WALL = time.time()
RESULT = "PRECHECK_NOT_RUN"
PHASE = "preflight"
MANIFEST = {"runs": [], "report_version": 1, "game": "CUSA34384",
            "installed_sha_expected": EXPECTED_INSTALLED,
            "trial_sha_expected": EXPECTED_TRIAL}
try:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
except (OSError, ValueError):
    pass

def note(*args):
    print(*args, flush=True)

def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for piece in iter(lambda: f.read(1024 * 1024), b""):
            h.update(piece)
    return h.hexdigest()

def fail(message):
    raise RuntimeError("SAFE_STOP: " + message)

def execute(cmd, env=None, timeout=12):
    try:
        return subprocess.run(cmd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout,
                              text=True, errors="replace", check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return type("CmdFailure", (), {"returncode": -1, "stdout": str(e)})()

def select_graphics_environment():
    env = os.environ.copy()
    candidates = []
    for name_file in glob.glob("/proc/[0-9]*/comm"):
        try:
            pid = int(name_file.split("/")[2])
            if os.stat(name_file).st_uid != os.getuid():
                continue
            name = Path(name_file).read_text().strip().lower()
            if "es-de" not in name and "sunshine" not in name:
                continue
            values = {}
            for item in Path("/proc/%d/environ" % pid).read_bytes().split(b"\0"):
                if b"=" in item:
                    k, v = item.split(b"=", 1)
                    values[k.decode(errors="replace")] = v.decode(errors="surrogateescape")
            if values.get("DISPLAY"):
                candidates.append((0 if "es-de" in name else 1, pid, name, values))
        except (OSError, ValueError, UnicodeError):
            continue
    candidates.sort()
    if candidates:
        _, pid, name, values = candidates[0]
        allowed = re.compile(
            r"^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|XDG_DATA_HOME|"
            r"XDG_SESSION_TYPE|DBUS_SESSION_BUS_ADDRESS|PATH|LD_LIBRARY_PATH|"
            r"PULSE_SERVER|SDL_.*|VK_.*|RADV_.*|MESA_.*|AMD_.*)$"
        )
        for k, v in values.items():
            if allowed.fullmatch(k):
                env[k] = v
        origin = "%s PID=%d" % (name, pid)
    else:
        origin = "SSH_FALLBACK"
        if not env.get("DISPLAY") and Path("/tmp/.X11-unix/X0").exists():
            env["DISPLAY"] = ":0"
        env.setdefault("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    if not env.get("DISPLAY"):
        fail("No graphical X11 session. Sunshine/ES-DE can remain running without a Moonlight client.")
    env.pop("SHADPS4_CPU_ID_RESTART", None)
    env["SHADPS4_CPU_ID_MODE"] = "auto"
    (WORK / "display-context.txt").write_text(
        "GRAPHICAL_ENV_SOURCE=%s\nDISPLAY=%s\nWAYLAND_DISPLAY=%s\nXAUTHORITY=%s\n"
        % (origin, env.get("DISPLAY"), env.get("WAYLAND_DISPLAY"),
           env.get("XAUTHORITY", "<unset>")))
    note("DISPLAY=%s, SOURCE=%s" % (env["DISPLAY"], origin))
    return env

def screenshot(path, env, failure_log):
    path.parent.mkdir(parents=True, exist_ok=True)
    commands = []
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        commands.append([
            ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-f", "x11grab", "-framerate", "1", "-i", env["DISPLAY"],
            "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "5",
            "-y", str(path)
        ])
    image_import = shutil.which("import")
    if image_import:
        commands.append([image_import, "-window", "root", "-resize",
                         "1280x720>", "-quality", "82", str(path)])
    for cmd in commands:
        output = execute(cmd, env=env, timeout=12)
        if output.returncode == 0 and path.is_file() and path.stat().st_size > 1200:
            return True
        with failure_log.open("a") as f:
            f.write("CMD=%r\nRC=%d\nERROR=%s\n" % (
                cmd, output.returncode, output.stdout[-1300:]))
        path.unlink(missing_ok=True)
    return False

def screen_metrics(img, last):
    try:
        from PIL import Image
        with Image.open(img) as source:
            gray = source.convert("L").resize((64, 36))
            data = list(gray.getdata())
        bright = round(sum(data) / len(data), 2)
        nonblack = round(sum(i >= 18 for i in data) / len(data), 4)
        movement = None if last is None else round(
            sum(abs(a - b) for a, b in zip(data, last)) / len(data), 3)
        return {"mean_luma": bright, "nonblack_fraction": nonblack,
                "pixel_change": movement}, data
    except (ImportError, OSError, ValueError):
        return {"note": "Pillow unavailable; screenshots still saved"}, None

def contact_sheet(shot_dir):
    try:
        from PIL import Image, ImageDraw
        frames = sorted(shot_dir.glob("*.jpg"))
        if not frames:
            return
        width, height, cols = 405, 256, 3
        rows = (len(frames) + cols - 1) // cols
        result = Image.new("RGB", (width * cols, height * rows), "#141414")
        pen = ImageDraw.Draw(result)
        for n, frame in enumerate(frames):
            with Image.open(frame) as opened:
                img = opened.convert("RGB")
                img.thumbnail((width - 10, height - 30))
                x = (n % cols) * width + 4
                y = (n // cols) * height + 25
                result.paste(img, (x, y))
                pen.text(((n % cols) * width + 8, (n // cols) * height + 5),
                         frame.stem, fill="white")
        result.save(shot_dir.parent / "contact-sheet.jpg", quality=85)
    except (ImportError, OSError, ValueError) as e:
        (shot_dir.parent / "contact-sheet-note.txt").write_text(str(e))

def sample_gpu():
    stats = {}
    for dev in sorted(Path("/sys/class/drm").glob("card*/device")):
        try:
            if (dev / "vendor").read_text().strip().lower() != "0x1002":
                continue
            key = dev.parent.name
            sample = {}
            for field in ("gpu_busy_percent", "mem_info_vram_used",
                          "mem_info_vram_total", "mem_info_gtt_used"):
                try:
                    sample[field] = int((dev / field).read_text().strip())
                except (OSError, ValueError):
                    pass
            stats[key] = sample
        except OSError:
            pass
    return stats

def process_state(pid):
    state = {}
    for field in ("status", "statm", "cmdline"):
        try:
            data = Path("/proc/%d/%s" % (pid, field)).read_bytes()
            if field == "cmdline":
                state[field] = data.replace(b"\x00", b" ").decode(errors="replace")[:900]
            elif field == "status":
                interesting = ("Name:", "State:", "Threads:", "VmRSS:", "VmSize:")
                state[field] = [line for line in data.decode(errors="replace").splitlines()
                                if line.startswith(interesting)]
            else:
                state[field] = data.decode(errors="replace").strip()
        except OSError:
            pass
    return state

def log_summary(file):
    try:
        with file.open("rb") as f:
            raw = f.read(22_000_000)
        contents = re.sub(r"\x1b\[[0-9;]*m", "", raw.decode(errors="replace"))
        lines = contents.splitlines()
        pattern = re.compile(
            "Device lost|GPU hang|context is lost|Soft recovery|Unreachable|"
            "Failed to compute offset for SRT walker|"
            "Unexpected instruction for offset computation|"
            "Clamped size from|visible_game_frame|HOST_QUIT|"
            "shaders? failed|Controller.*disconnect|"
            "Assertion Failed|Fatal|vm fault", re.I)
        frequencies = collections.Counter()
        representative = {}
        milestones = []
        for line in lines:
            if pattern.search(line):
                key = re.sub(r"\b0x[0-9a-f]+\b", "0xADDR", line, flags=re.I)
                key = re.sub(r"\b[0-9]{5,}\b", "N", key)
                frequencies[key] += 1
                representative.setdefault(key, line)
                if "visible_game_frame" in line or "HOST_QUIT" in line:
                    milestones.append(line[:400])
        flags = {
            "device_lost": "Device lost during submit" in contents
                or "context is lost" in contents.lower(),
            "fmt_crash": "parse_format_string" in contents
                and ("SIGSEGV" in contents or "Unhandled access violation" in contents),
            "shader_srt_failure_count": contents.count("Failed to compute offset for SRT walker"),
            "shader_findilsb32_count": contents.count("Unexpected instruction for offset computation, FindILsb32"),
            "shader_phi_count": contents.count("Unexpected instruction for offset computation, Phi"),
            "startup_visible_frame_marker": "visible_game_frame" in contents,
        }
        summary = {"flags": flags, "lines_analyzed": len(lines),
                   "milestones": milestones[-30:],
                   "top_errors": [{"count": n, "message": representative[key][:400]}
                                  for key, n in frequencies.most_common(28)],
                   "last_log_lines": lines[-35:]}
        return summary
    except (OSError, ValueError) as e:
        return {"error": str(e)}

def newest_game_logs(folder, t0):
    paths = []
    root = Path(os.environ.get("XDG_DATA_HOME") or str(HOME / ".local/share")) / "shadPS4/log"
    candidates = [root, HOME / ".local/share/shadPS4/log", TRIAL.parent / "user/log"]
    for loc in candidates:
        for name in ("CUSA34384.log", "shadps4.log"):
            p = loc / name
            if p in paths or not p.is_file():
                continue
            paths.append(p)
            try:
                if p.stat().st_mtime < t0 - 4:
                    continue
                with p.open("rb") as f:
                    if p.stat().st_size > 2_000_000:
                        f.seek(-2_000_000, os.SEEK_END)
                    data = f.read(2_000_000)
                (folder / ("game-" + name)).write_bytes(data)
            except OSError as e:
                (folder / "game-log-error.txt").write_text(str(e))

def stop_only_trial_group(proc):
    for signum, grace in ((signal.SIGINT, 6), (signal.SIGTERM, 4),
                          (signal.SIGKILL, 3)):
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signum)
            proc.wait(timeout=grace)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            continue

def run_once(label, env, seconds):
    folder = WORK / label
    folder.mkdir(parents=True)
    shot_dir = folder / "screenshots"
    shot_dir.mkdir()
    error_log = folder / "screenshot-errors.txt"
    mode = env.get("RADV_DEBUG", "<unset>")
    note("\nRUN=%s; CPU_ID=auto; RADV_DEBUG=%s; LIMIT=%ds" %
         (label, mode, seconds))
    start = time.monotonic()
    wall = time.time()
    cmd = [str(TRIAL), "--cpu-id-mode", "auto", "--game", "CUSA34384",
           "--fullscreen", "true"]
    env_run = env.copy()
    env_run["SHADPS4_CPU_ID_MODE"] = "auto"
    rows = []
    last = None
    proc = None
    timeout_hit = False
    with (folder / "emulator-stdout.log").open("wb") as stdout:
        proc = subprocess.Popen(cmd, env=env_run, stdout=stdout,
                                stderr=subprocess.STDOUT, start_new_session=True)
        (folder / "process.txt").write_text("PID=%d\nCOMMAND=%r\n" % (proc.pid, cmd))
        position = 0
        try:
            while True:
                elapsed = time.monotonic() - start
                if position < len(SHOT_TIMES) and elapsed >= SHOT_TIMES[position]:
                    label_time = SHOT_TIMES[position]
                    path = shot_dir / ("%02d_t%03ds.jpg" % (position, label_time))
                    valid = screenshot(path, env_run, error_log)
                    meta, last_new = screen_metrics(path, last) if valid else ({"error":"screenshot failed"}, None)
                    if last_new is not None:
                        last = last_new
                    rows.append({"elapsed_s": round(elapsed, 2), "intended_s": label_time,
                                 "filename": path.name if valid else None,
                                 "screen": meta, "gpu": sample_gpu(),
                                 "process": process_state(proc.pid)})
                    note("  t=%3ds shot=%s bright=%s changed=%s" %
                         (label_time, ("ok" if valid else "FAILED"),
                          meta.get("mean_luma", "?"), meta.get("pixel_change", "?")))
                    position += 1
                if proc.poll() is not None:
                    break
                if elapsed >= seconds:
                    timeout_hit = True
                    note("  Safety limit reached; stopping ONLY the trial process group.")
                    break
                time.sleep(0.3)
        finally:
            if proc.poll() is None:
                stop_only_trial_group(proc)
            proc.wait()
    # A final screenshot can reveal if the compositor or GPU display survived.
    screenshot(shot_dir / "99_after_exit.jpg", env_run, error_log)
    contact_sheet(shot_dir)
    (folder / "screenshot-timeline.json").write_text(json.dumps(rows, indent=2))
    summary = log_summary(folder / "emulator-stdout.log")
    (folder / "error-summary.json").write_text(json.dumps(summary, indent=2))
    newest_game_logs(folder, wall)
    duration = round(time.monotonic() - start, 2)
    changed = [r["screen"].get("pixel_change") for r in rows
               if r["intended_s"] >= 22 and r["screen"].get("pixel_change") is not None]
    static_screen = len(changed) >= 3 and all(x < 2.0 for x in changed[-3:])
    verdict = {
        "run":label, "RADV_DEBUG":mode, "return_code":proc.returncode,
        "duration_seconds":duration, "timed_out":timeout_hit,
        "screenshots_collected":len(list(shot_dir.glob("*.jpg"))),
        "static_last_three_frames":static_screen,
        "gpu_lost":summary.get("flags", {}).get("device_lost", False),
        "shader_srt_errors":summary.get("flags", {}).get("shader_srt_failure_count", 0),
        "shader_findilsb32_errors":summary.get("flags", {}).get("shader_findilsb32_count", 0),
        "shader_phi_errors":summary.get("flags", {}).get("shader_phi_count", 0),
        "menu_or_intro_confirmed": False,
        "needs_visual_review": True
    }
    (folder/"verdict.json").write_text(json.dumps(verdict, indent=2))
    MANIFEST["runs"].append(verdict)
    note("  END RUN=%s; rc=%s; GPU_LOST=%s; screenshots=%d; static=%s" %
         (label, proc.returncode, verdict["gpu_lost"],
          verdict["screenshots_collected"], static_screen))
    return verdict

def collect_radv_reports(previous_names):
    candidates = [p for p in HOME.glob("radv_dumps_*") if p.is_dir()
                  and str(p) not in previous_names
                  and p.stat().st_mtime >= START_WALL - 10]
    if not candidates:
        return
    dest = WORK / "radv-hang-reports"
    dest.mkdir()
    metadata = []
    remaining = SAVED_DUMP_BUDGET
    for directory in candidates[:5]:
        bucket = dest / directory.name
        bucket.mkdir(exist_ok=True)
        for file in sorted(directory.rglob("*")):
            if not file.is_file():
                continue
            try:
                sz = file.stat().st_size
                rel = file.relative_to(directory)
                metadata.append({"source": str(file), "bytes": sz})
                if remaining <= 0:
                    continue
                if sz > remaining and file.suffix.lower() not in (".log", ".txt"):
                    continue
                target = bucket / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if sz <= remaining:
                    shutil.copyfile(file, target)
                    remaining -= sz
                elif file.suffix.lower() in (".log", ".txt"):
                    length = min(remaining, 1_500_000)
                    with file.open("rb") as f:
                        f.seek(-length, os.SEEK_END)
                        target.write_bytes(f.read(length))
                    remaining -= length
            except (OSError, ValueError) as err:
                metadata.append({"source": str(file), "error": str(err)})
    (dest / "manifest.json").write_text(json.dumps(metadata, indent=2))

def collect_host_info():
    folder = WORK / "host"
    folder.mkdir(exist_ok=True)
    cmds = [
        ("gpu-pci.txt", ["lspci", "-nnk"], 12),
        ("vulkan-summary.txt", ["vulkaninfo", "--summary"], 18),
        ("processes.txt", ["ps", "-eo", "pid,comm,args"], 9),
        ("kernel-messages.txt",
         ["journalctl", "-k", "--no-pager", "--since", "@%d" % int(START_WALL)], 15),
        ("user-journal.txt",
         ["journalctl", "--user", "--no-pager", "--since", "@%d" % int(START_WALL)], 15),
    ]
    for filename, cmd, timeout in cmds:
        if shutil.which(cmd[0]) is None:
            (folder / filename).write_text("UNAVAILABLE_COMMAND=%s" % cmd[0])
            continue
        output = execute(cmd, timeout=timeout)
        text = output.stdout
        if filename == "user-journal.txt":
            text = "\n".join([line for line in text.splitlines()
                              if re.search(r"sunshine|shadps4|es-de|radv|amdgpu|reset|oom", line, re.I)])
        (folder / filename).write_text(text[-1_200_000:])
    (folder / "gpu-final.json").write_text(json.dumps(sample_gpu(), indent=2))

def main():
    global RESULT, PHASE
    note("=== UNATTENDED GOD OF WAR: SCREENSHOTS + GRAPHICS HANG DIAGNOSTICS ===")
    note("Moonlight is NOT required: this uses the already running X11 session.")
    note("Production emulator and SSH are never modified.")
    PHASE = "check verified binaries and runtime"
    if not TRIAL.is_file() or not INSTALLED.is_file():
        fail("Expected isolated trial or production binary is missing")
    if TRIAL.resolve() == INSTALLED.resolve():
        fail("Trial resolves to the live ES-DE binary")
    trial_sha = digest(TRIAL)
    live_sha = digest(INSTALLED)
    MANIFEST["trial_sha_actual"] = trial_sha
    MANIFEST["installed_sha_actual"] = live_sha
    if trial_sha != EXPECTED_TRIAL or live_sha != EXPECTED_INSTALLED:
        fail("Executable SHA-256 differs from the verified working versions")
    rt = TRIAL.parent / "cpu-id-runtime"
    for subpath in ("bin64/drrun", "libshadps4_cpu_id.so",
                    "lib64/release/libdynamorio.so"):
        if not (rt / subpath).is_file():
            fail("Missing trial CPU-ID bundle file: " + subpath)
    for comm in glob.glob("/proc/[0-9]*/comm"):
        try:
            pid = int(comm.split("/")[2])
            if os.stat(comm).st_uid != os.getuid():
                continue
            if Path(comm).read_text().strip() in ("shadps4", "drrun"):
                fail("A game is already running; not interrupting PID %d" % pid)
        except (OSError, ValueError):
            continue

    PHASE = "find graphical session and prove unattended screenshot capture"
    env = select_graphics_environment()
    preflight_file = WORK / "preflight-display.jpg"
    if not screenshot(preflight_file, env, WORK/"preflight-screenshot-errors.txt"):
        fail("Could not capture the X11 screen with ffmpeg/ImageMagick. No game launched.")
    note("SCREENSHOT_PREFLIGHT=PASS (%d bytes)" % preflight_file.stat().st_size)
    preexisting_dumps = {str(p) for p in HOME.glob("radv_dumps_*") if p.is_dir()}

    PHASE = "unattended baseline startup screenshots and GPU telemetry"
    baseline = run_once("01-baseline", env, 112)

    # RADV hang reports are optional. The instrumented run alters synchronization
    # and can change behavior, so compare it against the unmodified first run.
    needs_debug = (baseline["gpu_lost"] or
                   (baseline["timed_out"] and baseline["static_last_three_frames"]
                    and baseline["shader_srt_errors"] > 100))
    if needs_debug:
        PHASE = "check GPU/display after baseline before additional run"
        time.sleep(7)
        probe = WORK / "between-runs-display.jpg"
        display_healthy = screenshot(probe, env, WORK/"between-runs-errors.txt")
        if display_healthy:
            PHASE = "instrumented RADV hang report and screenshot trial"
            debug_env = env.copy()
            debug_env["RADV_DEBUG"] = "hang,noumr"
            note("GPU/stall evidence found. Running ONE instrumented RADV trial.")
            run_once("02-radv-hang", debug_env, 90)
        else:
            note("X display is no longer capturable after GPU failure; skipping retry.")
            MANIFEST["second_run_skipped"] = "DISPLAY_UNHEALTHY_AFTER_GPU_FAILURE"
    else:
        MANIFEST["second_run_skipped"] = "BASELINE_DID_NOT_MEET_GPU_HANG_OR_STATIC_STALL_TRIGGER"

    PHASE = "collect RADV reports and host logs"
    collect_radv_reports(preexisting_dumps)
    collect_host_info()
    PHASE = "verify ES-DE baseline still unchanged"
    if digest(INSTALLED) != EXPECTED_INSTALLED:
        fail("Unexpected change in production executable checksum")
    RESULT = "ARCHIVE_READY_FOR_SCREENSHOT_REVIEW"

try:
    main()
except KeyboardInterrupt:
    RESULT = "INTERRUPTED_WITH_EVIDENCE"
    (WORK / "unexpected-error.txt").write_text("User interrupted script with Ctrl+C.\n")
except Exception:
    RESULT = "SAFE_STOP_WITH_EVIDENCE"
    (WORK / "unexpected-error.txt").write_text(traceback.format_exc())
finally:
    MANIFEST.update({
        "result": RESULT, "last_phase": PHASE,
        "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "actual_production_sha": digest(INSTALLED) if INSTALLED.is_file() else None,
        "production_esde_modified": False,
        "ssh_session_modified": False,
        "human_intro_menu_review_required": True
    })
    (WORK / "manifest.json").write_text(json.dumps(MANIFEST, indent=2))
    try:
        with tarfile.open(REPORT, "w:gz", compresslevel=6) as tar:
            for child in sorted(WORK.iterdir()):
                tar.add(child, arcname=child.name, recursive=True)
        note("\nRESULT=%s" % RESULT)
        note("RUN_COUNT=%s" % len(MANIFEST["runs"]))
        note("REPORT=%s" % REPORT)
        note("SUNSHINE_MOONLIGHT=NOT_NEEDED_YET")
        note("UPLOAD_THE_ARCHIVE_WITH_SCREENSHOTS_FOR_VISUAL_REVIEW=YES")
        note("ESDE_BINARY_AND_SSH_SESSION=UNCHANGED")
    finally:
        shutil.rmtree(WORK, ignore_errors=True)

sys.exit(0 if RESULT == "ARCHIVE_READY_FOR_SCREENSHOT_REVIEW" else 1)
