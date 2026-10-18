#!/usr/bin/env python3
"""GoW automated stage-gated X11 input and image evidence, no game patching.

Uses the game's existing SDL keyboard bindings: N -> PS Cross,
W -> left-stick up and D -> left-stick right. This corrects the forward-only
route that got stuck in the 20:50 A/B test.
Sends XTest input ONLY to an X11 window whose _NET_WM_PID matches the
test-launched emulator PID. Uses an in-memory low-resolution screen signal
ONLY for control-state transitions, not for comparing A/B images or declaring
equivalent scene performance. No periodic screenshots are saved.
"""
from __future__ import annotations

import argparse
import ctypes as C
import ctypes.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time

W, H = 160, 90
SAMPLE_PERIOD = 2.0
MAX_BEFORE_CUTSCENE = 230.0
OBSERVE_AFTER_CUTSCENE = 95.0


def metrics(raw: bytes) -> dict:
    if len(raw) != W * H * 3:
        raise ValueError("SCREEN_CAPTURE_BAD_RGB_LENGTH")
    # Identified from observed 19:00 A/B capture; X/Y are normalized to
    # the full frame. Test at 160x90 so screenshots need not be decoded by PIL.
    def bright_fraction(x0, y0, x1, y1):
        bright = n = 0
        for y in range(int(y0 * H), int(y1 * H)):
            for x in range(int(x0 * W), int(x1 * W)):
                p = (y * W + x) * 3
                if (raw[p] + raw[p + 1] + raw[p + 2]) > 465:
                    bright += 1
                n += 1
        return bright / max(1, n)

    logo = bright_fraction(.29, .17, .68, .35)
    selected_continue = bright_fraction(.42, .52, .62, .57)
    warm = blue = bright = textured = 0
    n = 0
    previous_lum = None
    for y in range(10, H - 5):
        for x in range(5, W - 5):
            p = (y * W + x) * 3
            r, g, b = raw[p], raw[p + 1], raw[p + 2]
            lum = (r + g + b) // 3
            bright += lum > 110
            warm += (r > 36 and r > g * 1.25 and g > b * 1.30)
            blue += (b > 30 and b > r * 1.15 and b > g * 1.15)
            if previous_lum is not None:
                textured += abs(lum - previous_lum)
            previous_lum = lum
            n += 1
    bright /= max(1, n)
    warm /= max(1, n)
    blue /= max(1, n)
    texture = textured / max(1, n)
    menu = logo > .020 and selected_continue > .065
    # Snowlit approach preceded the wolf in recorded 20:10 capture.
    # Distinguish it from startup, blue cave and menu with high brightness.
    approach = (not menu and bright > .027 and texture > 5.0 and logo < .015)
    # This is an *candidate* wolf/cutscene signature, not semantic OCR.
    # It is only accepted after the snowy approach was seen, then visually
    # revalidated from the retained screenshots before performance claims.
    wolf_candidate = (not menu and warm > .09 and bright < .018 and
                      blue < .065 and texture > 2.0)
    return dict(logo=round(logo, 4), highlighted_continue=round(selected_continue, 4),
                warm=round(warm, 4), bright=round(bright, 4),
                blue=round(blue, 4), texture=round(texture, 3),
                menu=menu, approach=approach, wolf_candidate=wolf_candidate)


