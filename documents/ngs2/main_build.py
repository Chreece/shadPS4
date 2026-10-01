#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Recover the failed main deployment, then build combined main locally."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

import local_build

REVISION = 'f96686b8b5f0d813723d29e7de581efc1d2fb0a8'
FAILED = '2abd0fb0f807e84713517e6a25e982043897353f'
WORKING = 'f1c1c79073b811ada98b963d6a87c066b66e2bc8'
VALIDATION_HELPERS = {
    '10ff9e19a7d94340aaedd1e333f1a11abeeb9e75':
        '4b5ffcb3389ecabcda7e6a8ab68cf85fd37194376e21d68fc85ecfd4573a4ebd',
    '77c6bd3a1f116c605370e765464423a668f25ba1':
        '38deba4f5f8ead4149c5b50048d0227c79fe0b84ec9bf119a8fded72066b0e5e',
}


def restore_validation_session(home):
    profile = home / '.local/share/shadPS4/custom_configs/CUSA36843.json'
    if profile.is_symlink():
        raise RuntimeError('Unexpected per-game configuration symlink.')
    if not profile.exists():
        return
    current = profile.read_bytes()
    if not json.loads(current).get('Vulkan', {}).get('vkvalidation_enabled', False):
        return
    matches = {}
    for record in sorted((home / '.local/state/shadps4-graphics-readbacks').glob('test-*/state.json')):
        if record.is_symlink():
            continue
        try:
            state = json.loads(record.read_text())
        except (OSError, ValueError):
            continue
        if (state.get('purpose') != 'validation' or state.get('profile') != str(profile) or
                state.get('installed_sha256') != local_build.deploy.digest(current)):
            continue
        helper = record.parent / 'readbacks_test.py'
        expected = VALIDATION_HELPERS.get(state.get('revision'))
        try:
            if (not expected or helper.is_symlink() or
                    local_build.deploy.digest(helper.read_bytes()) != expected):
                continue
            original = base64.b64decode(state['original_base64'], validate=True)
            if local_build.deploy.digest(original) != state['original_sha256']:
                continue
            config = json.loads(original) if state['existed'] else {}
            if config.get('Vulkan', {}).get('vkvalidation_enabled', False):
                continue
            key = (state['existed'], original, state['original_mode'])
            matches[key] = (helper, record)
        except (OSError, ValueError, KeyError):
            continue
    if len(matches) != 1:
        raise RuntimeError('Validation is enabled without one matching test backup. Run the '
                           'original RESTORE command before this normal-speed visual test.')
    (existed, original, _), (helper, record) = next(iter(matches.items()))
    local_build.deploy.no_running_core()
    subprocess.run([sys.executable, str(helper), '--restore', str(record)], check=True)
    if profile.exists() != existed or (existed and profile.read_bytes() != original):
        raise RuntimeError('Validation restore did not produce the verified original configuration.')
    print('VALIDATION=RESTORED: temporary core/synchronization checks removed.', flush=True)


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
    local_build.GRAPHICS_TRACE = True
    local_build.OCCLUSION_TEST = True
    local_build.IMAGE_TRANSFER_TEST = True
    local_build.STARTUP_TEST = True
    local_build.LATE_AUDIO_TRACE = True
    try:
        if os.geteuid() == 0:
            raise RuntimeError('Run as your normal user, without sudo.')
        recover_failed_main(Path.home())
        restore_validation_session(Path.home())
        local_build.main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError,
            zipfile.BadZipFile) as error:
        print('MAIN_LOCAL_RESULT=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
