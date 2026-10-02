#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Resolve X11 authorization centrally for the captured Sunshine system service."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import display_session

UNITS = ('sunshine.service', 'sunshine-display-watchdog.service')
DROPIN = Path('/etc/systemd/system/sunshine.service.d/90-session-x11.conf')
GPU_PRESTART = Path('/etc/systemd/system/sunshine.service.d/gpu-recovery.conf')
WATCHDOG_SHA = '9df276592044e7c310ff8b0bccc616e742c6a49f1a0654bba527042577e95314'
KMS_SHA = '8f5339d867ce4f512411414558643e7c2480249b97c3224fb8e6560f5b8bc5a9'
MODULE_SHA = '5f222179a866eedc96f31d383cd949667ad945c5f77f3290fdd79a5f6ac67029'
RECOVERABLE_INSTALLERS = {
    '2d1d9b0f9b654b7f7045f0721adf7a0b89a97dc9b49c7bcd11cc6bc74afd6aa9',
    '891e763dfe700d3e8455700222c09046664582e44c46b418b4bda991b4f74c99',
}


def digest(content):
    return hashlib.sha256(content).hexdigest()


def directory(path):
    # Never follow a replaced ancestor when installing launch code or state.
    for item in reversed((path, *path.parents)):
        if not item.exists() and not item.is_symlink():
            item.mkdir(mode=0o700)
        info = item.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()) or
                (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
            raise RuntimeError('Unsafe directory: ' + str(item))


def regular(path, root=False):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != (0 if root else os.getuid()) or
            info.st_mode & 0o002):
        raise RuntimeError('Unexpected file owner, type or permissions: ' + str(path))
    return path.read_bytes(), stat.S_IMODE(info.st_mode)


def atomic(path, content, mode=0o600):
    directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def paths(home):
    helper = home / '.local/lib/sunshine-display/repair_sunshine_display.py'
    return [helper, helper.with_name('display_session.py'),
            home / '.local/bin/sunshine-display-watchdog',
            home / '.local/bin/sunshine-kms-guard.sh']


def authority(home, environment):
    """Publish only a symlink to an already readable, successfully probed authority.

    Sunshine keeps this stable filename in its environment. The existing global
    prep command and watchdog refresh its target before launches/after X restarts.
    No cookies are copied and the X server's access policy is unchanged.
    """
    root = home / '.local/state/sunshine-display'
    directory(root)
    if stat.S_IMODE(root.stat().st_mode) != 0o700 or root.stat().st_uid != os.getuid():
        raise RuntimeError('Sunshine display state must be a private user directory.')
    link = root / 'Xauthority'
    if link.exists() or link.is_symlink():
        if not link.is_symlink() or link.lstat().st_uid != os.getuid():
            raise RuntimeError('Unexpected Sunshine authority alias; preserved.')
    env = dict(environment, DISPLAY=':0', XAUTHORITY=str(link))
    resolved, source = display_session.resolve_environment(env)
    target = resolved.get('XAUTHORITY', '')
    if target != str(link):
        if not display_session.readable_authority(target):
            raise RuntimeError('Resolved authorization is not a safe readable file.')
        # mkdtemp makes the temporary symlink name private and collision-free.
        temporary = Path(tempfile.mkdtemp(prefix='.authority-', dir=root))
        try:
            (temporary / 'link').symlink_to(target)
            os.replace(temporary / 'link', link)
        finally:
            shutil.rmtree(temporary)
    elif not link.is_symlink():
        raise RuntimeError('X11 probe succeeded without the expected authority alias.')
    resolved['XAUTHORITY'] = str(link)
    if not display_session.probe(resolved):
        raise RuntimeError('X11 authorization changed while refreshing; retry.')
    return resolved, source


def dropin(home):
    helper = str(paths(home)[0])
    # systemd expands percent specifiers and dollar variables even in quotes.
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', helper):
        raise RuntimeError('Unsupported home path for the systemd override.')
    return ('# Managed by repair_sunshine_display.py; runs as the existing service User.\n'
            '[Service]\nExecStart=\nExecStart=/usr/bin/python3 ' + helper +
            ' --sunshine\n').encode()


