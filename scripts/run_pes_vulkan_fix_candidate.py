#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the Vulkan corrections in local Docker, then launch and capture PES."""

import fcntl
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid

REVISION = '8f86c45bacaabc38f5d30a85e6c418f39a6d2c22'
BRANCH = 'diag/pes-automatic-baseline'
ARENA_FIX = '201124a6c533020ffd9cabf5d84fb6d8e207a864'
IMGUI_FIX = 'eaa2e17e42c6a083af6650c83bc94992adeb6d92'
HELPER_REVISION = '4621958f1e0e749d429245c43e511a87e0a66fd5'
HELPER_REVISIONS = {
    'install_local_default.py': 'c501f37774ee14dddbca99ca1b84b2d3cb8a8761',
    'run_pes_startup_test.py': '9ff21ec091efa3f0f4889cee60bc79fdb2e2a5f8',
    'pes_test_cleanup.py': '9ff21ec091efa3f0f4889cee60bc79fdb2e2a5f8',
}
RAW = 'https://raw.githubusercontent.com/Chreece/shadPS4/'
HELPERS = {
    'install_local_default.py': '230c64cc1c835079ab907f122a0d099917a0610ca6c85d87d92a2b301395976d',
    'pes_current_baseline.py': '41b4da73dbc2b0fb64a306a441cd8f4b717bf6114e1a851f6753c333a68533ab',
    'trace_video_progress.py': 'c770af6e639464064ec543f99cb6181d75bf4b3bbe493ae6e8c015652d9b1a6b',
    'pes_frame_profile.py': '7b2f264f974b48ca8a98d3038404831326a7913efeeb2f7a2906d2fbec126df2',
    'collect_pes_runtime_context.py': '58cb93a9b52f2264e11d13259e379d676b7aa940c9522f7e80198943ae6ed612',
    'run_pes_startup_test.py': '78da37f276276cee635ec705045187384a33003c48653334fd2c47fb8951f271',
    'pes_test_cleanup.py': 'fe5d13cf6fe1cee2c1e50c3329c8092f220ec3ca27de7834ab4537d744330b96',
    'pes_graphics_launch.py': 'f4d1e82b02c3bc5c27d3bffce47f717248ba90b70ac1be68170717bd47175588',
    'pes_vulkan_validation.py': '04043e9a4c06832dc4b99388cdfff79550398b4aebff1976b2e72e46f338eed1',
}
MANIFEST_SHA256 = 'debe8f7df31cbc9044d1ae19f3363dbbc98639aa7f1cfb83ca9ea552e891cf50'
TARGET_ERRORS = ('VUID-VkBufferCreateInfo-size-06409',
                 'VUID-VkMappedMemoryRange-size-01389',
                 'VUID-VkMappedMemoryRange-size-01390')


def download(url, expected, path):
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError('Download checksum mismatch: ' + path.name)
    path.write_bytes(data)


def prepare_helpers(work):
    work.mkdir()
    print('Preparing checksum-verified build and capture helpers...', flush=True)
    for name, digest in HELPERS.items():
        revision = HELPER_REVISIONS.get(name, HELPER_REVISION)
        download(RAW + revision + '/scripts/' + name, digest, work / name)
    manifest = work / 'LOCAL_TEST_BASELINE.json'
    download(RAW + REVISION + '/documents/LOCAL_TEST_BASELINE.json', MANIFEST_SHA256, manifest)
    sys.path.insert(0, str(work))
    print('PES_TEST_HELPERS=' + str(work), flush=True)
    return json.loads(manifest.read_text())


def check_capture(result):
    required = ('frames_capture_passed', 'screenshots_complete', 'settings_unchanged',
                'launcher_unchanged', 'vulkan_preflight_passed', 'vulkan_layer_loaded')
    if result.get('errors') or not all(result.get(name) for name in required):
        raise RuntimeError('Capture or preservation check failed; upload the printed archive')
    if (not result.get('process_cleanup', {}).get('complete') or
            not result.get('debugger_cleanup', {}).get('complete')):
        raise RuntimeError('Test process/debugger cleanup was not verified')
    validation = result.get('vulkan_validation', {})
    if not validation.get('synchronization_enabled_in_log'):
        raise RuntimeError('Vulkan validation activation was not confirmed')
    counts = validation.get('message_counts', {})
    remaining = {name: counts[name] for name in TARGET_ERRORS if counts.get(name)}
    if remaining:
        raise RuntimeError('Target Vulkan errors remain: ' + json.dumps(remaining))
    packets = result.get('graphics', {}).get('event_count_lower_bounds', {})
    if not packets.get('image-upload') or not packets.get('present-image'):
        raise RuntimeError('No native upload/present activity; absence of errors is inconclusive')
    console = result.get('console_capture', {})
    if (console.get('status') != 'captured' or console.get('truncated') is not False or
            sum(part['bytes'] for part in console.get('parts', [])) != console.get('size_at_open')):
        raise RuntimeError('Console capture incomplete; absence of errors is inconclusive')
    print('PES_VULKAN_FIX_CHECK=PASS: target buffer-size and mapping errors absent', flush=True)
    print('PES_OTHER_VULKAN_MESSAGES=' + json.dumps(counts, sort_keys=True), flush=True)
    print('PES_VISUAL_EVIDENCE=archived; no visual report is needed', flush=True)


