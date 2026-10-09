#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unattended bounded test-owned Game:Main RIP capture after guest flip stall.

Runs a PINNED existing candidate build+Vulkan-validation+rollback workflow.
Starts Ghost automatically through the user's existing shadps4-esde launcher
on the already proven :0 X11 display; captures F12/Alt+F12 screenshots,
logs and bounded thread samples; terminates ONLY test-owned game/collector
processes; bundles the child trial report and screenshot PNGs in one archive.

Never changes the launcher, Sunshine, ES-DE, unrelated emulator sessions,
saves, controller pairing, SSH login, or global configuration. Does not run
sudo or install packages. A child build failure still produces an archive.
"""
from __future__ import annotations

import ctypes
import ctypes.util
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import fnmatch
import queue
import re
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
ENTRY = Path("/mnt/roms-all/ps4/Ghost of Tsushima.ps4")
LAUNCHER = HOME / ".local/bin/shadps4-esde"
BINARY = HOME / "Applications/shadps4/shadps4"
STATE = HOME / ".local/state/shadps4-playtest-logs"
BASE_SHA = "f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f"
TRIAL_REV = "766ed8d75505d648020ae8f7010e56c09fb1cfc2"
TRIAL_FILE = "ghost-no-gdb-guest-cpu-rip-validation-onerun.py"
DISPLAY = ":0"
READY = "READY - LAUNCH GHOST OF TSUSHIMA THROUGH MOONLIGHT -> ES-DE NOW."
OWNED_TRIAL_SHELL = "ghost-no-gdb-guest-cpu-rip-onerun.sh"
SCREEN_TIMES = (6, 12, 24, 42, 65, 95, 125, 145)
MAX_GAME_SECONDS = 156
MAX_PREPARE_SECONDS = 850
MAX_ROLLBACK_SECONDS = 110

STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = HOME / ("ghost-guest-cpu-rip-" + STAMP + ".tar.gz")
WORK = HOME / ".cache" / ("ghost-guest-cpu-rip-" + STAMP)
TRIAL_PATH = WORK / TRIAL_FILE
RIP_SAMPLER_REV = "6c8a892e33a69a3740402c782faf6641c2676d2d"
RIP_SAMPLER_BLOB = "9431283e0ece61aa299c0deb605743c1727cd737"
RIP_SAMPLER_NAME = "ghost-guest-cpu-stall-sampler-20261009.py"
RIP_SAMPLER = WORK / RIP_SAMPLER_NAME
RIP_OUTPUT = WORK / "guest-cpu-rip.txt"
RIP_STALL_SECONDS = 14
RIP_STALL_MIN_VBLANK = 120
SCREEN_DIR = WORK / "screenshots"

EVENTS: list[str] = []
GAME_PROC: subprocess.Popen | None = None
CHILD_PROC: subprocess.Popen | None = None
READER_THREAD: threading.Thread | None = None
CHILD_READY = threading.Event()
GAME_LAUNCH_TIME: float | None = None
GAME_PID: int | None = None
GAME_SID: int | None = None
GAME_START_TICKS: int | None = None
SESSION_DIR: Path | None = None
CHILD_RC: int | None = None
TEST_STATUS = "PREPARING"
SCREEN_ENV: dict[str, str] | None = None
STOP_REASON = "not_started"
RADV_DUMP_TOTAL_CAP = 180 * 1024 * 1024
RADV_DUMP_FILE_CAP = 24 * 1024 * 1024
RADV_DUMP_FILE_COUNT_CAP = 70
RADV_UMR_PRESENT = False
RADV_GUEST_ENV_VALUE: str | None = None
RADV_DUMPS_FOUND: list[str] = []
RADV_DUMP_FILE_NOTES: list[dict] = []


def say(message: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    EVENTS.append(line)
    print(line, flush=True)


def checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def proc_info(pid: int) -> dict | None:
    base = Path("/proc") / str(pid)
    try:
        if base.stat().st_uid != os.getuid():
            return None
        exe = os.readlink(base / "exe").removesuffix(" (deleted)")
        argv = (base / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        stat = (base / "stat").read_text().rsplit(") ", 1)[1].split()
        return {
            "pid": pid, "exe": exe, "argv": argv, "state": stat[0],
            "pgid": int(stat[2]), "sid": int(stat[3]),
            "start_ticks": int(stat[19]),
        }
    except (OSError, ValueError, IndexError):
        return None


def emulator_pids() -> list[int]:
    hits = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        info = proc_info(int(p.name))
        if info and Path(info["exe"]).name.lower() == "shadps4" and info["state"] != "Z":
            hits.append(info["pid"])
    return hits


def exact_ghost(pid: int) -> bool:
    info = proc_info(pid)
    return bool(info and Path(info["exe"]).name.lower() == "shadps4"
                and ("CUSA11456" in info["argv"] or
                     "Ghost of Tsushima.ps4" in info["argv"]))


def display_probe() -> dict[str, str]:
    """Find the X authority for existing :0; no X server is started or altered."""
    x11_path = ctypes.util.find_library("X11")
    tst_path = ctypes.util.find_library("Xtst")
    if not x11_path or not tst_path:
        raise RuntimeError("libX11/libXtst unavailable: screenshot shortcuts cannot run")
    xlib = ctypes.CDLL(x11_path)
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    options = [os.environ.get("XAUTHORITY", ""), str(HOME / ".Xauthority"), ""]
    # Desktop Sunshine/ES-DE processes may use an Xauthority outside ~/.Xauthority.
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            if p.stat().st_uid != os.getuid():
                continue
            cmd = (p / "comm").read_text(errors="replace").lower()
            if not any(name in cmd for name in ("sunshine", "es-de", "xorg", "xwayland")):
                continue
            raw = (p / "environ").read_bytes().split(b"\x00")
            values = [line.split(b"=", 1)[1].decode("utf-8", "replace")
                      for line in raw if line.startswith(b"XAUTHORITY=")]
            options.extend(values[:1])
        except (OSError, PermissionError, ValueError):
            continue
    tried = set()
    for opt in options:
        if opt in tried:
            continue
        tried.add(opt)
        if opt and not Path(opt).is_file():
            continue
        env = dict(os.environ)
        env["DISPLAY"] = DISPLAY
        if opt:
            env["XAUTHORITY"] = opt
        else:
            env.pop("XAUTHORITY", None)
        saved_display = os.environ.get("DISPLAY")
        saved_auth = os.environ.get("XAUTHORITY")
        try:
            os.environ["DISPLAY"] = DISPLAY
            if opt:
                os.environ["XAUTHORITY"] = opt
            else:
                os.environ.pop("XAUTHORITY", None)
            handle = xlib.XOpenDisplay(DISPLAY.encode("ascii"))
            if handle:
                xlib.XCloseDisplay(handle)
                say("DISPLAY_VERIFIED=" + DISPLAY + " xauthority=" + ("file" if opt else "default"))
                return env
        finally:
            if saved_display is None:
                os.environ.pop("DISPLAY", None)
            else:
                os.environ["DISPLAY"] = saved_display
            if saved_auth is None:
                os.environ.pop("XAUTHORITY", None)
            else:
                os.environ["XAUTHORITY"] = saved_auth
    raise RuntimeError("Cannot open active X11 display :0, screenshots would be unavailable")


def fake_screenshot_key(env: dict[str, str], overlay: bool) -> None:
    """Same F12 / Alt+F12 XTest capture previously verified for this host."""
    xlib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
    xst = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xlib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    xlib.XKeysymToKeycode.restype = ctypes.c_ubyte
    xlib.XFlush.argtypes = [ctypes.c_void_p]
    xst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                      ctypes.c_int, ctypes.c_ulong]
    old = os.environ.get("XAUTHORITY")
    try:
        if "XAUTHORITY" in env:
            os.environ["XAUTHORITY"] = env["XAUTHORITY"]
        else:
            os.environ.pop("XAUTHORITY", None)
        handle = xlib.XOpenDisplay(env["DISPLAY"].encode())
        if not handle:
            raise RuntimeError("X11 display became unavailable")
        try:
            f12 = xlib.XKeysymToKeycode(handle, 0xFFC9)
            alt = xlib.XKeysymToKeycode(handle, 0xFFE9)
            if not f12 or (overlay and not alt):
                raise RuntimeError("X11 screenshot keycodes unavailable")
            if overlay:
                xst.XTestFakeKeyEvent(handle, alt, 1, 0)
            xst.XTestFakeKeyEvent(handle, f12, 1, 0)
            xlib.XFlush(handle)
            time.sleep(0.13)
            xst.XTestFakeKeyEvent(handle, f12, 0, 0)
            if overlay:
                xst.XTestFakeKeyEvent(handle, alt, 0, 0)
            xlib.XFlush(handle)
        finally:
            xlib.XCloseDisplay(handle)
    finally:
        if old is None:
            os.environ.pop("XAUTHORITY", None)
        else:
            os.environ["XAUTHORITY"] = old


def root_screenshot(env: dict[str, str], elapsed: int) -> None:
    # Independent screenshot of the actual X11 desktop. Does not press keys.
    tool = shutil.which("import")
    if not tool:
        return
    target = SCREEN_DIR / f"desktop-{elapsed:03d}s.png"
    try:
        result = subprocess.run([tool, "-display", DISPLAY, "-window", "root",
                                 "-silent", str(target)], env=env, capture_output=True, timeout=11)
        if result.returncode != 0 or not target.exists():
            say(f"DESKTOP_SCREENSHOT_FAILED={elapsed}s returncode={result.returncode}")
        else:
            say(f"DESKTOP_SCREENSHOT={target.name} bytes={target.stat().st_size}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        say(f"DESKTOP_SCREENSHOT_ERROR={elapsed}s {type(exc).__name__}")


def capture_readonly_gpu_state() -> None:
    """Collect only available unprivileged AMD GPU status and kernel logs."""
    chunks: list[str] = []
    chunks.append("RADV_DEBUG=hang,noumr is scoped to test-launched shadPS4 only.\n")
    chunks.append("Mesa warning: hang mode also enables shader synchronization; "
                  "a stable game under this option is NOT proof of a fix.\n")
    for candidate in sorted(Path("/sys/class/drm").glob("card*/device")):
        try:
            vendor = (candidate / "vendor").read_text().strip()
            if vendor.lower() != "0x1002":
                continue
            chunks.append("\nDEVICE=" + str(candidate) + "\n")
            for name in ("vendor", "device", "gpu_busy_percent", "mem_busy_percent",
                         "current_link_speed", "current_link_width"):
                path = candidate / name
                if not path.is_file():
                    continue
                try:
                    chunks.append(name + "=" + path.read_text(errors="replace")[:500].strip() + "\n")
                except (OSError, PermissionError) as exc:
                    chunks.append(name + "=UNAVAILABLE " + str(exc) + "\n")
        except (OSError, PermissionError):
            continue
    # Kernel messages may be restricted for unprivileged users. Report that
    # limitation explicitly; do not prompt for sudo or change permissions.
    if shutil.which("journalctl"):
        try:
            proc = subprocess.run(
                ["journalctl", "-k", "-b", "--no-pager", "-n", "140"],
                capture_output=True, text=True, errors="replace", timeout=8
            )
            body = (proc.stdout + proc.stderr)[-18000:]
            (WORK / "kernel-log-unprivileged.txt").write_text(
                "returncode=" + str(proc.returncode) + "\n" + body
            )
            chunks.append("kernel_journal_returncode=" + str(proc.returncode) + "\n")
        except (OSError, subprocess.TimeoutExpired) as exc:
            chunks.append("kernel_journal_error=" + repr(exc) + "\n")
    (WORK / "gpu-status-unprivileged.txt").write_text("".join(chunks))


def radv_report_priority(path: Path) -> tuple[int, str]:
    """Never let many SPIR-V binaries crowd the fault/trace report out."""
    important = (
        "trace.log", "vm_fault.log", "pipeline.log", "addr_binding_report.log",
        "bo_ranges.log", "bo_history.log", "dmesg.log", "registers.log",
        "gpu_info.log", "app_info.log", "umr_waves.log", "umr_ring.log",
    )
    name = path.name
    if name in important:
        return (important.index(name), str(path))
    if path.suffix.lower() == ".spv":
        return (20, str(path))
    return (30, str(path))


def capture_radv_hang_reports() -> None:
    """Include only dumps from this test's shadPS4 PID and launch timestamp."""
    global RADV_DUMPS_FOUND, RADV_DUMP_FILE_NOTES
    RADV_DUMPS_FOUND = []
    RADV_DUMP_FILE_NOTES = []
    if GAME_LAUNCH_TIME is None:
        (WORK / "radv-dump-status.json").write_text(
            json.dumps({"status": "game_never_launched", "RADV_DEBUG": "hang,noumr",
                        "umr_available": RADV_UMR_PRESENT}, indent=2) + "\n"
        )
        return
    eligible = {pid for pid in (GAME_PID,) if pid}
    if SESSION_DIR and (SESSION_DIR / "session.meta").is_file():
        try:
            match = re.search(r"(?m)^launcher_pid=(\d+)$",
                              (SESSION_DIR / "session.meta").read_text(errors="replace"))
            if match:
                eligible.add(int(match.group(1)))
        except (OSError, ValueError):
            pass
    dump_root = WORK / "radv-dumps"
    dump_root.mkdir(exist_ok=True)
    total = 0
    captured_files = 0
    dirs = []
    for candidate in sorted(HOME.glob("radv_dumps_*")):
        match = re.fullmatch(r"radv_dumps_(\d+)_.+", candidate.name)
        if not match or int(match.group(1)) not in eligible:
            continue
        try:
            if candidate.is_symlink() or not candidate.is_dir() or (
                candidate.stat().st_mtime < GAME_LAUNCH_TIME - 5
            ):
                continue
        except OSError:
            continue
        dirs.append(candidate)
    for directory in dirs[:3]:
        RADV_DUMPS_FOUND.append(str(directory))
        prefix = dump_root / directory.name
        prefix.mkdir(exist_ok=True)
        for source in sorted(directory.rglob("*"), key=radv_report_priority):
            if captured_files >= RADV_DUMP_FILE_COUNT_CAP or total >= RADV_DUMP_TOTAL_CAP:
                RADV_DUMP_FILE_NOTES.append({"skipped": "total_capture_limit"})
                break
            try:
                if not source.is_file() or source.is_symlink():
                    continue
                relative = source.relative_to(directory)
                if len(relative.parts) > 3 or ".." in relative.parts:
                    continue
                extension = source.suffix.lower()
                if extension not in (".log", ".txt", ".json", ".spv", ".csv", ".trace"):
                    continue
                original_bytes = source.stat().st_size
                remaining = min(RADV_DUMP_FILE_CAP, RADV_DUMP_TOTAL_CAP - total)
                if remaining < 1:
                    break
                output = prefix / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                # For large driver traces the final records are more likely to
                # contain the GPU hang than the beginning. Preserve the tail
                # without collecting unlimited BO history / memory dumps.
                with source.open("rb") as inp, output.open("wb") as dst:
                    if original_bytes > remaining:
                        inp.seek(-remaining, os.SEEK_END)
                    shutil.copyfileobj(inp, dst, 1024 * 1024)
                written = output.stat().st_size
                total += written
                captured_files += 1
                RADV_DUMP_FILE_NOTES.append({
                    "original": str(source), "captured": str(output.relative_to(WORK)),
                    "original_bytes": original_bytes, "captured_bytes": written,
                    "tail_only": original_bytes > remaining
                })
            except (OSError, ValueError) as exc:
                RADV_DUMP_FILE_NOTES.append({"source": str(source), "error": repr(exc)})
    status = {
        "RADV_DEBUG": "hang,noumr",
        "changes_syncshaders": True,
        "umr_disabled": True,
        "umr_available": RADV_UMR_PRESENT,
        "game_pid_candidates": sorted(eligible),
        "driver_dump_directories": RADV_DUMPS_FOUND,
        "files_collected": captured_files,
        "bytes_collected": total,
        "capture_notes": RADV_DUMP_FILE_NOTES,
    }
    (WORK / "radv-dump-status.json").write_text(json.dumps(status, indent=2) + "\n")
    say(f"RADV_HANG_REPORTS={len(RADV_DUMPS_FOUND)} files={captured_files} bytes={total}")
    if not RADV_DUMPS_FOUND:
        say("RADV_DUMP_ABSENT: UMR/debug permissions may be unavailable or no driver hang dump was generated")


