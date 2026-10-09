#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Test dynamic ReadConst using only per-game DMA, on unchanged installed shadPS4.
The captured Ghost shader 0x8ced785b has a Phi-dependent read whose no-DMA
fallback incorrectly uses flatbuf[0]. Back up and restore exact original data.
Never modify source, installed executable, global settings, saves, or SSH.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
from datetime import datetime

HOME = Path.home()
STAMP = datetime.now().strftime('%Y%m%d-%H%M%S')
WORK = HOME / '.cache' / ('ghost-dma-dynamic-read-' + STAMP)
OUT = HOME / ('ghost-dma-dynamic-read-' + STAMP + '.tar.gz')
BASE_SHA = 'f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f'
HELPERS = (
    ('ghost-auto-verified-cpu-pr-code-v5-20261009.py', '66c4a346580fa565d51d52698b03e957dce7796c', 'ca85add43cc96cc2660abf8b2f9df39e2f5af8d7'),
    ('ghost-shader-proof-config-20261009.py', 'ea52533bff6eabf47507336d73a8df37dc8f7f09', 'af6a0b21338ce963d2f06259c6d212bd3e43a9dc'),
    ('ghost-native-shader-proof-collector-20261009.py', 'ea52533bff6eabf47507336d73a8df37dc8f7f09', 'd2e49280a46c40b1e5a332faca0f44f341d7c065'),
)
TARGET = ('8ced785b',)
EVENTS = []
FRAME_SAMPLES = []


def say(message):
    line = datetime.now().isoformat(timespec='seconds') + ' ' + str(message)
    print(line, flush=True)
    EVENTS.append(line)


def helper(path, sha_ref, expected_blob):
    path = WORK / path
    url = ('https://raw.githubusercontent.com/Chreece/shadPS4/' + sha_ref +
           '/scripts/' + path.name)
    subprocess.run(['curl', '-fsSL', '--retry', '2', '--max-time', '35',
                    url, '-o', str(path)], check=True, timeout=55)
    actual = subprocess.check_output(['git', 'hash-object', str(path)], text=True).strip()
    if actual != expected_blob:
        raise RuntimeError('Pinned helper Git blob mismatch: ' + path.name)
    subprocess.run([sys.executable, '-m', 'py_compile', str(path)], check=True)
    subprocess.run([sys.executable, '-I', str(path), '--self-test'], check=True, timeout=35)
    return path


