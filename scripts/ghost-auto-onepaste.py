#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unattended Ghost of Tsushima playtest: build, launch, capture, stop, package.

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

HOME = Path.home()
ENTRY = Path("/mnt/roms-all/ps4/Ghost of Tsushima.ps4")
LAUNCHER = HOME / ".local/bin/shadps4-esde"
BINARY = HOME / "Applications/shadps4/shadps4"
STATE = HOME / ".local/state/shadps4-playtest-logs"
BASE_SHA = "f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f"
TRIAL_REV = "2ceb305e721444f6d72961003174a113ba69c07d"
TRIAL_FILE = "ghost-arena-1g-validation-onerun.py"
DISPLAY = ":0"
READY = "READY - LAUNCH GHOST OF TSUSHIMA THROUGH MOONLIGHT -> ES-DE NOW."
SCREEN_TIMES = (6, 12, 24, 42, 65, 95, 125, 145)
MAX_GAME_SECONDS = 156
MAX_PREPARE_SECONDS = 850
MAX_ROLLBACK_SECONDS = 110

STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = HOME / ("ghost-auto-evidence-" + STAMP + ".tar.gz")
WORK = HOME / ".cache" / ("ghost-auto-evidence-" + STAMP)
TRIAL_PATH = WORK / TRIAL_FILE
SCREEN_DIR = WORK / "screenshots"

EVENTS: list[str] = []
GAME_PROC: subprocess.Popen | None = None
CHILD_PROC: subprocess.Popen | None = None
READER_THREAD: threading.Thread | None = None
CHILD_READY = threading.Event()
GAME_LAUNCH_TIME: float | None = None
GAME_PID: int | None = None
GAME_SID: int | None = None
SESSION_DIR: Path | None = None
CHILD_RC: int | None = None
TEST_STATUS = "PREPARING"
SCREEN_ENV: dict[str, str] | None = None
STOP_REASON = "not_started"


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
                    GAME_LAUNCH_TIME is not None and
                    Path(f"/proc/{extra}").stat().st_mtime >= GAME_LAUNCH_TIME - 4):
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
    STOP_REASON = "session_stopped"


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
                "ghost-arena-1g-" not in info["argv"]):
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
    else:
        say("NO_RUNTIME_LOG: game may have exited before runtime initialized")


def candidate_archive() -> None:
    log = WORK / "build-and-trial.log"
    if not log.is_file():
        return
    contents = log.read_text(errors="replace")
    paths = re.findall(r"(?m)^ARCHIVE=(.+\.tar\.gz)\s*$", contents)
    for path in reversed(paths):
        p = Path(path)
        if p.is_file() and p.parent == HOME and p.name.startswith("ghost-arena-1g-validation-"):
            shutil.copy2(p, WORK / "candidate-build-and-validation.tar.gz")
            say(f"CHILD_REPORT_INCLUDED={p}")
            return
    say("CHILD_REPORT_NOT_FOUND (log retained; build may have failed)")


def ready_preflight() -> dict[str, str]:
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
    return env