def patch_scripts(home, watchdog, kms):
    command = '/usr/bin/python3 ' + shlex.quote(str(paths(home)[0])) + ' --authority'
    start = b'repair_x11()\n{\n'
    addition = ('    XAUTHORITY="$(' + command + ')" || return\n'
                '    export XAUTHORITY\n').encode()
    kms_start = b'export XAUTHORITY=/home/chreece/.Xauthority\n'
    kms_new = ('XAUTHORITY="$(' + command + ')"\nexport XAUTHORITY\n').encode()
    # Accept only the captured originals or this exact patch, including on reruns.
    old_watchdog = watchdog.replace(start + addition, start, 1)
    old_kms = kms.replace(kms_new, kms_start, 1)
    if digest(old_watchdog) != WATCHDOG_SHA or digest(old_kms) != KMS_SHA:
        raise RuntimeError('Display guards differ from the supplied capture; files preserved.')
    result = (old_watchdog.replace(start, start + addition, 1),
              old_kms.replace(kms_start, kms_new, 1))
    for payload in result:
        subprocess.run(['bash', '-n'], input=payload, check=True, timeout=5)
    return result


def patch_prestart(home, original):
    """Repair the three stale-authority hooks observed in the 14:45 report.

    Keep all capability and GPU recovery commands; refresh the authority before
    the existing readiness loop, which runs before Sunshine's ExecStart wrapper.
    """
    text = original.decode()
    stale = str(home / '.Xauthority')
    stable = str(home / '.local/state/sunshine-display/Xauthority')
    helper = str(paths(home)[0])
    username = pwd.getpwuid(os.getuid()).pw_name
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', username) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', helper):
        raise RuntimeError('Unsupported service user or helper path.')
    # An explicit privilege drop also supports old PermissionsStartOnly units.
    prep = ('# SUNSHINE_X11_PRESTART_V1\n'
            'ExecStartPre=+/usr/sbin/runuser -u ' + username +
            ' -- /usr/bin/python3 ' + helper + ' --prepare\n')
    if prep in text:
        text = text.replace(prep, '', 1).replace(stable, stale)
    if 'SUNSHINE_X11_PRESTART' in text:
        raise RuntimeError('Unexpected earlier prestart repair; preserved.')
    lines = text.splitlines(keepends=True)
    section = None
    selected = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('['):
            section = stripped
        if section == '[Service]' and stripped.startswith('ExecStartPre=') and stale in line:
            if line.rstrip().endswith('\\'):
                raise RuntimeError('Unexpected multiline X11 startup hook; preserved.')
            selected.append(index)
    if (len(selected) != 3 or
            'for i in $(seq 1 90)' not in lines[selected[0]] or
            'X11 display :0 not ready' not in lines[selected[0]] or
            'xrandr --output HDMI-A-0 --mode 1920x1080 --rate 60 --primary' not in lines[selected[1]] or
            'xset -dpms s off s noblank' not in lines[selected[2]]):
        raise RuntimeError('X11 startup hooks differ from the supplied report; preserved.')
    for index in selected:
        lines[index] = lines[index].replace(stale, stable)
    lines[selected[0]] = prep + lines[selected[0]]
    return ''.join(lines).encode()