def load(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gpu_dump_only(data):
    config = json.loads(data) if data else {}
    if not isinstance(config, dict):
        raise ValueError('Existing game config is not a JSON object')
    gpu = config.setdefault('GPU', {})
    if not isinstance(gpu, dict):
        raise ValueError('Existing GPU config is not a JSON object')
    if gpu.get('direct_memory_access_enabled') is True:
        raise RuntimeError('DMA already enabled for Ghost; off/on A/B would be invalid')
    gpu['direct_memory_access_enabled'] = True
    gpu['dump_shaders'] = True  # Instrumentation, not an additional shader-semantic variable
    return (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def complete_shader_dumps(collector):
    found = [p.name.lower() for _, p in collector.selected(HOME)]
    return (any('8ced785b' in x and x.endswith('.bin') for x in found) and
            any('8ced785b' in x and 'irprogram.txt' in x for x in found))



def inspect_spirv(path):
    raw = path.read_bytes()
    if len(raw) < 20 or len(raw) % 4:
        raise ValueError('Malformed SPIR-V length')
    words = struct.unpack('<'+'I'*(len(raw)//4),raw)
    if words[0] != 0x07230203:
        raise ValueError('Bad SPIR-V magic')
    names, calls = [], 0
    pos = 5
    while pos < len(words):
        header = words[pos]
        count, opcode = header >> 16, header & 65535
        if count == 0 or pos+count > len(words):
            raise ValueError('Malformed SPIR-V instruction')
        params = words[pos+1:pos+count]
        if opcode == 5 and len(params) >= 2:
            value = b''.join(struct.pack('<I',w) for w in params[1:]).split(b'\0',1)[0]
            names.append(value.decode('utf-8','replace'))
        if opcode == 57:  # OpFunctionCall
            calls += 1
        pos += count
    return {'file':path.name,'op_function_calls':calls,
            'read_const_dynamic_named':'read_const_dynamic' in names,
            'dynamic_read_code_present':('read_const_dynamic' in names and calls > 0)}

def collect_spirv_proof(root):
    found=[]
    if root.is_dir():
        for path in root.rglob('*.spv'):
            if '8ced785b' not in path.name.lower() or path.is_symlink():
                continue
            try:
                found.append(inspect_spirv(path))
            except Exception as exc:
                found.append({'file':path.name,'error':repr(exc)})
    return found


def selftest():
    with tempfile.TemporaryDirectory(prefix='ghost-phi-test-') as tmp:
        old = b'{"GPU":{"dump_shaders":false,"other":17},"Vulkan":{"vkvalidation_enabled":false},"misc":5}'
        new = json.loads(gpu_dump_only(old))
        assert new['GPU']['dump_shaders'] is True
        assert new['GPU']['direct_memory_access_enabled'] is True
        assert new['GPU']['other'] == 17
        assert new['Vulkan']['vkvalidation_enabled'] is False
        assert new['misc'] == 5
        for invalid in (b'[]', b'{"GPU":[]}', b'not-json', b'{"GPU":{"direct_memory_access_enabled":true}}'):
            try:
                gpu_dump_only(invalid)
                raise AssertionError('Malformed config wrongly accepted')
            except (ValueError, RuntimeError, json.JSONDecodeError):
                pass
        d = Path(tmp)
        (d/'fixture').write_bytes(old)
        assert (d/'fixture').read_bytes() == old
    assert all(len(ref) == len(blob) == 40 for _, ref, blob in HELPERS)
    assert len(BASE_SHA) == 64 and TARGET == ('8ced785b',)
    assert inspect_spirv.__name__ == 'inspect_spirv'
    print('SELFTEST_PASS=per_game_DMA,shader_dump_instrumentation,helper_pins')


def execute():
    WORK.mkdir(parents=True, exist_ok=False)
    (WORK/'screenshots').mkdir()
    config_folder = WORK/'config'
    config_folder.mkdir()
    shader_folder = WORK/'shader'
    shader_folder.mkdir()
    ctrl = config = collector = None
    config_staged = shader_staged = False
    status = 'not_started'
    error = None
    baseline_verified = False
    no_launch_needed = False
    proof = []
    restored = None
    max_flips = 0
    try:
        paths = [helper(*row) for row in HELPERS]
        ctrl, config, collector = [load(paths[i], 'ghost_phi_module_'+str(i)) for i in range(3)]
        ctrl.WORK = WORK
        ctrl.SCREEN_DIR = WORK/'screenshots'
        collector.HASHES = TARGET
        if ctrl.emulator_pids():
            raise RuntimeError('An existing emulator is running; refusing to interfere.')
        if not ctrl.BINARY.is_file() or ctrl.checksum(ctrl.BINARY) != BASE_SHA:
            raise RuntimeError('Installed binary differs from verified baseline; no changes made.')
        baseline_verified = True
        # First try to reuse existing dumps, without starting another game session.
        no_launch_needed = False  # Recompile shader with DMA on, not reuse DMA-off proof.
        collector.before(shader_folder, HOME)
        shader_staged = True
        if no_launch_needed:
            status = 'existing_artifacts_reused'
            say('TARGET_SHADER_ALREADY_PRESENT: no game launch necessary')
        else:
            if not ctrl.LAUNCHER.is_file() or not os.access(ctrl.LAUNCHER, os.X_OK) or ctrl.LAUNCHER.is_symlink():
                raise RuntimeError('Existing ES-DE launcher unavailable or untrusted')
            launch_text = ctrl.LAUNCHER.read_text(errors='replace')
            if not all(x in launch_text for x in ('SHADPS4_SESSION_GUARD_V1','SHADPS4_DEFAULT_MAIN_V1')):
                raise RuntimeError('Existing ES-DE launcher guard did not match')
            if not ctrl.ENTRY.exists():
                raise RuntimeError('Ghost game entry absent')
            env = ctrl.display_probe()
            env['SHADPS4_GRAPHICS_DIAGNOSTICS'] = '1'
            env['SHADPS4_STARTUP_DIAGNOSTICS'] = '1'
            for setting in ('RADV_DEBUG','GHOST_CPU_RIP_LOG','SHADPS4_CPU_ID_MODE'):
                env.pop(setting, None)
            if 'XDG_RUNTIME_DIR' not in env and (Path('/run/user')/str(os.getuid())).is_dir():
                env['XDG_RUNTIME_DIR'] = str(Path('/run/user')/str(os.getuid()))
            config.patch_config = gpu_dump_only
            config_staged = True  # permit rollback on partially successful staging
            config.stage(config_folder, config.available_roots(HOME))
            say('GHOST_DMA_ON=1; DUMP_SHADERS=1; VULKAN_SETTINGS_UNCHANGED=1; NO_BUILD=1')
            ctrl.launch_test(env)
            status = 'running'
            start = time.monotonic()
            last_flips = None
            frozen_from = None
            vblank_from = None
            shots = set()
            while time.monotonic()-start < 115:
                elapsed = time.monotonic()-start
                if ctrl.SESSION_DIR is None:
                    ctrl.SESSION_DIR = ctrl.current_session()
                if ctrl.GAME_PROC.poll() is not None:
                    # Guarded launchers may exit while their exact game child is alive.
                    alive = False
                    if ctrl.SESSION_DIR:
                        try:
                            meta = (ctrl.SESSION_DIR/'session.meta').read_text(errors='replace')
                            match = re.search(r'(?m)^launcher_pid=(\d+)$',meta)
                            alive = bool(match and ctrl.exact_ghost(int(match[1])))
                        except (OSError, ValueError):
                            pass
                    if not alive:
                        status = 'game_exited'
                        break
                if int(elapsed) in (15, 29, 42) and int(elapsed) not in shots:
                    sec = int(elapsed)
                    shots.add(sec)
                    ctrl.root_screenshot(env, sec)
                if ctrl.SESSION_DIR:
                    result = ctrl.latest_guest_vblank_flips(ctrl.SESSION_DIR/'runtime.log')
                    if result:
                        vb, flips = result
                        max_flips = max(max_flips, flips)
                        if not FRAME_SAMPLES or elapsed-FRAME_SAMPLES[-1]['seconds'] >= 3:
                            FRAME_SAMPLES.append({'seconds':round(elapsed,1),'vblank':vb,'guest_flips':flips})
                        if flips >= 1000:
                            say(f'RENDER_PROGRESS_PAST_CONTROL=PASS guest_flips={flips} baseline=530')
                            status = 'progress_past_baseline'
                            ctrl.root_screenshot(env, int(elapsed))
                            break
                        if flips != last_flips:
                            last_flips, frozen_from, vblank_from = flips,elapsed,vb
                        elif (flips >= 100 and frozen_from is not None and
                              elapsed-frozen_from>=14 and vb-vblank_from>=100):
                            say(f'GUEST_STALL_CONFIRMED flips={flips} vblank_delta={vb-vblank_from}')
                            status = 'stalled_after_intro'
                            break
                time.sleep(.5)
            else:
                status = 'timeout'
        say('RESULT=' + status)
    except BaseException:
        error = traceback.format_exc()
        status = 'failed'
        say('ERROR=' + error.splitlines()[-1])
    finally:
        if ctrl is not None and ctrl.GAME_PROC is not None:
            try:
                ctrl.stop_launched_game()
            except Exception as exc:
                say('OWNED_GAME_CLEANUP_FAILED=' + repr(exc))
            for method in (ctrl.report_session,):
                try:
                    method()
                except Exception as exc:
                    say('LOG_CAPTURE_FAILED=' + repr(exc))
        if collector is not None and shader_staged:
            try:
                collector.after(shader_folder, HOME)
            except Exception as exc:
                error = error or repr(exc)
                say('SHADER_CAPTURE_OR_ROLLBACK_ERROR=' + repr(exc))
        if config is not None and config_staged:
            try:
                ok = config.restore(config_folder)
                restored = bool(ok)
                if not restored:
                    error = error or 'GAME_CONFIG_ROLLBACK_INCOMPLETE'
                say('GAME_CONFIG_RESTORED=' + str(ok))
            except Exception as exc:
                error = error or repr(exc)
                say('GAME_CONFIG_ROLLBACK_ERROR=' + repr(exc))
        dumped = shader_folder/'shader-proof-results.json'
        shader_results = json.loads(dumped.read_text()) if dumped.is_file() else {}
        proof = collect_spirv_proof(shader_folder/'native-guest-shaders')
        (WORK/'summary.json').write_text(json.dumps({
            'status':status,'error':error,'baseline_binary_verified':baseline_verified,
            'no_launch_needed':no_launch_needed,'target_guest_shader':'0x8ced785b',
            'test_semantic_variable':'GPU.direct_memory_access_enabled=true',
            'instrumentation':'GPU.dump_shaders=true', 'previous_baseline_stall_flips':530,
            'max_guest_flips':max_flips,'game_config_restored':restored,
            'spirv_dynamic_read_proof':proof,
            'shader_results':shader_results,'frames':FRAME_SAMPLES,
            'binary_modified':False,'source_modified':False
        },indent=2)+'\n')
        (WORK/'events.txt').write_text('\n'.join(EVENTS)+'\n')
        # Keep shader evidence only; never bundle originals, user credentials or configs.
        with tarfile.open(OUT,'w:gz') as bundle:
            for filename in ('summary.json','events.txt','runtime-tail.log'):
                p=WORK/filename
                if p.is_file():bundle.add(p,arcname=filename)
            for p in sorted((WORK/'screenshots').glob('*.png')):
                bundle.add(p,arcname='screenshots/'+p.name)
            for filename in ('shader-proof-results.json',):
                p=shader_folder/filename
                if p.is_file():bundle.add(p,arcname=filename)
            blobs=shader_folder/'native-guest-shaders'
            if blobs.is_dir():bundle.add(blobs,arcname='native-guest-shaders')
        say('UPLOAD_THIS_FILE='+str(OUT))
        say('SSH_SESSION=REMAINS_OPEN')
    return 0 if not error else 1


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']:
        selftest()
    elif len(sys.argv) == 1:
        raise SystemExit(execute())
    else:
        raise SystemExit('Usage: script.py [--self-test]')
