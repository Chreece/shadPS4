#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Launch the current baseline with automatic native-image and frame-API capture."""

import fcntl
import os
from pathlib import Path
import sys

import pes_current_baseline as baseline
import run_pes_startup_test as startup
import validate_video_diagnostic as validate


def run_validated(home):
    startup.require_idle()
    baseline.verify_installed(home)
    validate.launch(profile="frames")
    baseline.verify_installed(home)
    result = startup.run(home, profile="frames", screenshots=True)
    if (result.get("errors") or not result.get("frames_capture_passed") or
            not result.get("screenshots_complete") or not result.get("settings_unchanged") or
            not result.get("launcher_unchanged")):
        raise RuntimeError("Image capture failed; upload the printed archive")
    print("PES_IMAGE_CAPTURE=PASS (images collected; PES fix not established)", flush=True)


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
        print("PES_IMAGE_CAPTURE=FAIL: " + (str(error) or "Interrupted"), flush=True)
        sys.exit(1)
    finally:
        print("Returning to your existing SSH prompt.", flush=True)