def show(unit):
    result = subprocess.run(['systemctl', 'show', unit, '-p', 'User', '-p', 'ExecStart',
                             '-p', 'ActiveState', '-p', 'KillMode', '-p', 'MainPID',
                             '-p', 'ControlGroup', '-p', 'ControlPID', '-p', 'SubState'], text=True, capture_output=True,
                            check=True, timeout=10)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def service_preflight(home, installed=False, startup=False):
    for parent in DROPIN.parents:
        if parent.exists() or parent.is_symlink():
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError('Unexpected system service directory: ' + str(parent))
    username = pwd.getpwuid(os.getuid()).pw_name
    services = [show(unit) for unit in UNITS]
    for unit, info in zip(UNITS, services):
        allowed = {'active', 'activating', 'inactive', 'failed'} if startup and unit == UNITS[0] else {'active'}
        if info['User'] != username or info['ActiveState'] not in allowed:
            raise RuntimeError('Expected the current user\'s active system service: ' + unit)
        if info['KillMode'] not in ('control-group', 'mixed'):
            raise RuntimeError('Service cannot reliably close its old child processes: ' + unit)
        info['ControlGroup'] = info['ControlGroup'] or '/system.slice/' + unit
    expected = ('/usr/bin/python3' if installed else '/usr/bin/sunshine', str(paths(home)[2]))
    for info, executable in zip(services, expected):
        if not re.search(r'\bpath\s*=\s*' + re.escape(executable) + r'\s*;', info['ExecStart']):
            raise RuntimeError('Unexpected service start command; no service changes made.')
    sunshine_command = ('/usr/bin/python3 ' + str(paths(home)[0]) + ' --sunshine'
                        if installed else '/usr/bin/sunshine')
    if not re.search(r'argv\[\]\s*=\s*' + re.escape(sunshine_command) + r'\s*;', services[0]['ExecStart']):
        raise RuntimeError('Sunshine has additional start arguments; preserve them for review.')
    # Application-level environment overrides must not defeat the central fix.
    config = json.loads(regular(home / '.config/sunshine/apps.json')[0])
    if any(key in config.get('env', {}) for key in ('DISPLAY', 'XAUTHORITY')):
        raise RuntimeError('apps.json overrides display authorization; preserved for review.')
    return services


def startup_process(process, service, proc_root):
    # systemd explicitly identifies the ExecStartPre supervisor; its children are
    # service startup work, not a Moonlight app. No live Sunshine main may exist.
    if (service.get('ActiveState') != 'activating' or service.get('SubState') != 'start-pre' or
            service.get('MainPID') != '0' or service.get('ControlPID', '0') == '0'):
        return False
    pid = process.name
    for _ in range(24):
        if pid == service['ControlPID']:
            return True
        if pid in ('0', '1'):
            break
        try:
            fields = (proc_root / pid / 'stat').read_text().rsplit(')', 1)[1].split()
            pid = fields[1]
        except (OSError, IndexError):
            break
    return False


def mount_helper(process, name):
    # AppImage runtimes can leave a FUSE mount worker after the application exits.
    # Linux truncates the comm value of memfd:squashfuse to memfd:squashfus.
    # Check the executable and an open FUSE descriptor, not just a process name.
    if name not in {'squashfuse', 'squashfuse_ll', 'memfd:squashfus'}:
        return False
    try:
        executable = os.readlink(process / 'exe').removesuffix(' (deleted)')
        if name == 'memfd:squashfus':
            if executable not in {'/memfd:squashfuse', '/memfd:squashfuse_ll'}:
                return False
        elif Path(executable).name not in {'squashfuse', 'squashfuse_ll'}:
            return False
        for descriptor in (process / 'fd').iterdir():
            try:
                if os.readlink(descriptor) == '/dev/fuse':
                    return True
            except FileNotFoundError:
                continue
    except OSError:
        return False
    return False


def no_games(services, proc_root=Path('/proc')):
    groups = {info['ControlGroup'] for info in services}
    if not all(group.startswith('/system.slice/sunshine') for group in groups):
        raise RuntimeError('Unexpected service control group.')
    blocked = []
    for process in proc_root.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            name = (process / 'comm').read_text().strip().lower()
            group = (process / 'cgroup').read_text()
            in_sunshine = any(line.split(':', 2)[-1] == services[0]['ControlGroup']
                              for line in group.splitlines())
            launcher = False
            if in_sunshine and process.name == services[0].get('MainPID') and name.startswith('python3'):
                args = [os.fsdecode(arg) for arg in (process / 'cmdline').read_bytes().split(b'\0') if arg]
                launcher = args[1:] == [str(paths(Path.home())[0]), '--sunshine']
            # The frontend and verified mount workers can close with their service.
            # Still scan every process: accepting a worker must not hide its game.
            if name.startswith('shadps4') or (in_sunshine and name not in {'sunshine', 'es-de'}
                                             and not launcher and not startup_process(process, services[0], proc_root)
                                             and not mount_helper(process, name)):
                blocked.append(process.name + ':' + name)
        except FileNotFoundError:
            continue
    if blocked:
        raise RuntimeError('Close running games/other Sunshine apps first: ' + ', '.join(blocked))


