#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Capture one guarded launch using the live ES-DE process environment."""
from datetime import datetime, timezone
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET

REVISION = '286d0cca483ce80f9d4a4fe98d4620b6b003e0ca'
ENV_KEYS = ('DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR',
            'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'SDL_VIDEODRIVER', 'SDL_VIDEO_DRIVER',
            'LD_LIBRARY_PATH', 'LD_PRELOAD', 'APPDIR', 'APPIMAGE', 'PATH', 'PULSE_SINK')
ENV_KEYS += ('RADV_DEBUG', 'RADV_PERFTEST', 'VK_INSTANCE_LAYERS', 'VK_ICD_FILENAMES',
             'VK_DRIVER_FILES')


def launch_environment(frontend, sync_shaders):
    environment = dict(frontend)
    environment.pop('SHADPS4_GUARD_PARENT_PID', None)
    if sync_shaders:
        if environment.get('RADV_DEBUG', '').strip():
            raise RuntimeError('ES-DE already supplies RADV_DEBUG; preserve it and do not mix tests.')
        environment['RADV_DEBUG'] = 'syncshaders'
    return environment


def processes():
    result = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
            name = (path / 'comm').read_text().strip().lower()
            exe = str((path / 'exe').readlink()).removesuffix(' (deleted)')
            if name != 'es-de' and Path(exe).name.lower() not in ('shadps4', 'shadps4.exe'):
                continue
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] in ('Z', 'X'):
                continue
            environment = dict(os.fsdecode(item).split('=', 1) for item in
                               (path / 'environ').read_bytes().split(b'\0') if b'=' in item)
            result.append({'pid': int(path.name), 'name': name, 'exe': exe,
                           'cwd': str((path / 'cwd').readlink()), 'environment': environment})
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda p: p['pid'], reverse=True)


def public_processes(items):
    return [dict(p, environment={k: p['environment'][k] for k in ENV_KEYS
                                 if k in p['environment']}) for p in items]


def tail(path, limit=2 * 1024 * 1024):
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - limit))
        return stream.read(limit)


def snapshot(path):
    try:
        info = path.stat()
        return {'path': str(path), 'size': info.st_size, 'mtime': info.st_mtime,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if info.st_size < 1024 * 1024 else None}
    except OSError as error:
        return {'path': str(path), 'error': str(error)}


