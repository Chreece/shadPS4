#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Resolve an authenticated local X11 session without changing its access policy."""
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


def display_key(value):
    match = re.fullmatch(r'(?:unix)?:(\d+)(?:\.\d+)?', value or '')
    return ':' + match[1] if match else None


def readable_authority(value):
    path = Path(value)
    try:
        info = path.lstat()
        return (path.is_absolute() and stat.S_ISREG(info.st_mode) and
                info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022 and
                os.access(path, os.R_OK))
    except OSError:
        return False


def session_candidates(proc_root=Path('/proc')):
    candidates = []
    uid = os.getuid()
    for process in proc_root.iterdir():
        if not process.name.isdigit():
            continue
        try:
            owner = process.stat().st_uid
            name = (process / 'comm').read_text().strip().lower()
            if name in {'es-de', 'sunshine', 'openbox'} and owner == uid:
                pairs = [item.split(b'=', 1) for item in
                         (process / 'environ').read_bytes().split(b'\0') if b'=' in item]
                env = {os.fsdecode(k): os.fsdecode(v) for k, v in pairs
                       if k in (b'DISPLAY', b'XAUTHORITY')}
                display = display_key(env.get('DISPLAY'))
                auth = env.get('XAUTHORITY')
                if display and auth and readable_authority(auth):
                    candidates.append((display, auth, f'{name} pid={process.name}'))
            if name not in {'xorg', 'x'}:
                continue
            if owner != uid:
                cgroup = (process / 'cgroup').read_text()
                if owner != 0 or f'/user.slice/user-{uid}.slice/' not in cgroup:
                    continue
            args = [os.fsdecode(x) for x in (process / 'cmdline').read_bytes().split(b'\0') if x]
            displays = [display_key(arg) for arg in args if display_key(arg)]
            if len(displays) != 1 or '-auth' not in args:
                continue
            auth = args[args.index('-auth') + 1]
            if readable_authority(auth):
                candidates.append((displays[0], auth, f'xorg pid={process.name}'))
        except (OSError, IndexError, ValueError):
            continue
    return candidates


def probe(environment):
    executable = shutil.which('xprop')
    if not executable:
        raise RuntimeError('xprop is unavailable; display access was not verified.')
    try:
        result = subprocess.run([executable, '-root', '_NET_SUPPORTING_WM_CHECK'],
                                env=environment, capture_output=True, timeout=3)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def resolve_environment(environment, proc_root=Path('/proc')):
    original = dict(environment)
    target = display_key(original.get('DISPLAY'))
    if original.get('DISPLAY') and target is None:
        raise RuntimeError('Expected the local streaming display; refusing to redirect a remote display.')
    if target and probe(original):
        return original, 'inherited'
    candidates = session_candidates(proc_root)
    if target is None:
        displays = {display for display, _, _ in candidates}
        if len(displays) != 1:
            raise RuntimeError('No unique local display session; keep Moonlight/ES-DE open and retry.')
        target = displays.pop()
    seen = set()
    attempts = 0
    for display, authority, source in candidates:
        if display != target or (display, authority) in seen:
            continue
        seen.add((display, authority))
        attempts += 1
        if attempts > 8:
            break
        candidate = dict(original, DISPLAY=original.get('DISPLAY') or display, XAUTHORITY=authority)
        if probe(candidate):
            return candidate, source
    raise RuntimeError(f'Cannot authenticate to display {target}; launcher preserved. '
                       'No X11 permissions or authority files were changed.')


def launch_environment(environment):
    resolved, source = resolve_environment(environment)
    print(f"SHADPS4_DISPLAY=PASS display={resolved['DISPLAY']} source={source}", flush=True)
    return resolved
