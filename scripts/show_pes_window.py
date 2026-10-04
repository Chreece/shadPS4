#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bring the verified, already running PES X11 window to the foreground."""

import ctypes as C
import json
import os
from pathlib import Path
import re
import sys
import time

import trace_video_progress as trace


class ClientData(C.Union):
    _fields_ = [("b", C.c_char * 20), ("s", C.c_short * 10), ("l", C.c_long * 5)]


class ClientMessage(C.Structure):
    _fields_ = [("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
                ("display", C.c_void_p), ("window", C.c_ulong),
                ("message_type", C.c_ulong), ("format", C.c_int), ("data", ClientData)]


class Event(C.Union):
    _fields_ = [("client", ClientMessage), ("pad", C.c_long * 24)]


class X11:
    def __init__(self, display):
        self.lib = C.CDLL("libX11.so.6")
        D, W, I, U, P = C.c_void_p, C.c_ulong, C.c_int, C.c_uint, C.POINTER
        signatures = {
            "XOpenDisplay": (D, [C.c_char_p]), "XCloseDisplay": (I, [D]),
            "XDefaultRootWindow": (W, [D]), "XInternAtom": (W, [D, C.c_char_p, I]),
            "XGetWindowProperty": (I, [D, W, W, C.c_long, C.c_long, I, W,
                                        P(W), P(I), P(W), P(W), P(D)]),
            "XQueryTree": (I, [D, W, P(W), P(W), P(P(W)), P(U)]),
            "XFree": (I, [D]), "XMapRaised": (I, [D, W]),
            "XSendEvent": (I, [D, W, I, C.c_long, P(Event)]),
            "XSync": (I, [D, I]), "XGetInputFocus": (I, [D, P(W), P(I)]),
            "XSetInputFocus": (I, [D, W, I, W]),
            "XGetGeometry": (I, [D, W, P(W), P(I), P(I), P(U), P(U), P(U), P(U)]),
            "XTranslateCoordinates": (I, [D, W, W, I, I, P(I), P(I), P(W)]),
            "XSetErrorHandler": (D, [D]),
        }
        for name, (result, args) in signatures.items():
            function = getattr(self.lib, name)
            function.restype, function.argtypes = result, args
        self.errors = []
        # A vanished window must not let Xlib's default handler exit the caller.
        self.error_handler = C.CFUNCTYPE(I, D, D)(self._error)
        self.previous_handler = self.lib.XSetErrorHandler(self.error_handler)
        self.display = self.lib.XOpenDisplay(display.encode())
        if not self.display:
            self.lib.XSetErrorHandler(self.previous_handler)
            raise RuntimeError("Cannot open PES display " + display + "; keep Moonlight connected")
        self.root = self.lib.XDefaultRootWindow(self.display)

    def _error(self, display, event):
        self.errors.append("An X11 request failed (the window may have closed)")
        return 0

    def close(self):
        if self.display:
            self.lib.XCloseDisplay(self.display)
            self.display = None
            self.lib.XSetErrorHandler(self.previous_handler)

    def atom(self, name):
        return self.lib.XInternAtom(self.display, name.encode(), 0)

    def prop(self, window, name):
        actual, count, remaining = C.c_ulong(), C.c_ulong(), C.c_ulong()
        fmt, data = C.c_int(), C.c_void_p()
        status = self.lib.XGetWindowProperty(self.display, window, self.atom(name),
                                            0, 4096, 0, 0, C.byref(actual), C.byref(fmt),
                                            C.byref(count), C.byref(remaining), C.byref(data))
        try:
            if status or not data.value or remaining.value:
                return []
            if fmt.value == 32:
                # Xlib expands 32-bit protocol values into native unsigned longs.
                return list(C.cast(data, C.POINTER(C.c_ulong))[:count.value])
            if fmt.value == 8:
                return C.string_at(data, count.value).decode("utf-8", errors="replace")
            return []
        finally:
            if data.value:
                self.lib.XFree(data)

    def children(self, window):
        root, parent, count = C.c_ulong(), C.c_ulong(), C.c_uint()
        data = C.POINTER(C.c_ulong)()
        ok = self.lib.XQueryTree(self.display, window, C.byref(root), C.byref(parent),
                                C.byref(data), C.byref(count))
        try:
            return list(data[:count.value]) if ok and data else []
        finally:
            if data:
                self.lib.XFree(data)

    def find_window(self, pid):
        windows = set(self.prop(self.root, "_NET_CLIENT_LIST"))
        # Bare X sessions have no EWMH client list; also inspect WM frame children.
        pending = [(self.root, 0)]
        visited = set()
        while pending:
            window, depth = pending.pop()
            if window in visited:
                continue
            visited.add(window)
            if len(visited) > 4096:
                raise RuntimeError("Too many X11 windows to select PES safely")
            windows.add(window)
            if depth < 3:
                pending.extend((child, depth + 1) for child in self.children(window))
        matches = [window for window in windows if self.prop(window, "_NET_WM_PID") == [pid]]
        if len(matches) != 1:
            raise RuntimeError("Expected one X11 window for PES PID %d; found %d. "
                               "No other window was changed." % (pid, len(matches)))
        return matches[0]

    def geometry(self, window):
        root, child = C.c_ulong(), C.c_ulong()
        x, y = C.c_int(), C.c_int()
        width, height, border, depth = (C.c_uint() for _ in range(4))
        if not self.lib.XGetGeometry(self.display, window, C.byref(root), C.byref(x),
                                    C.byref(y), C.byref(width), C.byref(height),
                                    C.byref(border), C.byref(depth)):
            raise RuntimeError("PES window disappeared")
        self.lib.XTranslateCoordinates(self.display, window, self.root, 0, 0,
                                       C.byref(x), C.byref(y), C.byref(child))
        return {"x": x.value, "y": y.value, "width": width.value, "height": height.value}

    def state(self, window):
        focus, revert = C.c_ulong(), C.c_int()
        self.lib.XGetInputFocus(self.display, C.byref(focus), C.byref(revert))
        return {"window": hex(window), "title": self.prop(window, "_NET_WM_NAME") or
                self.prop(window, "WM_NAME"), "geometry": self.geometry(window),
                "desktop": self.prop(window, "_NET_WM_DESKTOP"),
                "current_desktop": self.prop(self.root, "_NET_CURRENT_DESKTOP"),
                "active_window": self.prop(self.root, "_NET_ACTIVE_WINDOW"),
                "input_focus": focus.value}

    def message(self, window, name, values):
        event = Event()
        event.client.type, event.client.send_event = 33, 1
        event.client.display, event.client.window = self.display, window
        event.client.message_type, event.client.format = self.atom(name), 32
        for index, value in enumerate(values):
            event.client.data.l[index] = value
        if not self.lib.XSendEvent(self.display, self.root, 0, (1 << 20) | (1 << 19),
                                   C.byref(event)):
            raise RuntimeError("Window manager rejected the activation request")

    def activate(self, window, pid):
        if self.prop(window, "_NET_WM_PID") != [pid]:
            raise RuntimeError("PES window identity changed; no action taken")
        self.errors.clear()
        managed = bool(self.prop(self.root, "_NET_SUPPORTING_WM_CHECK"))
        current = self.prop(self.root, "_NET_CURRENT_DESKTOP")
        if managed and current:
            self.message(window, "_NET_WM_DESKTOP", [current[0], 2])
        self.lib.XMapRaised(self.display, window)
        if managed:
            self.message(window, "_NET_ACTIVE_WINDOW", [2, 0, 0])
        else:
            # There is no window manager to honor an EWMH activation request.
            self.lib.XSync(self.display, 0)
            self.lib.XSetInputFocus(self.display, window, 1, 0)
        self.lib.XSync(self.display, 0)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = self.state(window)
            if self.errors:
                raise RuntimeError(self.errors[-1])
            focus = state["input_focus"]
            if (focus == window or focus in self.children(window)) and (
                    not managed or state["active_window"] == [window]):
                return state
            time.sleep(0.05)
        raise RuntimeError("PES window was found but focus was not granted: " + json.dumps(state))


def process_display(identity):
    proc = Path("/proc") / str(identity["pid"])
    values = dict(item.split(b"=", 1) for item in (proc / "environ").read_bytes().split(b"\0")
                  if b"=" in item)
    display = os.fsdecode(values.get(b"DISPLAY", b""))
    if not re.fullmatch(r"(?:unix)?:\d+(?:\.\d+)?", display):
        raise RuntimeError("PES has no local X11 DISPLAY; no window action taken")
    # Only this short-lived Python child uses the emulator's X authorization path.
    if values.get(b"XAUTHORITY"):
        os.environ["XAUTHORITY"] = os.fsdecode(values[b"XAUTHORITY"])
    else:
        os.environ.pop("XAUTHORITY", None)
    return display


def run():
    if os.getuid() == 0:
        raise RuntimeError("Run this as your normal desktop user, without sudo")
    identity = trace.find_process()
    display = process_display(identity)
    print("PES_WINDOW_PID=%d DISPLAY=%s" % (identity["pid"], display), flush=True)
    x = X11(display)
    try:
        window = x.find_window(identity["pid"])
        print("PES_WINDOW_BEFORE=" + json.dumps(x.state(window)), flush=True)
        current = (Path("/proc") / str(identity["pid"]) / "stat").read_text().rsplit(")", 1)[1].split()[19]
        if current != identity["start_ticks"]:
            raise RuntimeError("PES process identity changed; no window action taken")
        state = x.activate(window, identity["pid"])
        print("PES_WINDOW_AFTER=" + json.dumps(state), flush=True)
        print("PES_WINDOW_FOCUSED=YES. Check the TV now. This verifies focus, not game rendering.")
    finally:
        x.close()


if __name__ == "__main__":
    try:
        run()
    except (Exception, KeyboardInterrupt) as exc:
        print("PES_WINDOW_ACTION_FAILED=" + str(exc), file=sys.stderr)
        sys.exit(1)
    finally:
        print("Returning to your existing SSH prompt.", flush=True)
