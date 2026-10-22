#!/usr/bin/env python3
"""Validate saved SPIR-V on the CPU; never launch or modify the emulator."""
from __future__ import annotations
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import struct
import subprocess
import tarfile
import tempfile
import time

TOOLS = '7651d9234622041b3f1b09e5c684c7c6eb352d08'
HEADERS = '86f980c731e62ae4eaf383d320449d71687936bf'
ENV = dict(os.environ, GIT_TERMINAL_PROMPT='0', LC_ALL='C')
MAX_FILE = 16 * 1024 * 1024


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + '\n')


class Corpus:
    def __init__(self, source: Path):
        self.source = source.resolve(strict=True)
        self.tar = None
        if self.source.is_dir():
            self.members = None
        else:
            self.tar = tarfile.open(self.source, 'r:*')
            self.members = {}
            try:
                for entry in self.tar.getmembers():
                    path = PurePosixPath(entry.name)
                    if path.is_absolute() or '..' in path.parts:
                        raise RuntimeError('UNSAFE_ARCHIVE_PATH=' + entry.name)
                    if entry.name in self.members:
                        raise RuntimeError('DUPLICATE_ARCHIVE_PATH=' + entry.name)
                    self.members[entry.name] = entry
            except BaseException:
                self.tar.close()
                raise

    def close(self):
        if self.tar:
            self.tar.close()

    def read(self, name: str) -> bytes:
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            raise RuntimeError('UNSAFE_INPUT_PATH=' + name)
        if self.tar:
            entry = self.members[name]
            if not entry.isfile() or entry.size > MAX_FILE:
                raise RuntimeError('INVALID_INPUT_FILE=' + name)
            with self.tar.extractfile(entry) as stream:
                return stream.read(MAX_FILE + 1)
        item = self.source / name
        if item.is_symlink() or not item.is_file() or not item.resolve().is_relative_to(self.source):
            raise RuntimeError('INVALID_INPUT_FILE=' + name)
        if item.stat().st_size > MAX_FILE:
            raise RuntimeError('INPUT_TOO_LARGE=' + name)
        return item.read_bytes()

    def collect(self, work: Path):
        manifest = json.loads(self.read('info/compiler-artifacts-manifest.json'))
        build = json.loads(self.read('info/build-result.json'))
        write_json(work / 'input-build.json', build)
        write_json(work / 'input-capture-manifest.json', manifest)
        (work / 'input-runtime-final.txt').write_bytes(self.read('info/final.txt'))
        if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list):
            raise RuntimeError('INVALID_CAPTURE_MANIFEST')
        if len(manifest['files']) > 100000:
            raise RuntimeError('EXCESSIVE_CAPTURE_MANIFEST')
        inputs = work / 'inputs'
        inputs.mkdir()
        records = []
        seen = set()
        for entry in manifest['files']:
            name = entry['path']
            if not name.endswith('.spv'):
                continue
            if PurePosixPath(name).name != name or name.startswith('.') or name in seen:
                raise RuntimeError('INVALID_SHADER_NAME=' + name)
            seen.add(name)
            if entry['status'] != 'COPIED':
                raise RuntimeError('SPIRV_NOT_INCLUDED_IN_CAPTURE=' + name)
            raw = self.read('info/compiler-artifacts/' + name)
            if len(raw) != entry['bytes'] or sha(raw) != entry['sha256']:
                raise RuntimeError('SHADER_CAPTURE_CHECKSUM_MISMATCH=' + name)
            if len(raw) < 20 or len(raw) % 4 or struct.unpack_from('<I', raw)[0] != 0x07230203:
                raise RuntimeError('INVALID_SPIRV_CONTAINER=' + name)
            (inputs / name).write_bytes(raw)
            records.append({'file': name, 'sha256': sha(raw), 'bytes': len(raw)})
        if not records or len(records) != manifest['spirv_files']:
            raise RuntimeError('INCOMPLETE_SHADER_INVENTORY')
        write_json(work / 'input-shader-inventory.json', records)
        return records


def command(args, log: Path, timeout=1200, check=True):
    args = [str(x) for x in args]
    with log.open('ab') as stream:
        stream.write(('\n$ ' + ' '.join(args) + '\n').encode())
        stream.flush()
        process = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT,
                                   env=ENV, start_new_session=True)
        try:
            returncode = process.wait(timeout=timeout)
        except BaseException:
            # This group contains only the command we just started, never SSH or the game.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            raise
    if check and returncode:
        raise RuntimeError('COMMAND_FAILED=' + str(returncode) + ':' + args[0])
    return returncode


def output(args):
    return subprocess.check_output([str(x) for x in args], env=ENV,
                                   stderr=subprocess.STDOUT, timeout=30).decode().strip()


