#!/usr/bin/env python3
"""CPU-only Khronos SPIR-V validation of the exact captured GoW shader corpus.
No emulator launch, source/build mutation, GPU use, sudo, package installation,
shader substitution, or public upload. Download/extract a checksum-pinned Debian
utility locally. Validation is not proof of correct GPU semantics or gameplay.
"""
from __future__ import annotations
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import tarfile
import time
import traceback

CAPTURE_NAME = 'gow-real-shaders-20261028T173505Z-3869132.tar.gz'
CAPTURE_SHA = '0edc675319a0633e15f2fa12788a94f35ed234a5f95b48418b224e57726b00a7'
PREFIX = 'gow-real-shaders-20261028T173505Z-3869132-evidence/'
PACKAGE_NAME = 'spirv-tools_2025.1~rc1-1_amd64.deb'
PACKAGE_URL = 'https://deb.debian.org/debian/pool/main/s/spirv-tools/' + PACKAGE_NAME
PACKAGE_SHA = '2cc272ef1340cf0b47de6c5fcccaf91574d7a8f4061abb718d6c639463e85aab'
PACKAGE_SIZE = 4260164
MAX_MODULE = 4 * 1024**2
MAX_CORPUS = 32 * 1024**2
EXPECTED_MODULES = 60
MODULE_NAME = re.compile(r'(?:cs|vs|fs|ps|gs|hs|ls|es)_[A-Za-z0-9_]+\.spv\Z')


def require(ok: bool, why: str) -> None:
    if not ok:
        raise RuntimeError(why)


def digest(path: Path) -> str:
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def regular(path: Path) -> None:
    require(path.is_file() and not path.is_symlink() and path.resolve() == path,
            'NONREGULAR_OR_SYMLINK_PATH_REFUSED=' + str(path))


def read_member(tf: tarfile.TarFile, member: tarfile.TarInfo, maximum: int) -> bytes:
    require(member.isfile() and 0 <= member.size <= maximum,
            'ARCHIVE_MEMBER_REFUSED=' + member.name)
    f = tf.extractfile(member)
    require(f is not None, 'UNREADABLE_ARCHIVE_MEMBER=' + member.name)
    with f:
        content = f.read(maximum + 1)
    require(len(content) == member.size, 'ARCHIVE_MEMBER_LENGTH_MISMATCH')
    return content


def extract_modules(archive: Path, destination: Path,
                    expected_sha: str = CAPTURE_SHA) -> list[dict]:
    regular(archive)
    require(digest(archive) == expected_sha, 'CAPTURE_CHECKSUM_MISMATCH')
    destination.mkdir(parents=True, exist_ok=False)
    entries: dict[str, tarfile.TarInfo] = {}
    with tarfile.open(archive, 'r:gz') as tf:
        for index, member in enumerate(tf):
            require(index < 5000, 'ARCHIVE_MEMBER_COUNT_LIMIT')
            require(member.name not in entries, 'DUPLICATE_ARCHIVE_MEMBER')
            entries[member.name] = member
        manifest_member = entries.get(PREFIX + 'shader-manifest.json')
        require(manifest_member is not None, 'CAPTURE_MANIFEST_MISSING')
        manifest = json.loads(read_member(tf, manifest_member, 2 * 1024**2))
        require(manifest.get('spv_count') == EXPECTED_MODULES,
                'UNEXPECTED_CAPTURE_MODULE_COUNT')
        rows = [r for r in manifest['files'] if str(r.get('name', '')).endswith('.spv')]
        require(len(rows) == EXPECTED_MODULES and
                len({r['name'] for r in rows}) == EXPECTED_MODULES,
                'MANIFEST_MODULES_MISSING_OR_DUPLICATED')
        output, total = [], 0
        for row in sorted(rows, key=lambda r: r['name']):
            name = row['name']
            require(MODULE_NAME.fullmatch(name) is not None and PurePosixPath(name).name == name,
                    'UNSAFE_MODULE_FILENAME')
            member = entries.get(PREFIX + 'shaders/' + name)
            require(member is not None, 'MANIFEST_MODULE_MISSING=' + name)
            data = read_member(tf, member, MAX_MODULE)
            total += len(data)
            require(total <= MAX_CORPUS, 'CORPUS_SIZE_LIMIT')
            require(len(data) == row['size'] and hashlib.sha256(data).hexdigest() == row['sha256'],
                    'MODULE_CHECKSUM_MISMATCH=' + name)
            require(len(data) >= 20 and len(data) % 4 == 0 and data[:4] == b'\x03\x02\x23\x07',
                    'NOT_A_LITTLE_ENDIAN_SPIRV_MODULE=' + name)
            target = destination / name
            with target.open('xb') as f:
                f.write(data)
            output.append({'name': name, 'sha256': row['sha256'], 'size': len(data)})
    return output