def xlib():
    a = ctypes_library("X11")
    b = ctypes_library("Xtst")
    a.XOpenDisplay.argtypes = [C.c_char_p]
    a.XOpenDisplay.restype = C.c_void_p
    a.XCloseDisplay.argtypes = [C.c_void_p]
    a.XDefaultRootWindow.argtypes = [C.c_void_p]
    a.XDefaultRootWindow.restype = C.c_ulong
    a.XInternAtom.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
    a.XInternAtom.restype = C.c_ulong
    a.XGetWindowProperty.argtypes = [
        C.c_void_p, C.c_ulong, C.c_ulong, C.c_long, C.c_long, C.c_int,
        C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int),
        C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
        C.POINTER(C.POINTER(C.c_ubyte))]
    a.XGetWindowProperty.restype = C.c_int
    a.XQueryTree.argtypes = [
        C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
        C.POINTER(C.POINTER(C.c_ulong)), C.POINTER(C.c_uint)]
    a.XQueryTree.restype = C.c_int
    a.XFree.argtypes = [C.c_void_p]
    a.XRaiseWindow.argtypes = [C.c_void_p, C.c_ulong]
    a.XSetInputFocus.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_ulong]
    a.XStringToKeysym.argtypes = [C.c_char_p]
    a.XStringToKeysym.restype = C.c_ulong
    a.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
    a.XKeysymToKeycode.restype = C.c_uint
    a.XFlush.argtypes = [C.c_void_p]
    a.XSync.argtypes = [C.c_void_p, C.c_int]
    b.XTestQueryExtension.argtypes = [
        C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int),
        C.POINTER(C.c_int), C.POINTER(C.c_int)]
    b.XTestQueryExtension.restype = C.c_int
    b.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
    b.XTestFakeKeyEvent.restype = C.c_int
    return a, b


def ctypes_library(name):
    soname = ctypes.util.find_library(name)
    if not soname:
        raise RuntimeError("X11_LIBRARY_MISSING_" + name)
    return C.CDLL(soname)


class Keyboard:
    def __init__(self, display, pid):
        self.x, self.xtest = xlib()
        self.display = self.x.XOpenDisplay(display.encode())
        if not self.display:
            raise RuntimeError("X11_DISPLAY_UNAVAILABLE_" + display)
        self.root = self.x.XDefaultRootWindow(self.display)
        self.pid_atom = self.x.XInternAtom(self.display, b"_NET_WM_PID", 0)
        self.window = 0
        self.pid = pid
        self.down = set()
        checks = [C.c_int() for _ in range(4)]
        if not self.xtest.XTestQueryExtension(
                self.display, *[C.byref(x) for x in checks]):
            raise RuntimeError("XTEST_EXTENSION_MISSING")

    def find_pid_window(self):
        queue = [self.root]
        examined = 0
        while queue and examined < 10000:
            win = queue.pop(0)
            examined += 1
            atom = C.c_ulong()
            fmt = C.c_int()
            n = C.c_ulong()
            remaining = C.c_ulong()
            ptr = C.POINTER(C.c_ubyte)()
            err = self.x.XGetWindowProperty(
                self.display, win, self.pid_atom, 0, 1, 0, 0,
                C.byref(atom), C.byref(fmt), C.byref(n),
                C.byref(remaining), C.byref(ptr))
            try:
                if (err == 0 and fmt.value == 32 and n.value >= 1 and ptr):
                    target = C.cast(ptr, C.POINTER(C.c_ulong))[0]
                    if target == self.pid:
                        self.window = win
                        return win
            finally:
                if ptr:
                    self.x.XFree(C.cast(ptr, C.c_void_p))
            root = C.c_ulong()
            parent = C.c_ulong()
            children = C.POINTER(C.c_ulong)()
            count = C.c_uint()
            if self.x.XQueryTree(self.display, win, C.byref(root),
                                 C.byref(parent), C.byref(children),
                                 C.byref(count)):
                if children:
                    queue.extend(children[i] for i in range(count.value))
                    self.x.XFree(C.cast(children, C.c_void_p))
        return 0

    def press(self, key, down):
        if not self.window:
            raise RuntimeError("X11_EMULATOR_WINDOW_PID_NOT_VERIFIED")
        sym = self.x.XStringToKeysym(key.encode("ascii"))
        code = self.x.XKeysymToKeycode(self.display, sym)
        if not sym or not code:
            raise RuntimeError("X11_UNMAPPED_KEY_" + key)
        self.x.XRaiseWindow(self.display, self.window)
        self.x.XSetInputFocus(self.display, self.window, 1, 0)
        if not self.xtest.XTestFakeKeyEvent(self.display, code, int(down), 0):
            raise RuntimeError("XTEST_KEY_SEND_FAILED")
        self.x.XFlush(self.display)
        self.x.XSync(self.display, 0)
        if down:
            self.down.add(key)
        else:
            self.down.discard(key)

    def tap(self, key, duration=.18):
        self.press(key, True)
        time.sleep(duration)
        self.press(key, False)

    def close(self):
        if self.display:
            for key in sorted(self.down):
                try:
                    self.press(key, False)
                except Exception as exc:
                    print("KEY_RELEASE_ERROR=" + str(exc), flush=True)
            self.x.XCloseDisplay(self.display)
            self.display = None