def checkout(path: Path, url: str, revision: str, log: Path):
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError('TOOL_CHECKOUT_PATH_OCCUPIED=' + str(path))
        if output(['git', '-C', path, 'rev-parse', 'HEAD']) != revision:
            raise RuntimeError('TOOL_SOURCE_REVISION_CHANGED=' + str(path))
        if output(['git', '-C', path, 'status', '--porcelain', '--untracked-files=all']):
            raise RuntimeError('TOOL_SOURCE_MODIFIED=' + str(path))
        return
    staging = Path(tempfile.mkdtemp(prefix='.checkout-', dir=path.parent))
    try:
        command(['git', 'init', '-q', staging], log)
        command(['git', '-C', staging, '-c', 'http.version=HTTP/1.1',
                 '-c', 'fetch.recurseSubmodules=false', 'fetch', '-4', '--depth=1',
                 '--no-tags', '--no-recurse-submodules', url, revision], log, timeout=300)
        command(['git', '-C', staging, 'checkout', '-q', '--detach', 'FETCH_HEAD'], log)
        if output(['git', '-C', staging, 'rev-parse', 'HEAD']) != revision:
            raise RuntimeError('FETCHED_TOOL_REVISION_MISMATCH')
        os.rename(staging, path)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def build_tools(root: Path, report: Path):
    cache = root / 'tools' / ('spirv-validator-' + TOOLS[:12])
    if cache.is_symlink():
        raise RuntimeError('TOOL_CACHE_IS_SYMLINK')
    cache.mkdir(parents=True, exist_ok=True)
    log = report / 'tool-build.log'
    checkout(cache / 'source', 'https://github.com/KhronosGroup/SPIRV-Tools.git', TOOLS, log)
    checkout(cache / 'headers', 'https://github.com/KhronosGroup/SPIRV-Headers.git', HEADERS, log)
    deps = (cache / 'source/DEPS').read_text()
    if not re.search(r"'spirv_headers_revision':\s*'" + HEADERS + "'", deps):
        raise RuntimeError('OFFICIAL_HEADER_DEPENDENCY_CHANGED')
    build = cache / 'build'
    if build.is_symlink():
        raise RuntimeError('TOOL_BUILD_IS_SYMLINK')
    for program in ('git', 'cmake', 'ninja', 'nice'):
        if not shutil.which(program):
            raise RuntimeError('REQUIRED_BUILD_TOOL_MISSING=' + program)
    compiler = shutil.which('g++-14') or shutil.which('g++')
    if not compiler:
        raise RuntimeError('CXX_COMPILER_MISSING')
    command(['cmake', '-S', cache / 'source', '-B', build, '-G', 'Ninja',
             '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_CXX_COMPILER=' + compiler,
             '-DSPIRV-Headers_SOURCE_DIR=' + str(cache / 'headers'),
             '-DSPIRV_SKIP_TESTS=ON', '-DSPIRV_SKIP_EXECUTABLES=OFF',
             '-DSPIRV_BUILD_FUZZER=OFF', '-DSPIRV_TOOLS_USE_MIMALLOC=OFF',
             '-DSPIRV_WERROR=OFF'], log)
    command(['nice', '-n', '10', 'cmake', '--build', build, '--target',
             'spirv-val', 'spirv-dis', '--parallel', '2'], log)
    validator, disassembler = build / 'tools/spirv-val', build / 'tools/spirv-dis'
    for executable in (validator, disassembler):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError('BUILT_TOOL_NOT_FOUND=' + str(executable))
    write_json(report / 'tool-provenance.json', {
        'SPIRV-Tools': TOOLS, 'SPIRV-Headers': HEADERS,
        'validator_sha256': sha(validator.read_bytes()),
        'disassembler_sha256': sha(disassembler.read_bytes()),
        'version': output([validator, '--version']),
        'compiler': output([compiler, '--version']),
    })
    return validator, disassembler


def smoke_module(include_shader=True):
    def ins(op, *args):
        return [((len(args)+1) << 16) | op, *args]
    words = [0x07230203, 0x00010600, 0, 5, 0]
    if include_shader:
        words += ins(17, 1)
    words += ins(14, 0, 1)
    words += ins(15, 5, 1, 0x6e69616d, 0)
    words += ins(16, 1, 17, 1, 1, 1)
    words += ins(19, 2) + ins(33, 3, 2)
    words += ins(54, 2, 1, 0, 3) + ins(248, 4) + ins(253) + ins(56)
    return struct.pack('<%dI' % len(words), *words)


def validate(validator: Path, shader: Path, scalar=False):
    args = [str(validator), '--target-env', 'vulkan1.3']
    if scalar:
        args += ['--scalar-block-layout']
    args.append(str(shader))
    try:
        result = subprocess.run(args, capture_output=True, env=ENV, timeout=30)
        text = (result.stdout + result.stderr).decode(errors='replace')
        return {'returncode': result.returncode, 'output': text,
                'command': args, 'timeout': False}
    except subprocess.TimeoutExpired:
        return {'returncode': None, 'output': 'VALIDATOR_TIMEOUT',
                'command': args, 'timeout': True}


