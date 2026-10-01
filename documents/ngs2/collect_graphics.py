#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import io
import json
import os
from pathlib import Path
import re
import tarfile
import time


def diagnostic_paths(home, processes):
    revisions = set()
    for process in processes:
        release = Path(process['executable']).parent
        if release.parent == home / 'Applications/shadps4/releases':
            match = re.fullmatch(r'ngs2-([0-9a-f]{8})', release.name)
            if match:
                revisions.add(match[1])
    if processes:
        # A running core takes precedence over the selected next-launch entry.
        return [home / f'ngs2-diagnostic-{revision}.log' for revision in sorted(revisions)]
    try:
        wrapper = (home / '.local/bin/shadps4-esde').read_text()
        revisions.update(re.findall(r'^\s*# NGS2 isolated core selection: ([0-9a-f]{40})\s*$',
                                    wrapper, re.MULTILINE))
    except OSError:
        pass
    return [home / f'ngs2-diagnostic-{revision[:8]}.log' for revision in sorted(revisions)]


def memory_bytes(value):
    parts = value.split()
    if not parts or len(parts) > 2:
        return None
    units = {'B': 1, 'KiB': 1024, 'MiB': 1024 ** 2, 'GiB': 1024 ** 3}
    unit = parts[1] if len(parts) == 2 else 'B'
    if not parts[0].isdigit() or unit not in units:
        return None
    return int(parts[0]) * units[unit]


def collect_gpu_clients(proc_root=Path('/proc')):
    # A duplicated/shared DRM fd is one client, even across processes. See
    # https://docs.kernel.org/gpu/drm-usage-stats.html for accounting semantics.
    clients = {}
    denied = set()
    scanned = 0
    incomplete = False
    deadline = time.monotonic() + 8
    for process in sorted(proc_root.iterdir()):
        if not process.name.isdigit():
            continue
        if scanned >= 32768 or time.monotonic() >= deadline:
            incomplete = True
            break
        try:
            owner = {'pid': int(process.name), 'uid': process.stat().st_uid,
                     'name': (process / 'comm').read_text(errors='replace').strip()}
            with os.scandir(process / 'fdinfo') as entries:
                for entry in entries:
                    if scanned >= 32768 or time.monotonic() >= deadline:
                        incomplete = True
                        break
                    if not entry.name.isdigit():
                        continue
                    scanned += 1
                    try:
                        with open(entry.path, encoding='utf-8', errors='replace') as source:
                            lines = source.read(16384).splitlines()
                    except PermissionError:
                        denied.add(int(process.name))
                        continue
                    except OSError:
                        continue  # Processes and descriptors may exit during capture.
                    fields = {}
                    for line in lines:
                        key, separator, value = line.partition(':')
                        if separator and key.startswith('drm-'):
                            fields[key] = value.strip()
                    if 'drm-driver' not in fields:
                        continue
                    client_id = fields.get('drm-client-id')
                    device = fields.get('drm-pdev')
                    key = ((fields['drm-driver'], device, client_id) if client_id else
                           ('fd', process.name, entry.name))
                    if key not in clients:
                        # drm-memory-* is AMD's alias of drm-resident-*. Never add
                        # both, or treat drm-total-* (requested buffers) as residency.
                        resident = {}
                        for prefix in ('drm-memory-', 'drm-resident-'):
                            for field, value in fields.items():
                                if field.startswith(prefix):
                                    parsed = memory_bytes(value)
                                    if parsed is not None:
                                        resident[field[len(prefix):]] = parsed
                        clients[key] = {
                            'driver': fields['drm-driver'], 'pci_device': device,
                            'client_id': client_id, 'deduplicated': client_id is not None,
                            'processes': [], 'fd_count': 0, 'fields': fields,
                            'resident_bytes': resident,
                        }
                    client = clients[key]
                    client['fd_count'] += 1
                    if owner not in client['processes']:
                        client['processes'].append(owner)
        except PermissionError:
            denied.add(int(process.name))
        except OSError:
            continue
    return {
        'clients': list(clients.values()), 'fdinfo_scanned': scanned,
        'permission_denied_processes': len(denied), 'scan_limit_reached': incomplete,
        'notes': [
            'Read-only snapshot of accessible DRM clients; other users or containers may be hidden.',
            'One record per device/client ID. Missing client IDs cannot be deduplicated.',
            'Shared buffers can occur in multiple clients; do not sum clients as device usage.',
        ],
    }


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
    report = {'collector_version': 3, 'captured_unix': now, 'processes': [], 'gpu': [], 'logs': [],
              'log_settings': [], 'renderer_settings': [], 'warnings': []}
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            exe = (process / 'exe').resolve(strict=True)
            if exe.name.lower().removesuffix(' (deleted)') not in ('shadps4', 'shadps4.exe'):
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
                report['renderer_settings'].append({
                    'path': str(path),
                    'settings': {group: data[group] for group in ('GPU', 'Vulkan') if group in data},
                })
            except (OSError, ValueError) as error:
                report['warnings'].append(f'{path}: {error}')

    for card in sorted(Path('/sys/class/drm').glob('card*')):
        if not card.name[4:].isdigit():
            continue
        device = card / 'device'
        values = {'card': card.name, 'sysfs_path': str(device),
                  'pci_device': device.resolve().name}
        for key in ('vendor', 'device', 'mem_info_vram_total', 'mem_info_vram_used',
                    'mem_info_gtt_total', 'mem_info_gtt_used', 'gpu_busy_percent'):
            try:
                values['device_id' if key == 'device' else key] = (device / key).read_text().strip()
            except OSError:
                pass
        report['gpu'].append(values)

    report['gpu_clients'] = collect_gpu_clients()
    if report['gpu_clients']['permission_denied_processes']:
        report['warnings'].append('Some processes deny fdinfo access; GPU ownership is partial.')
    if report['gpu_clients']['scan_limit_reached']:
        report['warnings'].append('GPU client scan reached its time/file limit; ownership is partial.')
    if not report['gpu_clients']['clients']:
        report['warnings'].append('No accessible DRM client statistics found.')

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
            diagnostics = diagnostic_paths(home, report['processes'])
            report['expected_diagnostic_paths'] = [str(path) for path in diagnostics]
            for diagnostic in diagnostics:
                if diagnostic.is_file():
                    add_log(archive, diagnostic, diagnostic.name)
                else:
                    report['warnings'].append('Current-revision trace not found: ' + str(diagnostic))
            if not available:
                report['warnings'].append('No renderer logs found in the detected user directories.')
            add_bytes(archive, 'capture-info.json', json.dumps(report, indent=2).encode())
    print('GRAPHICS_REPORT=' + str(output))
    print('Renderer logs collected:', min(len(available), 6))
    for process in report['processes']:
        print('RUNNING_CORE=' + process['executable'])
    print('Accessible GPU clients (resident memory; shared buffers can overlap):')
    clients = sorted(report['gpu_clients']['clients'], reverse=True,
                     key=lambda item: item['resident_bytes'].get('vram', 0))
    for client in clients[:12]:
        owners = ', '.join(f"{owner['pid']} {owner['name']!r}" for owner in client['processes'])
        memory = client['resident_bytes']
        values = ' '.join(f'{region}={memory[region] / 1024 ** 2:.1f} MiB'
                          for region in ('vram', 'gtt') if region in memory)
        print(f"  {client['driver']} {client['pci_device']} {owners}: {values or 'no memory counters'}")
    for warning in report['warnings']:
        print('NOTE:', warning)
    print('Upload the GRAPHICS_REPORT file. No emulator settings or installation were changed.')


if __name__ == '__main__':
    main()
