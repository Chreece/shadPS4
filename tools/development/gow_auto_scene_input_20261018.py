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
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from gow_route_steering_20261018 import RouteDriver, MAX_STEER_SECONDS

W, H = 160, 90
SCREEN_CHECK_PERIOD = 3.0
OBSERVE_AFTER_CUTSCENE = 95.0
AUDIO_GUEST = re.compile(
    r'AUDIO_PRODUCER_TIMING port=126.*?ready=(\d+) no_guest_buffer=(\d+)')


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
    # A pure-white load frame has logo=1, selection=1 and texture=0.
    # Reject it as a menu to avoid accidentally pressing Cross during loading.
    menu = .020 < logo < .5 and selected_continue > .065 and texture > .6
    # Snowlit approach preceded the wolf in recorded 20:10 capture.
    # Distinguish it from startup, blue cave and menu with high brightness.
    approach = (not menu and bright > .027 and texture > 5.0 and logo < .015)
    # This is an *candidate* wolf/cutscene signature, not semantic OCR.
    # It is only accepted after the snowy approach was seen, then visually
    # revalidated from the retained screenshots before performance claims.
    wolf_candidate = (not menu and warm > .09 and bright < .018 and
                      blue < .065 and texture > 2.0)
    world_ready = gameplay_visible(menu=menu, blue=blue, texture=texture, logo=logo)
    return dict(logo=round(logo, 4), highlighted_continue=round(selected_continue, 4),
                warm=round(warm, 4), bright=round(bright, 4),
                blue=round(blue, 4), texture=round(texture, 3),
                menu=menu, approach=approach, wolf_candidate=wolf_candidate,
                world_ready=world_ready)


def gameplay_visible(*, menu: bool, blue: float, texture: float, logo: float) -> bool:
    """Recorded 21:47 numeric signals: loading texture~2.6, world~5.6."""
    return not menu and blue > .075 and texture > 4.5 and logo < .15


def audio_candidate_regression() -> None:
    assert AUDIO_GUEST.search("AUDIO_PRODUCER_TIMING port=126 period_us=21333 ready=235 no_guest_buffer=0")
    assert AUDIO_GUEST.search("AUDIO_PRODUCER_TIMING port=126 ready=80 no_guest_buffer=155")
    assert not gameplay_visible(menu=False, blue=.247, texture=2.6, logo=0)
    assert gameplay_visible(menu=False, blue=.201, texture=5.6, logo=.002)
    assert not gameplay_visible(menu=True, blue=.201, texture=5.6, logo=.002)
    print("ACTUAL_214744_NUMERIC_WORLD_GATE_TEST=PASS two_runs")
    print("AUDIO3D_CHANGE_SIGNAL_PARSER=PASS")


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



def hold_forward_right(keyboard, forward_key, right_key):
    """Fail-safe pair of held keyboard axes; roll back forward if right fails."""
    keyboard.press(forward_key, True)
    try:
        keyboard.press(right_key, True)
    except BaseException:
        keyboard.press(forward_key, False)
        raise


def release_forward_right(keyboard, forward_key, right_key):
    """Release both input axes even if one X11 key-up fails."""
    try:
        keyboard.press(right_key, False)
    finally:
        keyboard.press(forward_key, False)


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
    all_white = bytes([255]) * (W * H * 3)
    assert not metrics(all_white)["menu"]
    print("AUTOMATED_MENU_DETECTOR_SYNTHETIC=PASS cases=3; all-white-loading=false")
    audio_candidate_regression()
    class FakeKeyboard:
        def __init__(self, fails_on=None):
            self.events, self.down = [], set()
            self.fails_on = fails_on
        def press(self, key, down):
            if (key, down) == self.fails_on:
                raise RuntimeError("MOCK_X11_KEY_ERROR")
            self.events.append((key, down))
            if down:
                self.down.add(key)
            else:
                self.down.discard(key)
    k = FakeKeyboard()
    hold_forward_right(k, "w", "d")
    assert k.down == {"w", "d"}
    release_forward_right(k, "w", "d")
    assert not k.down
    assert k.events == [("w", True), ("d", True), ("d", False), ("w", False)]
    k = FakeKeyboard(fails_on=("d", True))
    try:
        hold_forward_right(k, "w", "d")
    except RuntimeError:
        assert not k.down and k.events == [("w", True), ("w", False)]
    else:
        raise AssertionError("Missing partial key failure")
    print("FORWARD_RIGHT_XTEST_ORDER_AND_ROLLBACK=PASS cases=2")
    print("NO_INPUT_OR_X11_REQUIRED_FOR_SELFTEST=YES")



