#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate Vulkan operations during a fresh PES run of the current Docker-built core."""

import fcntl
import os
from pathlib import Path
import sys

import pes_current_baseline as baseline
import run_pes_startup_test as startup


def run(home):
    startup.require_idle()
    baseline.verify_installed(home)
    result = startup.run(home, profile='frames', screenshots=True, graphics=True, validation=True)
    if (result.get('errors') or not result.get('frames_capture_passed') or
            not result.get('screenshots_complete') or not result.get('settings_unchanged') or
            not result.get('launcher_unchanged') or not result.get('vulkan_preflight_passed') or
            not result.get('vulkan_layer_loaded') or
            not result.get('vulkan_validation', {}).get('synchronization_enabled_in_log')):
        raise RuntimeError('Vulkan capture incomplete; upload the printed archive')
    print('PES_VULKAN_CAPTURE=PASS; capture completed, game fix not established', flush=True)


def main():
    if os.geteuid() == 0 or sys.argv[1:]:
        raise RuntimeError('Run as chreece without sudo or arguments')
    home = Path.home()
    deploy = home / '.local/state/shadps4-ngs2'
    deploy.mkdir(parents=True, exist_ok=True)
    cache = home / '.cache'
    cache.mkdir(exist_ok=True)
    with (deploy / 'deploy.lock').open('a') as deploy_lock, \
            (cache / 'shadps4-video-trace.lock').open('a') as trace_lock:
        fcntl.flock(deploy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(trace_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(home)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print('PES_VULKAN_CAPTURE=FAIL: ' + (str(error) or 'Interrupted'), flush=True)
        sys.exit(1)
    finally:
        print('Returning to your existing SSH prompt.', flush=True)