def verify_game_debug_environment() -> None:
    """Read *only* RADV_DEBUG from the launched Ghost process, not other env."""
    global RADV_GUEST_ENV_VALUE
    if SESSION_DIR is None:
        say("RADV_GUEST_ENV_UNAVAILABLE=no_game_session")
        return
    try:
        data = (SESSION_DIR / "session.meta").read_text(errors="replace")
        m = re.search(r"(?m)^launcher_pid=(\d+)$", data)
        if not m or not exact_ghost(int(m.group(1))):
            say("RADV_GUEST_ENV_UNAVAILABLE=unverified_emulator_pid")
            return
        raw = (Path("/proc") / m.group(1) / "environ").read_bytes().split(b"\x00")
        for item in raw:
            if item.startswith(b"RADV_DEBUG="):
                RADV_GUEST_ENV_VALUE = item.partition(b"=")[2].decode("utf-8", "replace")
                break
        say("RADV_DEBUG_CONFIRMED_IN_GUEST=" + repr(RADV_GUEST_ENV_VALUE))
        if RADV_GUEST_ENV_VALUE != "hang,noumr":
            say("WARNING: RADV_DEBUG=hang,noumr not confirmed in guest; missing RADV dump inconclusive")
    except (OSError, PermissionError) as exc:
        say("RADV_GUEST_ENV_READ_UNAVAILABLE=" + repr(exc))


