#!/usr/bin/env python3
"""Atomically pin a provenance-checked native shadPS4 build for ES-DE.

Does not rebuild, run or stop emulators, or alter the ES-DE wrapper, config or saves.
"""
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

PIN = '0d82e3ff401f2a5fafc7543f8afb9ab004be30f8'
HOME = Path.home()
PROJECT = Path(os.environ.get('SHADPS4_ESDE_PROJECT_ROOT', HOME / 'projects/shadps4'))
INSTALL = Path(os.environ.get('SHADPS4_ESDE_INSTALL_ROOT', HOME / 'Applications/shadps4'))
WRAPPER = Path(os.environ.get('SHADPS4_ESDE_RUNNER', HOME / '.local/bin/shadps4-esde'))
BASE = PROJECT / 'tests' / ('universal-upstream-' + PIN[:12])
CANDIDATE = BASE / 'build/shadps4'
SOURCE = BASE / 'source'
CORE = INSTALL / 'shadps4'
LOGS = PROJECT / 'logs'
ROLLBACKS = PROJECT / 'ESDE/rollback'


def fail(message):
    raise RuntimeError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, record):
    temp = path.with_name('.' + path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        temp.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_copy(source, target):
    temp = target.with_name('.' + target.name + '.tmp-' + uuid.uuid4().hex)
    try:
        if source.is_symlink():
            temp.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, temp)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def check_existing_core():
    if CORE.is_symlink():
        return 'symlink'
    if CORE.is_file():
        return 'file'
    if CORE.exists():
        fail('INSTALLED_CORE_IS_NOT_A_FILE: ' + str(CORE))
    return 'absent'