def screenshot(directory: Path, label: str | None = None):
    # In-memory capture only. No frame dumps and no disk I/O in the 95-second
    # measured observation phase after the cutscene candidate is detected.
    # Image signal is used to decide when to RELEASE keys, never to compare A/B.
    if label is not None:
        raise RuntimeError("SCREENSHOT_FILES_DISABLED_BY_LOG_ONLY_POLICY")
    capture = subprocess.run(
        ["import", "-display", os.environ.get("DISPLAY", ":0"),
         "-window", "root", "png:-"],
        capture_output=True, timeout=9)
    if capture.returncode or not capture.stdout:
        raise RuntimeError("IN_MEMORY_SCREEN_CAPTURE_FAILED_" +
                           capture.stderr.decode(errors="replace")[-250:])
    output = subprocess.run(
        ["convert", "png:-", "-resize", f"{W}x{H}!",
         "-depth", "8", "rgb:-"],
        input=capture.stdout, capture_output=True, timeout=9)
    if output.returncode:
        raise RuntimeError("IMAGE_RGB_EXTRACTION_FAILED_" +
                           output.stderr.decode(errors="replace")[-250:])
    return metrics(output.stdout)

def alive(pid):
    try:
        os.kill(pid, 0)
        state = (Path("/proc") / str(pid) / "stat").read_text()
        return state.rsplit(") ", 1)[1][0] not in "ZX"
    except (OSError, PermissionError, IndexError):
        return False


def publish(path, doc):
    tmp = path.with_suffix(".partial")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def selftest():
    raw = bytearray(W * H * 3)
    assert not metrics(raw)["menu"]
    def block(x0, y0, x1, y1, color):
        for y in range(int(y0 * H), int(y1 * H)):
            for x in range(int(x0 * W), int(x1 * W)):
                p = (y * W + x) * 3
                raw[p:p+3] = bytes(color)
    block(.29, .17, .68, .35, (240, 240, 240))
    block(.42, .52, .62, .57, (245, 245, 245))
    assert metrics(raw)["menu"]
    print("AUTOMATED_MENU_DETECTOR_SYNTHETIC=PASS")
    print("NO_INPUT_OR_X11_REQUIRED_FOR_SELFTEST=YES")


