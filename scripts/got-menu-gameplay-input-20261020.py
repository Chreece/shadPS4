#!/usr/bin/env python3
"""Send one Continue key to the isolated emulator's own X11 window."""
import argparse
import ctypes as C
import ctypes.util
import json
import os
import signal
import time


def owns_window(pid, group, executable):
    try:
        return os.getpgid(pid) == group and os.path.realpath(f"/proc/{pid}/exe") == executable
    except (OSError, ProcessLookupError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", type=int)
    parser.add_argument("--exe")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check and (args.group is None or not args.exe):
        parser.error("--group and --exe are required to send input")
    executable = os.path.realpath(args.exe) if args.exe else ""
    x = C.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
    xt = C.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
    display_t, window_t, atom_t = C.c_void_p, C.c_ulong, C.c_ulong

    def bind(lib, name, result, *arguments):
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = result, list(arguments)
        return fn

    bind(x, "XOpenDisplay", display_t, C.c_char_p)
    bind(x, "XDefaultRootWindow", window_t, display_t)
    bind(x, "XInternAtom", atom_t, display_t, C.c_char_p, C.c_int)
    bind(x, "XGetWindowProperty", C.c_int, display_t, window_t, atom_t, C.c_long,
         C.c_long, C.c_int, atom_t, C.POINTER(atom_t), C.POINTER(C.c_int),
         C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.POINTER(C.c_ubyte)))
    bind(x, "XFree", C.c_int, C.c_void_p)
    bind(x, "XQueryTree", C.c_int, display_t, window_t, C.POINTER(window_t),
         C.POINTER(window_t), C.POINTER(C.POINTER(window_t)), C.POINTER(C.c_uint))
    bind(x, "XStringToKeysym", C.c_ulong, C.c_char_p)
    bind(x, "XKeysymToKeycode", C.c_uint, display_t, C.c_ulong)
    bind(x, "XSetInputFocus", C.c_int, display_t, window_t, C.c_int, C.c_ulong)
    bind(x, "XGetInputFocus", C.c_int, display_t, C.POINTER(window_t), C.POINTER(C.c_int))
    bind(x, "XRaiseWindow", C.c_int, display_t, window_t)
    bind(x, "XSync", C.c_int, display_t, C.c_int)
    bind(x, "XCloseDisplay", C.c_int, display_t)
    bind(xt, "XTestFakeKeyEvent", C.c_int, display_t, C.c_uint, C.c_int, C.c_ulong)
    bind(xt, "XTestQueryExtension", C.c_int, display_t, C.POINTER(C.c_int),
         C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(C.c_int))
    display = x.XOpenDisplay(None)
    if not display:
        raise RuntimeError("Cannot open the capture X11 display")
    errors = []
    error_callback_type = C.CFUNCTYPE(C.c_int, display_t, C.c_void_p)
    @error_callback_type
    def on_x_error(display, event):
        errors.append(True)
        return 0
    bind(x, "XSetErrorHandler", C.c_void_p, error_callback_type)
    x.XSetErrorHandler(on_x_error)
    key = 0
    pressed = False
    try:
        event, error, major, minor = C.c_int(), C.c_int(), C.c_int(), C.c_int()
        if not xt.XTestQueryExtension(display, C.byref(event), C.byref(error), C.byref(major), C.byref(minor)):
            raise RuntimeError("XTest input extension is unavailable")
        if args.check:
            if not x.XKeysymToKeycode(display, x.XStringToKeysym(b"n")):
                raise RuntimeError("No N key mapping on capture display")
            print(json.dumps({"status": "INPUT_PREFLIGHT_PASS"}), flush=True)
            return
        root = x.XDefaultRootWindow(display)
        pid_atom = x.XInternAtom(display, b"_NET_WM_PID", 1)
        if not pid_atom:
            raise RuntimeError("No X11 window PID property available")
        queue = [(root, 0)]
        matches = []
        visited = set()
        while queue and len(visited) < 4096:
            window, depth = queue.pop(0)
            if window in visited:
                continue
            visited.add(window)
            actual, fmt, count, remaining = atom_t(), C.c_int(), C.c_ulong(), C.c_ulong()
            data = C.POINTER(C.c_ubyte)()
            rc = x.XGetWindowProperty(display, window, pid_atom, 0, 1, 0, 6,
                                     C.byref(actual), C.byref(fmt), C.byref(count),
                                     C.byref(remaining), C.byref(data))
            pid = 0
            if rc == 0 and actual.value == 6 and fmt.value == 32 and count.value == 1:
                pid = C.cast(data, C.POINTER(C.c_ulong))[0]
            if data:
                x.XFree(data)
            if pid and owns_window(pid, args.group, executable):
                matches.append((window, pid))
                continue
            if depth >= 5:
                continue
            parent, tree_root, length = window_t(), window_t(), C.c_uint()
            children = C.POINTER(window_t)()
            if x.XQueryTree(display, window, C.byref(tree_root), C.byref(parent),
                            C.byref(children), C.byref(length)):
                queue.extend((children[i], depth + 1) for i in range(length.value))
            if children:
                x.XFree(children)
        if len(matches) != 1:
            raise RuntimeError(f"Expected one owned emulator window; found {len(matches)}")
        window, pid = matches[0]
        key = x.XKeysymToKeycode(display, x.XStringToKeysym(b"n"))
        if not key or not owns_window(pid, args.group, executable):
            raise RuntimeError("Key mapping or emulator ownership changed")
        x.XRaiseWindow(display, window)
        x.XSetInputFocus(display, window, 2, 0)
        x.XSync(display, 0)
        focused, revert = window_t(), C.c_int()
        x.XGetInputFocus(display, C.byref(focused), C.byref(revert))
        if errors or focused.value != window:
            raise RuntimeError("Emulator window did not receive focus; no key sent")
        pressed = True
        if not xt.XTestFakeKeyEvent(display, key, 1, 0):
            raise RuntimeError("XTest key-down failed")
        x.XSync(display, 0)
        time.sleep(0.12)
        if not xt.XTestFakeKeyEvent(display, key, 0, 0):
            raise RuntimeError("XTest key-up failed")
        x.XSync(display, 0)
        pressed = False
        print(json.dumps({"status": "KEY_SENT", "key": "n", "pid": pid,
                          "window": window, "gameplay_confirmed": False}), flush=True)
    finally:
        if pressed and key:
            xt.XTestFakeKeyEvent(display, key, 0, 0)
            x.XSync(display, 0)
        x.XCloseDisplay(display)


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise InterruptedError(f"Interrupted by signal {signum}")
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "INPUT_FAILED", "error": str(error)}), flush=True)
        raise SystemExit(2)
