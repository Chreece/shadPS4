#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build current main with retained fixes, trace PES startup, capture and close the test."""

import fcntl
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib
import json
import os
import re
from pathlib import Path
import sys
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid

REVISION = 'e31d619e875adaf898a5506da49557c632c4e4d8'
ARENA_FIX = '201124a6c533020ffd9cabf5d84fb6d8e207a864'
IMGUI_FIX = 'eaa2e17e42c6a083af6650c83bc94992adeb6d92'
HELPER_REVISION = '4621958f1e0e749d429245c43e511a87e0a66fd5'
HELPER_REVISIONS = {
    'pes_refresh_baseline.py': '19153e3113228c22b4f233487043d768469532f3',
    'pes_current_baseline.py': '334f662444665c4647101ced7a2938082d54fb20',
    'install_local_default.py': '19153e3113228c22b4f233487043d768469532f3',
    'run_pes_startup_test.py': '3be159501a0935153a4384d2f9e6d97d2117b894',
    'pes_test_cleanup.py': '9ff21ec091efa3f0f4889cee60bc79fdb2e2a5f8',
    'pes_frame_profile.py': 'ab5f09f3c6e602fecc0c164d9db1329c5c24f26c',
    'validate_pes_guest_wait.py': 'ab5f09f3c6e602fecc0c164d9db1329c5c24f26c',
    'pes_gpu_frame.py': '3be159501a0935153a4384d2f9e6d97d2117b894',
    'pes_gpu_frame.cpp': '3be159501a0935153a4384d2f9e6d97d2117b894',
    'pes_graphics_launch.py': '40bde232e0e9c11b1712c182d28b4b3fbbedf397',
}
RAW = 'https://raw.githubusercontent.com/Chreece/shadPS4/'
HELPERS = {
    'pes_refresh_baseline.py': '2d0ba312366bbcbd1ea22b60528e0110de69a857218f1735afb87f894998b49f',
    'install_local_default.py': '8c7a85d2819775ad853d692ba754f905ae2d0306864de31bef7dd91e81a2288c',
    'pes_current_baseline.py': 'b044656a37aadbb54e27903125cc6e4648fdd6d171741b0039a37f7ff6b66224',
    'trace_video_progress.py': 'c770af6e639464064ec543f99cb6181d75bf4b3bbe493ae6e8c015652d9b1a6b',
    'validate_pes_guest_wait.py': '106368e005251edfc1eb9d3b35dc9defad10640ae70d9eec15efce39d2e98182',
    'pes_frame_profile.py': '372a6ae31acac770d10d5f93ae0a9a190140757c2d0c125d308573c96f1842f2',
    'collect_pes_runtime_context.py': '58cb93a9b52f2264e11d13259e379d676b7aa940c9522f7e80198943ae6ed612',
    'run_pes_startup_test.py': '1df09f76e745ddd34c62e81263f343c2cd113aa5afc5b2f23bb90a91f6a717ad',
    'pes_test_cleanup.py': 'fe5d13cf6fe1cee2c1e50c3329c8092f220ec3ca27de7834ab4537d744330b96',
    'pes_graphics_launch.py': '43665b3cd96c540cae5bf82cf8b33434119149f5c8b1700c86412bade0bcf570',
    'pes_vulkan_validation.py': '04043e9a4c06832dc4b99388cdfff79550398b4aebff1976b2e72e46f338eed1',
    'pes_gpu_frame.py': 'ddb38620a125a1aefc6a88e2b7a65990132901367f4c45e3dd5d182341e75ee1',
    'pes_gpu_frame.cpp': 'a17fe13ed30d67b21395c1ae7b0d4e04105557b5a39c991724e7bcf37b3adb23',
}
MANIFEST_SHA256 = 'af44ad689c6dfbe49d65e455a44cfa3537717c40b774affe7743725706645887'
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



