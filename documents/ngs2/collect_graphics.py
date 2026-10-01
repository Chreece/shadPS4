#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import io
import json
import os
from pathlib import Path
import tarfile
import time


def main():
    home = Path.home()
    now = time.time()
    stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(now))
    output = home / f'shadps4-graphics-{stamp}-{os.getpid()}.tar.gz'
    roots = {home / '.local/share/shadPS4', home / 'Applications/shadps4/user'}
    data_home = os.environ.get('XDG_DATA_HOME')
    if data_home:
        roots.add(Path(data_home) / 'shadPS4')
    candidates = set()
    report = {'captured_unix': now, 'processes': [], 'gpu': [], 'logs': [],
              'log_settings': [], 'warnings': []}
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            exe = (process / 'exe').resolve(strict=True)
            if 'shadps4' not in exe.name.lower():
                continue
            cwd = (process / 'cwd').resolve(strict=True)
            report['processes'].append({'pid': int(process.name), 'executable': str(exe),
                                        'cwd': str(cwd)})
            roots.add(cwd / 'user')
            roots.add(exe.parent / 'user')
            for item in (process / 'environ').read_bytes().split(b'\0'):
                if item.startswith(b'XDG_DATA_HOME='):
                    value = os.fsdecode(item.split(b'=', 1)[1])
                    if value:
                        roots.add(Path(value) / 'shadPS4')
            for fd in (process / 'fd').iterdir():
                try:
                    path = fd.resolve(strict=True)
                    if path.parent.name == 'log' and path.suffix in ('.txt', '.log'):
                        candidates.add(path)
                        roots.add(path.parent.parent)
                except OSError:
                    pass
        except OSError:
            continue

    if not report['processes']:
        report['warnings'].append('No running shadPS4 process found; logs may be from an older run.')
    for root in sorted(roots):
        for name in ('shad_log.txt', 'CUSA36843.log'):
            path = root / 'log' / name
            if path.is_file():
                candidates.add(path.resolve())
        for path in (root / 'config.json', root / 'custom_configs/CUSA36843.json'):
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text())
                report['log_settings'].append({'path': str(path), 'Log': data.get('Log', {})})
            except (OSError, ValueError) as error:
                report['warnings'].append(f'{path}: {error}')

    for device in sorted(Path('/sys/class/drm').glob('card[0-9]*/device')):
        values = {'device': str(device)}
        for key in ('vendor', 'device', 'mem_info_vram_total', 'mem_info_vram_used',
                    'mem_info_gtt_total', 'mem_info_gtt_used', 'gpu_busy_percent'):
            try:
                values[key] = (device / key).read_text().strip()
            except OSError:
                pass
        report['gpu'].append(values)

    def add_bytes(archive, name, content):
        info = tarfile.TarInfo(name)
        info.size = len(content)
        info.mtime = int(now)
        info.mode = 0o600
        archive.addfile(info, io.BytesIO(content))

    def add_log(archive, path, name):
        try:
            with path.open('rb') as source:
                stat = os.fstat(source.fileno())
                limit = 4 * 1024 * 1024
                if stat.st_size <= limit:
                    content = source.read(stat.st_size)
                else:
                    content = source.read(256 * 1024)
                    content += b'\n[collector: middle omitted; final 3 MiB follows]\n'
                    source.seek(max(0, stat.st_size - 3 * 1024 * 1024))
                    content += source.read(3 * 1024 * 1024)
                add_bytes(archive, name, content)
                report['logs'].append({'source': str(path), 'archive_name': name,
                                       'original_bytes': stat.st_size, 'modified_unix': stat.st_mtime,
                                       'age_seconds': round(now - stat.st_mtime, 1),
                                       'excerpt': stat.st_size > limit})
        except OSError as error:
            report['warnings'].append(f'{path}: {error}')

    available = []
    for path in candidates:
        try:
            available.append((path.stat().st_mtime, path))
        except OSError:
            pass
    with output.open('xb') as raw:
        os.fchmod(raw.fileno(), 0o600)
        with tarfile.open(fileobj=raw, mode='w:gz') as archive:
            for index, (_, path) in enumerate(sorted(available, reverse=True)[:6]):
                add_log(archive, path, f'renderer-{index + 1}-{path.name}')
            diagnostic = home / 'ngs2-diagnostic-f1c1c790.log'
            if diagnostic.is_file():
                add_log(archive, diagnostic, diagnostic.name)
            if not available:
                report['warnings'].append('No renderer logs found in the detected user directories.')
            add_bytes(archive, 'capture-info.json', json.dumps(report, indent=2).encode())
    print('GRAPHICS_REPORT=' + str(output))
    print('Renderer logs collected:', min(len(available), 6))
    for warning in report['warnings']:
        print('NOTE:', warning)
    print('Upload the GRAPHICS_REPORT file. No emulator settings or installation were changed.')


if __name__ == '__main__':
    main()
