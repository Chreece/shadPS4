#!/usr/bin/env python3
"""Locate the exact historical reference by checksum, then reuse its guarded runner.

Read-only search under ~/projects/shadps4. No other executable is an acceptable
fallback. A missing match produces an inventory and no game launch. The original
runner still owns locking, private profiles, bounded gameplay and preservation.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import time

PARENT_SHA = '155381d9f334a7c67c4de96b824859dad779c2178b62829e4b9cf857a356043c'
REFERENCE_SHA = '23203faed6bcfbc17e3129c489e16face8fa64266a94275e730e559ca518cb8e'
SKIP_DIRS = frozenset({'.git', 'externals', 'node_modules', '.cache', '__pycache__',
    'user', 'games', 'roms', 'savedata', 'saves', 'screenshots', 'shader', 'shaders',
    'logs', 'CMakeFiles', '_deps', 'evidence', 'verification'})
MAX_DIRECTORIES = 15000
MAX_CANDIDATES = 512
MAX_HASH_BYTES = 64 * 1024**3
MAX_SECONDS = 120


def identity(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def original_status(path):
    try:
        s = path.lstat()
        return {'status': 'symlink' if stat.S_ISLNK(s.st_mode) else
                         'regular' if stat.S_ISREG(s.st_mode) else 'not_regular',
                'bytes': s.st_size}
    except FileNotFoundError:
        return {'status': 'missing'}
    except OSError as e:
        return {'status': 'unreadable', 'error': str(e)}


def locate_reference(root, original, report_path, expected=REFERENCE_SHA,
                     max_seconds=MAX_SECONDS, max_hash_bytes=MAX_HASH_BYTES):
    root = root.resolve(strict=True)
    began = time.monotonic()
    report = {'expected_sha256': expected, 'root': str(root),
              'original_path': str(original), 'original_path_state': original_status(original),
              'status': 'SEARCHING', 'read_only': True, 'entries': [], 'hash_bytes': 0,
              'directories': 0, 'directory_errors': [], 'limits_hit': [],
              'scope_exclusions': sorted(SKIP_DIRS), 'selected_path': None}
    seen_files, seen_dirs = set(), set()

    def save():
        report['elapsed_seconds'] = round(time.monotonic() - began, 3)
        report_path.write_text(json.dumps(report, indent=2) + '\n')

    def stop_reason():
        if time.monotonic() - began >= max_seconds:
            return 'time_limit'
        if len(report['entries']) >= MAX_CANDIDATES:
            return 'candidate_limit'
        return None

    def inspect(path):
        row = {'path': str(path)}
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                row['status'] = 'OUTSIDE_PROJECT_REFUSED'
                report['entries'].append(row)
                return None
            row['resolved_path'] = str(resolved)
            flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0) | os.O_NONBLOCK
            fd = os.open(resolved, flags)
            with os.fdopen(fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                row['bytes'] = before.st_size
                if not stat.S_ISREG(before.st_mode):
                    row['status'] = 'NON_REGULAR_REFUSED'
                elif identity(before) in seen_files:
                    return None
                else:
                    seen_files.add(identity(before))
                    header = stream.read(64)
                    if len(header) < 20 or header[:6] != b'\x7fELF\x02\x01' or header[18:20] != b'\x3e\x00':
                        row['status'] = 'NOT_X86_64_ELF'
                    elif before.st_size + report['hash_bytes'] > max_hash_bytes:
                        row['status'] = 'HASH_BYTE_LIMIT'
                        report['limits_hit'].append('hash_byte_limit')
                    else:
                        stream.seek(0)
                        h = hashlib.sha256()
                        while True:
                            if time.monotonic() - began >= max_seconds:
                                row['status'] = 'TIME_LIMIT_DURING_HASH'
                                report['limits_hit'].append('time_limit')
                                break
                            block = stream.read(4 * 1024**2)
                            if not block:
                                after = os.fstat(stream.fileno())
                                if identity(before) != identity(after) or identity(after) != identity(resolved.stat()):
                                    row['status'] = 'CHANGED_DURING_HASH_REFUSED'
                                else:
                                    row['sha256'] = h.hexdigest()
                                    row['status'] = ('EXACT_MATCH' if os.access(resolved, os.X_OK) else
                                                     'EXACT_BYTES_NOT_EXECUTABLE') if row['sha256'] == expected else 'DIFFERENT_BINARY'
                                break
                            h.update(block)
                            report['hash_bytes'] += len(block)
        except (OSError, RuntimeError) as e:
            row['status'] = 'UNREADABLE'
            row['error'] = str(e)[:500]
        report['entries'].append(row)
        if row.get('status') == 'EXACT_MATCH':
            report['selected_path'] = row['resolved_path']
            return Path(row['resolved_path'])
        return None

    def eligible(name):
        lower = name.lower()
        return 'shadps4' in lower and not lower.endswith(('.tar.gz', '.zip', '.7z', '.log', '.json', '.py', '.sh', '.cpp', '.h', '.o'))

    def walk_error(error):
        if len(report['directory_errors']) < 100:
            report['directory_errors'].append(str(error)[:500])

    def candidates():
        # Try the recorded file, then its immediate siblings, then remaining
        # GoW/Upstream/project locations. Never traverse directory symlinks.
        yield original
        try:
            if original.parent.resolve(strict=True).is_relative_to(root):
                for p in sorted(original.parent.iterdir()):
                    if eligible(p.name):
                        yield p
        except OSError:
            pass
        for base in (root / 'GodOfWarRagnarok', root / 'Upstream', root):
            if not base.is_dir() or base.is_symlink():
                continue
            for directory, dirs, names in os.walk(base, topdown=True, followlinks=False, onerror=walk_error):
                d = Path(directory)
                try:
                    key = (d.stat().st_dev, d.stat().st_ino)
                except OSError as e:
                    walk_error(e)
                    dirs[:] = []
                    continue
                if key in seen_dirs:
                    dirs[:] = []
                    continue
                seen_dirs.add(key)
                report['directories'] += 1
                if report['directories'] > MAX_DIRECTORIES:
                    report['limits_hit'].append('directory_limit')
                    return
                if time.monotonic() - began >= max_seconds:
                    report['limits_hit'].append('time_limit')
                    return
                dirs[:] = sorted(n for n in dirs if n.lower() not in SKIP_DIRS and
                                 not n.lower().startswith(('playtest-', 'evidence-')) and
                                 not n.lower().endswith('-evidence') and not (d / n).is_symlink())
                for n in sorted(names):
                    if eligible(n):
                        yield d / n

    for path in candidates():
        reason = stop_reason()
        if reason:
            report['limits_hit'].append(reason)
            break
        found = inspect(path)
        if found:
            report['status'] = 'EXACT_REFERENCE_LOCATED'
            save()
            print('EXACT_REFERENCE_LOCATED=' + str(found), flush=True)
            print('EXACT_REFERENCE_SHA256=' + expected, flush=True)
            return found, report
        if report['entries'] and len(report['entries']) % 8 == 0:
            save()
            print('REFERENCE_SEARCH candidates=' + str(len(report['entries'])) +
                  ' hashed_gib=' + str(round(report['hash_bytes'] / 1024**3, 2)), flush=True)
    report['status'] = 'SEARCH_INCOMPLETE_NO_VERIFIED_MATCH' if report['limits_hit'] or report['directory_errors'] else 'NO_MATCH_IN_SCANNED_SCOPE'
    save()
    print('REFERENCE_RESOLUTION=' + report['status'] + '; GAME_NOT_LAUNCHED', flush=True)
    return None, report


def load_parent(path=None):
    path = path or Path.home() / 'projects/shadps4/scripts/gow_proven_reference.py'
    if not path.is_file() or path.is_symlink():
        raise RuntimeError('ORIGINAL_REFERENCE_RUNNER_MISSING=' + str(path))
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA:
        raise RuntimeError('ORIGINAL_REFERENCE_RUNNER_CHANGED_REFUSE')
    spec = importlib.util.spec_from_loader('verified_historical_reference', loader=None)
    module = importlib.util.module_from_spec(spec)
    # Worker/configure-private subprocesses re-enter this checksum-verified
    # wrapper, not the unresolved old filename-only launch path.
    module.__file__ = str(Path(__file__).resolve())
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def install_resolution(module):
    original = module.REFERENCE
    verify_original, worker_original, json_original = module.verify, module.worker, module.write_json
    state = {'report': None, 'evidence': None}

    def worker(tag):
        state['evidence'] = module.LOGS / (tag + '-evidence')
        return worker_original(tag)

    def verify(path, digest):
        if path != original or state['report'] is not None:
            return verify_original(path, digest)
        # This hook runs inside the original worker's shared locks, after it
        # verified PR94 HEAD/clean state and the four retained executables.
        module.require(digest == REFERENCE_SHA, 'REFERENCE_IDENTITY_CHANGED')
        destination = state['evidence'] / 'reference-resolution.json'
        found, report = locate_reference(module.ROOT, original, destination, digest)
        state['report'] = report
        # Replace the mapping rather than mutating the dictionary currently
        # being iterated by the original worker. A missing old path is not an
        # executable modified by this run; its status belongs in resolution.
        module.PROTECTED = {p: s for p, s in module.PROTECTED.items() if p != original}
        if found is None:
            raise RuntimeError('EXACT_HISTORICAL_REFERENCE_NOT_LOCATED_NO_FALLBACK; see reference-resolution.json')
        verify_original(found, digest)
        module.REFERENCE = found
        module.PROTECTED[found] = digest
        return None

    def write_json(path, value):
        if path.name == 'result.json' and state['report'] is not None:
            value['reference_resolution'] = state['report']
            value['resolved_reference_path'] = state['report']['selected_path']
        return json_original(path, value)

    module.worker, module.verify, module.write_json = worker, verify, write_json
    return module


if __name__ == '__main__':
    raise SystemExit(install_resolution(load_parent()).main())
