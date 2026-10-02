#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Temporarily enable NGS2 LFE tracing through the existing guarded ES-DE launcher."""
import argparse
import hashlib
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


def instrument(original, logfile, audio_review=False):
    text = original.decode()
    lines = [line for line in text.splitlines() if line.startswith('exec ')
             and line.endswith(' --game "$game" --fullscreen true')]
    if len(lines) != 1 or '# SHADPS4_SESSION_GUARD_V1\n' not in text:
        raise RuntimeError('Unrecognized guarded launcher; preserved.')
    extra = 'export SHADPS4_NGS2_DIAGNOSTICS=1\n' if audio_review else ''
    return text.replace(lines[0],
                        extra + 'export SHADPS4_NGS2_LFE_DIAGNOSTICS=1\n' + lines[0] +
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


def selected_install(home, expected_revision):
    if not re.fullmatch(r'[0-9a-f]{40}', expected_revision or ''):
        raise RuntimeError('Audio review requires the expected 40-character revision.')
    core = (home / 'Applications/shadps4/shadps4').resolve(strict=True)
    with core.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    for state_file in (home / '.local/state/shadps4-default-main').glob('install-*/state.json'):
        try:
            state = json.loads(state_file.read_text())
        except (OSError, ValueError):
            continue
        if (state.get('revision') == expected_revision and state.get('home') == str(home) and
                state.get('binary') == str(core) and state.get('binary_sha256') == digest):
            return {'revision': expected_revision, 'binary': str(core), 'binary_sha256': digest,
                    'installer_state': str(state_file)}
    raise RuntimeError('Selected binary does not match a verified installation of ' +
                       expected_revision + '. Nothing changed.')


def audio_summary(logfile):
    patterns = {
        'successful_filter_stage_records':
            r'NGS2_DIAG .*control-stage .*command=(?:1000000a|20000006) .*result=0(?: |$)',
        'filter_rejection_records': r'NGS2_DIAG .*filter-rejected ',
        'block_rejection_records': r'NGS2_DIAG .*block-rejected ',
        'control_error_records': r'NGS2_DIAG .*control-error ',
        'render_error_records': r'NGS2_DIAG .*render-error ',
        'decoder_error_records': r'NGS2_DIAG .*decode-error ',
        'failed_voice_state_records': r'NGS2_DIAG .*state-query .*flags=10(?: |$)',
        'empty_playing_state_records': r'NGS2_DIAG .*state-query .*flags=23(?: |$)',
    }
    compiled = {key: re.compile(pattern) for key, pattern in patterns.items()}
    counts = dict.fromkeys(patterns, 0)
    examples = {key: [] for key in patterns if key != 'successful_filter_stage_records'}
    nonfinite = 0
    diagnostics = 0
    with logfile.open(errors='replace') as stream:
        for line in stream:
            diagnostics += 'NGS2_DIAG ' in line
            for key, pattern in compiled.items():
                if pattern.search(line):
                    counts[key] += 1
                    if key in examples and len(examples[key]) < 8:
                        examples[key].append(line.strip())
            if 'NGS2_LFE output ' in line:
                match = re.search(r' nonfinite=(\d+)', line)
                if match:
                    nonfinite += int(match[1])
    return {'diagnostic_records': diagnostics, 'record_counts': counts,
            'diagnostic_budget_may_be_exhausted': diagnostics >= 1792,
            'nonfinite_output_samples_observed': nonfinite, 'examples': examples,
            'scope': 'Bounded and sampled diagnostics, not exact lifetime error totals. '
                     'A successful filter stage does not prove its entire transaction committed. '
                     'No recorded error does not prove all audio behavior is correct.'}


def collect(home, audio_review=False, expected_revision=None):
    wrapper = home / '.local/bin/shadps4-esde'
    if games():
        raise RuntimeError('Exit the running game first. Nothing changed.')
    if wrapper.is_symlink() or not wrapper.is_file():
        raise RuntimeError('Expected the existing regular ES-DE launcher; preserved.')
    original = wrapper.read_bytes()
    mode = stat.S_IMODE(wrapper.stat().st_mode)
    selected = selected_install(home, expected_revision) if audio_review else None
    prefix = 'shadps4-audio-review-' if audio_review else 'shadps4-lfe-routing-'
    work = Path(tempfile.mkdtemp(prefix=prefix, dir=home))
    work.chmod(0o700)
    logfile = work / 'emulator.log'
    modified = instrument(original, logfile, audio_review)
    (work / 'launcher.original').write_bytes(original)
    if selected:
        (work / 'selected-install.json').write_text(json.dumps(selected, indent=2))
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
    if audio_review:
        review = audio_summary(logfile) if logfile.exists() else {'diagnostic_records': 0}
        report.update(audio_review=review, selected_install=selected,
                      observed_core_matches_expected=bool(detected) and all(
                          path == selected['binary'] for path in detected.values()))
    (work / 'summary.json').write_text(json.dumps(report, indent=2))
    archive = work.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz') as output:
        output.add(work, arcname=work.name)
    archive.chmod(0o600)
    print(json.dumps(report, indent=2))
    print(f'{"AUDIO_REPORT" if audio_review else "LFE_REPORT"}={archive}', flush=True)
    if not detected or not report['outputs']:
        raise RuntimeError('No NGS2 LFE output windows captured; report is incomplete.')
    if audio_review and (not report['observed_core_matches_expected'] or
                         not report['audio_review']['diagnostic_records']):
        raise RuntimeError('Expected core or audio diagnostics not observed; upload the report.')


def interrupted(signum, frame):
    raise KeyboardInterrupt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio-review', action='store_true')
    parser.add_argument('--expected-revision')
    args = parser.parse_args()
    if args.expected_revision and not args.audio_review:
        parser.error('--expected-revision requires --audio-review')
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)
    try:
        collect(Path.home(), args.audio_review, args.expected_revision)
    except (OSError, RuntimeError, KeyboardInterrupt) as error:
        print(f'{"AUDIO_CAPTURE" if args.audio_review else "LFE_CAPTURE"}_RESULT=FAIL: {error}')
        raise SystemExit(1)
