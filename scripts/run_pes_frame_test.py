#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate frame hooks in local Docker before using them on the installed PES build."""

import fcntl
import os
from pathlib import Path
import sys

import run_pes_startup_test as startup
import validate_video_diagnostic as validate
import pes_current_baseline as baseline


def run_validated(home):
    baseline.verify_installed(home)
    # Any build/test failure raises before the game launcher or attach can run.
    validate.launch(profile="frames")
    baseline.verify_installed(home)
    result = startup.run(home, profile="frames", reuse_existing=True)
    if (result.get("errors") or not result.get("frames_capture_passed") or
            not result.get("settings_unchanged") or not result.get("launcher_unchanged")):
        raise RuntimeError("Frame capture or preservation check failed; upload the printed archive")
    print("PES_FRAME_CAPTURE=PASS (diagnostic completed; game fix not established)", flush=True)


def main():
    if os.geteuid() == 0 or sys.argv[1:]:
        raise RuntimeError("Run as chreece without sudo or arguments")
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    with (cache / "shadps4-video-trace.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_validated(Path.home())


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print("PES_FRAME_CAPTURE=FAIL: " + (str(error) or "Interrupted"), flush=True)
        sys.exit(1)
    finally:
        print("Returning to your existing SSH prompt.", flush=True)
