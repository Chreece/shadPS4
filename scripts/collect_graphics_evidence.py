#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Capture one guarded ES-DE game session from the verified installed revision."""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import time


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    atomic_write(path, (json.dumps(value, indent=2) + '\n').encode(), 0o600)


def atomic_write(path, content, mode):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.graphics-')
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def games():
    found = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid == os.getuid():
                executable = os.readlink(path / 'exe')
                if Path(executable.removesuffix(' (deleted)')).name.lower() == 'shadps4':
                    found[path.name] = executable
        except OSError:
            pass
    return found


def selected_install(home, revision):
    if not re.fullmatch(r'[0-9a-f]{40}', revision or ''):
        raise RuntimeError('An exact 40-character expected revision is required.')
    core = (home / 'Applications/shadps4/shadps4').resolve(strict=True)
    checksum = digest(core)
    for path in (home / '.local/state/shadps4-default-main').glob('install-*/state.json'):
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if (state.get('revision') == revision and state.get('home') == str(home) and
                state.get('binary') == str(core) and state.get('binary_sha256') == checksum):
            return {'revision': revision, 'binary': str(core), 'binary_sha256': checksum,
                    'installer_state': str(path)}
    raise RuntimeError('The selected core does not match the verified expected installation.')


def instrument(original, script, work):
    text = original.decode()
    lines = [line for line in text.splitlines() if line.startswith('exec ')
             and line.endswith(' --game "$game" --fullscreen true')]
    if (len(lines) != 1 or '# SHADPS4_SESSION_GUARD_V1\n' not in text or
            '# SHADPS4_DEFAULT_MAIN_V1\n' not in text):
        raise RuntimeError('Unrecognized normal guarded launcher; preserved.')
    prefix = 'exec python3 ' + shlex.quote(str(script)) + ' --run ' + shlex.quote(str(work))
    return text.replace(lines[0], prefix + ' --command ' + lines[0][5:], 1).encode()


def settings(home):
    result = {}
    root = home / '.local/share/shadPS4'
    for path in (root / 'config.json', root / 'custom_configs/CUSA36843.json'):
        if path.is_file():
            try:
                data = json.loads(path.read_text())
                result[str(path)] = {key: data.get(key) for key in ('GPU', 'Vulkan', 'Audio')}
            except (OSError, ValueError) as error:
                result[str(path)] = {'capture_error': str(error)}
    return result


def analyze(logfile):
    events, last_counts, vuids = Counter(), {}, Counter()
    examples, times = [], []
    render_errors = valid_initializations = invalid_markers = 0
    if logfile.exists():
        with logfile.open(errors='replace') as stream:
            for line in stream:
                match = re.search(r'GRAPHICS_DIAG ms=(\d+) event=([\w-]+) count=(\d+)', line)
                if match:
                    event = match[2]
                    events[event] += 1
                    last_counts[event] = max(last_counts.get(event, 0), int(match[3]))
                    times.append(int(match[1]))
                    if event == 'depth-growth':
                        fields = dict(re.findall(r'([\w-]+)=(\d+)(?=\s|$)', line))
                        try:
                            valid = (fields['upload-recorded'] == '1' and
                                     fields['copy-returned'] == '1' and fields['dst-samples'] == '1' and
                                     (int(fields['dst-layers']) > int(fields['src-layers']) or
                                      int(fields['dst-mips']) > int(fields['src-mips'])))
                        except KeyError:
                            valid = False
                        valid_initializations += bool(valid)
                        invalid_markers += not valid
                vuids.update(re.findall(r'VUID-[\w-]+', line))
                error = (any(tag in line for tag in ('[Render', '[Shader', '[VideoCore')) and
                         any(tag in line for tag in ('<Error>', '<Critical>', 'Assertion Failed')))
                render_errors += bool(error)
                if (error or 'event=depth-growth' in line or 'VUID-' in line) and len(examples) < 40:
                    examples.append(line.strip())
    if invalid_markers:
        path_result = 'INVALID_EVIDENCE_RECORD'
    elif events['depth-growth-uninitialized']:
        path_result = 'UNINITIALIZED_PATH_OBSERVED'
    elif events['depth-growth']:
        path_result = 'INITIALIZATION_PATH_OBSERVED'
    else:
        path_result = 'NOT_OBSERVED'
    return {'graphics_diagnostics_started': bool(events['enabled']),
            'depth_growth_result': path_result, 'sampled_records': dict(events),
            'valid_initialization_records': valid_initializations,
            'invalid_initialization_records': invalid_markers,
            'event_count_lower_bounds': last_counts, 'validation_vuids': dict(vuids),
            'renderer_error_records': render_errors, 'examples': examples,
            'last_graphics_event_ms': max(times) if times else None,
            'visual_result': 'UNVERIFIED_REQUIRES_USER_FEEDBACK',
            'scope': 'Markers establish CPU upload/copy recording, not GPU readback or visual '
                     'correctness. Counts are sampled lower bounds. Quiet logs do not prove success. '
                     'Existing Vulkan validation settings are unchanged.'}