def sudo(*args):
    subprocess.run(['sudo', '-n', *map(str, args)], check=True, timeout=50)


def write_dropin(payload, state):
    if payload is None:
        sudo('/usr/bin/rm', '--', DROPIN)
    else:
        source = state / 'override.conf'
        atomic(source, payload)
        sudo('/usr/bin/install', '-d', '-m', '0755', DROPIN.parent)
        sudo('/usr/bin/install', '-m', '0644', '-o', 'root', '-g', 'root', source, DROPIN)
    sudo('/usr/bin/systemctl', 'daemon-reload')


def activate():
    sudo('/usr/bin/systemctl', '--no-block', 'start', *UNITS)
    if not wait_service_state(True, seconds=30):
        raise RuntimeError('Sunshine service startup is pending or failed; check the saved journal.')
    for unit in UNITS:
        subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=True, timeout=10)


def verify(home):
    expected = str(home / '.local/state/sunshine-display/Xauthority')
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        info = show(UNITS[0])
        if info['ActiveState'] == 'failed':
            break
        try:
            process = Path('/proc') / info['MainPID']
            data = (process / 'environ').read_bytes()
            env = dict(os.environ)
            env.update({os.fsdecode(k): os.fsdecode(v) for k, v in
                        (part.split(b'=', 1) for part in data.split(b'\0') if b'=' in part)})
            if (os.readlink(process / 'exe') == '/usr/bin/sunshine' and
                    env.get('XAUTHORITY') == expected and env.get('DISPLAY') == ':0' and
                    display_session.probe(env)):
                print('SUNSHINE_X11_ACCESS=PASS; the running Sunshine environment opens display :0.', flush=True)
                return
        except FileNotFoundError:
            pass
        time.sleep(0.25)
    raise RuntimeError('Running Sunshine did not inherit verified X11 authorization.')


def same(path, before, root=False):
    if before is None:
        return not path.exists() and not path.is_symlink()
    return regular(path, root)[0] == before


def apply(home, entries, before_dropin, after_dropin, backup, gpu=None):
    subprocess.run(['sudo', '-v'], check=True)  # Interactive terminal only; never read a password.
    services = service_preflight(home, installed=before_dropin is not None, startup=gpu is not None)
    no_games(services)
    if not same(DROPIN, before_dropin, root=True):
        raise RuntimeError('Sunshine override changed during checks; preserved.')
    for path, before, _, _ in entries:
        if not same(path, before):
            raise RuntimeError('A display repair target changed during checks: ' + str(path))
    applied = []
    override_changed = False
    gpu_changed = False
    try:
        # Stop the existing processes before replacing scripts. No Xorg/AI service is touched.
        sudo('/usr/bin/systemctl', '--no-block', 'stop', *reversed(UNITS))
        if not wait_service_state(False):
            raise RuntimeError('Service stop is pending; files preserved.')
        if gpu and not same(GPU_PRESTART, gpu[0], root=True):
            raise RuntimeError('GPU recovery configuration changed; preserved.')
        for path, before, after, mode in entries:
            if not same(path, before):
                raise RuntimeError('A repair target changed while stopping services.')
            if after is None:
                path.unlink()
            else:
                atomic(path, after, mode)
            applied.append((path, before, after, mode))
        if gpu:
            atomic(backup / 'gpu-prestart.install', gpu[1])
            gpu_changed = True
            sudo('/usr/bin/install', '-m', format(gpu[2], 'o'), '-o', 'root', '-g', 'root',
                 backup / 'gpu-prestart.install', GPU_PRESTART)
        override_changed = True
        write_dropin(after_dropin, backup)
        activate()
        if after_dropin is not None:
            verify(home)
    except BaseException as initial_error:
        with (backup / 'failure.txt').open('w') as report:
            report.write('INITIAL_FAILURE=' + str(initial_error) + '\n')
            recovery_capture(report, home, 'BEFORE ROLLBACK')
        print('FAILURE_REPORT=' + str(backup / 'failure.txt'), flush=True)
        # Exact snapshots only: do not overwrite a subsequent unrelated edit.
        sudo('/usr/bin/systemctl', '--no-block', 'stop', *reversed(UNITS))
        if not wait_service_state(False):
            raise RuntimeError(str(initial_error) + '; rollback stop pending. See FAILURE_REPORT.') from initial_error
        for path, before, after, mode in reversed(applied):
            if not same(path, after):
                raise RuntimeError('File changed during recovery; use the printed backup: ' + str(path))
            if before is None:
                path.unlink()
            else:
                atomic(path, before, mode)
        if gpu_changed:
            if not (same(GPU_PRESTART, gpu[1], root=True) or same(GPU_PRESTART, gpu[0], root=True)):
                raise RuntimeError('GPU recovery configuration changed after repair; preserved.')
            atomic(backup / 'gpu-prestart.restore', gpu[0])
            sudo('/usr/bin/install', '-m', format(gpu[2], 'o'), '-o', 'root', '-g', 'root',
                 backup / 'gpu-prestart.restore', GPU_PRESTART)
        if override_changed:
            if not (same(DROPIN, after_dropin, root=True) or same(DROPIN, before_dropin, root=True)):
                raise RuntimeError('Service override changed during recovery; backup: ' + str(backup))
            if before_dropin is not None or DROPIN.exists():
                write_dropin(before_dropin, backup)
            else:
                sudo('/usr/bin/systemctl', 'daemon-reload')
        try:
            activate()
        except (RuntimeError, OSError, subprocess.SubprocessError) as rollback_error:
            raise RuntimeError(str(initial_error) + '; original files restored, original startup also failed: ' +
                               str(rollback_error) + '. See FAILURE_REPORT.') from initial_error
        raise initial_error