def verify_build():
    if not CANDIDATE.is_file() or CANDIDATE.is_symlink() or not os.access(CANDIDATE, os.X_OK):
        fail('MISSING_NON_EXECUTABLE_OR_LINKED_NATIVE_BUILD: ' + str(CANDIDATE))
    if CANDIDATE.open('rb').read(4) != b'\x7fELF':
        fail('CANDIDATE_NOT_NATIVE_ELF')
    if not SOURCE.is_dir():
        fail('MISSING_SOURCE_CHECKOUT: ' + str(SOURCE))
    actual_commit = subprocess.check_output(
        ['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
    if actual_commit != PIN:
        fail('SOURCE_COMMIT_MISMATCH: ' + actual_commit)
    digest = sha256(CANDIDATE)
    records = []
    for path in LOGS.glob('universal-upstream-*/result.json'):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if (record.get('result') == 'PASS_NATIVE_BUILD_CPU_AND_STARTUP_TESTS_GPU_NOT_RUN'
                and record.get('source_commit') == PIN
                and record.get('candidate_binary') == str(CANDIDATE)
                and record.get('binary_sha256') == digest):
            records.append(path)
    if not records:
        fail('MISSING_SUCCESSFUL_BUILD_RECORD_MATCHING_SHA256')
    return digest, max(records, key=lambda p: p.stat().st_mtime)


def install():
    if not WRAPPER.is_file() or not os.access(WRAPPER, os.X_OK):
        fail('MISSING_EXECUTABLE_ESDE_WRAPPER: ' + str(WRAPPER))
    digest, record = verify_build()
    release = INSTALL / 'releases' / ('universal-' + PIN[:12] + '-' + digest[:12])
    binary = release / 'shadps4'
    release.mkdir(parents=True, exist_ok=True)
    if binary.exists() or binary.is_symlink():
        if binary.is_symlink() or not binary.is_file() or sha256(binary) != digest:
            fail('VERSIONED_RELEASE_EXISTS_WITH_DIFFERENT_CONTENT')
    else:
        atomic_copy(CANDIDATE, binary)
    if sha256(binary) != digest:
        fail('VERSIONED_RELEASE_COPY_HASH_MISMATCH')
    current = check_existing_core()
    if CORE.is_symlink() and CORE.resolve() == binary.resolve():
        print('ALREADY_ACTIVE=' + str(binary))
        print('SHA256=' + digest)
        return
    timestamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    rollback = ROLLBACKS / ('before-universal-' + timestamp + '-' + uuid.uuid4().hex[:8])
    rollback.mkdir(parents=True)
    backup = rollback / 'previous-shadps4'
    if current != 'absent':
        if current == 'symlink':
            backup.symlink_to(os.readlink(CORE))
        else:
            shutil.copy2(CORE, backup)
    metadata = {
        'source_commit': PIN, 'sha256': digest,
        'verified_build_record': str(record), 'candidate': str(CANDIDATE),
        'versioned_binary': str(binary), 'installed_core': str(CORE),
        'old_core_type': current, 'rollback_directory': str(rollback),
        'wrapper_unchanged': str(WRAPPER),
        'timestamp_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    atomic_json(rollback / 'metadata.json', metadata)
    temp_link = INSTALL / ('.shadps4.next-' + uuid.uuid4().hex)
    try:
        temp_link.symlink_to(binary)
        os.replace(temp_link, CORE)
    finally:
        temp_link.unlink(missing_ok=True)
    if not CORE.is_symlink() or CORE.resolve() != binary.resolve():
        fail('FAILED_TO_ACTIVATE_VERSIONED_BINARY')
    log = LOGS / ('esde-pin-' + timestamp + '-' + uuid.uuid4().hex[:8] + '.json')
    atomic_json(log, metadata)
    print('PINNED_SOURCE=' + PIN)
    print('ACTIVE_BINARY=' + str(CORE.resolve()))
    print('ACTIVE_SHA256=' + sha256(CORE))
    print('ROLLBACK_DIR=' + str(rollback))
    print('INSTALL_LOG=' + str(log))
    print('ESDE_WRAPPER_UNCHANGED=' + str(WRAPPER))
    print('NO_EMULATOR_LAUNCHED_OR_STOPPED=YES')


def restore(rollback):
    # Never restore from an arbitrary outside location.
    if rollback.parent.resolve() != ROLLBACKS.resolve():
        fail('ROLLBACK_LOCATION_NOT_UNDER_PROJECT')
    record = json.loads((rollback / 'metadata.json').read_text())
    if record['installed_core'] != str(CORE) or record['source_commit'] != PIN:
        fail('ROLLBACK_TARGET_OR_SOURCE_MISMATCH')
    original_type = record['old_core_type']
    if original_type == 'absent':
        fail('NO_PREVIOUS_CORE_TO_RESTORE')
    original = rollback / 'previous-shadps4'
    if original_type == 'symlink' and not original.is_symlink():
        fail('MISSING_PREVIOUS_SYMLINK')
    if original_type == 'file' and (not original.is_file() or original.is_symlink()):
        fail('MISSING_PREVIOUS_BINARY')
    check_existing_core()
    atomic_copy(original, CORE)
    print('RESTORED_ORIGINAL_CORE=' + str(CORE))
    print('NO_EMULATOR_LAUNCHED_OR_STOPPED=YES')


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    ROLLBACKS.mkdir(parents=True, exist_ok=True)
    (PROJECT / 'tests').mkdir(parents=True, exist_ok=True)
    INSTALL.mkdir(parents=True, exist_ok=True)
    lock = PROJECT / 'tests/.universal-upstream-build.lock'
    with lock.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail('UNIVERSAL_BUILD_OR_TEST_IS_CURRENTLY_RUNNING')
        if len(sys.argv) == 1:
            install()
        elif len(sys.argv) == 3 and sys.argv[1] == '--restore':
            restore(Path(sys.argv[2]))
        else:
            fail('USAGE: installer.py [--restore /path/to/rollback-directory]')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ESDE_PIN_ERROR=' + str(exc), file=sys.stderr)
        sys.exit(1)