def current_session() -> Path | None:
    if GAME_LAUNCH_TIME is None:
        return None
    choices = []
    for meta in STATE.glob("*Ghost_of_Tsushima.ps4/session.meta"):
        try:
            if meta.stat().st_mtime < GAME_LAUNCH_TIME - 2:
                continue
            data = meta.read_text(errors="replace")
            pid_match = re.search(r"(?m)^launcher_pid=(\d+)$", data)
            if not pid_match:
                continue
            pid = int(pid_match.group(1))
            info = proc_info(pid)
            if not info or not exact_ghost(pid):
                continue
            if GAME_SID is not None and info["sid"] != GAME_SID and pid != GAME_PID:
                # Some wrappers can launch the emulator in a detached session.
                # Accept only a game process created after our own launcher.
                if GAME_START_TICKS is None or info["start_ticks"] < GAME_START_TICKS:
                    continue
            choices.append((meta.stat().st_mtime, meta.parent))
        except (OSError, PermissionError, ValueError):
            continue
    return max(choices, default=(0, None))[1]


def log_tail(path: Path, max_bytes: int = 85000) -> str:
    try:
        with path.open("rb") as stream:
            if path.stat().st_size > max_bytes:
                stream.seek(-max_bytes, os.SEEK_END)
            return stream.read().decode("utf-8", "replace")
    except OSError:
        return ""


def lwp_sample(pid: int, elapsed: int) -> None:
    info = proc_info(pid)
    if not info:
        return
    name = WORK / (f"threads-{elapsed:03d}s.txt")
    try:
        r = subprocess.run(["ps", "-L", "-p", str(pid), "-o",
                            "pid,tid,stat,pcpu,wchan:28,comm"], capture_output=True,
                           text=True, timeout=8)
        name.write_text(r.stdout + "\n" + r.stderr)
    except (subprocess.TimeoutExpired, OSError) as exc:
        name.write_text(f"thread snapshot failure: {exc}\n")
    text = log_tail(SESSION_DIR / "runtime.log") if SESSION_DIR else ""
    matches = re.findall(
        r"GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)", text)
    if matches:
        say(f"FRAME_STATUS_{elapsed}s vblank={matches[-1][0]} guest_flips={matches[-1][1]} "
            f"pending={matches[-1][2]} queued={matches[-1][3]}")


def alive_session_pids(sid: int) -> list[int]:
    pids = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        info = proc_info(int(path.name))
        if info and info["sid"] == sid and info["state"] not in ("Z", "X"):
            pids.append(info["pid"])
    return pids


def stop_launched_game() -> None:
    """Terminate this trial's own session, never an existing emulator or SSH."""
    global STOP_REASON
    if GAME_PROC is None or GAME_SID is None:
        return
    pid = GAME_PROC.pid
    known = alive_session_pids(GAME_SID)
    if known:
        say(f"TEST_SESSION_GRACEFUL_SHUTDOWN sid={GAME_SID} processes={known}")
        try:
            os.killpg(GAME_SID, signal.SIGTERM)
        except ProcessLookupError:
            pass
        end = time.monotonic() + 9
        while time.monotonic() < end and alive_session_pids(GAME_SID):
            time.sleep(0.3)
        remaining = alive_session_pids(GAME_SID)
        if remaining:
            say(f"TEST_SESSION_FORCE_STOP sid={GAME_SID} remaining={remaining}")
            try:
                os.killpg(GAME_SID, signal.SIGKILL)
            except ProcessLookupError:
                pass
    # If the wrapper moved the game to a fresh session, the session.meta PID
    # must still match the exact executable/game and have been seen in this trial.
    if SESSION_DIR and (SESSION_DIR / "session.meta").is_file():
        m = re.search(r"(?m)^launcher_pid=(\d+)$",
                      (SESSION_DIR / "session.meta").read_text(errors="replace"))
        if m:
            extra = int(m.group(1))
            info = proc_info(extra)
            if (info and exact_ghost(extra) and extra != pid and
                    info["sid"] != GAME_SID and
                    GAME_START_TICKS is not None and
                    info["start_ticks"] >= GAME_START_TICKS):
                say(f"DETACHED_TEST_GHOST_SHUTDOWN={extra}")
                try:
                    os.kill(extra, signal.SIGTERM)
                    time.sleep(2)
                    if exact_ghost(extra):
                        os.kill(extra, signal.SIGKILL)
                except (OSError, PermissionError):
                    pass
    try:
        GAME_PROC.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass
    if STOP_REASON not in ("game_exited", "time_budget_reached"):
        STOP_REASON = "session_stopped"