def validate_previous_archive(archive):
    """Use actual user A/B screenshots as regression fixtures; no game or X11."""
    cases = [
        ("B_SPARSE_CPU_UPLOAD", 60, "menu"),
        ("B_SPARSE_CPU_UPLOAD", 120, "approach"),
        ("A_DENSE_CPU_UPLOAD", 90, "wolf_candidate"),
        ("A_DENSE_CPU_UPLOAD", 120, "wolf_candidate"),
    ]
    with tarfile.open(archive, "r:gz") as tf:
        for mode, second, expected in cases:
            match = [member for member in tf.getmembers() if member.name.endswith(
                f"/sparse-cpu-upload-ab/{mode}/frame-{second}.png")]
            if len(match) != 1 or match[0].size > 7 * 2**20:
                raise RuntimeError(f"ARCHIVE_REQUIRED_FRAME_MISSING={mode}:{second}")
            png = tf.extractfile(match[0]).read()
            proc = subprocess.run(
                ["convert", "png:-", "-resize", f"{W}x{H}!",
                 "-depth", "8", "rgb:-"], input=png,
                capture_output=True, timeout=12)
            if proc.returncode:
                raise RuntimeError(f"ARCHIVE_FRAME_RGB_FAILED={mode}:{second}")
            row = metrics(proc.stdout)
            print(f"OLD_SCENE_CLASSIFIER_{mode}_{second}={expected} "
                  + json.dumps(row, sort_keys=True), flush=True)
            if not row[expected]:
                raise RuntimeError(f"OLD_SCENE_CLASSIFIER_REGRESSION_{mode}_{second}")
    print("PRIOR_REAL_SCENE_DETECTION=PASS cases=4")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--preflight", action="store_true")
    p.add_argument("--validate-old-archive", type=Path)
    p.add_argument("--pid", type=int)
    p.add_argument("--display", default=":0")
    p.add_argument("--out", type=Path)
    p.add_argument("--launch-monotonic", type=float)
    p.add_argument("--cross-key", default="n")
    p.add_argument("--forward-key", default="w")
    p.add_argument("--right-key", default="d")
    p.add_argument("--max-seconds", type=int, default=350)
    a = p.parse_args()
    if a.self_test:
        selftest()
        return 0
    if not shutil.which("import") or not shutil.which("convert"):
        raise SystemExit("IMAGEMAGICK_IMPORT_AND_CONVERT_REQUIRED_NO_INPUT")
    if a.validate_old_archive is not None:
        if not a.validate_old_archive.is_file():
            raise SystemExit("OLD_CAPTURE_ARCHIVE_NOT_FOUND")
        validate_previous_archive(a.validate_old_archive)
        return 0
    # Fail closed for custom bindings rather than send keystrokes with unknown
    # effect. Caller may set both keys explicitly after checking input_config.
    if not (len(a.cross_key) == len(a.forward_key) ==
            len(a.right_key) == 1 and
            a.cross_key.isalpha() and a.forward_key.isalpha() and
            a.right_key.isalpha() and
            len({a.cross_key, a.forward_key, a.right_key}) == 3):
        raise SystemExit("INVALID_KEY_CONFIG_NO_INPUT")
    if a.preflight:
        # Verify libraries, XTest and exact DISPLAY before launching game.
        keyboard = Keyboard(a.display, -1)
        keyboard.close()
        print("X11_XTEST_KEYBOARD_READY=YES")
        return 0
    if (not a.pid or not a.out or a.launch_monotonic is None or
            a.max_seconds < 200 or a.max_seconds > 480):
        raise SystemExit("INVALID_AUTOPILOT_ARGUMENTS_NO_INPUT")
    a.out.mkdir(parents=True, exist_ok=True)
    os.environ["DISPLAY"] = a.display
    status_path = a.out / "auto-progress.json"
    record = {"pid": a.pid, "display": a.display, "mode": "WAIT_MENU",
              "menu_seen_at": None, "cross_at": None, "forward_at": None,
              "right_at": None, "right_released_at": None,
              "approach_at": None, "cutscene_candidate_at": None,
              "cutscene_at": None, "done_at": None, "success": False,
              "observations": [], "input_actions": [], "error": None,
              "source": "observed X11 frames + XTest keyboard",
              "gamepad_equivalent": f"cross={a.cross_key}, forward={a.forward_key}, right={a.right_key}",
              "cutscene_confirmation": "UNVERIFIED_SCREEN_CANDIDATE; LOG_ONLY_PERFORMANCE",
              "image_comparison": "DISABLED; no periodic screenshot files"}
    stop = False
    def interrupted(signum, _):
        nonlocal stop
        stop = True
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    keyboard = None
    menu_hits = 0
    approach_hits = 0
    wolf_hits = 0
    try:
        keyboard = Keyboard(a.display, a.pid)
        start = time.monotonic()
        window_deadline = start + 45
        while not stop and not keyboard.find_pid_window():
            if not alive(a.pid):
                raise RuntimeError("GAME_EXITED_BEFORE_OWN_WINDOW")
            if time.monotonic() > window_deadline:
                raise RuntimeError("NO_X11_WINDOW_WITH_EXACT_GAME_PID_NO_KEYS_SENT")
            time.sleep(.4)
        record["window_id"] = hex(keyboard.window)
        print("EXACT_GAME_WINDOW_VERIFIED=" + hex(keyboard.window), flush=True)
        while not stop and alive(a.pid):
            elapsed = round(time.monotonic() - a.launch_monotonic, 2)
            if elapsed > a.max_seconds:
                raise RuntimeError("AUTOPILOT_TIMEOUT_STAGE_" + record["mode"])
            # Screen classification ends at the tentative cinematic start.
            # Once observing, no capture subprocess should run.
            observed = screenshot(a.out) if record["mode"] != "OBSERVING_CUTSCENE" else None
            if observed is not None:
                sample = {"t": elapsed, **observed}
                record["observations"].append(sample)
                record["observations"] = record["observations"][-260:]
            state = record["mode"]
            if state == "WAIT_MENU":
                menu_hits = menu_hits + 1 if observed["menu"] else 0
                if menu_hits >= 2:
                    record["menu_seen_at"] = elapsed
                    keyboard.tap(a.cross_key)
                    record["cross_at"] = round(time.monotonic() - a.launch_monotonic, 2)
                    record["input_actions"].append({"action": "PLAYSTATION_CROSS_CONTINUE", "t": record["cross_at"]})
                    print(f"MENU_CONFIRMED_AND_CROSS_SENT={elapsed}", flush=True)
                    # Holding forward across loading is intentional; movement
                    # begins immediately once the guest starts accepting axes.
                    time.sleep(1.5)
                    keyboard.press(a.forward_key, True)
                    keyboard.press(a.right_key, True)
                    record["forward_at"] = round(time.monotonic() - a.launch_monotonic, 2)
                    record["right_at"] = record["forward_at"]
                    record["input_actions"].append({"action": "LEFT_STICK_FORWARD_AND_RIGHT_DOWN", "t": record["forward_at"]})
                    print("AUTO_STEERING=FORWARD_PLUS_RIGHT keys=" +
                          a.forward_key + "+" + a.right_key, flush=True)
                    record["mode"] = "FORWARD_RIGHT_TO_CUTSCENE"
            elif state == "FORWARD_RIGHT_TO_CUTSCENE":
                if observed["approach"]:
                    approach_hits += 1
                    if approach_hits >= 2 and record["approach_at"] is None:
                        record["approach_at"] = elapsed
                        print(f"WORLD_APPROACH_CANDIDATE={elapsed}", flush=True)
                elif approach_hits < 2:
                    approach_hits = 0
                # Snowy approach can be missed with different camera movement,
                # so do not require it as a mandatory gate (20:50 B false fail).
                # Observe at least 35s after Continue and require 5 consecutive
                # samples of the candidate visual signature. This is a navigation
                # heuristic, NOT a semantic match and not a performance win.
                if (record["cross_at"] is not None and
                    elapsed - record["cross_at"] >= 35.0 and
                    observed["wolf_candidate"]):
                    wolf_hits += 1
                else:
                    wolf_hits = 0
                if wolf_hits >= 5:
                    record["cutscene_candidate_at"] = elapsed
                    keyboard.press(a.right_key, False)
                    keyboard.press(a.forward_key, False)
                    record["right_released_at"] = elapsed
                    record["input_actions"].append({"action": "LEFT_STICK_FORWARD_AND_RIGHT_UP", "t": elapsed})
                    record["cutscene_at"] = elapsed
                    record["mode"] = "OBSERVING_CUTSCENE"
                    print(f"CUTSCENE_VISUAL_CANDIDATE_UNVERIFIED={elapsed} ALL_MOVEMENT_RELEASED=YES", flush=True)
            elif state == "OBSERVING_CUTSCENE":
                elapsed_from_cutscene = elapsed - record["cutscene_at"]
                if elapsed_from_cutscene >= OBSERVE_AFTER_CUTSCENE:
                    record["done_at"] = elapsed
                    record["mode"] = "COMPLETE"
                    record["success"] = True
                    print(f"AUTOPILOT_NAVIGATION_CANDIDATE=YES POST_CANDIDATE_LOG_SECONDS={OBSERVE_AFTER_CUTSCENE} FRAME_COMPARE=DISABLED", flush=True)
                    break
            publish(status_path, record)
            time.sleep(SAMPLE_PERIOD)
        if stop:
            raise RuntimeError("AUTOPILOT_INTERRUPTED")
        if not record["success"]:
            raise RuntimeError("AUTOPILOT_EXITED_WITHOUT_CUTSCENE_CANDIDATE_NO_SCENE_COMPARISON")
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        print("AUTOPILOT_FAILURE=" + record["error"], flush=True)
    finally:
        if keyboard:
            keyboard.close()
        publish(status_path, record)
    return 0 if record["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
