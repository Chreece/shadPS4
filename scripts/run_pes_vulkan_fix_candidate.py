#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the Vulkan corrections in local Docker, then launch and capture PES."""

import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.request

REVISION = '69d96cdb2a8155d5dd65bd6ad10e7376c3d13af7'
BRANCH = 'diag/pes-vulkan-fixes'
ARENA_FIX = '201124a6c533020ffd9cabf5d84fb6d8e207a864'
IMGUI_FIX = 'eaa2e17e42c6a083af6650c83bc94992adeb6d92'
HELPER_REVISION = '4621958f1e0e749d429245c43e511a87e0a66fd5'
RAW = 'https://raw.githubusercontent.com/Chreece/shadPS4/'
HELPERS = {
    'install_local_default.py': '32767d7efad83ce1778c9c7e4a624b0c15c4154edcdccdc932b7fc73cb17883f',
    'pes_current_baseline.py': '41b4da73dbc2b0fb64a306a441cd8f4b717bf6114e1a851f6753c333a68533ab',
    'trace_video_progress.py': 'c770af6e639464064ec543f99cb6181d75bf4b3bbe493ae6e8c015652d9b1a6b',
    'pes_frame_profile.py': '7b2f264f974b48ca8a98d3038404831326a7913efeeb2f7a2906d2fbec126df2',
    'collect_pes_runtime_context.py': '58cb93a9b52f2264e11d13259e379d676b7aa940c9522f7e80198943ae6ed612',
    'run_pes_startup_test.py': 'bea26850486af3f89c596bf6158b99f08d55a356df42814a07b224ff4f41eb47',
    'pes_graphics_launch.py': 'f4d1e82b02c3bc5c27d3bffce47f717248ba90b70ac1be68170717bd47175588',
    'pes_vulkan_validation.py': '04043e9a4c06832dc4b99388cdfff79550398b4aebff1976b2e72e46f338eed1',
}
MANIFEST_SHA256 = '75ab4c5ac256dc465541ed6ba0aec3978690dd11227d835fa9b11e22e90e663f'
TARGET_ERRORS = ('VUID-VkBufferCreateInfo-size-06409',
                 'VUID-VkMappedMemoryRange-size-01389',
                 'VUID-VkMappedMemoryRange-size-01390')


def download(url, expected, path):
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError('Download checksum mismatch: ' + path.name)
    path.write_bytes(data)


def prepare_helpers(home):
    work = Path(tempfile.mkdtemp(prefix='shadps4-vulkan-fixes-', dir=home))
    print('Preparing checksum-verified build and capture helpers...', flush=True)
    for name, digest in HELPERS.items():
        download(RAW + HELPER_REVISION + '/scripts/' + name, digest, work / name)
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
    print('PES_GAME_RESULT=UNVERIFIED; upload the archive and say whether the menu appeared', flush=True)


def run(home, manifest, installer, baseline, startup):
    manifest = dict(manifest, candidate_fixes={
        'sparse_arena_limit': ARENA_FIX,
        'imgui_mapping': IMGUI_FIX,
        'pes_runtime_result': 'unverified',
        'retained_tiling_fix': '1522b405a109f9f5f1db3c0852b62c1e27c2539b',
        'retained_image_refresh_fix': 'c293dbcadbcb545676947500936df69fcda7a8bf',
    })
    baseline.prepare(home, REVISION, BRANCH, manifest, installer.build)
    baseline.verify_installed(home)
    check_capture(startup.run(home, profile='frames', screenshots=True,
                              graphics=True, validation=True))


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
        manifest = prepare_helpers(home)
        run(home, manifest, importlib.import_module('install_local_default'),
            importlib.import_module('pes_current_baseline'),
            importlib.import_module('run_pes_startup_test'))


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print('PES_VULKAN_FIX_TEST=FAIL: ' + (str(error) or 'Interrupted'), flush=True)
        sys.exit(1)
    finally:
        print('Returning to your existing SSH prompt.', flush=True)
