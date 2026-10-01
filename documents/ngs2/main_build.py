#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Recover the failed main deployment, then build combined main locally."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

import local_build

REVISION = '038bb3d83e751e50328abb98f04fcb2c3ee7897e'
FAILED = '2abd0fb0f807e84713517e6a25e982043897353f'
WORKING = 'f1c1c79073b811ada98b963d6a87c066b66e2bc8'


def recover_failed_main(home):
    deploy = local_build.deploy
    wrapper = home / '.local/bin/shadps4-esde'
    if wrapper.is_symlink():
        raise RuntimeError('Unexpected launcher symlink.')
    current = wrapper.read_bytes()
    if ('# NGS2 isolated core selection: ' + FAILED).encode() not in current:
        return
    deploy.no_running_core()
    states = []
    for path in sorted((home / '.local/state/shadps4-ngs2').glob('*/deployment.json')):
        try:
            states.append((path, json.loads(path.read_text())))
        except (OSError, ValueError):
            continue
    matches = [(p, s) for p, s in states if s.get('commit') == FAILED
               and s.get('wrapper') == str(wrapper)
               and s.get('installed_wrapper_sha256') == deploy.digest(current)]
    if len(matches) != 1:
        raise RuntimeError('No unique verified deployment record for failed main; launcher preserved.')
    state_file, state = matches[0]
    backup = state_file.parent / 'shadps4-esde.before'
    if backup.is_symlink():
        raise RuntimeError('Unexpected launcher backup symlink.')
    original = backup.read_bytes()
    if deploy.digest(original) != state.get('original_wrapper_sha256'):
        raise RuntimeError('Launcher backup checksum mismatch.')
    marker = '# NGS2 isolated core selection: ' + WORKING
    if marker.encode() not in original:
        raise RuntimeError('Backup does not select the known working audio build; launcher preserved.')
    current_guard, _ = deploy.split_session_guard(current.decode(), home)
    original_guard, _ = deploy.split_session_guard(original.decode(), home)
    if current_guard != original_guard:
        raise RuntimeError('Rollback would change the single-instance guard; launcher preserved.')
    binary = home / 'Applications/shadps4/releases/ngs2-f1c1c790/shadps4'
    local_build.selection(original, binary)  # Validate the complete known dispatcher.
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError('The retained working executable is unavailable.')
    expected = [s for _, s in states if s.get('commit') == WORKING
                and s.get('binary') == str(binary) and s.get('wrapper') == str(wrapper)]
    if not any(s.get('binary_sha256') == deploy.digest(binary.read_bytes()) for s in expected):
        raise RuntimeError('The working executable does not match its deployment record.')
    evidence = Path(tempfile.mkdtemp(prefix='ngs2-crash-2abd0fb0-', dir=home))
    data_root = Path(os.environ.get('XDG_DATA_HOME') or home / '.local/share')
    for path in (home / 'ngs2-diagnostic-2abd0fb0.log',
                 data_root / 'shadPS4/log/shad_log.txt'):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as source:
                source.seek(max(0, path.stat().st_size - 2 * 1024 * 1024))
                (evidence / path.name).write_bytes(source.read(2 * 1024 * 1024))
    (evidence / 'deployment.json').write_bytes(state_file.read_bytes())
    (evidence / 'launcher.failed').write_bytes(current)
    print('CRASH_EVIDENCE=' + str(evidence), flush=True)
    deploy.restore(state_file)
    print('RECOVERY=PASS: retained f1c1c790 selected before rebuilding.', flush=True)


if __name__ == '__main__':
    local_build.REVISION = REVISION
    local_build.SOURCE_BRANCH = 'main'
    local_build.GRAPHICS_TEST = True
    local_build.STARTUP_TEST = True
    try:
        if os.geteuid() == 0:
            raise RuntimeError('Run as your normal user, without sudo.')
        recover_failed_main(Path.home())
        local_build.main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError,
            zipfile.BadZipFile) as error:
        print('MAIN_LOCAL_RESULT=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
