#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Temporarily enable NGS2 LFE tracing through the existing guarded ES-DE launcher."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import tarfile
import tempfile
import time


def games():
    found = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            executable = os.readlink(path / 'exe')
            if Path(executable).name.lower() == 'shadps4':
                found[path.name] = executable
        except OSError:
            pass
    return found


def instrument(original, logfile):
    text = original.decode()
    lines = [line for line in text.splitlines() if line.startswith('exec ')
             and line.endswith(' --game "$game" --fullscreen true')]
    if len(lines) != 1 or '# SHADPS4_SESSION_GUARD_V1\n' not in text:
        raise RuntimeError('Unrecognized guarded launcher; preserved.')
    return text.replace(lines[0],
                        'export SHADPS4_NGS2_LFE_DIAGNOSTICS=1\n' + lines[0] +
                        ' >' + shlex.quote(str(logfile)) + ' 2>&1', 1).encode()


def atomic_write(path, content, mode):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.lfe-')
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def snapshot(work, number):
    for label, command in [('streams', ['pactl', 'list', 'sink-inputs']),
                           ('sinks', ['pactl', 'list', 'sinks']),
                           ('pipewire', ['pw-dump'])]:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            output = result.stdout + result.stderr
        except (OSError, subprocess.TimeoutExpired) as error:
            output = str(error)
        (work / f'{number:03d}-{label}.txt').write_text(output)


def summary(logfile):
    outputs = {}
    trace = logfile.read_text(errors='replace') if logfile.exists() else ''
    for line in trace.splitlines():
        match = re.search(r'NGS2_LFE output system=(\d+).*?output=(\d+) channels=(\d+) '
                          r'frames=(\d+) peaks=([^ ]+) lfe-nonzero=(\d+)', line)
        if not match:
            continue
        key = f'system={match[1]} output={match[2]} channels={match[3]}'
        entry = outputs.setdefault(key, {'frames_observed': 0, 'lfe_nonzero_samples': 0,
                                         'maximum_peaks': [0.0] * 8})
        entry['frames_observed'] += int(match[4])
        entry['lfe_nonzero_samples'] += int(match[6])
        entry['maximum_peaks'] = [max(a, b) for a, b in
                                  zip(entry['maximum_peaks'], map(float, match[5].split(',')))]
    return {'outputs': outputs, 'capture_limit_reached': 'capture-limit-reached' in trace,
            'filter_rejection_records': trace.count('NGS2_LFE filter-rejected '),
            'note': 'Output windows count all frames between reports. Voice/route snapshots '
                    'are periodic. An abrupt process exit can leave an unreported final window.'}


def collect(home):
    wrapper = home / '.local/bin/shadps4-esde'
    if games():
        raise RuntimeError('Exit the running game first. Nothing changed.')
    if wrapper.is_symlink() or not wrapper.is_file():
        raise RuntimeError('Expected the existing regular ES-DE launcher; preserved.')
    original = wrapper.read_bytes()
    mode = stat.S_IMODE(wrapper.stat().st_mode)
    work = Path(tempfile.mkdtemp(prefix='shadps4-lfe-routing-', dir=home))
    logfile = work / 'emulator.log'
    modified = instrument(original, logfile)
    (work / 'launcher.original').write_bytes(original)
    detected = {}
    number = 0
    next_snapshot = 0
    try:
        atomic_write(wrapper, modified, mode)
        print('NOW launch the ordinary game entry in ES-DE. Play 2–3 minutes, then exit.', flush=True)
        started = time.monotonic()
        while time.monotonic() - started < 900:
            active = games()
            if active:
                detected.update(active)
                if time.monotonic() >= next_snapshot:
                    snapshot(work, number)
                    number += 1
                    next_snapshot = time.monotonic() + 20
            elif detected:
                break
            elif time.monotonic() - started > 180:
                print('No game launch detected within three minutes.', flush=True)
                break
            time.sleep(1)
    finally:
        if wrapper.read_bytes() == modified:
            atomic_write(wrapper, original, mode)
            print('Original launcher restored.', flush=True)
        else:
            print('Launcher changed separately; preserved. Backup:', work / 'launcher.original')
    report = summary(logfile)
    report.update(game_processes=detected, host_snapshots=number)
    (work / 'summary.json').write_text(json.dumps(report, indent=2))
    archive = work.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz') as output:
        output.add(work, arcname=work.name)
    print(json.dumps(report, indent=2))
    print(f'LFE_REPORT={archive}', flush=True)
    if not detected or not report['outputs']:
        raise RuntimeError('No NGS2 LFE output windows captured; report is incomplete.')


def interrupted(signum, frame):
    raise KeyboardInterrupt


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)
    try:
        collect(Path.home())
    except (OSError, RuntimeError, KeyboardInterrupt) as error:
        print(f'LFE_CAPTURE_RESULT=FAIL: {error}')
        raise SystemExit(1)
