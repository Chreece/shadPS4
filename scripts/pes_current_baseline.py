#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Require a current, locally built baseline before PES tests."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

UPSTREAM = 'https://github.com/shadps4-emu/shadPS4.git'
STATE = '.local/state/shadps4-current-baseline/installed.json'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require_current(manifest):
    if manifest.get('schema') != 1 or manifest['upstream']['repository'] != 'shadps4-emu/shadPS4':
        raise RuntimeError('Unrecognized test baseline manifest')
    expected = {'refs/heads/main': manifest['upstream']['revision']}
    for pr in manifest['pending_fixes']:
        expected[f"refs/pull/{int(pr['number'])}/head"] = pr['head']
    if not all(re.fullmatch(r'[0-9a-f]{40}', sha) for sha in expected.values()):
        raise RuntimeError('Baseline contains an invalid revision')
    result = subprocess.run(['git', 'ls-remote', '--refs', UPSTREAM, *expected],
                            capture_output=True, text=True, timeout=45, check=True)
    actual = {ref: sha for sha, ref in (line.split() for line in result.stdout.splitlines())}
    changed = [f'{ref}: expected {sha}, current {actual.get(ref, "unavailable")}'
               for ref, sha in expected.items() if actual.get(ref) != sha]
    if changed:
        raise RuntimeError('Baseline is outdated; rebuild the reviewed integration before testing. '
                           + '; '.join(changed))
    print('PES_BASELINE_UPSTREAM=' + manifest['upstream']['revision'], flush=True)
    print('PES_BASELINE_PENDING_PRS=' + ','.join(str(p['number']) for p in manifest['pending_fixes']),
          flush=True)


def require_no_game():
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid == os.getuid() and (proc / 'exe').readlink().name.lower() == 'shadps4':
                raise RuntimeError('Close the game normally before building/selecting the new baseline')
        except OSError:
            continue


def preserved_files(home):
    root = home / '.local/share/shadPS4'
    paths = [home / '.local/bin/shadps4-esde', root / 'config.json',
             root / 'custom_configs/CUSA18676.json']
    return {str(path): digest(path) if path.exists() else None for path in paths}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def atomic_link(path, target):
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    os.close(fd)
    os.unlink(name)
    try:
        Path(name).symlink_to(target)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def prepare(home, revision, branch, manifest, builder):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise RuntimeError('An exact emulator revision is required')
    require_current(manifest)
    require_no_game()
    root = home / 'Applications/shadps4'
    core = root / 'shadps4'
    if root.is_symlink() or not core.is_symlink() or not core.is_file():
        raise RuntimeError('Expected the existing symlink-selected emulator; preserved')
    previous_link = core.readlink()
    previous = core.resolve(strict=True)
    previous_sha = digest(previous)
    before = preserved_files(home)
    built = builder(home, revision, branch)
    require_current(manifest)
    require_no_game()
    if (not core.is_symlink() or core.readlink() != previous_link or
            digest(previous) != previous_sha or preserved_files(home) != before):
        raise RuntimeError('Installation or settings changed during build; selection preserved')
    sha = digest(built)
    releases = root / 'releases'
    release = releases / ('baseline-' + revision[:12] + '-' + sha)
    if releases.is_symlink() or release.is_symlink():
        raise RuntimeError('Unexpected release symlink; preserved')
    release.mkdir(parents=True, exist_ok=True)
    binary = release / 'shadps4'
    if binary.is_symlink() or (binary.exists() and digest(binary) != sha):
        raise RuntimeError('Candidate release contains a different binary; preserved')
    if not binary.exists():
        fd, name = tempfile.mkstemp(prefix='shadps4.', dir=release)
        os.close(fd)
        try:
            shutil.copy2(built, name)
            Path(name).chmod(0o755)
            os.replace(name, binary)
        finally:
            Path(name).unlink(missing_ok=True)
    if digest(binary) != sha:
        raise RuntimeError('Candidate copy checksum mismatch; selection preserved')
    record = {'schema': 1, 'revision': revision, 'source_branch': branch,
              'binary': str(binary), 'binary_sha256': sha, 'manifest': manifest,
              'previous_binary': str(previous), 'previous_sha256': previous_sha,
              'preserved_files': before, 'local_docker_build_passed': True}
    require_no_game()
    if core.readlink() != previous_link or preserved_files(home) != before:
        raise RuntimeError('Installation changed before selection; preserved')
    atomic_json(release / 'baseline.json', record)
    try:
        atomic_link(core, binary)
        if core.resolve() != binary or digest(core) != sha:
            raise RuntimeError('Selected binary verification failed')
        atomic_json(home / STATE, record)
    except BaseException:
        atomic_link(core, previous_link)
        raise
    print('PES_BASELINE_BUILD=PASS', flush=True)
    print('PES_BASELINE_REVISION=' + revision, flush=True)
    print('PES_BASELINE_BINARY_SHA256=' + sha, flush=True)
    print('PES_BASELINE_PREVIOUS_BINARY=' + str(previous), flush=True)
    print('PES_BASELINE_SETTINGS_UNCHANGED=YES', flush=True)
    return record


def verify_installed(home):
    state = home / STATE
    if not state.is_file():
        raise RuntimeError('Build/select the current reviewed baseline before running this capture')
    record = json.loads(state.read_text())
    if record.get('schema') != 1 or record.get('local_docker_build_passed') is not True:
        raise RuntimeError('Missing successful local Docker build record')
    require_current(record['manifest'])
    core = home / 'Applications/shadps4/shadps4'
    binary = Path(record['binary'])
    if (not core.is_symlink() or core.resolve() != binary or
            digest(core) != record['binary_sha256']):
        raise RuntimeError('Selected emulator differs from the verified current baseline')
    import trace_video_progress as trace
    trace.REVISION = record['revision']
    trace.BINARY_SHA256 = record['binary_sha256']
    trace.BASELINE_METADATA = {'revision': record['revision'],
                               'source_branch': record['source_branch'],
                               'manifest': record['manifest']}
    print('PES_BASELINE_REVISION=' + record['revision'], flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--source-branch', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError('Run as chreece without sudo')
    import install_local_default as installer
    home = Path.home()
    lock_root = home / '.local/state/shadps4-ngs2'
    lock_root.mkdir(parents=True, exist_ok=True)
    capture_lock = home / '.cache/shadps4-video-trace.lock'
    capture_lock.parent.mkdir(exist_ok=True)
    with (lock_root / 'deploy.lock').open('a') as deploy_lock, capture_lock.open('a') as trace_lock:
        fcntl.flock(deploy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(trace_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prepare(home, args.revision, args.source_branch,
                json.loads(args.manifest.read_text()), installer.build)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print('PES_BASELINE_BUILD=FAIL: ' + (str(error) or 'Interrupted'), flush=True)
    finally:
        print('Returning to your existing SSH prompt.', flush=True)
