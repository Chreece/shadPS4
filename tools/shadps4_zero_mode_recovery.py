#!/usr/bin/env python3
"""Undo the global readback test setting without replacing the emulator.

revert: restore only GPU.readbacks_mode to zero; retire the old mutating helpers.
capture: request the existing diagnostic's bounded draw trace at mode zero.
collect: archive new runtime evidence without config files, saves or binaries.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import uuid

OLD_HELPERS = (
    'shadps4_readback_docker_trial.py', 'shadps4_ui_readback_control.py',
    'shadps4_restore_ui_fonts.py', 'shadps4_ui_draw_diagnostic.py',
)
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key: ' + key)
        value[key] = item
    return value


def parse(data: bytes) -> dict:
    return json.loads(data.decode('utf-8-sig'), object_pairs_hook=unique_object)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def json_write(path: Path, data: dict) -> None:
    atomic(path, (json.dumps(data, indent=2) + '\n').encode())


def zero_mode_bytes(data: bytes) -> tuple[bytes, int]:
    before = parse(data)
    value = before.get('GPU', {}).get('readbacks_mode')
    if type(value) is not int or value not in (0, 1, 2):
        raise ValueError('Unexpected GPU.readbacks_mode schema; nothing changed')
    if value == 0:
        return data, value
    # Locate the two object keys using a JSON decoder, not a broad text replacement.
    text = data.decode('utf-8')
    decoder = json.JSONDecoder(object_pairs_hook=unique_object)

    def skip(i):
        while i < len(text) and text[i].isspace():
            i += 1
        return i

    def field(start, wanted):
        i = skip(start)
        if text[i] != '{':
            raise ValueError('Expected a JSON object')
        i += 1
        while True:
            i = skip(i)
            if text[i] == '}':
                raise ValueError('Missing JSON key: ' + wanted)
            key, end = decoder.raw_decode(text, i)
            i = skip(end)
            if text[i] != ':':
                raise ValueError('Invalid JSON separator')
            lo = skip(i + 1)
            _, hi = decoder.raw_decode(text, lo)
            if key == wanted:
                return lo, hi
            i = skip(hi)
            if text[i] == ',':
                i += 1
            elif text[i] == '}':
                raise ValueError('Missing JSON key: ' + wanted)
            else:
                raise ValueError('Invalid object separator')

    root = 1 if text.startswith('\ufeff') else 0
    gpu_start, _ = field(root, 'GPU')
    lo, hi = field(gpu_start, 'readbacks_mode')
    modified = (text[:lo] + '0' + text[hi:]).encode('utf-8')
    expected = parse(data)
    expected['GPU']['readbacks_mode'] = 0
    if parse(modified) != expected:
        raise ValueError('Unexpected unrelated configuration change')
    return modified, value


class Recovery:
    def __init__(self, home: Path):
        self.home = home
        self.config = home / '.local/share/shadPS4/config.json'
        self.binary = home / 'Applications/shadps4/shadps4'
        self.trial = home / '.cache/shadps4-clean-verified-main/readback-docker-trial'
        self.root = self.trial / 'zero-mode-recovery'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.record = self.root / 'record.json'
        self.sessions = home / '.local/state/shadps4-playtest-logs'

    def processes(self):
        found = []
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            try:
                exe = os.readlink(path / 'exe').removesuffix(' (deleted)')
                comm = (path / 'comm').read_text().strip().lower()
                if Path(exe).name.lower() == 'shadps4' or comm == 'shadps4':
                    found.append((path, exe))
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
        return found

    def require_closed(self):
        running = self.processes()
        if running:
            raise RuntimeError('Close shadPS4 normally first. No process was killed. PIDs=' +
                               ','.join(p.name for p, _ in running))

    def mode(self):
        data = parse(self.config.read_bytes())
        mode = data.get('GPU', {}).get('readbacks_mode')
        if type(mode) is not int or mode not in (0, 1, 2):
            raise RuntimeError('Unexpected readback configuration')
        return mode

    def retire_helpers(self):
        backup_dir = self.root / 'retired-helpers'
        backup_dir.mkdir(exist_ok=True, mode=0o700)
        stub = ("#!/usr/bin/env python3\n"
                "raise SystemExit('RETIRED: this test helper could change global settings or "
                "replace the retained emulator. Use shadps4_zero_mode_recovery.py collect "
                "for evidence. No changes performed.')\n").encode()
        retired = []
        for name in OLD_HELPERS:
            path = self.home / '.cache' / name
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_file():
                raise RuntimeError('Refusing to replace non-regular helper: ' + str(path))
            original = path.read_bytes()
            if original != stub:
                backup = backup_dir / (name + '.' + hashlib.sha256(original).hexdigest() + '.txt')
                if not backup.exists():
                    atomic(backup, original)
                atomic(path, stub, 0o700)
            retired.append(name)
        return retired

    def revert(self):
        self.require_closed()
        if self.config.is_symlink() or not self.config.is_file():
            raise RuntimeError('Expected the recorded regular config.json; no other path was edited')
        current = self.config.read_bytes()
        replacement, mode = zero_mode_bytes(current)
        original_binary = digest(self.binary) if self.binary.is_file() else None
        record = parse(self.record.read_bytes()) if self.record.exists() else {
            'started': time.time(), 'config': str(self.config), 'mode_before': mode,
            'retained_binary_sha256': original_binary,
        }
        backup = self.root / ('config-before-' + hashlib.sha256(current).hexdigest() + '.json')
        if mode != 0 and not backup.exists():
            atomic(backup, current)
        self.require_closed()
        if self.config.read_bytes() != current:
            raise RuntimeError('Config changed concurrently; rollback not applied')
        if replacement != current:
            atomic(self.config, replacement, stat.S_IMODE(self.config.stat().st_mode))
        if self.mode() != 0:
            raise RuntimeError('Readback rollback did not verify')
        # These backups are private and are never included in the report archive.
        record['status'] = 'global_mode_restored'
        record['mode_after'] = 0
        record['reverted_at'] = time.time()
        json_write(self.record, record)
        record['retired_helpers'] = self.retire_helpers()
        record['status'] = 'complete'
        after_binary = digest(self.binary) if self.binary.is_file() else None
        if after_binary != original_binary:
            raise RuntimeError('Another process changed the binary during rollback; no binary was written by this helper')
        record['retained_binary_sha256'] = original_binary
        json_write(self.record, record)
        print('GLOBAL_READBACKS_MODE=0')
        print('CONFIG_CHANGE=GPU.readbacks_mode ONLY')
        print('INSTALLED_BINARY=UNCHANGED')
        print('BINARY_SHA256=' + str(original_binary))
        print('OLD_SETTING_CHANGING_HELPERS=RETIRED')
        print('NO_GAME_OR_GLOBAL_DEFAULT_OVERRIDE_ADDED')
        print('LOCAL_RECOVERY_RECORD=' + str(self.record))

    def collect(self):
        record = parse(self.record.read_bytes())
        output = self.home / ('shadps4-zero-mode-' + time.strftime('%Y%m%d-%H%M%S') + '-' +
                              uuid.uuid4().hex[:6] + '.tar.gz')
        snapshots = self.root / ('evidence-' + uuid.uuid4().hex[:8])
        snapshots.mkdir(mode=0o700)
        audit = {'configured_readbacks_mode': self.mode(),
                 'current_binary_sha256': digest(self.binary) if self.binary.is_file() else None,
                 'started': record['started'], 'sessions': []}
        remaining_total = 256 * 1024 * 1024
        for directory in sorted(self.sessions.iterdir()) if self.sessions.is_dir() else []:
            meta = directory / 'session.meta'
            if directory.is_symlink() or not directory.is_dir() or not meta.is_file() or meta.is_symlink():
                continue
            text = meta.read_text(errors='replace')
            match = re.search(r'^started=(.+)$', text, re.M)
            if not match:
                continue
            try:
                started = dt.datetime.fromisoformat(match[1]).timestamp()
            except ValueError:
                continue
            if started < record['started']:
                continue
            dest = snapshots / directory.name
            dest.mkdir()
            result = {'session': directory.name}
            for name in ('session.meta', 'runtime.log'):
                src = directory / name
                if not src.is_file() or src.is_symlink():
                    continue
                limit = min(src.stat().st_size, 64 * 1024 * 1024, remaining_total)
                if limit <= 0:
                    result['archive_budget_reached'] = True
                    break
                content = src.open('rb')
                with content:
                    data = content.read(limit)
                (dest / name).write_bytes(data)
                remaining_total -= len(data)
                if name == 'runtime.log':
                    log = ANSI.sub('', data.decode(errors='replace'))
                    revision = re.search(r'\bRevision ([0-9a-f]{40})', log)
                    mode = re.search(r'GPU readbacksMode: (\d+)', log)
                    result.update(revision=revision[1] if revision else None,
                                  runtime_readbacks_mode=int(mode[1]) if mode else None,
                                  truncated=len(data) < src.stat().st_size,
                                  critical_count=log.count('<Critical>'))
            audit['sessions'].append(result)
        json_write(snapshots / 'audit.json', audit)
        with tarfile.open(output, 'w:gz') as archive:
            archive.add(snapshots, arcname='zero-mode/runtime')
            captures = self.root / 'captures'
            if captures.is_dir():
                archive.add(captures, arcname='zero-mode/captures')
        print('ARCHIVE=' + str(output))
        print(json.dumps(audit, indent=2))

    def capture(self):
        record = parse(self.record.read_bytes())
        if self.mode() != 0:
            raise RuntimeError('Capture requires mode 0; it will not change settings')
        processes = self.processes()
        if len(processes) != 1:
            raise RuntimeError('Open the retained emulator first; expected one shadPS4 process')
        proc, _ = processes[0]
        if digest(proc / 'exe') != record['retained_binary_sha256']:
            raise RuntimeError('Running binary differs from the retained diagnostic; no capture requested')
        trace_root = self.config.parent / 'ui-draw-trace'
        trace_root.mkdir(exist_ok=True)
        if trace_root.is_symlink():
            raise RuntimeError('Refusing a symlink trace directory')
        request = trace_root / 'request'
        if request.exists() or request.is_symlink():
            raise RuntimeError('A previous capture request is still pending; left unchanged')
        token = 'mode0-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
        started = time.time()
        atomic(request, (token + '\n').encode())
        trace = trace_root / (token + '.trace')
        print('ZERO_MODE_CAPTURE_ARMED=' + token, flush=True)
        complete = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if trace.is_file() and not trace.is_symlink():
                with trace.open('rb') as f:
                    f.seek(max(0, trace.stat().st_size - 4096))
                    if b'CAPTURE_END ' in f.read():
                        complete = True
                        break
            if not proc.exists():
                break
            time.sleep(.25)
        time.sleep(2)
        dest = self.root / 'captures' / token
        dest.mkdir(parents=True)
        if trace.is_file() and not trace.is_symlink():
            with trace.open('rb') as f:
                (dest / trace.name).write_bytes(f.read(32 * 1024 * 1024))
        if request.is_file() and not request.is_symlink() and request.read_text().strip() == token:
            request.unlink()
        images = self.config.parent / 'screenshots'
        count = 0
        for image in sorted(images.glob('*')) if images.is_dir() else []:
            if count >= 4:
                break
            if image.is_file() and not image.is_symlink() and image.stat().st_mtime >= started and \
               image.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'} and image.stat().st_size < 40 * 1024 * 1024:
                shutil.copy2(image, dest / ('game-' + str(count) + image.suffix))
                count += 1
        json_write(dest / 'capture.json', {'complete': complete, 'mode': 0, 'screenshots': count,
                                          'binary_sha256': record['retained_binary_sha256']})
        print('DRAW_CAPTURE=' + ('COMPLETE' if complete else 'INCOMPLETE'))
        self.collect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('revert', 'collect', 'capture'))
    args = parser.parse_args()
    r = Recovery(Path.home())
    with (r.trial / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            getattr(r, args.action)()
            return 0
        except (Exception, KeyboardInterrupt) as error:
            print('STOPPED=' + str(error), file=sys.stderr)
            return 1
        finally:
            print('SSH_SESSION=REMAINS_OPEN')


if __name__ == '__main__':
    raise SystemExit(main())