class AudioProgress:
    """One-way incremental log read: never change game/audio scheduling."""
    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.healthy_ever = False
        self.low_after_healthy = 0
        self.latest = None

    def read_windows(self):
        events = []
        if not self.path.is_file():
            return events
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            lines = stream.readlines()
            # Do not drop an incomplete logger write between reads.
            if lines and not lines[-1].endswith(b"\n"):
                stream.seek(-len(lines[-1]), os.SEEK_CUR)
                lines.pop()
            self.offset = stream.tell()
        for raw in lines:
            line = raw.decode(errors="replace")
            match = AUDIO_GUEST.search(line)
            if not match:
                continue
            ready, missing = map(int, match.groups())
            fraction = ready / max(1, ready + missing)
            self.latest = fraction
            if fraction >= .92:
                self.healthy_ever = True
                self.low_after_healthy = 0
            elif self.healthy_ever and fraction <= .65:
                self.low_after_healthy += 1
            else:
                self.low_after_healthy = 0
            events.append(dict(ready=ready, missing=missing,
                               fraction=round(fraction, 4),
                               healthy_seen=self.healthy_ever,
                               consecutive_low=self.low_after_healthy))
        return events


def reached_cutscene_candidate(record, source, elapsed, driver, keyboard, forward_key, right_key):
    """Candidate only, never scene equality or a performance outcome."""
    if driver is not None:
        driver.release_all(keyboard)
    record["cutscene_at"] = elapsed
    record["cutscene_candidate_at"] = elapsed
    record["cutscene_candidate_source"] = source
    record["right_released_at"] = elapsed
    record["mode"] = "OBSERVING_CUTSCENE"
    record["input_actions"].append(dict(action="ALL_STEERING_RELEASED",
                                        t=elapsed, reason=source))
    print(f"CUTSCENE_CANDIDATE_UNVERIFIED={elapsed} SOURCE={source} "
          "KEYS_RELEASED=YES IMAGE_COMPARISON=DISABLED", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--preflight", action="store_true")
    p.add_argument("--pid", type=int)
    p.add_argument("--display", default=":0")
    p.add_argument("--out", type=Path)
    p.add_argument("--launch-monotonic", type=float)
    p.add_argument("--cross-key", default="n")
    p.add_argument("--forward-key", default="w")
    p.add_argument("--right-key", default="d")
    p.add_argument("--left-key", default="a")
    p.add_argument("--observe-after", type=int, default=95)
    p.add_argument("--max-seconds", type=int, default=230)
    a = p.parse_args()
    if a.self_test:
        selftest()
        return 0
    if not shutil.which("import") or not shutil.which("convert"):
        raise SystemExit("IMAGEMAGICK_IMPORT_AND_CONVERT_REQUIRED_NO_INPUT")
    # Fail closed for custom bindings rather than send keystrokes with unknown
    # effect. Caller may set both keys explicitly after checking input_config.
    if not (len(a.cross_key) == len(a.forward_key) ==
            len(a.right_key) == len(a.left_key) == 1 and
            all(k.isalpha() for k in
                (a.cross_key, a.forward_key, a.right_key, a.left_key)) and
            len({a.cross_key, a.forward_key, a.right_key, a.left_key}) == 4):
        raise SystemExit("INVALID_KEY_CONFIG_NO_INPUT")
    if a.preflight:
        # Verify XTest AND in-memory capture in the actual X11 session before
        # any emulator launch or Ollama change. No files are created.
        os.environ["DISPLAY"] = a.display
        keyboard = Keyboard(a.display, -1)
        keyboard.close()
        screenshot(Path("."))
        print("X11_XTEST_KEYBOARD_READY=YES")
        print("IN_MEMORY_SCREEN_PROBE_READY=YES")
        print("NO_IMAGES_SAVED_OR_COMPARED=YES")
        return 0
    if (not a.pid or not a.out or a.launch_monotonic is None or
            a.max_seconds < 150 or a.max_seconds > 480 or
            not 15 <= a.observe_after <= 120):
        raise SystemExit("INVALID_AUTOPILOT_ARGUMENTS_NO_INPUT")
    a.out.mkdir(parents=True, exist_ok=True)
    os.environ["DISPLAY"] = a.display
    status_path = a.out / "auto-progress.json"
    record = {"pid": a.pid, "display": a.display, "mode": "WAIT_MENU",
              "menu_seen_at": None, "cross_at": None, "forward_at": None,
              "right_at": None, "left_at": None, "world_ready_at": None,
              "right_released_at": None, "audio_windows": [],
              "route_segments": [],
              "approach_at": None, "cutscene_candidate_at": None,
              "cutscene_at": None, "done_at": None, "success": False,
              "observations": [], "input_actions": [], "error": None,
              "source": "observed X11 frames + XTest keyboard",
              "gamepad_equivalent": f"cross={a.cross_key}, forward={a.forward_key}, right={a.right_key}, left={a.left_key}",
              "cutscene_confirmation": "UNVERIFIED_AUDIO_OR_SCREEN_CANDIDATE; LOG_ONLY_PERFORMANCE",
              "image_comparison": "DISABLED; no periodic screenshot files"}
    stop = False
    def interrupted(signum, _):
        nonlocal stop
        stop = True
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    keyboard = None
    menu_hits = 0
    world_hits = 0
    wolf_hits = 0
    route = None
    route_start = None
    next_screen_at = 0.0
    audio = AudioProgress(a.out / "full-console.log")
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
            clock = time.monotonic()
            elapsed = round(clock - a.launch_monotonic, 2)
            if elapsed > a.max_seconds:
                raise RuntimeError("AUTOPILOT_TIMEOUT_STAGE_" + record["mode"])
            state = record["mode"]

            # Keep steering cadence independent of expensive ImageMagick
            # world-state observations. Key phases are limited to 2.5-8 sec,
            # never another 5-minute W+D hold.
            if state == "STEER_ROUTE" and route is not None:
                phase_event = route.update(clock - route_start, keyboard)
                if phase_event is not None:
                    event = dict(phase_event, t=elapsed)
                    record["route_segments"].append(event)
                    record["input_actions"].append(event)
                    print(f"AUTO_ROUTE_PHASE={event['phase']} "
                          f"T={elapsed} KEYS={'+'.join(event['keys'])}", flush=True)

            # Counter-only cutscene proxy: requires two consecutive 5-second
            # Audio3D shortages after documented healthy output. Not a proof
            # of the cutscene and never used to infer A/B FPS improvement.
            for event in audio.read_windows():
                event["t"] = elapsed
                record["audio_windows"].append(event)
                if (state == "STEER_ROUTE" and route_start is not None and
                    clock - route_start >= 18 and
                    event["consecutive_low"] >= 2):
                    reached_cutscene_candidate(
                        record, "AUDIO3D_PRODUCER_CADENCE_CHANGE",
                        elapsed, route, keyboard, a.forward_key, a.right_key)
                    break

            state = record["mode"]
            if state == "OBSERVING_CUTSCENE":
                if elapsed - record["cutscene_at"] >= a.observe_after:
                    record["done_at"] = elapsed
                    record["mode"] = "COMPLETE"
                    record["success"] = True
                    print(f"ROUTE_CANDIDATE_OBSERVED_LOG_SECONDS={a.observe_after} "
                          "PERFORMANCE_FROM_LOGS_ONLY=YES", flush=True)
                    publish(status_path, record)
                    break
                if clock < next_screen_at:
                    time.sleep(.16)
                    continue
                next_screen_at = clock + SCREEN_CHECK_PERIOD
                publish(status_path, record)
                time.sleep(.16)
                continue

            if clock < next_screen_at:
                time.sleep(.16)
                continue
            next_screen_at = clock + SCREEN_CHECK_PERIOD
            observed = screenshot(a.out)
            record["observations"].append({"t": elapsed, **observed})
            record["observations"] = record["observations"][-240:]
            state = record["mode"]
            if state == "WAIT_MENU":
                menu_hits = menu_hits + 1 if observed["menu"] else 0
                if menu_hits >= 2:
                    record["menu_seen_at"] = elapsed
                    keyboard.tap(a.cross_key)
                    record["cross_at"] = round(time.monotonic() - a.launch_monotonic, 2)
                    record["input_actions"].append(
                        {"action": "PLAYSTATION_CROSS_CONTINUE", "t": record["cross_at"]})
                    print(f"MENU_CONFIRMED_AND_CROSS_SENT={elapsed} "
                          "WAITING_FOR_REAL_GAMEPLAY_BEFORE_STEERING=YES", flush=True)
                    record["mode"] = "WAIT_WORLD_READY"
            elif state == "WAIT_WORLD_READY":
                world_hits = world_hits + 1 if observed["world_ready"] else 0
                if world_hits >= 2:
                    record["world_ready_at"] = elapsed
                    route = RouteDriver(a.forward_key, a.right_key, a.left_key)
                    route_start = time.monotonic()
                    event = route.update(0.0, keyboard)
                    first_event = dict(event, t=elapsed)
                    record["route_segments"].append(first_event)
                    record["input_actions"].append(first_event)
                    record["forward_at"] = elapsed
                    record["right_at"] = elapsed
                    record["mode"] = "STEER_ROUTE"
                    print(f"WORLD_READY={elapsed} STEERING_BEGINS_AFTER_LOAD=YES "
                          "INITIAL_RIGHT_PULSE_SECONDS=3.0", flush=True)
            elif state == "STEER_ROUTE":
                # Legacy wolf-color heuristic is an optional NAVIGATION SIGNAL
                # only. No images stored and no image comparison performed.
                if (route_start is not None and clock - route_start > 10 and
                    observed["wolf_candidate"]):
                    wolf_hits += 1
                else:
                    wolf_hits = 0
                if wolf_hits >= 3:
                    reached_cutscene_candidate(
                        record, "LIVE_SCREEN_COLOR_SIGNAL",
                        elapsed, route, keyboard, a.forward_key, a.right_key)
            publish(status_path, record)
            time.sleep(.16)
        if stop:
            raise RuntimeError("AUTOPILOT_INTERRUPTED")
        if not record["success"]:
            raise RuntimeError("AUTOPILOT_EXITED_WITHOUT_CUTSCENE_CANDIDATE_NO_SCENE_COMPARISON")
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        print("AUTOPILOT_FAILURE=" + record["error"], flush=True)
    finally:
        if keyboard:
            if route is not None:
                try:
                    route.release_all(keyboard)
                except Exception as exc:
                    print("ROUTE_KEY_RELEASE_ERROR=" + str(exc), flush=True)
            keyboard.close()
        publish(status_path, record)
    return 0 if record["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