def run(home, work, session, manifest, installer, baseline, startup):
    manifest = dict(manifest, candidate_fixes={
        'sparse_arena_limit': ARENA_FIX,
        'imgui_mapping': IMGUI_FIX,
        'pes_runtime_result': 'unverified',
        'retained_tiling_fix': '1522b405a109f9f5f1db3c0852b62c1e27c2539b',
        'retained_image_refresh_fix': 'c293dbcadbcb545676947500936df69fcda7a8bf',
    })
    def build(*args):
        session['build_started'] = True
        return installer.build(*args, cleanup_token=session['ownership_token'])
    baseline.prepare(home, REVISION, BRANCH, manifest, build)
    baseline.verify_installed(home)
    check_capture(startup.run(home, profile='frames', screenshots=True,
                              graphics=True, validation=True, close_after=True,
                              work=work / 'startup', archive=False, trace_delay_seconds=40))


def cleanup_containers(token):
    label = 'org.shadps4.pes-test'
    def docker(*args):
        return subprocess.run(['docker', *args], capture_output=True, text=True,
                              check=True, timeout=15).stdout
    query = ('ps', '-aq', '--no-trunc', '--filter', 'label=' + label + '=' + token)
    owned = docker(*query).split()
    if len(owned) > 16 or any(len(item) != 64 or any(c not in '0123456789abcdef' for c in item) for item in owned):
        raise RuntimeError('Unexpected Docker ownership results; no container removed')
    removed = []
    for container in owned:
        records = json.loads(docker('inspect', container))
        if (len(records) != 1 or records[0]['Id'] != container or
                records[0]['Config'].get('Labels', {}).get(label) != token):
            raise RuntimeError('Container ownership changed; not removed')
        docker('rm', '--force', container)
        removed.append(container)
    remaining = docker(*query).split()
    return {'complete': not remaining, 'removed': removed, 'remaining': remaining}


class Tee:
    def __init__(self, terminal, output):
        self.terminal, self.output = terminal, output

    def write(self, value):
        self.terminal.write(value)
        return self.output.write(value)

    def flush(self):
        self.terminal.flush()
        self.output.flush()


def collect_build_log(source, work, offset):
    if not source.is_file():
        return {'status': 'not_started'}
    end = source.stat().st_size
    if end < offset:
        return {'status': 'log_replaced', 'initial_offset': offset, 'end': end}
    size = end - offset
    limit = 32 * 1024 * 1024
    ranges = [(offset, size)] if size <= limit else [(offset, limit // 2), (end - limit // 2, limit // 2)]
    with source.open('rb') as stream:
        for index, (start, length) in enumerate(ranges):
            stream.seek(start)
            (work / ('build.' + str(index) + '.log')).write_bytes(stream.read(length))
    return {'status': 'captured', 'bytes': size, 'truncated': size > limit,
            'initial_offset': offset, 'end': end}


def execute_session(home):
    work = Path(tempfile.mkdtemp(prefix='shadps4-pes-test-', dir=home))
    session = {'schema': 1, 'candidate_revision': REVISION, 'errors': [],
               'ownership_token': uuid.uuid4().hex, 'build_started': False,
               'visual_review': 'assistant_reviews_archived_screenshots'}
    build_log = home / '.cache/shadps4-ngs2-local/ca67919d-docker/default-main-build.log'
    offset = build_log.stat().st_size if build_log.is_file() else 0
    with (work / 'runner.log').open('w') as output:
        with redirect_stdout(Tee(sys.stdout, output)), redirect_stderr(Tee(sys.stderr, output)):
            try:
                manifest = prepare_helpers(work / 'helpers')
                session['baseline'] = manifest
                run(home, work, session, manifest, importlib.import_module('install_local_default'),
                    importlib.import_module('pes_current_baseline'),
                    importlib.import_module('run_pes_startup_test'))
            except (Exception, KeyboardInterrupt) as error:
                session['errors'].append(type(error).__name__ + ': ' + (str(error) or 'Interrupted'))
                print('PES_TEST_ERROR=' + session['errors'][-1], flush=True)
            finally:
                if session['build_started']:
                    try:
                        session['container_cleanup'] = cleanup_containers(session['ownership_token'])
                        if not session['container_cleanup']['complete']:
                            session['errors'].append('Test build containers remain')
                    except Exception as error:
                        session['errors'].append('container cleanup: ' + str(error))
                try:
                    session['build_log'] = collect_build_log(build_log, work, offset)
                except OSError as error:
                    session['errors'].append('build log: ' + str(error))
                session['completed'] = not session['errors']
                (work / 'session.json').write_text(json.dumps(session, indent=2) + '\n')
    archive = work.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz') as target:
        for path in sorted(work.rglob('*')):
            if (path.is_file() and not path.is_symlink() and path.name != 'emulator.log' and
                    not {'validation-package', 'validation-runtime', '__pycache__'}.intersection(path.relative_to(work).parts)):
                target.add(path, arcname=str(path.relative_to(work)), recursive=False)
    print('PES_TEST_ARCHIVE=' + str(archive), flush=True)
    print('Upload that archive only. No visual report is needed.', flush=True)
    return 0 if session['completed'] else 1


def main():
    if os.geteuid() == 0 or sys.argv[1:]:
        raise RuntimeError('Run as chreece without sudo or arguments')
    home = Path.home()
    deploy = home / '.local/state/shadps4-ngs2'
    deploy.mkdir(parents=True, exist_ok=True)
    cache = home / '.cache'
    cache.mkdir(exist_ok=True)
    with (deploy / 'deploy.lock').open('a') as deploy_lock, \
            (cache / 'shadps4-video-trace.lock').open('a') as trace_lock:
        fcntl.flock(deploy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(trace_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return execute_session(home)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        print('PES_VULKAN_FIX_TEST=FAIL: ' + (str(error) or 'Interrupted'), flush=True)
        sys.exit(1)
    finally:
        print('Returning to your existing SSH prompt.', flush=True)