def restore(wrapper, modified, original, mode):
    if not wrapper.is_symlink() and wrapper.is_file():
        current = wrapper.read_bytes()
        if current == original:
            return True
        if current == modified:
            atomic_write(wrapper, original, mode)
            return True
    return False


def running_identity(pid):
    try:
        return {'running_executable': os.readlink(f'/proc/{pid}/exe'),
                'running_binary_sha256': digest(f'/proc/{pid}/exe')}
    except OSError as error:
        return {'identity_error': str(error)}


def run_game(work, command):
    selected = json.loads((work / 'selected-install.json').read_text())
    if (not command or str(Path(command[0]).resolve(strict=True)) != selected['binary'] or
            digest(command[0]) != selected['binary_sha256']):
        raise RuntimeError('Launch does not match the captured installation; no game started.')
    fd = os.open(work / 'claimed', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    env = os.environ.copy()
    env['SHADPS4_GRAPHICS_DIAGNOSTICS'] = '1'
    with (work / 'emulator.log').open('xb') as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        record = {'pid': process.pid, 'started_unix': time.time(), 'command': command,
                  'graphics_diagnostics_requested': True}
        record.update(running_identity(process.pid))
        write_json(work / 'process.json', record)
        returncode = process.wait()
        log.flush()
    write_json(work / 'exit.json', {'returncode': returncode, 'ended_unix': time.time()})
    return returncode


def timeout_reason(wait_elapsed, game_elapsed, wait_seconds, session_seconds):
    if game_elapsed is None:
        if wait_seconds > 0 and wait_elapsed >= wait_seconds:
            return 'No game launch within the requested waiting period.'
    elif session_seconds > 0 and game_elapsed >= session_seconds:
        return 'Session limit reached; game was not stopped. Capture is partial.'
    return None


def nonnegative_minutes(value):
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError('Minutes must be zero or positive; zero disables the limit.')
    return parsed


def collect(home, revision, wait_minutes=0, session_minutes=30):
    if games():
        raise RuntimeError('Exit the game normally before arming capture. Nothing changed.')
    selected = selected_install(home, revision)
    wrapper = home / '.local/bin/shadps4-esde'
    if wrapper.is_symlink() or not wrapper.is_file():
        raise RuntimeError('Expected the existing regular ES-DE launcher; preserved.')
    original = wrapper.read_bytes()
    mode = stat.S_IMODE(wrapper.stat().st_mode)
    work = Path(tempfile.mkdtemp(prefix='shadps4-graphics-evidence-', dir=home))
    work.chmod(0o700)
    script = work / 'collect_graphics_evidence.py'
    shutil.copy2(__file__, script)
    modified = instrument(original, script, work)
    (work / 'launcher.original').write_bytes(original)
    (work / 'launcher.capture').write_bytes(modified)
    write_json(work / 'selected-install.json', selected)
    write_json(work / 'settings-before.json', settings(home))
    write_json(work / 'restore.json', {'wrapper': str(wrapper), 'mode': mode})
    interruption = None
    restored = False
    missing_since = None
    game_started = None
    try:
        if games() or wrapper.read_bytes() != original:
            raise RuntimeError('Game/launcher changed during preparation; preserved.')
        atomic_write(wrapper, modified, mode)
        print('CAPTURE_ARMED=' + str(work), flush=True)
        print('CAPTURE_REVISION=' + selected['revision'], flush=True)
        print('CAPTURE_BINARY_SHA256=' + selected['binary_sha256'], flush=True)
        print('WAIT_FOR_LAUNCH=' + (str(wait_minutes) + ' minutes' if wait_minutes else
                                  'unlimited; Ctrl+C restores the launcher'), flush=True)
        print('NOW launch the ordinary game entry in ES-DE. Reproduce the graphics problem, '
              'then EXIT THE GAME normally. This command creates the report after exit.', flush=True)
        print('RECOVER_LAUNCHER=python3 ' + shlex.quote(str(script)) +
              ' --restore ' + shlex.quote(str(work)), flush=True)
        started = time.monotonic()
        while not (work / 'exit.json').exists():
            now = time.monotonic()
            if (work / 'worker-error.json').exists():
                interruption = 'Launch worker failed; see worker-error.json.'
                break
            if (work / 'process.json').exists():
                if game_started is None:
                    game_started = now
                observed = json.loads((work / 'process.json').read_text())
                if not Path(f"/proc/{observed['pid']}").exists():
                    missing_since = missing_since or time.monotonic()
                    if time.monotonic() - missing_since > 2:
                        interruption = 'Game ended without an exit record; capture is partial.'
                        break
                else:
                    missing_since = None
            elif (work / 'claimed').exists() and time.time() - (work / 'claimed').stat().st_mtime > 20:
                interruption = 'Launch was claimed but no game process was recorded.'
                break
            interruption = timeout_reason(now - started,
                                          None if game_started is None else now - game_started,
                                          wait_minutes * 60, session_minutes * 60)
            if interruption:
                break
            time.sleep(0.5)
    except (KeyboardInterrupt, OSError, RuntimeError) as error:
        interruption = str(error) or 'Capture interrupted; game was not stopped.'
    finally:
        restored = restore(wrapper, modified, original, mode)
        print('LAUNCHER_RESTORED=' + str(restored), flush=True)
    write_json(work / 'settings-after.json', settings(home))
    report = analyze(work / 'emulator.log')
    process = json.loads((work / 'process.json').read_text()) if (work / 'process.json').exists() else {}
    exited = json.loads((work / 'exit.json').read_text()) if (work / 'exit.json').exists() else {}
    renderer_logs = []
    for name in ('shad_log.txt', 'shadps4.log'):
        path = home / '.local/share/shadPS4/log' / name
        if path.is_file() and process and path.stat().st_mtime >= process['started_unix'] - 1:
            if path.stat().st_size <= 110 * 1024 * 1024:
                shutil.copy2(path, work / ('renderer-' + name))
                renderer_logs.append(name)
    identity = (process.get('running_executable') == selected['binary'] and
                process.get('running_binary_sha256') == selected['binary_sha256'])
    report.update(selected_install=selected, process=process, exit=exited,
                  exact_running_binary_verified=identity, launcher_restored=restored,
                  fresh_renderer_logs=renderer_logs,
                  interruption=interruption, report_created_unix=time.time())
    report['capture_complete'] = bool(identity and exited and not interruption and
                                      report['graphics_diagnostics_started'])
    report['session_exit'] = ('NOT_OBSERVED' if not exited else
                              'NORMAL' if exited['returncode'] == 0 else 'ABNORMAL')
    report['settings_unchanged'] = ((work / 'settings-before.json').read_bytes() ==
                                    (work / 'settings-after.json').read_bytes())
    write_json(work / 'summary.json', report)
    archive = work.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz') as output:
        output.add(work, arcname=work.name)
    archive.chmod(0o600)
    print('GRAPHICS_REPORT=' + str(archive), flush=True)
    print('CAPTURE_COMPLETE=' + str(report['capture_complete']), flush=True)
    print('DEPTH_GROWTH_RESULT=' + report['depth_growth_result'], flush=True)
    print('VISUAL_RESULT=UNVERIFIED_REQUIRES_USER_FEEDBACK', flush=True)
    if not report['capture_complete'] or not restored:
        raise RuntimeError('Capture incomplete or launcher changed; upload the report for review.')


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--expected-revision')
    mode.add_argument('--run', type=Path)
    mode.add_argument('--restore', type=Path)
    parser.add_argument('--command', nargs=argparse.REMAINDER)
    parser.add_argument('--wait-minutes', type=nonnegative_minutes, default=0,
                        help='Wait for launch; 0 waits until launch or Ctrl+C (default).')
    parser.add_argument('--session-minutes', type=nonnegative_minutes, default=30,
                        help='Capture limit after game launch; 0 disables this limit.')
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError('Run as your desktop user, without sudo.')
    if args.run:
        try:
            return run_game(args.run, args.command)
        except (OSError, RuntimeError, ValueError) as error:
            write_json(args.run / 'worker-error.json', {'error': str(error)})
            raise
    root = Path.home() / '.local/state/shadps4-ngs2'
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.restore:
            data = json.loads((args.restore / 'restore.json').read_text())
            if not restore(Path(data['wrapper']), (args.restore / 'launcher.capture').read_bytes(),
                           (args.restore / 'launcher.original').read_bytes(), data['mode']):
                raise RuntimeError('Launcher changed separately; preserved.')
            print('LAUNCHER_RESTORED=True')
            return 0
        for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, interrupted)
        collect(Path.home(), args.expected_revision, args.wait_minutes, args.session_minutes)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print('GRAPHICS_CAPTURE_RESULT=FAIL: ' + str(error), flush=True)
        raise SystemExit(1)