def launch_test(env: dict[str, str]) -> None:
    global GAME_PROC, GAME_LAUNCH_TIME, GAME_PID, GAME_SID, SESSION_DIR, STOP_REASON
    if emulator_pids():
        raise RuntimeError("An emulator appeared before auto-launch; refusing.")
    GAME_LAUNCH_TIME = time.time()
    with (WORK / "launcher-stdout.log").open("w") as out:
        GAME_PROC = subprocess.Popen(
            [str(LAUNCHER), str(ENTRY)], cwd=str(HOME), env=env,
            stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    GAME_PID = GAME_PROC.pid
    GAME_SID = GAME_PID
    say(f"AUTOMATIC_GAME_LAUNCH pid={GAME_PID} sid={GAME_SID} display={DISPLAY}")
    STOP_REASON = "running"
    # Prevent rushing screenshot shortcuts before shadPS4's window initializes.
    start = time.monotonic()
    while time.monotonic() - start < 30 and GAME_PROC.poll() is None:
        SESSION_DIR = current_session()
        if SESSION_DIR:
            say("GAME_SESSION=" + str(SESSION_DIR))
            break
        time.sleep(0.5)
    if not SESSION_DIR:
        say("SESSION_META_NOT_YET_SEEN; continuing bounded observation")


def observe_game(env: dict[str, str]) -> None:
    global SESSION_DIR, STOP_REASON
    if GAME_PROC is None:
        return
    start = time.monotonic()
    last_sample = -100.0
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
    global CHILD_PROC
    if CHILD_PROC is None or CHILD_PROC.poll() is not None:
        return
    # The validation wrapper and nested build script have their OWN session.
    # Signal them only if they did not finish cleanup after the game exited.
    say("CHILD_TRIAL_TIMEOUT: requesting bounded rollback via SIGTERM")
    try:
        os.killpg(CHILD_PROC.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        CHILD_PROC.wait(timeout=40)
    except subprocess.TimeoutExpired:
        # Never kill the shell while it is performing source/binary rollback.
        say("CHILD_TRIAL_ROLLBACK_STILL_RUNNING; leaving rollback alive and reporting.")


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
    say("ONE-PASTE: automatic build -> launch -> screenshots -> logs -> stop -> archive")
    # Fail closed BEFORE compiling or changing configs when the automatic
    # launch cannot access the same guarded X11 session and game used before.
    SCREEN_ENV = ready_preflight()
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
    try:
        CHILD_RC = CHILD_PROC.wait(timeout=MAX_ROLLBACK_SECONDS)
    except subprocess.TimeoutExpired:
        terminate_child()
        CHILD_RC = CHILD_PROC.poll()
    if READER_THREAD:
        READER_THREAD.join(timeout=5)
    say(f"CHILD_TRIAL_EXIT_CODE={CHILD_RC}")
    TEST_STATUS = "FINISHED" if CHILD_RC == 0 else "CHILD_FAILED"


def test() -> None:
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
            cleanup_test_watchers()
        except Exception as exc:
            say("WATCHER_CLEANUP_ERROR=" + repr(exc))
        try:
            # Screenshot files may take a moment to finish writing after F12.
            if GAME_LAUNCH_TIME:
                time.sleep(1.5)
            collect_screenshots()
            report_session()
            candidate_archive()
        except Exception as exc:
            say("EVIDENCE_COLLECTION_ERROR=" + repr(exc))
        if BINARY.exists():
            try:
                observed = checksum(BINARY)
                say(f"INSTALLED_BINARY_SHA256={observed} baseline_restored={observed == BASE_SHA}")
            except OSError as exc:
                say("INSTALLED_BINARY_CHECK_FAILED=" + repr(exc))
        else:
            say("INSTALLED_BINARY_MISSING")
        (WORK / "events.txt").write_text("\n".join(EVENTS) + "\n")
        (WORK / "summary.json").write_text(json.dumps({
            "test_status": TEST_STATUS,
            "child_returncode": CHILD_RC,
            "stop_reason": STOP_REASON,
            "game_pid": GAME_PID,
            "game_session": str(SESSION_DIR) if SESSION_DIR else None,
            "screenshots_collected": len(list(SCREEN_DIR.glob("*.png"))),
            "error": error,
            "installed_baseline_sha256": BASE_SHA,
            "running_after": [
                pid for pid in emulator_pids()
                if GAME_SID is not None and proc_info(pid)
                and proc_info(pid)["sid"] == GAME_SID
            ],
        }, indent=2) + "\n")
        try:
            with tarfile.open(OUT, "w:gz") as bundle:
                for item in sorted(WORK.iterdir()):
                    if item == TRIAL_PATH or item.name == "__pycache__":
                        continue
                    bundle.add(item, arcname=item.name)
            say("ARCHIVE_READY=" + str(OUT))
            say("UPLOAD_THIS_FILE=" + str(OUT))
        except Exception as exc:
            say("ARCHIVE_CREATION_FAILED=" + repr(exc))
            return 2
        say("All processes created by this test have been asked to stop; "
            "the existing SSH session and unrelated services were preserved.")
    return 0 if TEST_STATUS == "FINISHED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
