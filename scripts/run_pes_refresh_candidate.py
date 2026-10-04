#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the image-refresh correction locally and capture a fresh PES startup."""

import fcntl
import json
import os
from pathlib import Path
import sys

import install_local_default as installer
import pes_current_baseline as baseline
import run_pes_packet_test as packets

REVISION = '0df5448ec371a465c4e841c16ca73aea7658fde1'
BRANCH = 'diag/pes-image-refresh'
FIX_REVISION = 'c293dbcadbcb545676947500936df69fcda7a8bf'


def run(home, manifest):
    manifest = dict(manifest, candidate_fix={
        'revision': FIX_REVISION, 'source_branch': 'fix/preserve-gpu-image-mips',
        'change': 'Preserve GPU-rendered mips when CPU backing bytes are unchanged',
        'pes_runtime_result': 'unverified',
        'retained_tiling_fix': '1522b405a109f9f5f1db3c0852b62c1e27c2539b',
    })
    baseline.prepare(home, REVISION, BRANCH, manifest, installer.build)
    packets.run(home)


def main():
    if os.geteuid() == 0 or sys.argv[1:]:
        raise RuntimeError('Run as chreece without sudo or arguments')
    home = Path.home()
    manifest = json.loads(Path(__file__).with_name('LOCAL_TEST_BASELINE.json').read_text())
    deploy = home / '.local/state/shadps4-ngs2'
    deploy.mkdir(parents=True, exist_ok=True)
    cache = home / '.cache'
    cache.mkdir(exist_ok=True)
    with (deploy / 'deploy.lock').open('a') as deploy_lock, \
            (cache / 'shadps4-video-trace.lock').open('a') as trace_lock:
        fcntl.flock(deploy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(trace_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(home, manifest)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print('PES_REFRESH_TEST=FAIL: ' + (str(error) or 'Interrupted'), flush=True)
        sys.exit(1)
    finally:
        print('Returning to your existing SSH prompt.', flush=True)
