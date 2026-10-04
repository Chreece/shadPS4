#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real X11 activation tests, run only on a disposable Xvfb display."""

import ctypes as C
import faulthandler
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

from show_pes_window import X11


@unittest.skipUnless(os.environ.get("PES_WINDOW_X11_TEST") == "1", "requires isolated Xvfb")
class BareSession(unittest.TestCase):
    def setUp(self):
        self.x = X11(os.environ["DISPLAY"])
        self.windows = []
        self.addCleanup(self.cleanup_windows)
        D, W, I, U = C.c_void_p, C.c_ulong, C.c_int, C.c_uint
        for name, result, args in (
            ("XCreateSimpleWindow", W, [D, W, I, I, U, U, U, W, W]),
            ("XChangeProperty", I, [D, W, W, W, I, I, D, I]),
            ("XStoreName", I, [D, W, C.c_char_p]),
            ("XDestroyWindow", I, [D, W]), ("XIconifyWindow", I, [D, W, I]),
        ):
            function = getattr(self.x.lib, name)
            function.restype, function.argtypes = result, args
        self.target = self.create(12345, "PES fixture")
        self.decoy = self.create(12346, "Unrelated window")
        self.x.lib.XSync(self.x.display, 0)

    def cleanup_windows(self):
        for window in self.windows:
            self.x.lib.XDestroyWindow(self.x.display, window)
        self.x.close()

    def create(self, pid, name):
        x = self.x
        window = x.lib.XCreateSimpleWindow(x.display, x.root, 20, 30, 320, 240, 0, 0, 0)
        self.windows.append(window)
        data = (C.c_ulong * 1)(pid)
        x.lib.XChangeProperty(x.display, window, x.atom("_NET_WM_PID"), x.atom("CARDINAL"),
                              32, 0, data, 1)
        x.lib.XStoreName(x.display, window, name.encode())
        x.lib.XMapRaised(x.display, window)
        x.lib.XSync(x.display, 0)
        return window

    def until(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.05)
        self.fail("X11 state did not reach the expected condition")

    def test_bare_session_selects_and_focuses_exact_pid(self):
        self.assertEqual(self.x.find_window(12345), self.target)
        state = self.x.activate(self.target, 12345)
        self.assertEqual(state["input_focus"], self.target)
        self.assertEqual(state["title"], "PES fixture")
        self.assertEqual(state["geometry"]["width"], 320)
        self.assertEqual(self.x.find_window(12346), self.decoy)

    def test_ambiguous_pid_does_not_change_focus(self):
        duplicate = self.create(12345, "Duplicate PES fixture")
        if self.x.prop(self.x.root, "_NET_SUPPORTING_WM_CHECK"):
            # Let the WM handle the new window before measuring the read-only lookup.
            self.until(lambda: duplicate in self.x.prop(self.x.root, "_NET_CLIENT_LIST"))
        self.x.activate(self.decoy, 12346)
        before = self.x.state(self.decoy)["input_focus"]
        with self.assertRaisesRegex(RuntimeError, "found 2"):
            self.x.find_window(12345)
        self.assertEqual(self.x.state(self.decoy)["input_focus"], before)

    def test_wrong_pid_does_not_change_focus(self):
        self.x.activate(self.decoy, 12346)
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            self.x.activate(self.target, 12346)
        self.assertEqual(self.x.state(self.decoy)["input_focus"], self.decoy)


@unittest.skipUnless(os.environ.get("PES_WINDOW_X11_TEST") == "1", "requires isolated Xvfb")
class ManagedSession(BareSession):
    @classmethod
    def setUpClass(cls):
        cls.ready = Path("/tmp/pes-window-wm-ready")
        cls.ready.unlink(missing_ok=True)
        cls.wm = subprocess.Popen(["openbox", "--sm-disable", "--startup",
                                   "touch /tmp/pes-window-wm-ready"], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        x = X11(os.environ["DISPLAY"])
        try:
            deadline = time.monotonic() + 5
            while not cls.ready.exists() or not x.prop(x.root, "_NET_SUPPORTING_WM_CHECK"):
                if time.monotonic() > deadline or cls.wm.poll() is not None:
                    cls.wm.terminate()
                    cls.wm.wait(timeout=5)
                    raise RuntimeError("Test window manager did not start")
                time.sleep(0.05)
        finally:
            x.close()

    @classmethod
    def tearDownClass(cls):
        cls.wm.terminate()
        cls.wm.wait(timeout=5)

    def setUp(self):
        super().setUp()
        self.until(lambda: self.target in self.x.prop(self.x.root, "_NET_CLIENT_LIST") and
                   self.decoy in self.x.prop(self.x.root, "_NET_CLIENT_LIST"))

    def test_minimized_window_restored(self):
        self.x.activate(self.target, 12345)
        self.x.lib.XIconifyWindow(self.x.display, self.target, 0)
        self.x.lib.XSync(self.x.display, 0)
        hidden = self.x.atom("_NET_WM_STATE_HIDDEN")
        self.until(lambda: hidden in self.x.prop(self.target, "_NET_WM_STATE"))
        self.x.activate(self.decoy, 12346)
        state = self.x.activate(self.target, 12345)
        self.assertEqual(state["input_focus"], self.target)
        self.assertNotIn(hidden, self.x.prop(self.target, "_NET_WM_STATE"))
        self.assertEqual(self.x.find_window(12346), self.decoy)

    def test_other_workspace_restored_without_switching_desktop(self):
        current = self.x.prop(self.x.root, "_NET_CURRENT_DESKTOP")
        self.assertGreater(self.x.prop(self.x.root, "_NET_NUMBER_OF_DESKTOPS")[0], 1)
        other = 1 if current[0] == 0 else 0
        self.x.message(self.target, "_NET_WM_DESKTOP", [other, 2])
        self.x.lib.XSync(self.x.display, 0)
        self.until(lambda: self.x.prop(self.target, "_NET_WM_DESKTOP") == [other])
        self.x.activate(self.decoy, 12346)
        state = self.x.activate(self.target, 12345)
        self.assertEqual(state["desktop"], current)
        self.assertEqual(state["current_desktop"], current)
        self.assertEqual(self.x.prop(self.decoy, "_NET_WM_DESKTOP"), current)


if __name__ == "__main__":
    faulthandler.dump_traceback_later(20, exit=True)
    server = None
    try:
        if os.environ.get("PES_WINDOW_X11_TEST") == "1" and not os.environ.get("DISPLAY"):
            print("X11_SELFTEST=starting_isolated_display", flush=True)
            # Only used inside the disposable container: no host X socket or network.
            server = subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1024x768x24",
                                       "-nolisten", "tcp", "-noreset", "-ac", "-extension", "GLX"])
            deadline = time.monotonic() + 5
            while not Path("/tmp/.X11-unix/X99").exists():
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Isolated X display did not start")
                time.sleep(0.05)
            os.environ["DISPLAY"] = ":99"
        result = unittest.main(verbosity=2, exit=False)
        sys.exit(not result.result.wasSuccessful())
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=3)