def cleanup_child_session() -> None:
    """Reap test-created helpers only after verifying the source rollback."""
    if CHILD_PROC is None or CHILD_PROC.poll() is None:
        return
    source = HOME / ".cache/shadps4-ghost-fullstack-20261008-131621/source"
    if not source.is_dir():
        say("SOURCE_UNAVAILABLE: skipping group kill until rollback can be verified")
        return
    try:
        proc = subprocess.run(["git", "-C", str(source), "status", "--porcelain"],
                              capture_output=True, text=True, timeout=12)
        if proc.returncode or proc.stdout.strip():
            say("SOURCE_DIRTY_AFTER_CHILD: refusing to kill possible rollback helpers")
            return
    except (subprocess.TimeoutExpired, OSError) as exc:
        say("SOURCE_STATUS_UNKNOWN: preserving possible rollback helpers: " + repr(exc))
        return
    sid = CHILD_PROC.pid  # Python child was spawned with start_new_session=True.
    members = alive_session_pids(sid)
    if not members:
        return
    say(f"LEFTOVER_TEST_HELPERS={members}; stopping only child session sid={sid}")
    try:
        os.killpg(sid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    limit = time.monotonic() + 6
    while time.monotonic() < limit and alive_session_pids(sid):
        time.sleep(0.3)
    left = alive_session_pids(sid)
    if left:
        say(f"FORCIBLY_STOPPING_TEST_HELPERS={left} sid={sid}")
        try:
            os.killpg(sid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def cleanup_test_watchers() -> None:
    """Stop ONLY a watcher started by the child trial, by exact PID and argv."""
    output = WORK / "build-and-trial.log"
    if not output.is_file():
        return
    text = output.read_text(errors="replace")
    ids = [int(v) for v in re.findall(r"Companion watcher active \(PID=(\d+)\)", text)]
    for pid in sorted(set(ids)):
        info = proc_info(pid)
        if not info:
            continue
        if ("ghost-mip-candidate-watch.py" not in info["argv"] or
                "ghost-no-gdb-" not in info["argv"]):
            say(f"WATCHER_PID_NOT_RECOGNIZED={pid}, refusing cleanup")
            continue
        say(f"STOP_TEST_WATCHER_PID={pid}")
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(12):
                if proc_info(pid) is None:
                    break
                time.sleep(0.25)
            if proc_info(pid) is not None:
                os.kill(pid, signal.SIGKILL)
        except (OSError, PermissionError):
            pass


def collect_screenshots() -> int:
    SCREEN_DIR.mkdir(exist_ok=True, parents=True)
    if GAME_LAUNCH_TIME is None:
        return 0
    locations = [
        HOME / ".local/share/shadPS4",
        HOME / "Applications/shadps4/user",
        HOME / ".cache/shadps4-esde-latest-pending/source/user",
    ]
    if os.environ.get("XDG_DATA_HOME"):
        locations.append(Path(os.environ["XDG_DATA_HOME"]) / "shadPS4")
    seen = set()
    count = 0
    for root in locations:
        for file in sorted((root / "screenshots").glob("CUSA11456_*.png")):
            try:
                st = file.stat()
                if st.st_mtime < GAME_LAUNCH_TIME - 3 or st.st_size < 50:
                    continue
                unique = (file.name, st.st_size)
                if unique in seen:
                    continue
                seen.add(unique)
                dest = SCREEN_DIR / file.name
                shutil.copy2(file, dest)
                count += 1
                if count >= 26:
                    say("SCREENSHOT_CAP_REACHED=26")
                    return count
            except (OSError, PermissionError) as exc:
                say(f"SCREENSHOT_COPY_FAILED={file.name}: {exc}")
    say(f"GAME_SCREENSHOT_PNGS={count}")
    return count


def report_session() -> None:
    if SESSION_DIR is None:
        say("SESSION_LOG_UNAVAILABLE: shadps4-esde did not create a matching session.meta")
        return
    for name in ("session.meta",):
        p = SESSION_DIR / name
        if p.is_file():
            shutil.copy2(p, WORK / name)
    runtime = SESSION_DIR / "runtime.log"
    if runtime.is_file():
        text = log_tail(runtime, 12000000)
        (WORK / "runtime-tail.log").write_text(text)
        frames = re.findall(
            r"GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)", text)
        if frames:
            say(f"FINAL_FRAME_STATE={frames[-1]} PEAK_FLIPS={max(int(x[1]) for x in frames)}")
        release_packets = re.findall(
            r"GHOST_RELEASE_MEM_PACKET seq=(\d+) data_sel=(\d+) int_sel=(\d+)", text)
        release_none = sum(data == "0" for _, data, _ in release_packets)
        release_irq_only = sum(irq == "1" for _, _, irq in release_packets)
        release_unknown = sum(int(data) > 5 or int(irq) > 3
                              for _, data, irq in release_packets)
        (WORK / "release-mem-results.json").write_text(
            json.dumps({
                "experiment": "native RELEASE_MEM no-data-write and IRQ-only",
                "packet_samples": len(release_packets),
                "observed_none_data_sel": release_none,
                "observed_irq_only_int_sel": release_irq_only,
                "unknown_selector_records": release_unknown,
                "observed_selector_pairs": sorted(set(
                    (data, irq) for _, data, irq in release_packets)),
                "signal_fence_traps": text.count("SignalFence: Unreachable code!"),
                "gpu_lost": text.count("VK_ERROR_DEVICE_LOST"),
                "radv_context_lost": text.count("context is lost"),
                "guest_peak_flips": max((int(m[1]) for m in frames), default=0)
            }, indent=2) + "\n"
        )
        say("RELEASE_MEM_PACKET_SAMPLES=" + str(len(release_packets)))
        say("RELEASE_MEM_NONE_OBSERVED=" + str(release_none))
        say("RELEASE_MEM_IRQ_ONLY_OBSERVED=" + str(release_irq_only))
        say("RELEASE_MEM_UNEXPECTED_SELECTORS=" + str(release_unknown))
        say("RELEASE_MEM_SIGNALFENCE_TRAPS=" +
            str(text.count("SignalFence: Unreachable code!")))
        fs361a_compile_hits = text.count("GHOST_FS361A_LOOPCAP_64_INSTALLED shader=")
        (WORK / "fs361a-loopcap64-results.json").write_text(
            json.dumps({
                "shader": "0x361a48f5",
                "guard_limit": 64,
                "compile_hits": fs361a_compile_hits,
                "target_shader_active_in_logged_draws": text.count("guest_shader=0x361a48f5"),
                "gpu_lost": text.count("VK_ERROR_DEVICE_LOST"),
                "radv_context_lost": text.count("context is lost"),
                "storage_usage_00339": text.count("VUID-VkWriteDescriptorSet-descriptorType-00339"),
                "storage_format_07028": text.count("VUID-vkCmdDispatchIndirect-OpTypeImage-07028"),
                "peak_guest_flips": max((int(m[1]) for m in frames), default=0),
            }, indent=2) + "\n"
        )
        say("FS361A_LOOPCAP64_COMPILED=" + str(fs361a_compile_hits))
        say("FS361A_LOOPCAP64_TARGET_DRAW_EVENTS=" +
            str(text.count("guest_shader=0x361a48f5")))
        target_lines = [line for line in text.splitlines() if "GHOST_GFX513_" in line]
        (WORK / "gfx513-last-guest-shaders.txt").write_text(
            "\n".join(target_lines[-180:]) + "\n")
        guest_hashes = sorted(set(re.findall(
            r"GHOST_GFX513_STAGE seq=\d+ stage=\d+ guest_shader=(0x[0-9a-fA-F]+)",
            text)))
        say("GFX513_DRAW_EVENTS=" + str(text.count("GHOST_GFX513_DRAW seq=")))
        say("GFX513_STAGE_EVENTS=" + str(text.count("GHOST_GFX513_STAGE seq=")))
        say("GFX513_UNIQUE_GUEST_SHADERS=" + ",".join(guest_hashes[:40]))
    else:
        say("NO_RUNTIME_LOG: game may have exited before runtime initialized")


def candidate_archive() -> None:
    """Embed the exact pinned child validation archive if present."""
    log = WORK / "build-and-trial.log"
    listed: list[Path] = []
    if log.is_file():
        contents = log.read_text(errors="replace")
        listed = [Path(text.strip()) for text in re.findall(
            r"(?m)^ARCHIVE=(.+?\.tar\.gz)\s*$", contents
        )]
    # Previous captures sometimes reported an archive but did not package it.
    # Fall back to globbing *only* files born during this own trial.
    discovered = list(HOME.glob("ghost-no-gdb-validation-*.tar.gz"))
    options = list(reversed(listed)) + sorted(discovered, key=lambda p:p.stat().st_mtime,
                                              reverse=True)
    seen = set()
    notes = []
    for p in options:
        if p in seen:
            continue
        seen.add(p)
        try:
            eligible = (p.is_file() and not p.is_symlink() and p.parent == HOME and
                        p.name.startswith("ghost-no-gdb-validation-") and
                        p.stat().st_mtime >= datetime.strptime(STAMP, "%Y%m%d-%H%M%S").timestamp() - 15)
            notes.append({"path": str(p), "eligible": eligible,
                          "exists": p.is_file()})
            if eligible:
                target = WORK / "candidate-build-and-validation.tar.gz"
                shutil.copy2(p, target)
                with tarfile.open(target, "r:gz") as archive:
                    archive.getmembers()  # verify tar index is readable
                say(f"CHILD_REPORT_INCLUDED={p} bytes={target.stat().st_size}")
                break
        except (OSError, tarfile.TarError) as exc:
            notes.append({"path": str(p), "error": repr(exc)})
    else:
        say("CHILD_REPORT_NOT_FOUND (candidate log retained)")
    (WORK / "child-archive-discovery.json").write_text(
        json.dumps(notes[:40], indent=2) + "\n"
    )


def ready_preflight() -> dict[str, str]:
    global RADV_UMR_PRESENT
    if emulator_pids():
        raise RuntimeError("Existing shadPS4 running; refusing to take over other session.")
    if not LAUNCHER.is_file() or not os.access(LAUNCHER, os.X_OK) or LAUNCHER.is_symlink():
        raise RuntimeError("Existing shadps4-esde launcher missing/not executable/symlinked.")
    launcher_text = LAUNCHER.read_text(errors="replace")
    if ("SHADPS4_SESSION_GUARD_V1" not in launcher_text or
            "SHADPS4_DEFAULT_MAIN_V1" not in launcher_text):
        raise RuntimeError("Launcher differs from verified ES-DE guard; refusing unreviewed launch.")
    if not ENTRY.exists():
        raise RuntimeError(f"Ghost ROM entry missing: {ENTRY}")
    if not BINARY.is_file():
        raise RuntimeError("Installed shadPS4 binary missing.")
    env = display_probe()
    runtime = Path("/run/user") / str(os.getuid())
    if "XDG_RUNTIME_DIR" not in env and runtime.is_dir():
        env["XDG_RUNTIME_DIR"] = str(runtime)
    env["SHADPS4_GRAPHICS_DIAGNOSTICS"] = "1"
    env["SHADPS4_STARTUP_DIAGNOSTICS"] = "1"
    env["RADV_DEBUG"] = "hang,noumr"
    if RIP_OUTPUT.exists() or RIP_OUTPUT.is_symlink():
        raise RuntimeError("RIP output path already exists; refusing")
    env["GHOST_CPU_RIP_LOG"] = str(RIP_OUTPUT)
    RADV_UMR_PRESENT = shutil.which("umr") is not None
    say("RADV_FAULT_DIAGNOSTICS_ARMED=hang,noumr UMR_DISABLED=1 installed_umr=" + str(RADV_UMR_PRESENT))
    say("RADV_DEBUG=hang,noumr changes GPU synchronization; not a timing-equivalent A/B")
    return env


def launch_test(env: dict[str, str]) -> None:
    global GAME_PROC, GAME_LAUNCH_TIME, GAME_PID, GAME_SID, GAME_START_TICKS, SESSION_DIR, STOP_REASON
    if emulator_pids():
        raise RuntimeError("An emulator appeared before auto-launch; refusing.")
    GAME_LAUNCH_TIME = time.time()
    with (WORK / "launcher-stdout.log").open("w") as out:
        GAME_PROC = subprocess.Popen(
            [str(LAUNCHER), str(ENTRY)], cwd=str(HOME), env=env,
            stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    GAME_PID = GAME_PROC.pid
    GAME_SID = GAME_PID
    launched = proc_info(GAME_PID)
    GAME_START_TICKS = launched["start_ticks"] if launched else None
    say(f"AUTOMATIC_GAME_LAUNCH pid={GAME_PID} sid={GAME_SID} display={DISPLAY}")
    STOP_REASON = "running"
    # Prevent rushing screenshot shortcuts before shadPS4's window initializes.
    start = time.monotonic()
    while time.monotonic() - start < 30 and GAME_PROC.poll() is None:
        SESSION_DIR = current_session()
        if SESSION_DIR:
            say("GAME_SESSION=" + str(SESSION_DIR))
            verify_game_debug_environment()
            break
        time.sleep(0.5)
    if not SESSION_DIR:
        say("SESSION_META_NOT_YET_SEEN; continuing bounded observation")


def latest_guest_vblank_flips(runtime_file: Path) -> tuple[int, int] | None:
    """Last guest frame counter from capped runtime-log tail."""
    if not runtime_file.is_file() or runtime_file.is_symlink():
        return None
    try:
        with runtime_file.open("rb") as source:
            source.seek(0, os.SEEK_END)
            end = source.tell()
            source.seek(max(0, end - 65536))
            body = source.read(65536).decode("utf-8", "replace")
    except (OSError, PermissionError):
        return None
    matches = re.findall(
        r"GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=\d+ queued=\d+", body)
    if not matches:
        return None
    return tuple(map(int, matches[-1]))


def sample_confirmed_guest_cpu_stall() -> bool:
    """No signal until actual test-owned game PID and opt-in log are verified."""
    if SESSION_DIR is None or not RIP_SAMPLER.is_file():
        say("GHOST_CPU_RIP_UNAVAILABLE=no_session_or_sampler")
        return False
    meta = SESSION_DIR / "session.meta"
    if not meta.is_file():
        return False
    match = re.search(r"(?m)^launcher_pid=(\d+)$", meta.read_text(errors="replace"))
    if not match:
        say("GHOST_CPU_RIP_UNAVAILABLE=no_guest_pid")
        return False
    pid = int(match.group(1))
    if not exact_ghost(pid) or not RIP_OUTPUT.is_file() or RIP_OUTPUT.is_symlink():
        say("GHOST_CPU_RIP_PROBE_NOT_ARMED_OR_IDENTITY_FAILED")
        return False
    try:
        run = subprocess.run(
            [sys.executable, "-I", str(RIP_SAMPLER), str(pid), str(WORK)],
            capture_output=True, text=True, timeout=22)
        for line in (run.stdout + run.stderr).splitlines():
            say(line)
        evidence = WORK / "guest-cpu-hotloop.json"
        if evidence.is_file():
            result = json.loads(evidence.read_text()).get("result")
            say("GHOST_CPU_STALL_DIAGNOSTIC_STATUS=" + str(result))
            if run.returncode == 0 and result == "GUEST_RIP_SAMPLED":
                return True
        if run.returncode:
            say("GHOST_CPU_SAMPLER_FAILED rc=" + str(run.returncode))
    except Exception as exc:
        say("GHOST_CPU_STALL_DIAGNOSTIC_ERROR=" + repr(exc))
    return False


def observe_game(env: dict[str, str]) -> None:
    global SESSION_DIR, STOP_REASON
    if GAME_PROC is None:
        return
    start = time.monotonic()
    last_sample = -100.0
    last_cpu_frame_check = -100.0
    recent_flips = None
    frozen_since = None
    frozen_vblank = None
    rip_attempted = False
    completed_shots = set()
    while time.monotonic() - start < MAX_GAME_SECONDS:
        elapsed = time.monotonic() - start
        if not SESSION_DIR:
            SESSION_DIR = current_session()
        if GAME_PROC.poll() is not None:
            # The wrapper may exit while an exact game child remains; if no
            # matching session game survives, the launch is complete.
            child_alive = False
            if SESSION_DIR:
                m = re.search(r"(?m)^launcher_pid=(\d+)$",
                              (SESSION_DIR / "session.meta").read_text(errors="replace"))
                child_alive = bool(m and exact_ghost(int(m.group(1))))
            if not child_alive:
                STOP_REASON = "game_exited"
                say(f"GAME_EXITED_AFTER={elapsed:.1f}s rc={GAME_PROC.returncode}")
                # Even if SDL crashed before its own screenshot shortcut,
                # grab the X desktop if ImageMagick is available.
                root_screenshot(env, int(elapsed))
                break
        for target in SCREEN_TIMES:
            if target in completed_shots or elapsed < target:
                continue
            completed_shots.add(target)
            try:
                fake_screenshot_key(env, False)
                say(f"SCREENSHOT_TRIGGERED t={target}s type=game")
                time.sleep(0.9)
                fake_screenshot_key(env, True)
                say(f"SCREENSHOT_TRIGGERED t={target}s type=overlay")
            except Exception as exc:
                say(f"SCREENSHOT_TRIGGER_ERROR={target}s {type(exc).__name__}: {exc}")
            if target in (12, 65, 125):
                root_screenshot(env, target)
        if not rip_attempted and elapsed >= 35 and elapsed - last_cpu_frame_check >= 2:
            last_cpu_frame_check = elapsed
            frames = latest_guest_vblank_flips(
                SESSION_DIR / "runtime.log") if SESSION_DIR else None
            if frames:
                vb, flips = frames
                if recent_flips is None or flips != recent_flips:
                    recent_flips = flips
                    frozen_since = elapsed
                    frozen_vblank = vb
                elif (flips >= 100 and frozen_since is not None and
                      elapsed - frozen_since >= RIP_STALL_SECONDS and
                      frozen_vblank is not None and
                      vb - frozen_vblank >= RIP_STALL_MIN_VBLANK):
                    rip_attempted = True
                    say(f"GHOST_CPU_STALL_CONFIRMED guest_flips={flips} "
                        f"vblank_delta={vb-frozen_vblank} "
                        f"stalled_seconds={elapsed-frozen_since:.1f}")
                    if sample_confirmed_guest_cpu_stall():
                        STOP_REASON = "guest_cpu_stall_sampled"
                        say("GHOST_CPU_STALL_PROOF_CAPTURED: intentional "
                            "test cleanup begins; game did not crash")
                        break
        if elapsed - last_sample >= 18:
            last_sample = elapsed
            pid = GAME_PID
            if SESSION_DIR:
                m = re.search(r"(?m)^launcher_pid=(\d+)$",
                              (SESSION_DIR / "session.meta").read_text(errors="replace"))
                if m and exact_ghost(int(m.group(1))):
                    pid = int(m.group(1))
            if pid:
                lwp_sample(pid, int(elapsed))
        time.sleep(0.35)
    else:
        STOP_REASON = "time_budget_reached"
        say(f"GAME_OBSERVATION_LIMIT_REACHED={MAX_GAME_SECONDS}s")


def terminate_child() -> None:
    """Request inner shell EXIT rollback; never TERM/KILL the whole build group."""
    global CHILD_PROC
    if CHILD_PROC is None or CHILD_PROC.poll() is not None:
        return
    sid = CHILD_PROC.pid  # Isolated group created at subprocess launch.
    script_pids = []
    for pid in alive_session_pids(sid):
        info = proc_info(pid)
        if not info:
            continue
        if (Path(info["exe"]).name in ("bash", "sh") and
                OWNED_TRIAL_SHELL in info["argv"]):
            script_pids.append(pid)
    try:
        if script_pids:
            say(f"REQUESTING_SHELL_TRAP_ROLLBACK pids={script_pids}")
            for pid in script_pids:
                os.kill(pid, signal.SIGTERM)
        else:
            say("REQUESTING_CHILD_PYTHON_FINALLY_VIA_SIGINT")
            os.kill(CHILD_PROC.pid, signal.SIGINT)
    except (ProcessLookupError, PermissionError) as exc:
        say("CHILD_CANCEL_SIGNAL_ERROR=" + repr(exc))
    try:
        CHILD_PROC.wait(timeout=95)
        say("CHILD_EXITED_AFTER_ROLLBACK_REQUEST")
    except subprocess.TimeoutExpired:
        # Source/binary restoration matters more than a forced process kill.
        say("CHILD_ROLLBACK_STILL_RUNNING: no broad kill; see report and allow "
            "the isolated build trial to finish its EXIT cleanup.")


def reader(proc: subprocess.Popen, ready: threading.Event, output: Path) -> None:
    try:
        with output.open("w") as save:
            for line in proc.stdout:
                save.write(line)
                save.flush()
                print(line, end="", flush=True)
                if READY in line:
                    ready.set()
    except (OSError, UnicodeError) as exc:
        say(f"BUILD_OUTPUT_READER_ERROR={exc}")


def orchestrate() -> None:
    global CHILD_PROC, CHILD_RC, READER_THREAD, SCREEN_ENV, TEST_STATUS
    WORK.mkdir(parents=True, exist_ok=True)
    SCREEN_DIR.mkdir(exist_ok=True)
    say("GHOST CPU HOTLOOP: native CPU RIP probe -> confirmed frame stall -> 16 guest TID samples -> rollback")
    say("NO_GDB_HARDWARE_WATCHPOINT=1; RADV_DEBUG=hang,noumr only in test-owned Ghost process")
    # Fail closed BEFORE compiling or changing configs when the automatic
    # launch cannot access the same guarded X11 session and game used before.
    SCREEN_ENV = ready_preflight()
    sampler_url = ("https://raw.githubusercontent.com/Chreece/shadPS4/" +
                   RIP_SAMPLER_REV + "/scripts/" + RIP_SAMPLER_NAME)
    subprocess.run(["curl", "-fsSL", "--retry", "2", "--max-time", "35",
                    sampler_url, "-o", str(RIP_SAMPLER)], check=True, timeout=50)
    actual_sampler_blob = subprocess.check_output(
        ["git", "hash-object", str(RIP_SAMPLER)], text=True).strip()
    if actual_sampler_blob != RIP_SAMPLER_BLOB:
        raise RuntimeError("Guest CPU sampler Git blob verification failed")
    subprocess.run([sys.executable, "-m", "py_compile", str(RIP_SAMPLER)], check=True)
    subprocess.run([sys.executable, "-I", str(RIP_SAMPLER), "--self-test"], check=True)
    say("PINNED_CPU_RIP_SAMPLER_SELFTEST_PASS")
    trial_url = ("https://raw.githubusercontent.com/Chreece/shadPS4/" +
                 TRIAL_REV + "/scripts/" + TRIAL_FILE)
    subprocess.run(["curl", "-fsSL", "--retry", "2", "--max-time", "35",
                    trial_url, "-o", str(TRIAL_PATH)], check=True, timeout=50)
    subprocess.run([sys.executable, "-m", "py_compile", str(TRIAL_PATH)], check=True)
    subprocess.run([sys.executable, "-I", str(TRIAL_PATH), "--self-test"], check=True)
    say("PINNED_VULKAN_TRIAL_DOWNLOADED_AND_SELFTESTED")
    CHILD_PROC = subprocess.Popen(
        [sys.executable, "-I", str(TRIAL_PATH)],
        cwd=str(HOME), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", bufsize=1, start_new_session=True)
    READER_THREAD = threading.Thread(
        target=reader, args=(CHILD_PROC, CHILD_READY, WORK / "build-and-trial.log"),
        daemon=True)
    READER_THREAD.start()
    start = time.monotonic()
    while not CHILD_READY.is_set() and CHILD_PROC.poll() is None:
        if time.monotonic() - start >= MAX_PREPARE_SECONDS:
            raise TimeoutError("Build did not reach READY within bounded prepare window")
        time.sleep(0.3)
    if not CHILD_READY.is_set():
        CHILD_RC = CHILD_PROC.wait(timeout=12)
        raise RuntimeError(f"Candidate compilation/preflight stopped before READY, rc={CHILD_RC}")
    say("CHILD_BUILD_READY: starting game WITHOUT Moonlight/ES-DE menu interaction")
    launch_test(SCREEN_ENV)
    TEST_STATUS = "CAPTURING"
    observe_game(SCREEN_ENV)
    say("ENDING_AUTOMATED_GAME_SESSION; cleanup begins")
    stop_launched_game()
    # Give the child build/test script its own normal EXIT/rollback path.
    if STOP_REASON == "game_exited" and SESSION_DIR is None:
        # The nested trial may otherwise wait 10 minutes for an emulator
        # session that never started. Ask its shell to run EXIT rollback now.
        say("AUTO_LAUNCH_DID_NOT_CREATE_SESSION: requesting immediate child cleanup")
        terminate_child()
    try:
        CHILD_RC = CHILD_PROC.wait(timeout=MAX_ROLLBACK_SECONDS)
    except subprocess.TimeoutExpired:
        terminate_child()
        CHILD_RC = CHILD_PROC.poll()
    if READER_THREAD:
        READER_THREAD.join(timeout=5)
    say(f"CHILD_TRIAL_EXIT_CODE={CHILD_RC}")
    if CHILD_RC != 0:
        TEST_STATUS = "CHILD_FAILED"
    elif STOP_REASON == "guest_cpu_stall_sampled" and GAME_PROC is not None and (
        GAME_PROC.returncode in (-signal.SIGTERM, -signal.SIGKILL, 0)
    ):
        TEST_STATUS = "GUEST_CPU_STALL_SAMPLED_EXPECTED_CLEANUP"
        say("GUEST_CPU_STALL_SAMPLED_EXPECTED_CLEANUP: game stopped by this "
            "controller after capturing RIP samples, not a spontaneous crash")
    elif STOP_REASON == "time_budget_reached" and GAME_PROC is not None and (
        GAME_PROC.returncode in (-signal.SIGTERM, -signal.SIGKILL, 0)
    ):
        TEST_STATUS = "EXPECTED_TIMEOUT_CLEANUP"
        say("EXPECTED_TIMEOUT_CLEANUP: test reached its time limit; "
            "the runner intentionally stopped the guest, not a spontaneous crash")
    elif GAME_PROC is not None and GAME_PROC.returncode not in (None, 0):
        TEST_STATUS = "GAME_CRASHED"
        say(f"GAME_CRASH_RESULT exit_code={GAME_PROC.returncode}; archiving")
    else:
        TEST_STATUS = "FINISHED"


def test() -> None:
    global HOME, WORK, GAME_LAUNCH_TIME, GAME_PID, SESSION_DIR
    assert READY.startswith("READY - LAUNCH GHOST")
    assert str(ENTRY).endswith("Ghost of Tsushima.ps4")
    assert LAUNCHER.name == "shadps4-esde"
    assert SCREEN_TIMES == tuple(sorted(set(SCREEN_TIMES)))
    assert all(0 < n < MAX_GAME_SECONDS for n in SCREEN_TIMES)
    assert 60 <= MAX_GAME_SECONDS <= 240
    assert re.fullmatch(r"[0-9a-f]{40}", TRIAL_REV)
    assert re.fullmatch(r"[0-9a-f]{64}", BASE_SHA)
    assert "Ghost of Tsushima.ps4" in "shadps4 --game /mnt/roms-all/ps4/Ghost of Tsushima.ps4"
    # Test the session-meta detection syntax without opening any device.
    m = re.search(r"(?m)^launcher_pid=(\d+)$",
                  "schema=2\nlauncher_pid=123456\nentry=/mnt/roms-all/ps4/Ghost of Tsushima.ps4\n")
    assert m and int(m.group(1)) == 123456
    assert "GHOST_TRACE" in "GHOST_TRACE vblank=100 guest_flips=85 pending=0 queued=0"
    assert "ghost-no-gdb-guest-cpu-rip-validation-onerun.py" == TRIAL_FILE
    assert TRIAL_REV == "766ed8d75505d648020ae8f7010e56c09fb1cfc2"
    assert OWNED_TRIAL_SHELL == "ghost-no-gdb-guest-cpu-rip-onerun.sh"
    assert OUT.name.startswith("ghost-guest-cpu-rip-")
    assert RIP_SAMPLER_REV == "6c8a892e33a69a3740402c782faf6641c2676d2d"
    assert RIP_SAMPLER_BLOB == "9431283e0ece61aa299c0deb605743c1727cd737"
    assert RIP_STALL_SECONDS == 14 and RIP_STALL_MIN_VBLANK == 120
    with tempfile.TemporaryDirectory(prefix="ghost-frame-fixture-") as temp:
        log = Path(temp) / "runtime.log"
        log.write_text(
            "GHOST_TRACE vblank=180 guest_flips=400 pending=1 queued=0\n"
            "GHOST_TRACE vblank=360 guest_flips=620 pending=0 queued=0\n")
        assert latest_guest_vblank_flips(log) == (360, 620)
        assert latest_guest_vblank_flips(Path(temp) / "missing") is None
    sample = "GHOST_GFX513_STAGE seq=3 stage=4 guest_shader=0x1234 images=1"
    assert re.findall(r"GHOST_GFX513_STAGE seq=\d+ stage=\d+ guest_shader=(0x[0-9a-fA-F]+)", sample) == ["0x1234"]
    assert RADV_DUMP_TOTAL_CAP <= 180 * 1024 * 1024
    assert RADV_DUMP_FILE_CAP <= 24 * 1024 * 1024
    assert RADV_DUMP_FILE_COUNT_CAP <= 70
    assert capture_radv_hang_reports.__name__ == "capture_radv_hang_reports"
    names = [Path("00000.spv"), Path("other.log"), Path("trace.log"),
             Path("pipeline.log"), Path("vm_fault.log")]
    ordered = [p.name for p in sorted(names, key=radv_report_priority)]
    assert ordered[:3] == ["trace.log", "vm_fault.log", "pipeline.log"]
    assert ordered.index("00000.spv") < ordered.index("other.log")
    assert verify_game_debug_environment.__name__ == "verify_game_debug_environment"
    assert datetime.strptime(STAMP, "%Y%m%d-%H%M%S").strftime("%Y%m%d-%H%M%S") == STAMP
    # Avoid the previous false report that a child archive was missing just
    # because the working folder's mtime changed during screenshot collection.
    assert "ghost-no-gdb-validation-" in "ghost-no-gdb-validation-20261008.tar.gz"
    assert "STALL_SNAP_REV" not in globals()
    # Exercise new dump and archive collectors using only throwaway files.
    # No GPU, root privilege, game launch, process signals or settings changes.
    originals = (HOME, WORK, GAME_LAUNCH_TIME, GAME_PID, SESSION_DIR)
    try:
        with tempfile.TemporaryDirectory(prefix="ghost-radv-hang-selftest-") as tmp:
            temp = Path(tmp)
            HOME = temp
            WORK = temp / "work"
            WORK.mkdir()
            GAME_PID = 54321
            GAME_LAUNCH_TIME = time.time() - 1
            SESSION_DIR = None
            wanted = temp / "radv_dumps_54321_fixture"
            wanted.mkdir()
            (wanted / "trace.log").write_text("last trace point\nGPU_HANG=1\n")
            other = temp / "radv_dumps_99999_fixture"
            other.mkdir()
            (other / "trace.log").write_text("UNRELATED_PID_MUST_NOT_CAPTURE\n")
            capture_radv_hang_reports()
            report = json.loads((WORK / "radv-dump-status.json").read_text())
            assert report["game_pid_candidates"] == [54321]
            assert report["files_collected"] == 1, report
            assert report["RADV_DEBUG"] == "hang,noumr"
            assert all("99999" not in folder for folder in report["driver_dump_directories"])
            assert (WORK / "radv-dumps" / wanted.name / "trace.log").is_file()
            dummy = temp / "report.txt"
            dummy.write_text("ghost test")
            child = temp / "ghost-no-gdb-validation-fixture.tar.gz"
            with tarfile.open(child, "w:gz") as bundle:
                bundle.add(dummy, arcname="report.txt")
            (WORK / "build-and-trial.log").write_text("ARCHIVE=" + str(child) + "\n")
            candidate_archive()
            archived = WORK / "candidate-build-and-validation.tar.gz"
            assert archived.is_file() and tarfile.is_tarfile(archived)
            assert "eligible" in (WORK / "child-archive-discovery.json").read_text()
    finally:
        HOME, WORK, GAME_LAUNCH_TIME, GAME_PID, SESSION_DIR = originals
    print("SELFTEST PASS: RADV hang dump PID scoping, bounded prioritized capture and "
          "nested validation archive discovery, all tested with temporary files")

    print("SELFTEST PASS: pinned workflow, launcher/game path, monitor timing, "
          "session PID extraction. No graphics/process operations performed.")


def main() -> int:
    global TEST_STATUS
    if sys.argv[1:] == ["--self-test"]:
        test()
        return 0
    WORK.mkdir(parents=True, exist_ok=True)
    error = None
    try:
        orchestrate()
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        TEST_STATUS = "FAILED"
        say("TRIAL_ERROR=" + error)
        (WORK / "error-traceback.txt").write_text(traceback.format_exc())
    finally:
        try:
            stop_launched_game()
        except Exception as exc:
            say("GAME_CLEANUP_ERROR=" + repr(exc))
        try:
            if CHILD_PROC and CHILD_PROC.poll() is None:
                terminate_child()
        except Exception as exc:
            say("CHILD_CLEANUP_ERROR=" + repr(exc))
        try:
            cleanup_child_session()
        except Exception as exc:
            say("HELPER_CLEANUP_ERROR=" + repr(exc))
        try:
            cleanup_test_watchers()
        except Exception as exc:
            say("WATCHER_CLEANUP_ERROR=" + repr(exc))
        try:
            if GAME_LAUNCH_TIME:
                time.sleep(1.5)
            collect_screenshots()
            report_session()
        except Exception as exc:
            say("GAME_EVIDENCE_COLLECTION_ERROR=" + repr(exc))
        try:
            capture_radv_hang_reports()
        except Exception as exc:
            say("RADV_HANG_COLLECTION_ERROR=" + repr(exc))
        try:
            capture_readonly_gpu_state()
        except Exception as exc:
            say("GPU_STATE_COLLECTION_ERROR=" + repr(exc))
        try:
            candidate_archive()
        except Exception as exc:
            say("INNER_REPORT_COLLECTION_ERROR=" + repr(exc))
        if BINARY.exists():
            try:
                observed = checksum(BINARY)
                say(f"INSTALLED_BINARY_SHA256={observed} baseline_restored={observed == BASE_SHA}")
            except OSError as exc:
                say("INSTALLED_BINARY_CHECK_FAILED=" + repr(exc))
        else:
            say("INSTALLED_BINARY_MISSING")
        (WORK / "events.txt").write_text("\n".join(EVENTS) + "\n")
        count_images = len(list(SCREEN_DIR.glob("*.png")))
        if GAME_LAUNCH_TIME and count_images == 0:
            say("SCREENSHOT_WARNING: no PNG saved despite capture triggers; check X11/SDL evidence")
            if TEST_STATUS == "FINISHED":
                TEST_STATUS = "INCOMPLETE_NO_SCREENSHOTS"
        (WORK / "summary.json").write_text(json.dumps({
            "test_status": TEST_STATUS,
            "child_returncode": CHILD_RC,
            "stop_reason": STOP_REASON,
            "game_exit_code": GAME_PROC.returncode if GAME_PROC is not None else None,
            "RADV_DEBUG": "hang,noumr",
            "RADV_hang_enables_shader_synchronization": True,
            "RADV_umr_disabled": True,
            "driver_debug_guest_confirmed": RADV_GUEST_ENV_VALUE == "hang,noumr",
            "UMR_available": RADV_UMR_PRESENT,
            "RADV_DEBUG_seen_in_guest": RADV_GUEST_ENV_VALUE,
            "RADV_dumps_found": RADV_DUMPS_FOUND,
            "game_pid": GAME_PID,
            "game_session": str(SESSION_DIR) if SESSION_DIR else None,
            "screenshots_collected": count_images,
            "error": error,
            "installed_baseline_sha256": BASE_SHA,
            "running_after": [
                pid for pid in emulator_pids()
                if GAME_START_TICKS is not None and proc_info(pid)
                and proc_info(pid)["start_ticks"] >= GAME_START_TICKS
                and exact_ghost(pid)
            ],
        }, indent=2) + "\n")
        try:
            with tarfile.open(OUT, "w:gz") as bundle:
                for item in sorted(WORK.iterdir()):
                    if item in (TRIAL_PATH, RIP_SAMPLER) or item.name == "__pycache__":
                        continue
                    bundle.add(item, arcname=item.name)
            say("ARCHIVE_READY=" + str(OUT))
            say("UPLOAD_THIS_FILE=" + str(OUT))
        except Exception as exc:
            say("ARCHIVE_CREATION_FAILED=" + repr(exc))
            return 2
        game_left = alive_session_pids(GAME_SID) if GAME_SID is not None else []
        child_left = alive_session_pids(CHILD_PROC.pid) if CHILD_PROC is not None else []
        if game_left or child_left:
            say(f"WARNING_TEST_PROCESSES_REMAIN game={game_left} helpers={child_left}")
        else:
            say("ALL_TEST_SESSIONS_EXITED; SSH/Sunshine/ES-DE and unrelated processes preserved.")
    return 0 if TEST_STATUS in ("FINISHED", "EXPECTED_TIMEOUT_CLEANUP") else 1


if __name__ == "__main__":
    raise SystemExit(main())