def install(home):
    helper, module, watchdog, kms = paths(home)
    originals = []
    for path in paths(home):
        originals.append(regular(path) if path.exists() or path.is_symlink() else (None, 0o700))
    if originals[2][0] is None or originals[3][0] is None:
        raise RuntimeError('The captured display guards are missing.')
    source = Path(__file__).read_bytes()
    resolver = Path(__file__).with_name('display_session.py').read_bytes()
    if digest(resolver) != MODULE_SHA:
        raise RuntimeError('Display resolver checksum mismatch.')
    for old, new in zip(originals[:2], (source, resolver)):
        if old[0] is not None and old[0] != new:
            raise RuntimeError('An unrelated Sunshine display helper exists; preserved.')
    new_scripts = patch_scripts(home, originals[2][0], originals[3][0])
    gpu_before, gpu_mode = regular(GPU_PRESTART, root=True)
    if gpu_mode & 0o022:
        raise RuntimeError('Unexpected writable system startup configuration; preserved.')
    gpu_after = patch_prestart(home, gpu_before)
    if not Path('/usr/sbin/runuser').is_file():
        raise RuntimeError('The system runuser executable is missing.')
    payloads = (source, resolver, *new_scripts)
    expected_dropin = dropin(home)
    old_dropin = regular(DROPIN, root=True)[0] if DROPIN.exists() or DROPIN.is_symlink() else None
    if old_dropin is not None and old_dropin != expected_dropin:
        raise RuntimeError('A different Sunshine display override exists; preserved.')
    services = service_preflight(home, installed=old_dropin is not None, startup=True)
    no_games(services)
    authority(home, os.environ)
    if (all(before == after for (before, _), after in zip(originals, payloads)) and
            old_dropin == expected_dropin and gpu_before == gpu_after):
        verify(home)
        print('SUNSHINE_DISPLAY_REPAIR=ALREADY_INSTALLED')
        return
    if old_dropin is not None:
        raise RuntimeError('An incomplete prior repair exists; use its printed restore command first.')
    root = home / '.local/state/sunshine-display'
    backup = Path(tempfile.mkdtemp(prefix='backup-', dir=root))
    entries = []
    record = []
    for index, (path, (before, mode), after) in enumerate(zip(paths(home), originals, payloads)):
        if before is not None:
            atomic(backup / f'before-{index}', before)
        entries.append((path, before, after, mode))
        record.append({'before': digest(before) if before is not None else None,
                       'after': digest(after), 'mode': mode})
    atomic(backup / 'gpu-prestart.original', gpu_before)
    atomic(backup / 'state.json', json.dumps({'files': record, 'dropin': expected_dropin.decode(),
           'gpu_prestart': {'before': digest(gpu_before), 'after': digest(gpu_after), 'mode': gpu_mode}}).encode())
    atomic(backup / 'repair_sunshine_display.py', source, 0o700)
    atomic(backup / 'display_session.py', resolver, 0o700)
    print('BACKUP=' + str(backup), flush=True)
    print('RESTORE=/usr/bin/python3 ' + shlex.quote(str(backup / 'repair_sunshine_display.py')) +
          ' --restore ' + shlex.quote(str(backup)), flush=True)
    apply(home, entries, old_dropin, expected_dropin, backup, (gpu_before, gpu_after, gpu_mode))
    print('SUNSHINE_DISPLAY_REPAIR=PASS; reconnect Moonlight and test ES-DE exit/relaunch.')
    print('Visible-window/game testing is pending; no emulator or audio configuration was changed.')


