# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
import zipfile

HERE = Path(__file__).resolve().parent
BASE_TREE = 'cf5f4396c91519925e42ee7ae9c9be7514bc76f1'
BASE_BINARY_SHA = '8e9d11e300a550ed428e29af9951a5c9e8f8a832108a20d4f66d6d2a1d6d72de'
FFMPEG_SHA = 'aacbbfb8e622b684bc5d3b4cd6c9f9f77f5def64ae8d83c0c5b3ebe657aa33dd'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args],
                                   stderr=subprocess.STDOUT, timeout=60).decode().strip()


def command(args, log, cwd=None, timeout=3600):
    print('BUILD=' + ' '.join(map(str, args)), flush=True)
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    # Bound idle HTTP transfers, including git launched by CMake's runtime builder.
    count = int(env.get('GIT_CONFIG_COUNT', '0'))
    for offset, (key, value) in enumerate((('http.lowSpeedLimit', '1024'), ('http.lowSpeedTime', '30'))):
        env[f'GIT_CONFIG_KEY_{count + offset}'] = key
        env[f'GIT_CONFIG_VALUE_{count + offset}'] = value
    env['GIT_CONFIG_COUNT'] = str(count + 2)
    with log.open('a') as output:
        process = subprocess.Popen(list(map(str, args)), cwd=cwd, env=env,
                                   stdout=output, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        start = time.monotonic()
        try:
            while True:
                try:
                    code = process.wait(timeout=15)
                    break
                except subprocess.TimeoutExpired:
                    elapsed = int(time.monotonic() - start)
                    print(f'BUILD_RUNNING={elapsed}s; log: {log}', flush=True)
                    if elapsed >= timeout:
                        raise RuntimeError('Build step timed out; see build.log')
            if code:
                print(log.read_text(errors='replace')[-4000:], flush=True)
                raise RuntimeError('Build step failed; see build.log')
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def build(cache, evidence):
    original = cache / 'sse4a-report-source-d7ff0dffa041'
    baseline_build = cache / 'sse4a-report-build-d7ff0dffa041'
    if git(original, 'rev-parse', 'HEAD^{tree}') != BASE_TREE:
        raise RuntimeError('The last tested source differs; it was left untouched')
    if git(original, 'status', '--porcelain', '--untracked-files=no', '--ignore-submodules=all'):
        raise RuntimeError('The last tested source has local edits; it was left untouched')
    if digest(baseline_build / 'shadps4') != BASE_BINARY_SHA:
        raise RuntimeError('The last tested binary differs; it was left untouched')
    patch = HERE / 'automatic.patch'
    patch_sha = digest(patch)
    source = cache / ('auto-cpu-source-' + patch_sha[:12])
    build = cache / ('auto-cpu-build-' + patch_sha[:12])
    log = evidence / 'build.log'
    base = git(original, 'rev-parse', 'HEAD')
    if not source.exists():
        command(['git', 'clone', '--shared', '--no-checkout', original, source], log, timeout=180)
        command(['git', 'checkout', '--detach', base], log, source, timeout=180)
    if git(source, 'status', '--porcelain', '--untracked-files=no', '--ignore-submodules=all'):
        raise RuntimeError('The isolated source has local edits; it was left untouched')
    if git(source, 'rev-parse', 'HEAD') == base:
        command(['git', 'apply', '--check', '--index', patch], log, source, timeout=60)
        command(['git', 'apply', '--index', patch], log, source, timeout=60)
        command(['git', '-c', 'user.name=shadPS4 playtest', '-c', 'user.email=playtest@localhost',
                 '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-m',
                 'playtest: automatic CPU identity startup on tested graphics source'], log, source)
    # Reversing exactly this patch must reproduce the known baseline tree.
    index = evidence / 'verification-index'
    check_env = dict(os.environ, GIT_INDEX_FILE=str(index))
    try:
        subprocess.run(['git', 'read-tree', 'HEAD'], cwd=source, env=check_env, check=True)
        subprocess.run(['git', 'apply', '--reverse', '--cached', str(patch)], cwd=source,
                       env=check_env, check=True)
        restored = subprocess.check_output(['git', 'write-tree'], cwd=source, env=check_env).decode().strip()
        if restored != BASE_TREE:
            raise RuntimeError('Unexpected changes in the isolated source')
    finally:
        index.unlink(missing_ok=True)
    status = git(source, 'submodule', 'status', '--recursive')
    if any(line[:1] in {'+', '-', 'U'} for line in status.splitlines()):
        command(['git', '-c', 'submodule.alternateErrorStrategy=info', 'submodule', 'update',
                 '--init', '--recursive', '--jobs', '4', '--reference', original], log, source)
    if any(line[:1] in {'+', '-', 'U'} for line in git(source, 'submodule', 'status', '--recursive').splitlines()):
        raise RuntimeError('A dependency differs from the tested source')
    compilers = {}
    for line in (baseline_build / 'CMakeCache.txt').read_text().splitlines():
        match = re.fullmatch(r'(CMAKE_C_COMPILER|CMAKE_CXX_COMPILER):[^=]+=(.+)', line)
        if match:
            compilers[match[1]] = match[2]
    if len(compilers) != 2 or not all(Path(p).is_file() for p in compilers.values()):
        raise RuntimeError('The tested build compilers are unavailable')
    short = git(source / 'externals/ffmpeg-core', 'rev-parse', '--short', 'HEAD')
    candidates = list((baseline_build / 'externals').glob('ffmpeg-94dde08*.zip'))
    candidates += list((cache / 'working-build-95b74819840d/externals').glob('ffmpeg-94dde08*.zip'))
    cached = next((p for p in candidates if digest(p) == FFMPEG_SHA), None)
    if cached is None or not short.startswith('94dde08'):
        raise RuntimeError('The verified FFmpeg cache is missing or changed')
    destination = build / 'externals' / ('ffmpeg-' + short + '.zip')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached, destination)
    libraries = destination.with_suffix('') / 'lib'
    libraries.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination) as archive:
        for name in ('avformat', 'avcodec', 'swscale', 'avutil', 'avfilter', 'swresample'):
            filename = 'lib' + name + '.a'
            content = archive.read(filename)
            if not content.startswith(b'!<arch>\n'):
                raise RuntimeError('Invalid FFmpeg library')
            (libraries / filename).write_bytes(content)
    configure = ['cmake', '-S', source, '-B', build, '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release',
                 '-DENABLE_TESTS=OFF', '-DENABLE_UPDATER=OFF', '-DCMAKE_CXX_SCAN_FOR_MODULES=OFF',
                 '-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF', '-DENABLE_CPU_ID_TRANSLATION=ON']
    configure += ['-D' + key + '=' + value for key, value in compilers.items()]
    if shutil.which('ccache'):
        configure += ['-DCMAKE_C_COMPILER_LAUNCHER=ccache', '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache']
    command(configure, log)
    command(['cmake', '--build', build, '--target', 'shadps4', '--parallel',
             str(max(1, min(4, len(os.sched_getaffinity(0)))))], log)
    command(['git', 'diff', '--exit-code', 'HEAD', '--'], log, source)
    binary = build / 'shadps4'
    help_text = subprocess.check_output([binary, '--help'], stderr=subprocess.STDOUT, timeout=30).decode()
    if '--cpu-id-mode' not in help_text:
        raise RuntimeError('The candidate does not include automatic CPU identity startup')
    info = {'source': str(source), 'source_tree': git(source, 'rev-parse', 'HEAD^{tree}'),
            'base_tree': BASE_TREE, 'patch_sha256': patch_sha, 'binary': str(binary),
            'binary_sha256': digest(binary), 'built': True}
    (evidence / 'build.json').write_text(json.dumps(info, indent=2) + '\n')
    return binary, info