def self_test(validator: Path, report: Path):
    folder = report / 'validator-controls'
    folder.mkdir()
    valid, invalid = folder / 'valid.spv', folder / 'missing-shader-capability.spv'
    valid.write_bytes(smoke_module())
    invalid.write_bytes(smoke_module(False))
    a, b = validate(validator, valid), validate(validator, invalid)
    write_json(folder / 'results.json', {'valid': a, 'invalid': b})
    if a['returncode'] != 0 or b['returncode'] is None or b['returncode'] <= 0:
        raise RuntimeError('VALIDATOR_POSITIVE_OR_NEGATIVE_CONTROL_FAILED')


def run_validation(validator: Path, disassembler: Path, report: Path, records):
    results = []
    failures = report / 'failed-shaders'
    failures.mkdir()
    for index, item in enumerate(records, 1):
        shader = report / 'inputs' / item['file']
        strict = validate(validator, shader)
        row = dict(item, strict=strict)
        if strict['returncode'] == 0:
            row['status'] = 'PASS_STRICT'
        else:
            scalar = validate(validator, shader, scalar=True)
            row['scalar_layout'] = scalar
            row['status'] = ('REQUIRES_SCALAR_LAYOUT_REVIEW' if scalar['returncode'] == 0
                             else 'VALIDATION_FAILED')
            if strict['returncode'] is None or strict['returncode'] < 0:
                row['status'] = 'VALIDATOR_EXECUTION_FAILED'
            shutil.copyfile(shader, failures / shader.name)
            try:
                rc = command([disassembler, shader, '-o',
                              failures / (shader.name + '.spvasm')],
                             report / 'disassembly.log', timeout=30, check=False)
                row['disassembler_exit'] = rc
            except subprocess.TimeoutExpired:
                row['disassembler_exit'] = 'TIMEOUT'
            write_json(failures / (shader.name + '.json'), row)
        results.append(row)
        write_json(report / 'validation-results.json', results)
        if index % 25 == 0 or index == len(records):
            print(f'SHADERS_CHECKED={index}/{len(records)}', flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'projects/shadps4')
    parser.add_argument('--input', type=Path, required=True,
                        help='Existing runtime archive or its directory')
    args = parser.parse_args()
    def stop(signum, _frame):
        raise KeyboardInterrupt('AUDIT_INTERRUPTED_BY_SIGNAL=' + str(signum))
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, stop)
    root = args.root.expanduser().resolve()
    logs = root / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    report = Path(tempfile.mkdtemp(prefix='offline-spirv-' + time.strftime('%Y%m%d-%H%M%S-'), dir=logs))
    status = {'status': 'STARTING', 'game_launched': False, 'emulator_rebuilt': False,
              'settings_changed': False, 'GPU_used': False, 'input': str(args.input),
              'script_sha256': sha(Path(__file__).read_bytes())}
    corpus = None
    code = 2
    lock = None
    try:
        lock_path = logs / '.offline-spirv-validator.lock'
        lock = os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        corpus = Corpus(args.input.expanduser())
        records = corpus.collect(report)
        status['shader_count'] = len(records)
        print('INPUT_CHECKSUMS_VERIFIED=' + str(len(records)), flush=True)
        print('BUILDING_CPU_ONLY_VALIDATOR; EMULATOR_NOT_STARTED', flush=True)
        validator, disassembler = build_tools(root, report)
        self_test(validator, report)
        print('VALIDATOR_CONTROLS=PASS', flush=True)
        results = run_validation(validator, disassembler, report, records)
        status['counts'] = dict(Counter(x['status'] for x in results))
        status['status'] = ('ALL_CAPTURED_SHADERS_PASS_STRICT_VALIDATION'
                            if all(x['status'] == 'PASS_STRICT' for x in results)
                            else 'VALIDATION_FINDINGS_REVIEW_REQUIRED')
        status['meaning'] = 'Not a GPU test, hang diagnosis, or proof of rendering correctness.'
        code = 0 if status['status'].startswith('ALL_') else 2
    except (Exception, KeyboardInterrupt) as error:
        status.update(status='AUDIT_INCOMPLETE', error=repr(error))
        print('AUDIT_ERROR=' + repr(error), flush=True)
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, signal.SIG_IGN)
        if corpus:
            corpus.close()
        status['exit'] = code
        write_json(report / 'summary.json', status)
        archive = report.with_suffix('.tar.gz')
        with tarfile.open(archive, 'x:gz') as tar:
            for path in sorted(report.iterdir()):
                if path.name != 'inputs':
                    tar.add(path, arcname=path.name, recursive=True)
        if lock:
            lock.close()
        print('STATUS=' + status['status'], flush=True)
        print('GAME_LAUNCHED=NO; EMULATOR_REBUILT=NO; SETTINGS_CHANGED=NO', flush=True)
        print('UPLOAD_THIS=' + str(archive), flush=True)
        print('SSH_SESSION=OPEN', flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