def restore(home, backup):
    root = home / '.local/state/sunshine-display'
    if backup.parent != root or not backup.name.startswith('backup-') or backup.is_symlink():
        raise RuntimeError('Unexpected display repair backup path.')
    state = json.loads(regular(backup / 'state.json')[0])
    expected_dropin = dropin(home)
    if state['dropin'].encode() != expected_dropin or not same(DROPIN, expected_dropin, root=True):
        raise RuntimeError('Service override changed after repair; preserved.')
    if len(state['files']) != 4:
        raise RuntimeError('Unexpected backup entries.')
    entries = []
    for index, (path, entry) in enumerate(zip(paths(home), state['files'])):
        current, _ = regular(path)
        if digest(current) != entry['after']:
            raise RuntimeError('A file changed after repair; preserved: ' + str(path))
        before = regular(backup / f'before-{index}')[0] if entry['before'] is not None else None
        if before is not None and digest(before) != entry['before']:
            raise RuntimeError('Backup checksum mismatch.')
        mode = entry['mode']
        if not isinstance(mode, int) or mode & ~0o777 or mode & 0o002:
            raise RuntimeError('Invalid backup mode.')
        entries.append((path, current, before, mode))
    gpu = None
    if 'gpu_prestart' in state:
        current, _ = regular(GPU_PRESTART, root=True)
        original = regular(backup / 'gpu-prestart.original')[0]
        record = state['gpu_prestart']
        if digest(current) != record['after'] or digest(original) != record['before']:
            raise RuntimeError('GPU recovery configuration changed after repair; preserved.')
        mode = record['mode']
        if not isinstance(mode, int) or mode & ~0o777 or mode & 0o022:
            raise RuntimeError('Invalid GPU recovery backup permissions.')
        gpu = current, original, mode
    apply(home, entries, expected_dropin, None, backup, gpu)
    print('SUNSHINE_DISPLAY_RESTORE=PASS; prior service command and display guards restored.')