def command(argv: list[str], log: Path, timeout: int = 60) -> dict:
    """Capture a bounded CPU utility; never launch an emulator or shell."""
    began = time.monotonic()
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout, check=False)
        data = p.stdout
        result = {'argv': argv, 'returncode': p.returncode, 'timeout': False}
    except subprocess.TimeoutExpired as exc:
        data = exc.stdout or b''
        result = {'argv': argv, 'returncode': None, 'timeout': True}
    require(len(data) <= 16 * 1024**2, 'UTILITY_OUTPUT_LIMIT')
    log.write_bytes(data)
    result['seconds'] = round(time.monotonic() - began, 3)
    result['log'] = log.name
    return result


def checked(argv: list[str], log: Path, timeout: int = 60) -> dict:
    result = command(argv, log, timeout)
    require(result['returncode'] == 0 and not result['timeout'],
            'UTILITY_FAILED=' + log.name)
    return result


def get_tools(work: Path, evidence: Path) -> tuple[Path, Path]:
    require(platform.machine() in ('x86_64', 'amd64'), 'AMD64_HOST_REQUIRED')
    for name in ('curl', 'dpkg-deb'):
        require(shutil.which(name) is not None, 'MISSING_UTILITY=' + name)
    package = work / PACKAGE_NAME
    checked(['curl', '-4fL', '--proto', '=https', '--tlsv1.2', '--retry', '2',
             '--connect-timeout', '15', '--max-time', '90', PACKAGE_URL,
             '-o', str(package)], evidence / 'validator-download.log', 280)
    regular(package)
    require(package.stat().st_size == PACKAGE_SIZE and digest(package) == PACKAGE_SHA,
            'DEBIAN_VALIDATOR_PACKAGE_CHECKSUM_MISMATCH')
    # dpkg-deb --extract writes files only; no package scripts or system install.
    private = work / 'unpacked-validator'
    private.mkdir()
    checked(['dpkg-deb', '--extract', str(package), str(private)],
            evidence / 'validator-extract.log', 30)
    val, dis = private / 'usr/bin/spirv-val', private / 'usr/bin/spirv-dis'
    regular(val)
    regular(dis)
    require(os.access(val, os.X_OK) and os.access(dis, os.X_OK), 'VALIDATOR_NOT_EXECUTABLE')
    checked([str(val), '--version'], evidence / 'validator-version.txt', 15)
    checked([str(dis), '--version'], evidence / 'disassembler-version.txt', 15)
    (evidence / 'validator-provenance.json').write_text(json.dumps({
        'package_url': PACKAGE_URL, 'package_sha256': PACKAGE_SHA,
        'package_bytes': PACKAGE_SIZE, 'validator_sha256': digest(val),
        'disassembler_sha256': digest(dis), 'system_installation': False,
        'version_scope': 'Pinned Debian trixie tool, not necessarily the newest upstream validator',
    }, indent=2) + '\n')
    return val, dis


def validate_modules(val: Path, dis: Path, modules: list[dict],
                     corpus: Path, evidence: Path) -> list[dict]:
    diagnostics = evidence / 'validation'
    assembly = evidence / 'disassembly'
    diagnostics.mkdir()
    assembly.mkdir()
    output = []
    # These layout features are enabled in the captured emulator profile.
    # Do not use skip-block-layout, relax-logical-pointer, or skip-validation.
    flags = ['--target-env', 'vulkan1.3', '--scalar-block-layout',
             '--uniform-buffer-standard-layout']
    for index, entry in enumerate(modules, 1):
        name = entry['name']
        path = corpus / name
        require(digest(path) == entry['sha256'], 'INPUT_CHANGED_DURING_VALIDATION=' + name)
        result = command([str(val), *flags, str(path)],
                         diagnostics / (name + '.txt'), 15)
        r = {**entry, 'validation': result}
        if result['timeout'] or result['returncode'] not in (0, 1):
            r['verdict'] = 'TOOL_ERROR_OR_TIMEOUT'
        else:
            r['verdict'] = 'VALIDATOR_ACCEPTED' if result['returncode'] == 0 else 'VALIDATOR_REJECTED'
        # Disassemble the unchanged module even after rejection to locate errors.
        listing = assembly / (name + '.spvasm')
        r['disassembly'] = command([str(dis), '--no-color', '--raw-id', str(path),
                                     '-o', str(listing)], diagnostics / (name + '.dis.txt'), 15)
        output.append(r)
        print(f'SHADER_VALIDATION={index}/{len(modules)} {name} {r["verdict"]}', flush=True)
    return output