def collect_startup_trace(work):
    summary = {'events': {}, 'api_call_lower_bounds': {}, 'api_return_samples': {},
               'first_ms': None, 'last_ms': None, 'trace_observed': False}
    marker = re.compile(r'STARTUP_DIAG ms=(\d+) thread=\[(.*?)\] event=([\w-]+) (.*)')
    api = re.compile(r'api=(\w+) call=(\d+)(?: result=([^ ]+))?')
    output = work / 'startup-trace.log'
    with output.open('w') as target:
        for path in sorted((work / 'startup').glob('console.*.log')):
            with path.open(errors='replace') as source:
                for line in source:
                    match = marker.search(line)
                    if not match:
                        continue
                    target.write(match[0].rstrip() + '\n')
                    when, event = int(match[1]), match[3]
                    summary['first_ms'] = when if summary['first_ms'] is None else min(summary['first_ms'], when)
                    summary['last_ms'] = when if summary['last_ms'] is None else max(summary['last_ms'], when)
                    summary['events'][event] = summary['events'].get(event, 0) + 1
                    call = api.match(match[4])
                    if call:
                        name, count, result = call[1], int(call[2]), call[3]
                        summary['api_call_lower_bounds'][name] = max(
                            summary['api_call_lower_bounds'].get(name, 0), count)
                        if result is not None:
                            values = summary['api_return_samples'].setdefault(name, {})
                            if result in values or len(values) < 256:
                                values[result] = values.get(result, 0) + 1
    summary['trace_observed'] = bool(summary['events'].get('api-enter') and
                                     summary['events'].get('api-return') and
                                     summary['events'].get('file-read'))
    (work / 'startup-trace.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def validate_wait(work, token):
    preflight = importlib.import_module('validate_pes_guest_wait')
    preflight.validate(work / 'guest-wait-preflight', token)
    return preflight


def refresh_source(work, manifest):
    refresh = importlib.import_module('pes_refresh_baseline')
    return refresh.prepare(work / 'source-refresh', REVISION, manifest)


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
        return installer.build(*args, cleanup_token=session['ownership_token'],
                               local_source=session['source']['source_directory'])
    baseline.prepare(home, session['candidate_revision'], session['source']['source_branch'], manifest, build)
    preflight = validate_wait(work, session['ownership_token'])
    collector = importlib.import_module('pes_gpu_frame').prepare(
        home, work / 'gpu-frame', session['ownership_token'])
    baseline.verify_installed(home)
    try:
        result = startup.run(home, profile='frames', screenshots=True,
                             graphics=True, validation=True, close_after=True,
                             work=work / 'startup', archive=False, trace_delay_seconds=40,
                             gpu_collector=collector)
    finally:
        session['startup_trace'] = collect_startup_trace(work)
    if not result.get('process_cleanup', {}).get('complete'):
        raise RuntimeError('PES must be closed before GPU replay')
    if collector.captured is not None:
        collector.replay()
    preflight.check_samples(json.loads((work / 'startup/frames/frames-trace.json').read_text()))
    check_capture(result)
    if not result.get('gpu_frame_captured'):
        raise RuntimeError('Complete GPU frame missing')
    if not session['startup_trace']['trace_observed']:
        raise RuntimeError('Native startup API/file trace missing; upload the printed archive')
    print('PES_STARTUP_TEST=PASS; startup calls, files, GPU frame and screenshots archived', flush=True)


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
                session['source'] = refresh_source(work, manifest)
                session['candidate_revision'] = session['source']['revision']
                manifest = session['source']['manifest']
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
            if (path.is_file() and not path.is_symlink() and path.name not in {'emulator.log', 'gpu-runtime.tar'} and
                    not (path.suffix == '.rdc' and path.stat().st_size > 24 * 1024 * 1024) and
                    not {'validation-package', 'validation-runtime', '__pycache__', 'gpu-runtime', 'gpu-build', 'source-checkout'}.intersection(path.relative_to(work).parts)):
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
        print('PES_GPU_TEST=FAIL: ' + (str(error) or 'Interrupted'), flush=True)
        sys.exit(1)
    finally:
        print('Returning to your existing SSH prompt.', flush=True)