def recovery_plan(home):
    """Accept installed, rolled-back, or partially restored files from a known attempt."""
    root = home / '.local/state/sunshine-display'
    problems = []
    for backup in sorted(root.glob('backup-*'), key=lambda p: p.lstat().st_mtime, reverse=True):
        try:
            if backup.is_symlink() or not backup.is_dir() or backup.stat().st_uid != os.getuid():
                raise RuntimeError('Unexpected backup directory')
            state = json.loads(regular(backup / 'state.json')[0])
            records = state['files']
            if (len(records) != 4 or records[0]['before'] is not None or
                    records[1]['before'] is not None or
                    records[0]['after'] not in RECOVERABLE_INSTALLERS or
                    records[1]['after'] != MODULE_SHA or
                    [r['before'] for r in records[2:]] != [WATCHDOG_SHA, KMS_SHA] or
                    state['dropin'].encode() != dropin(home)):
                raise RuntimeError('Backup does not describe a known failed installation')
            entries = []
            for index, (path, record) in enumerate(zip(paths(home), records)):
                before = regular(backup / f'before-{index}')[0] if record['before'] else None
                if before is not None and digest(before) != record['before']:
                    raise RuntimeError('Original backup checksum mismatch')
                current = regular(path)[0] if path.exists() or path.is_symlink() else None
                current_hash = digest(current) if current is not None else None
                if current_hash not in (record['before'], record['after']):
                    raise RuntimeError('A repair target was subsequently edited: ' + str(path))
                mode = record['mode']
                if not isinstance(mode, int) or mode & ~0o777 or mode & 0o002:
                    raise RuntimeError('Invalid backup file permissions')
                entries.append((path, current, before, mode))
            override = regular(DROPIN, root=True)[0] if DROPIN.exists() or DROPIN.is_symlink() else None
            if override is not None and override != dropin(home):
                raise RuntimeError('Sunshine override was subsequently edited')
            return backup, entries, override
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            problems.append(str(backup) + ': ' + str(error))
    raise RuntimeError('No compatible recovery backup. ' + '; '.join(problems))


def recovery_command(report, command):
    report.write('\n=== ' + shlex.join(command) + ' ===\n')
    report.flush()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=12)
        report.write(result.stdout + result.stderr + f'\nrc={result.returncode}\n')
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as error:
        report.write(str(error) + '\n')
        return False
    finally:
        report.flush()


def recovery_capture(report, home, phase):
    report.write('\n=== ' + phase + ' ===\n')
    properties = ['User', 'Type', 'NotifyAccess', 'ActiveState', 'SubState', 'Result',
                  'MainPID', 'ControlPID', 'ControlGroup', 'ExecMainStatus', 'ExecStart',
                  'ExecStartPre', 'ExecStartPost', 'ExecStop', 'TimeoutStartUSec',
                  'TimeoutStopUSec', 'Job', 'After', 'Requires', 'Restart',
                  'NRestarts', 'PrivateTmp', 'ProtectHome', 'DropInPaths', 'KillMode']
    for unit in UNITS:
        recovery_command(report, ['systemctl', 'show', unit] +
                         [item for prop in properties for item in ('-p', prop)])
    recovery_command(report, ['sudo', '-n', '/usr/bin/journalctl', '--no-pager',
                             '-o', 'short-precise', '-u', UNITS[0], '-u', UNITS[1],
                             '--since', '-30min', '-n', '160'])
    recovery_command(report, ['systemctl', '--user', 'show', 'headless-x.service',
                             '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID'])
    for path in [home / '.config/sunshine/sunshine.log',
                 home / '.local/state/sunshine-display-watchdog.log']:
        try:
            with path.open('rb') as handle:
                handle.seek(max(0, path.stat().st_size - 24000))
                report.write('\n=== ' + str(path) + ' tail ===\n' +
                             handle.read().decode(errors='replace'))
        except OSError as error:
            report.write(str(error) + '\n')
    report.flush()