def main(sync_shaders=False):
    if os.geteuid() == 0 or sys.platform != 'linux':
        raise RuntimeError('Run as your normal desktop user, without sudo.')
    home = Path.home()
    output = Path(tempfile.mkdtemp(prefix='ngs2-launch-check-', dir=home))
    wrapper = home / '.local/bin/shadps4-esde'
    helper = home / '.local/lib/shadps4-session-guard/guard.py'
    trace = home / 'ngs2-diagnostic-286d0cca.log'
    before = processes()
    report = {'started_utc': datetime.now(timezone.utc).isoformat(),
              'test': 'radv-syncshaders' if sync_shaders else 'launch-only',
              'before': public_processes(before), 'trace_before': snapshot(trace)}
    frontend = [p for p in before if p['name'] == 'es-de']
    cores = [p for p in before if Path(p['exe']).name.lower() in ('shadps4', 'shadps4.exe')]
    child = None
    try:
        if cores:
            raise RuntimeError('An emulator is already running; no additional launch was requested.')
        if not frontend:
            raise RuntimeError('No live ES-DE process found; keep Moonlight/ES-DE open.')
        displays = {p['environment'].get('DISPLAY') for p in frontend}
        if len(displays) != 1 or not re.fullmatch(r'(?:unix)?:\d+(?:\.\d+)?', next(iter(displays)) or ''):
            raise RuntimeError('The live ES-DE display is ambiguous or not local; no launch requested.')
        body = wrapper.read_text()
        prefix = ('# SHADPS4_SESSION_GUARD_V1\n'
                  'if [[ "${SHADPS4_GUARD_PARENT_PID:-}" != "$PPID" ]]; then\n'
                  f'    exec python3 {shlex.quote(str(helper))} run "$0" "$@"\n'
                  'fi\nunset SHADPS4_GUARD_PARENT_PID\n'
                  '# END SHADPS4_SESSION_GUARD_V1\n')
        if (wrapper.is_symlink() or helper.is_symlink() or
                not body.partition('\n')[2].startswith(prefix) or
                '# NGS2_PROBE_DISPATCH_V1' not in body or
                '# NGS2 isolated core selection: ' + REVISION not in body):
            raise RuntimeError('Expected the guarded 286d0cca test launcher; no launch requested.')
        selected = frontend[0]
        report['frontend_pid_used'] = selected['pid']
        token = output / 'probe.ps4'
        token.write_text('CUSA36843|ngs2probe\n')
        environment = launch_environment(selected['environment'], sync_shaders)
        report['requested_environment'] = {k: environment[k] for k in ENV_KEYS if k in environment}
        with (output / 'launcher-console.log').open('wb') as console:
            child = subprocess.Popen(['/bin/bash', str(wrapper), str(token)],
                                     cwd=selected['cwd'], env=environment, stdin=subprocess.DEVNULL,
                                     stdout=console, stderr=subprocess.STDOUT,
                                     start_new_session=True, close_fds=True)
        report['launcher_pid'] = child.pid
        print('One guarded launch requested; checking startup for up to 12 seconds.', flush=True)
        for _ in range(24):
            if child.poll() is not None:
                break
            time.sleep(0.5)
        report['during'] = public_processes(processes())
        if sync_shaders:
            expected_exe = str(home / 'Applications/shadps4/releases/ngs2-286d0cca/shadps4')
            report['sync_observed_in_core'] = any(
                p['exe'] == expected_exe and p['environment'].get('RADV_DEBUG') == 'syncshaders'
                for p in report['during'])
        if sync_shaders and child.poll() is None:
            print('SYNC TEST: compare the same affected area, then exit the emulator normally.\n'
                  'This SSH command waits to collect the completed trace; it does not stop the game.\n'
                  'RADV_DEBUG=syncshaders applies only to this launch and may reduce performance.',
                  flush=True)
            child.wait()
        report['launcher_exit_code'] = child.poll()
    except (RuntimeError, OSError, ValueError) as error:
        report['launch_note'] = str(error)
    report['after'] = public_processes(processes())
    report['trace_after'] = snapshot(trace)
    report['trace_changed'] = report['trace_before'] != report['trace_after']
    entry = Path('/mnt/roms-all/ps4/Red Dead Redemption [NGS2 trace].ps4')
    report['esde_entry'] = snapshot(entry)
    try:
        with entry.open('rb') as stream:
            report['esde_entry']['first_line'] = repr(stream.readline(512))
    except OSError as error:
        report['esde_entry']['read_error'] = str(error)
    configs = [home / '.emulationstation', home / 'ES-DE', home / '.config/ES-DE']
    for p in frontend:
        if p['environment'].get('XDG_CONFIG_HOME'):
            configs.append(Path(p['environment']['XDG_CONFIG_HOME']) / 'ES-DE')
    report['ps4_systems'] = []
    for config in dict.fromkeys(configs):
        xml = config / 'custom_systems/es_systems.xml'
        if xml.is_file():
            try:
                for system in ET.parse(xml).getroot().findall('system'):
                    if system.findtext('name') == 'ps4':
                        report['ps4_systems'].append({'source': str(xml), 'xml': ET.tostring(system, encoding='unicode')})
            except (OSError, ET.ParseError) as error:
                report.setdefault('warnings', []).append(str(error))
    files = [wrapper, helper, helper.with_name('display_session.py'),
             home / 'Applications/shadps4/releases/ngs2-286d0cca/run_diagnostic.py', trace,
             output / 'launcher-console.log',
             home / '.local/share/shadPS4/log/shad_log.txt',
             home / '.local/share/shadPS4/log/shadps4.log']
    for config in dict.fromkeys(configs):
        files.extend([config / 'es_log.txt', config / 'logs/es_log.txt',
                      config / 'es_log.txt.bak', config / 'logs/es_log.txt.bak'])
    report['files'] = []
    archive = output.with_suffix('.tar.gz')
    with archive.open('xb') as raw:
        os.fchmod(raw.fileno(), 0o600)
        with tarfile.open(fileobj=raw, mode='w:gz') as bundle:
            def add(name, data):
                item = tarfile.TarInfo(name)
                item.mode = 0o600
                item.size = len(data)
                bundle.addfile(item, io.BytesIO(data))
            for index, path in enumerate(dict.fromkeys(files)):
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    name = f'{index}-{path.name}'
                    report['files'].append(dict(snapshot(path), archive_name=name))
                    add(name, tail(path))
                except OSError as error:
                    report.setdefault('warnings', []).append(str(error))
            add('launch-report.json', json.dumps(report, indent=2).encode())
    print('LAUNCH_REPORT=' + str(archive), flush=True)
    if report.get('launch_note'):
        print(report['launch_note'])
    elif child is not None:
        print('LAUNCHER_EXIT=' + str(child.poll()))
        console = output / 'launcher-console.log'
        if console.exists():
            print(tail(console, 5000).decode(errors='replace'))
    print('Upload LAUNCH_REPORT. No installation/settings changed and no process was stopped.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--radv-sync-shaders', action='store_true',
                        help='Use RADV syncshaders for one launch and collect after normal exit.')
    args = parser.parse_args()
    try:
        main(args.radv_sync_shaders)
    except (RuntimeError, OSError, ValueError) as error:
        print('LAUNCH_CAPTURE=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
