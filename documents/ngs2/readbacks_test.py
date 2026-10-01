#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reversible RDR precise-buffer-readback comparison on the existing test core."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile

REVISION = '10ff9e19a7d94340aaedd1e333f1a11abeeb9e75'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def no_running_core():
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            target = (process / 'exe').readlink()
        except OSError:
            continue
        if target.name.lower().removesuffix(' (deleted)') in ('shadps4', 'shadps4.exe'):
            raise RuntimeError(f'Close shadPS4 normally first (PID {process.name}), then rerun.')


def profile_path(home):
    root = home / '.local/share/shadPS4'
    profile = root / 'custom_configs/CUSA36843.json'
    for path in (root, profile.parent, profile):
        if path.is_symlink():
            raise RuntimeError('Unexpected configuration symlink: ' + str(path))
    return profile


def atomic_write(path, data, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(home):
    no_running_core()
    profile = profile_path(home)
    root = profile.parent.parent
    data_home = os.environ.get('XDG_DATA_HOME')
    if data_home and Path(data_home) / 'shadPS4' != root:
        raise RuntimeError('XDG_DATA_HOME differs from the captured installation; nothing changed.')
    wrapper = home / '.local/bin/shadps4-esde'
    if '# NGS2 isolated core selection: ' + REVISION not in wrapper.read_text():
        raise RuntimeError('The selected test build is not 10ff9e19; nothing changed.')
    global_config = json.loads((root / 'config.json').read_text())
    existed = profile.exists()
    original = profile.read_bytes() if existed else b''
    config = json.loads(original) if existed else {}
    if not isinstance(config, dict) or not isinstance(config.get('GPU', {}), dict):
        raise RuntimeError('Unexpected per-game configuration format; nothing changed.')
    previous = config.get('GPU', {}).get('readbacks_mode',
                                        global_config.get('GPU', {}).get('readbacks_mode', 0))
    if previous == 2:
        print('READBACK_TEST_RESULT=UNCHANGED: precise readbacks are already selected.')
        return
    config.setdefault('GPU', {})['readbacks_mode'] = 2
    installed = (json.dumps(config, indent=2) + '\n').encode()
    mode = stat.S_IMODE(profile.stat().st_mode) if existed else 0o600
    parent = home / '.local/state/shadps4-graphics-readbacks'
    parent.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='test-', dir=parent))
    helper = backup / 'readbacks_test.py'
    atomic_write(helper, Path(__file__).read_bytes())
    record = backup / 'state.json'
    state = {'revision': REVISION, 'profile': str(profile), 'existed': existed,
             'original_base64': base64.b64encode(original).decode(),
             'original_sha256': digest(original), 'original_mode': mode,
             'installed_sha256': digest(installed), 'previous_effective_mode': previous}
    atomic_write(record, (json.dumps(state, indent=2) + '\n').encode())
    profile.parent.mkdir(parents=True, exist_ok=True)
    no_running_core()
    profile_path(home)
    if profile.exists() != existed or (existed and profile.read_bytes() != original):
        raise RuntimeError('Configuration changed during preparation; nothing was replaced.')
    atomic_write(profile, installed, mode)
    print('READBACK_TEST_RESULT=PASS: CUSA36843 GPU.readbacks_mode = 2 (Precise).')
    print('PREVIOUS_EFFECTIVE_MODE=' + str(previous))
    print('CURRENT_BUILD=' + REVISION)
    print('RESTORE=python3 ' + shlex.quote(str(helper)) + ' --restore ' + shlex.quote(str(record)))
    print('Start the same NGS2 trace entry and compare character details and moving scenery.')
    print('Global settings, audio, saves, launcher and executable were not modified.')


def restore(home, record):
    no_running_core()
    state = json.loads(record.read_text())
    profile = profile_path(home)
    if state['profile'] != str(profile) or state['revision'] != REVISION:
        raise RuntimeError('Restore record does not match this test installation.')
    original = base64.b64decode(state['original_base64'], validate=True)
    if digest(original) != state['original_sha256']:
        raise RuntimeError('Backup checksum mismatch; configuration preserved.')
    if ((state['existed'] and profile.exists() and profile.read_bytes() == original) or
            (not state['existed'] and not profile.exists())):
        print('READBACK_RESTORE_RESULT=PASS: already restored.')
        return
    if not profile.exists() or digest(profile.read_bytes()) != state['installed_sha256']:
        raise RuntimeError('Per-game settings changed after this test; preserved for review.')
    if state['existed']:
        atomic_write(profile, original, state['original_mode'])
    else:
        profile.unlink()
    print('READBACK_RESTORE_RESULT=PASS: original per-game configuration restored exactly.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', type=Path)
    args = parser.parse_args()
    try:
        if os.geteuid() == 0:
            raise RuntimeError('Run as your normal user, without sudo.')
        if args.restore:
            restore(Path.home(), args.restore)
        else:
            apply(Path.home())
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print('READBACK_TEST_RESULT=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