def wait_service_state(active, seconds=20):
    deadline = time.monotonic() + seconds
    while True:
        states = [show(unit) for unit in UNITS]
        if active:
            complete = all(state['ActiveState'] == 'active' for state in states)
        else:
            complete = all(state['ActiveState'] in ('inactive', 'failed') and
                           state.get('MainPID') == '0' for state in states)
        if complete:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def recover(home):
    fd, report_path = tempfile.mkstemp(prefix='sunshine-start-recovery-', suffix='.txt', dir=home)
    print('RECOVERY_REPORT=' + report_path, flush=True)
    with os.fdopen(fd, 'w') as report:
        try:
            subprocess.run(['sudo', '-v'], check=True)
            recovery_capture(report, home, 'BEFORE RECOVERY')
            backup, entries, override = recovery_plan(home)
            report.write('\nBACKUP=' + str(backup) + '\n')
            services = [show(unit) for unit in UNITS]
            username = pwd.getpwuid(os.getuid()).pw_name
            for unit, service in zip(UNITS, services):
                if service['User'] != username or service['KillMode'] not in ('control-group', 'mixed'):
                    raise RuntimeError('Service ownership or shutdown scope changed; recovery stopped')
                # Inactive units have an empty ControlGroup property.
                service['ControlGroup'] = service['ControlGroup'] or '/system.slice/' + unit
            commands = [('/usr/bin/sunshine', '/usr/bin/python3 ' + str(paths(home)[0]) + ' --sunshine'),
                        (str(paths(home)[2]),)]
            for service, allowed in zip(services, commands):
                if not any(re.search(r'argv\[\]\s*=\s*' + re.escape(command) + r'\s*;',
                                     service['ExecStart']) for command in allowed):
                    raise RuntimeError('Service start command changed; recovery stopped')
            no_games(services)
            changed = override is not None or any(current != before for _, current, before, _ in entries)
            if changed:
                if not recovery_command(report, ['sudo', '-n', '/usr/bin/systemctl', '--no-block',
                                                  'stop', *reversed(UNITS)]):
                    raise RuntimeError('Could not enqueue a scoped service stop; files preserved')
                if not wait_service_state(False):
                    raise RuntimeError('Service stop is still pending; files preserved. See recovery report')
                # Revalidate all files together after service shutdown, before changing any.
                for path, current, _, _ in entries:
                    if not same(path, current):
                        raise RuntimeError('Repair target changed during recovery; preserved')
                if not same(DROPIN, override, root=True):
                    raise RuntimeError('Service override changed during recovery; preserved')
                for path, current, before, mode in entries:
                    if current == before:
                        continue
                    if before is None:
                        path.unlink()
                    else:
                        atomic(path, before, mode)
                if override is not None:
                    sudo('/usr/bin/rm', '--', DROPIN)
                sudo('/usr/bin/systemctl', 'daemon-reload')
            print('SUNSHINE_RECOVERY_FILES=RESTORED; original service command and guard scripts.', flush=True)
            recovery_command(report, ['sudo', '-n', '/usr/bin/systemctl', 'reset-failed', *UNITS])
            accepted = True
            for unit in UNITS:
                ok = recovery_command(report, ['sudo', '-n', '/usr/bin/systemctl', '--no-block', 'start', unit])
                accepted = accepted and ok
            ready = wait_service_state(True) if accepted else False
            for unit in UNITS:
                state = show(unit)
                print(unit + ': ' + state['ActiveState'] + '/' + state.get('SubState', '?'), flush=True)
            print('SUNSHINE_RECOVERY_RESULT=' + ('SERVICES_ACTIVE' if ready else 'START_PENDING_OR_FAILED'), flush=True)
            print('Upload RECOVERY_REPORT; visible-window recovery is not yet confirmed.', flush=True)
        finally:
            recovery_capture(report, home, 'AFTER RECOVERY')
            print('RECOVERY_REPORT=' + report_path, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sunshine', action='store_true')
    group.add_argument('--authority', action='store_true')
    group.add_argument('--prepare', action='store_true')
    group.add_argument('--restore', type=Path)
    group.add_argument('--recover', action='store_true', help='Recover the known timed-out attempts and save systemd diagnostics')
    args = parser.parse_args()
    if os.geteuid() == 0 or sys.platform != 'linux':
        raise RuntimeError('Run as the normal Linux desktop user, not with sudo.')
    home = Path.home()
    if args.sunshine or args.authority or args.prepare:
        # At boot the independently managed headless X service may still be starting.
        deadline = time.monotonic() + (30 if args.sunshine or args.prepare else 0)
        while True:
            try:
                env, source = authority(home, os.environ)
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        if args.authority:
            print(env['XAUTHORITY'])
        elif args.prepare:
            print('SUNSHINE_DISPLAY_PREPARE=PASS source=' + source, flush=True)
        else:
            print('SUNSHINE_DISPLAY_START=PASS source=' + source, flush=True)
            os.execve('/usr/bin/sunshine', ['/usr/bin/sunshine'], env)
        return
    root = home / '.local/state/sunshine-display'
    directory(root)
    fd = os.open(root / 'install.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Unexpected repair lock.')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.recover:
            recover(home)
        elif args.restore:
            restore(home, args.restore)
        else:
            install(home)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('SUNSHINE_DISPLAY_REPAIR=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
