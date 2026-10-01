#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Keep ES-DE launches on one existing shadPS4 session, without stopping games."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time

MARKER = '# SHADPS4_SESSION_GUARD_V1'


def paths(home):
    return (home / '.local/bin/shadps4-esde',
            home / '.local/lib/shadps4-session-guard/guard.py',
            home / '.local/state/shadps4-session-guard')


def prefix(helper):
    return (MARKER + '\n'
            'if [[ "${SHADPS4_GUARD_PARENT_PID:-}" != "$PPID" ]]; then\n'
            f'    exec python3 {shlex.quote(str(helper))} run "$0" "$@"\n'
            'fi\n'
            'unset SHADPS4_GUARD_PARENT_PID\n'
            '# END SHADPS4_SESSION_GUARD_V1\n')


def identity(process):
    fields = (process / 'stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] in ('Z', 'X'):
        return None
    return int(process.name), int(fields[19])


def cores():
    found = []
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            executable = str((process / 'exe').readlink()).removesuffix(' (deleted)')
            if Path(executable).name.lower() not in ('shadps4', 'shadps4.exe'):
                continue
            key = identity(process)
            if key:
                found.append({'pid': key[0], 'started': key[1], 'executable': executable})
        except (OSError, ValueError, IndexError):
            continue
    return sorted(found, key=lambda item: (item['started'], item['pid']), reverse=True)


def alive(core):
    try:
        return identity(Path('/proc') / str(core['pid'])) == (core['pid'], core['started'])
    except (OSError, ValueError, IndexError):
        return False


def command(arguments):
    try:
        return subprocess.run(arguments, capture_output=True, text=True, timeout=3,
                              close_fds=True)
    except (OSError, subprocess.SubprocessError):
        return None


def focus_existing(found):
    # Prefer the active game's window when older duplicates already exist.
    chosen = found[0]
    if shutil.which('xdotool'):
        active = command(['xdotool', 'getactivewindow', 'getwindowpid'])
        if active and active.returncode == 0:
            chosen = next((core for core in found
                           if str(core['pid']) == active.stdout.strip()), chosen)
    if not alive(chosen):
        return chosen, False
    if shutil.which('wmctrl'):
        windows = command(['wmctrl', '-lp'])
        if windows and windows.returncode == 0:
            for line in windows.stdout.splitlines():
                fields = line.split(None, 4)
                if len(fields) >= 3 and fields[2] == str(chosen['pid']):
                    result = command(['wmctrl', '-ia', fields[0]])
                    if result and result.returncode == 0:
                        return chosen, True
    if shutil.which('xdotool'):
        windows = command(['xdotool', 'search', '--onlyvisible', '--pid', str(chosen['pid'])])
        if windows and windows.returncode == 0:
            for window in windows.stdout.splitlines():
                if window.isdigit():
                    result = command(['xdotool', 'windowactivate', window])
                    if result and result.returncode == 0:
                        return chosen, True
    return chosen, False


def say(message):
    try:
        print(message, flush=True)
    except BrokenPipeError:
        pass  # A disconnected frontend must not break session monitoring.


def reuse(found):
    chosen, focused = focus_existing(found)
    say(f"SHADPS4_REUSE_PID={chosen['pid']} FOCUS_REQUEST={'sent' if focused else 'unavailable'}")
    if len(found) > 1:
        say('Existing duplicate games were left running; no session was terminated.')
    while alive(chosen):
        time.sleep(0.5)
    return 0


def private_directory(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError(f'Expected a private user-owned directory: {directory}')


def run(home, wrapper, arguments):
    expected, helper, state = paths(home)
    if wrapper != expected or wrapper.is_symlink():
        raise RuntimeError('Unexpected ES-DE launcher path.')
    text = wrapper.read_text()
    if not text.partition('\n')[2].startswith(prefix(helper)):
        raise RuntimeError('Session guard prefix is missing or changed; refusing an unguarded launch.')
    private_directory(state)
    fd = os.open(state / 'session.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Unexpected session lock owner/type.')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another invocation may be starting the game. Never queue a new game
            # that could unexpectedly launch when the existing session ends.
            say('SHADPS4_DUPLICATE_BLOCKED=1')
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                found = cores()
                if found:
                    return reuse(found)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return 0
                except BlockingIOError:
                    time.sleep(0.2)
            say('The original launcher is still starting; no additional game was launched.')
            return 0
        found = cores()
        if found:
            return reuse(found)
        environment = dict(os.environ, SHADPS4_GUARD_PARENT_PID=str(os.getpid()))
        # Only this supervisor owns the lock: helpers/AppImage mounts must not
        # inherit it and block all future launches after the game has exited.
        child = subprocess.Popen(['bash', str(wrapper), *arguments], env=environment,
                                 close_fds=True)
        code = child.wait()
        # Some original launchers detach the emulator. Continue guarding its
        # lifetime even after that shell has returned.
        found = cores()
        if found:
            reuse(found)
        return code if code >= 0 else 128 - code


def atomic_write(path, content, mode):
    fd, name = tempfile.mkstemp(prefix=path.name + '.tmp-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        os.replace(name, path)
    finally:
        if os.path.lexists(name):
            os.unlink(name)


def install(home):
    wrapper, helper, state = paths(home)
    if wrapper.is_symlink() or not wrapper.is_file():
        raise RuntimeError('Expected the existing regular shadps4-esde launcher.')
    original = wrapper.read_bytes()
    first, separator, body = original.decode().partition('\n')
    if first not in ('#!/usr/bin/env bash', '#!/bin/bash', '#!/usr/bin/bash') or not separator:
        raise RuntimeError('Expected the existing Bash launcher; nothing was changed.')
    mode = stat.S_IMODE(wrapper.stat().st_mode)
    if not mode & 0o111:
        raise RuntimeError('Existing launcher is not executable.')
    guarded = prefix(helper)
    payload = Path(__file__).read_bytes()
    if helper.is_symlink() or (helper.exists() and helper.read_bytes() != payload):
        raise RuntimeError('A different session helper exists; refusing to overwrite it.')
    if MARKER in body:
        if body.startswith(guarded) and helper.is_file():
            say('SHADPS4_GUARD_RESULT=ALREADY_INSTALLED')
            status()
            return
        raise RuntimeError('Unrecognized session guard; nothing was changed.')
    replacement = (first + '\n' + guarded + body).encode()
    subprocess.run(['bash', '-n'], input=replacement, check=True, timeout=5)
    compile(payload, str(helper), 'exec')
    private_directory(state)
    private_directory(helper.parent)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = state / ('shadps4-esde.before-' + stamp)
    atomic_write(backup, original, mode)
    atomic_write(helper, payload, 0o700)
    record = {'backup': str(backup), 'original_sha256': hashlib.sha256(original).hexdigest(),
              'installed_sha256': hashlib.sha256(replacement).hexdigest()}
    atomic_write(state / 'installation.json', json.dumps(record, indent=2).encode(), 0o600)
    if wrapper.is_symlink() or wrapper.read_bytes() != original:
        raise RuntimeError('Launcher changed during installation; it was left alone.')
    atomic_write(wrapper, replacement, mode)
    say('SHADPS4_GUARD_RESULT=PASS')
    say('LAUNCHER_BACKUP=' + str(backup))
    say('Protection applies to future launches through shadps4-esde. Existing games were not stopped.')
    if not any(shutil.which(tool) for tool in ('wmctrl', 'xdotool')):
        say('NOTE: wmctrl/xdotool unavailable; duplicate blocking works, automatic window focus does not.')
    status()


def uninstall(home):
    wrapper, helper, _ = paths(home)
    if wrapper.is_symlink():
        raise RuntimeError('Unexpected launcher symlink.')
    original = wrapper.read_bytes()
    first, _, body = original.decode().partition('\n')
    guarded = prefix(helper)
    if not body.startswith(guarded):
        raise RuntimeError('Expected the installed guard prefix; nothing was changed.')
    replacement = (first + '\n' + body[len(guarded):]).encode()
    subprocess.run(['bash', '-n'], input=replacement, check=True, timeout=5)
    if wrapper.read_bytes() != original:
        raise RuntimeError('Launcher changed; it was left alone.')
    atomic_write(wrapper, replacement, stat.S_IMODE(wrapper.stat().st_mode))
    say('SHADPS4_GUARD_RESULT=REMOVED; existing games were not stopped.')


def status():
    found = cores()
    say('RUNNING_SHADPS4_COUNT=' + str(len(found)))
    for core in found:
        say(f"PID={core['pid']} CORE={core['executable']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'uninstall', 'status', 'run'))
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'run':
        if not args.arguments:
            raise RuntimeError('Missing launcher path.')
        return run(Path.home(), Path(args.arguments[0]), args.arguments[1:])
    if args.arguments:
        raise RuntimeError('Unexpected arguments.')
    if args.action in ('install', 'uninstall') and os.geteuid() == 0:
        raise RuntimeError('Run as your normal desktop user, without sudo.')
    {'install': lambda: install(Path.home()), 'uninstall': lambda: uninstall(Path.home()),
     'status': status}[args.action]()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print('SHADPS4_GUARD_RESULT=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
