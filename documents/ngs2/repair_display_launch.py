#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Repair the known shadPS4 guard's display environment and reuse the installed test core."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile

import deploy_test as deploy
import display_session

WORKING = '6e00d2ccadfa4ec1f5a7bbd46ad8a233857bbbfa'
TARGET = '286d0cca483ce80f9d4a4fe98d4620b6b003e0ca'
GUARD_SHA256 = '32d76d15f4a04f25475a40cce1d1b7614508aea54e532ff094f39467a1d19531'
NEEDLE = b'        environment = dict(os.environ, SHADPS4_GUARD_PARENT_PID=str(os.getpid()))\n'
INSERT = (b'        from display_session import launch_environment\n'
          b'        environment = launch_environment(environment)\n')


def read_regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Expected a regular user-owned file: ' + str(path))
    return path.read_bytes(), stat.S_IMODE(info.st_mode)


def patched_guard(current):
    original = current
    if current.count(NEEDLE + INSERT) == 1:
        original = current.replace(NEEDLE + INSERT, NEEDLE, 1)
    if deploy.digest(original) != GUARD_SHA256 or original.count(NEEDLE) != 1:
        raise RuntimeError('The session guard differs from the verified version; preserved.')
    updated = original.replace(NEEDLE, NEEDLE + INSERT, 1)
    compile(updated, 'guard.py', 'exec')
    return updated


def selection(home, current):
    wrapper = home / '.local/bin/shadps4-esde'
    guard, _ = deploy.split_session_guard(current.decode(), home)
    if not guard:
        raise RuntimeError('Expected the existing single-instance guard; launcher preserved.')
    binary = home / 'Applications/shadps4/releases' / ('ngs2-' + TARGET[:8]) / 'shadps4'
    payload, mode = read_regular(binary)
    if not mode & 0o111:
        raise RuntimeError('Retained test binary is not executable.')
    records = []
    binary_hash = deploy.digest(payload)
    for path in (home / '.local/state/shadps4-ngs2').glob('*/deployment.json'):
        try:
            content, _ = read_regular(path)
            state = json.loads(content)
            if (state.get('commit') == TARGET and state.get('wrapper') == str(wrapper) and
                    state.get('binary') == str(binary) and
                    state.get('binary_sha256') == binary_hash):
                records.append(state)
        except (OSError, ValueError, RuntimeError):
            continue
    if not records:
        raise RuntimeError('Cannot verify the retained 286d0cca executable; launcher preserved.')
    if any(deploy.digest(current) == s.get('installed_wrapper_sha256') for s in records):
        return current
    old_marker = ('# NGS2 isolated core selection: ' + WORKING).encode()
    old_runner = str(home / 'Applications/shadps4/releases/ngs2-6e00d2cc/run_diagnostic.py').encode()
    if current.count(old_marker) != 1 or current.count(old_runner) != 1:
        raise RuntimeError('Expected the retained 6e00d2cc launcher; current selection preserved.')
    candidate = current.replace(old_marker, ('# NGS2 isolated core selection: ' + TARGET).encode(), 1)
    candidate = candidate.replace(old_runner, str(binary.parent / 'run_diagnostic.py').encode(), 1)
    if not any(s.get('original_wrapper_sha256') == deploy.digest(current) and
               s.get('installed_wrapper_sha256') == deploy.digest(candidate) for s in records):
        raise RuntimeError('Launcher does not match the recorded test deployment; preserved.')
    subprocess.run(['bash', '-n'], input=candidate, check=True, timeout=5)
    return candidate