def main(root: Path | None = None) -> int:
    os.umask(0o077)
    root = (root or Path.home() / 'projects/shadps4').resolve(strict=True)
    logs = root / 'logs'
    logs.mkdir(exist_ok=True)
    require(logs.resolve() == logs and not logs.is_symlink(), 'LOG_DIRECTORY_SYMLINK_REFUSED')
    tag = 'gow-spirv-offline-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
    evidence = logs / (tag + '-evidence')
    evidence.mkdir()
    tools = root / 'tools'
    tools.mkdir(exist_ok=True)
    require(tools.resolve() == tools and not tools.is_symlink(), 'TOOLS_DIRECTORY_SYMLINK_REFUSED')
    work = tools / tag
    work.mkdir()
    archive = logs / (tag + '.tar.gz')
    status = {'schema_version': 2, 'record_id': tag, 'record_type': 'CAPTURED_SPIRV_CPU_VALIDATION',
              'recorded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'game_id': 'CUSA34384', 'tested_games': [], 'game_launched': False,
              'gpu_work': False, 'source_modified': False, 'system_installation': False,
              'capture_archive': CAPTURE_NAME, 'capture_sha256': CAPTURE_SHA,
              'gameplay': 'NOT_TESTED', 'status': 'PREFLIGHT', 'modules': [],
              'limits': 'Validator rejection requires interpretation; accepted SPIR-V does not prove correct resources or guest semantics.'}
    rc = 1
    lock = (logs / 'gow-spirv-offline.lock').open('a+')
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        status['status'] = 'VERIFY_CAPTURE'
        corpus = work / 'unchanged-modules'
        modules = extract_modules(logs / CAPTURE_NAME, corpus)
        print('CAPTURE_MANIFEST=PASS modules=' + str(len(modules)), flush=True)
        status['status'] = 'FETCH_PROJECT_LOCAL_VALIDATOR'
        val, dis = get_tools(work, evidence)
        status['status'] = 'VALIDATING_ACTUAL_MODULES'
        status['modules'] = validate_modules(val, dis, modules, corpus, evidence)
        status['accepted'] = sum(m['verdict'] == 'VALIDATOR_ACCEPTED' for m in status['modules'])
        status['rejected'] = sum(m['verdict'] == 'VALIDATOR_REJECTED' for m in status['modules'])
        status['tool_errors'] = sum(m['verdict'] == 'TOOL_ERROR_OR_TIMEOUT' for m in status['modules'])
        status['status'] = ('VALIDATION_INCOMPLETE' if status['tool_errors'] else
                            'REJECTIONS_REQUIRE_REVIEW' if status['rejected'] else
                            'ALL_CAPTURED_MODULES_ACCEPTED_STRUCTURALLY')
        rc = 1 if status['tool_errors'] else 2 if status['rejected'] else 0
    except Exception as exc:
        status['failure'] = str(exc)
        (evidence / 'failure.txt').write_text(traceback.format_exc())
        print('OFFLINE_VALIDATION_FAILED=' + str(exc), flush=True)
    finally:
        status['exit_code'] = rc
        (evidence / 'result.json').write_text(json.dumps(status, indent=2) + '\n')
        try:
            with tarfile.open(archive, 'w:gz') as tf:
                tf.add(evidence, arcname=evidence.name)
            row = {k: v for k, v in status.items() if k != 'modules'}
            row['archive'] = str(archive)
            row['archive_sha256'] = digest(archive)
            row['decision'] = 'REVIEW_ACTUAL_MODULE_RESULTS; NO_AUTOMATIC_EMULATOR_PATCH_OR_GAME_RUN'
            diary = root / 'diary/trials.jsonl'
            diary.parent.mkdir(exist_ok=True)
            require(diary.parent.resolve() == diary.parent and not diary.is_symlink(),
                    'DIARY_SYMLINK_REFUSED')
            with diary.open('a') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.write(json.dumps(row, separators=(',', ':')) + '\n')
                f.flush()
                os.fsync(f.fileno())
            print('UPLOAD_THIS=' + str(archive), flush=True)
        except Exception as exc:
            print('ARCHIVE_OR_DIARY_FAILED=' + str(exc), flush=True)
            rc = 1
        lock.close()
        print('NO_GAME_NO_GPU_NO_REBUILD=YES\nSSH_SESSION=OPEN', flush=True)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