def install(home):
    deploy.no_running_core()
    wrapper = home / '.local/bin/shadps4-esde'
    helper = home / '.local/lib/shadps4-session-guard/guard.py'
    sidecar = helper.with_name('display_session.py')
    current, wrapper_mode = read_regular(wrapper)
    original_guard, guard_mode = read_regular(helper)
    new_guard = patched_guard(original_guard)
    candidate = selection(home, current)
    module = Path(__file__).with_name('display_session.py').read_bytes()
    compile(module, str(sidecar), 'exec')
    sidecar_before = None
    sidecar_mode = 0o700
    if sidecar.exists() or sidecar.is_symlink():
        sidecar_before, sidecar_mode = read_regular(sidecar)
        if sidecar_before != module:
            raise RuntimeError('An unrelated display helper already exists; preserved.')
    # The supplied host report identifies the Sunshine/headless X session as :0.
    resolved, source = display_session.resolve_environment(dict(os.environ, DISPLAY=':0'))
    print('DISPLAY_CHECK=PASS display=' + resolved['DISPLAY'] + ' source=' + source, flush=True)
    if original_guard == new_guard and current == candidate and sidecar_before == module:
        print('DISPLAY_REPAIR_RESULT=ALREADY_INSTALLED; core=286d0cca; no rebuild.')
        return
    root = home / '.local/state/shadps4-display-launcher'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError('Unexpected display repair state directory.')
    backup = Path(tempfile.mkdtemp(prefix='repair-', dir=root))
    originals = [(wrapper, current, wrapper_mode, candidate),
                 (helper, original_guard, guard_mode, new_guard),
                 (sidecar, sidecar_before, sidecar_mode, module)]
    entries = []
    for index, (path, before, mode, after) in enumerate(originals):
        if before is not None:
            (backup / f'before-{index}').write_bytes(before)
        entries.append({'path': str(path), 'mode': mode, 'existed': before is not None,
                        'before_sha256': deploy.digest(before) if before is not None else None,
                        'after_sha256': deploy.digest(after)})
    record = backup / 'state.json'
    record.write_text(json.dumps({'entries': entries}, indent=2))
    for name in ('repair_display_launch.py', 'deploy_test.py', 'display_session.py'):
        (backup / name).write_bytes(Path(__file__).with_name(name).read_bytes())
    restore_command = ('python3 ' + shlex.quote(str(backup / 'repair_display_launch.py')) +
                       ' --restore ' + shlex.quote(str(record)))
    print('RESTORE=' + restore_command, flush=True)
    deploy.no_running_core()
    if read_regular(wrapper)[0] != current or read_regular(helper)[0] != original_guard:
        raise RuntimeError('Launcher or guard changed during checks; active files preserved.')
    applied = []
    try:
        for index in (2, 1, 0):
            path, before, mode, after = originals[index]
            if (path.is_symlink() or path.exists() != (before is not None) or
                    (path.exists() and path.read_bytes() != before)):
                raise RuntimeError('A repair target changed; refusing to replace it.')
            deploy.atomic_write(path, after, mode)
            applied.append(index)
    except Exception:
        for index in reversed(applied):
            path, before, mode, after = originals[index]
            if not path.is_symlink() and path.read_bytes() == after:
                if before is None:
                    path.unlink()
                else:
                    deploy.atomic_write(path, before, mode)
        raise
    print('DISPLAY_REPAIR_RESULT=PASS; core=286d0cca; existing binary reused, no rebuild.')
    print('Launch the existing NGS2 test entry once from ES-DE while Moonlight is connected.')


def restore(home, record):
    root = home / '.local/state/shadps4-display-launcher'
    if record.is_symlink() or record.parent.parent != root or record.name != 'state.json':
        raise RuntimeError('Unexpected repair backup path.')
    state = json.loads(read_regular(record)[0])
    expected = [home / '.local/bin/shadps4-esde',
                home / '.local/lib/shadps4-session-guard/guard.py',
                home / '.local/lib/shadps4-session-guard/display_session.py']
    entries = state['entries']
    if [Path(entry['path']) for entry in entries] != expected:
        raise RuntimeError('Unexpected restore targets.')
    originals = []
    for index, entry in enumerate(entries):
        current, _ = read_regular(expected[index])
        if deploy.digest(current) != entry['after_sha256']:
            raise RuntimeError('A file changed after repair; restore stopped to preserve it.')
        before = read_regular(record.parent / f'before-{index}')[0] if entry['existed'] else None
        if before is not None and deploy.digest(before) != entry['before_sha256']:
            raise RuntimeError('Repair backup checksum mismatch.')
        mode = entry['mode']
        if not isinstance(mode, int) or mode & ~0o777 or not mode & 0o111:
            raise RuntimeError('Invalid repair backup mode.')
        originals.append((expected[index], before, mode))
    deploy.no_running_core()
    for path, before, mode in originals:
        if before is None:
            path.unlink()
        else:
            deploy.atomic_write(path, before, mode)
    print('DISPLAY_RESTORE_RESULT=PASS; previous launcher and guard restored.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0 or sys.platform != 'linux':
        raise RuntimeError('Run as your normal Linux desktop user, without sudo.')
    home = Path.home()
    locks = [home / '.local/state/shadps4-ngs2/deploy.lock',
             home / '.local/state/shadps4-session-guard/session.lock']
    with contextlib.ExitStack() as stack:
        for lock in locks:
            fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            handle = stack.enter_context(os.fdopen(fd, 'r+'))
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError('Unexpected launcher lock owner/type.')
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.restore:
            restore(home, args.restore)
        else:
            install(home)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('DISPLAY_REPAIR_RESULT=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
