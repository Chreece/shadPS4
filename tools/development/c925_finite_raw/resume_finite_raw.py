#!/usr/bin/env python3
"""Resume exact native PR100; verify model release instead of rebuilding shaders.
No C++/configuration writes, service restarts, forced GPU eviction or injected input.
"""
from __future__ import annotations
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess as sp
import sys
import tarfile
import time
import traceback

EXPECTED = '9f0f6c9fe83f15aec6dea1dcf3f23226d944472daa0bdb6258cff13a95f219d3'
PREVIOUS = 'got-c925-finite-raw-images-20261028T171004960983Z'
PROFILE_TRIAL = 'got-c925-remaining-shaders-20261028T155744943557Z'
BASE = 'c925-pr95-scalar-bounds-20261028T112756671630889Z'
RUNTIME_SHA = 'df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250'
MIN_FREE = 8 * 1024**3
KEPT = {
 'src/video_core/renderer_vulkan/vk_rasterizer.cpp': 'ebed2441ceda3d6b3e6a6ebe1afe7469ec35f9d8a99c3b7f0feb1a3fb533b4a0',
 'src/video_core/texture_cache/texture_cache.cpp': '3273c68d3c8a9932867603bb6cb12e6b546b228cfa32c7c4267a4d0de4207ed0',
}
# These are native-archive SHA256 values, not inferred branch contents.
SOURCES = {
 'src/shader_recompiler/ir/passes/resource_discover_pass.cpp': '7cb823f358f1c243456ad5db834097df55b0a1a38c1e8833af4a8c4ad9b3d97f',
 'src/shader_recompiler/ir/passes/resource_pass.h': '0913930b9b2e946befeecd3ef01f5f2f96ff5806f560150b3bca799115ca5a74',
 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp': 'f9616b3f42ad31271b6b295a01458706108b13beda995434f360f8ba7c82a76b',
 'src/shader_recompiler/ir/opcodes.inc': '79768a7254dd61a3018c62d3d17f50700a0e5f3400bae132800c113f79f59bc9',
 'src/shader_recompiler/ir/ir_emitter.h': '10c2ab32f127f871ebbabc4d4191f538e40c00680231602b5fc4dad63e1a538a',
 'src/shader_recompiler/ir/ir_emitter.cpp': '8b979c5f79b0e18afb733c2f530395c2413d46b5b37cf77cbe1357d45acc17c5',
 'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h': '32c1a30b77b3cc9a1d0cab265ce08d4a5e879caf017d6e6ab10a5045f3795f7f',
 'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp': '72628d2b5e2447677296a1463d8d10e2e960d74009cb9f22b9a2085857da4677',
 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp': 'a8a6067d65e8683f29b5cd87100b223c39508fba84cea186e6baa1b6f39f5044',
 'src/shader_recompiler/ir/passes/finite_raw_image_plan.h': '02fc08b56b1307487a2d5883127105afb7ce035e66255cae7bad944791f9bc53',
}


def dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def model_name(m: dict) -> str:
    return m.get('name') or m.get('model') or ''


def safe_model(m: dict) -> dict:
    return {k: m[k] for k in ('name', 'model', 'digest', 'size_vram', 'context_length',
                              'expires_at', 'runner') if k in m}


def verify_sources(u, source: Path) -> None:
    root = source.resolve()
    for rel, expected in {**SOURCES, **KEPT}.items():
        p = source / rel
        if p.is_symlink() or not p.resolve().is_relative_to(root) or u.sha(p) != expected:
            raise RuntimeError('NATIVE_SOURCE_OR_ACCEPTED_FIX_CHANGED:' + rel)


def verified_profile(candidate: Path, prior: dict) -> Path:
    profile = Path(prior['diagnostic_profile']).resolve()
    if profile.name != 'user' or not profile.is_relative_to((candidate / 'runs').resolve()):
        raise RuntimeError('PROFILE_OUTSIDE_ISOLATED_CANDIDATE')
    if not (profile / 'config.json').is_file():
        raise RuntimeError('ISOLATED_PROFILE_MISSING')
    for p in profile.rglob('*'):
        if p.is_symlink():
            raise RuntimeError('ISOLATED_PROFILE_SYMLINK_REFUSED')
    for p in (profile/'config.json', profile/'custom_configs/CUSA11456.json'):
        if p.is_file():
            home = json.loads(p.read_text()).get('General', {}).get('home_dir', '')
            if home and not Path(home).expanduser().resolve().is_relative_to(profile):
                raise RuntimeError('EXTERNAL_SAVE_ROOT_REFUSED')
    return profile


def verified_headroom(u, api, evid: Path, pending: list, result: dict, *,
                      grace: float = 45, clock=time.monotonic, sleep=time.sleep) -> None:
    """Read-only until actual free VRAM is insufficient; one unload per initial model.

    API response, subsequent resident-model lists and memory measurements are separate
    evidence. Never mistake HTTP success, a missing model or elapsed time for free VRAM.
    """
    free = u.free_vram()
    result['vram_before_bytes'] = free
    u.emit(f'VRAM_FREE_BYTES={free} REQUIRED={MIN_FREE}')
    if free < 0:
        raise RuntimeError('VRAM_COUNTER_UNAVAILABLE_NO_UNLOAD')
    if free >= MIN_FREE:
        result['vram_at_launch_bytes'] = free
        result['ollama_action'] = 'NONE_ALREADY_SUFFICIENT'
        return

    endpoint = (None, 'http://127.0.0.1:7869')
    try:
        data = api.request_at(endpoint, '/api/ps', timeout=4)
        if not isinstance(data.get('models'), list):
            raise RuntimeError('MODEL_LIST_MALFORMED')
        api.selected = endpoint
        api.record(endpoint, 'KNOWN_HOST_PORT_OK models=' + str(len(data['models'])))
    except Exception as exc:
        api.record(endpoint, type(exc).__name__ + ':' + str(exc)[:200])
        data = api.discover()  # Same tested local/Docker-only fallback; no container restart.
    models = data['models']
    dump(evid/'ollama-before.json', data)
    # Validate all restoration metadata before the first mutating request.
    names = set()
    for m in models:
        name, context = model_name(m), m.get('context_length')
        if not name or name in names or type(context) is not int or context <= 0:
            raise RuntimeError('MODEL_RESTORATION_METADATA_INVALID_NO_UNLOAD')
        names.add(name)
        u.model_expiry(m)
    if len(models) > 16:
        raise RuntimeError('UNEXPECTED_MODEL_COUNT_NO_UNLOAD')

    observations = []
    for index, m in enumerate(models):
        if u.free_vram() >= MIN_FREE:
            break
        # A CPU-only model cannot supply the needed GPU memory.
        if m.get('size_vram') == 0:
            continue
        name = model_name(m)
        pending.append(m)  # A timed-out POST may have succeeded; restoration must still run.
        dump(evid/'ollama-restore-pending.json', {'endpoint': api.selected, 'models': pending})
        body = {'model': name, 'prompt': '', 'stream': False, 'keep_alive': 0,
                'options': {'num_ctx': m['context_length']}}
        receipt = {'model': name, 'endpoint': api.selected, 'request': body}
        u.emit('REQUESTING_MODEL_UNLOAD=' + name)
        try:
            response = api.request('/api/generate', body, timeout=25)
            # Keep the actual status fields, not generated text/system-prompt content.
            receipt['response'] = {k: response[k] for k in ('model', 'done', 'done_reason',
                'total_duration', 'load_duration', 'error') if k in response}
            receipt['response_characters'] = len(str(response.get('response', '')))
        except Exception as exc:
            receipt['request_error'] = type(exc).__name__ + ':' + str(exc)[:500]
        dump(evid/f'ollama-unload-{index}.json', receipt)

        start = clock()
        observed_absent = False
        consecutive = 0
        present = None
        while True:
            now = clock()
            obs = {'elapsed_after_unload_seconds': round(now-start, 3), 'target': name,
                   'free_vram_bytes': u.free_vram()}
            try:
                state = api.request('/api/ps', timeout=3)
                resident = state['models']
                obs['models'] = [safe_model(x) for x in resident]
                present = any(model_name(x) == name or
                              (m.get('digest') and x.get('digest') == m['digest']) for x in resident)
                obs['target_present'] = bool(present)
            except Exception as exc:
                present = None
                obs['model_list_error'] = type(exc).__name__ + ':' + str(exc)[:250]
            observations.append(obs)
            dump(evid/'vram-and-model-observations.json', observations)
            free = obs['free_vram_bytes']
            result['vram_at_launch_bytes'] = free
            u.emit(f'MODEL_RELEASE elapsed={now-start:.1f}s present={present} free_bytes={free}')
            if free < 0:
                raise RuntimeError('VRAM_COUNTER_LOST_NO_GAME')
            if present is True and observed_absent:
                result['headroom_result'] = 'MODEL_REAPPEARED_AFTER_ABSENCE'
                raise RuntimeError('MODEL_RELOADED_EXTERNALLY_NO_REPEATED_UNLOAD')
            if present is False:
                observed_absent = True
            consecutive = consecutive + 1 if present is False and free >= MIN_FREE else 0
            if consecutive >= 2:
                result['headroom_result'] = 'MODEL_ABSENT_AND_VRAM_VERIFIED_TWICE'
                return
            if clock()-start >= grace:
                break
            sleep(min(1.0, max(0, grace-(clock()-start))))
        if not observed_absent or present is not False:
            result['headroom_result'] = 'UNLOAD_NOT_CONFIRMED_BY_MODEL_LIST'
            raise RuntimeError('MODEL_UNLOAD_NOT_CONFIRMED_NO_GAME')
        # A model is gone but other residents/allocations still consume memory.
        # Move to the next originally captured model once, never kill a service.
    result['vram_at_launch_bytes'] = u.free_vram()
    if result['vram_at_launch_bytes'] < MIN_FREE:
        result['headroom_result'] = 'MODEL_RELEASE_INSUFFICIENT_OR_NO_GPU_MODEL'
        raise RuntimeError('VRAM_REMAINS_BELOW_8GIB_NO_GAME')
    result['headroom_result'] = 'VRAM_SUFFICIENT_WITHOUT_ADDITIONAL_UNLOAD'


def memory_evidence(u, evid: Path) -> None:
    """Bounded, read-only visibility. Missing permissions are not a clean result."""
    gpu = []
    for p in Path('/sys/class/drm').glob('card*/device/mem_info_vram_total'):
        try:
            gpu.append({'device': str(p.parent.resolve()), 'total': int(p.read_text()),
                        'used': int(p.with_name('mem_info_vram_used').read_text())})
        except (OSError, ValueError) as exc:
            gpu.append({'path': str(p), 'error': str(exc)})
    dump(evid/'gpu-memory-final.json', gpu)
    for exe, args, name in (
        ('rocm-smi', ['--showmeminfo', 'vram', '--showpids'], 'gpu-processes.log'),
        ('amd-smi', ['process'], 'amd-processes.log')):
        if shutil.which(exe):
            try:
                (evid/name).write_bytes(u.command([exe, *args], timeout=10))
            except Exception as exc:
                (evid/name).write_text(type(exc).__name__ + ':' + str(exc))
            break


def capture_fresh(u, profile: Path, evid: Path, started: float) -> None:
    log = (evid/'game.log').read_text(errors='replace') if (evid/'game.log').is_file() else ''
    hashes = set(re.findall(r'FiniteRawImageRead: shader=0x([0-9a-fA-F]+)', log))
    hashes.add('ee963aae')  # Evidence-file filter only; never a renderer/runtime condition.
    inventory, used = [], 0
    dumps = profile/'shader/dumps'
    for p in sorted(dumps.glob('*')):
        if not p.is_file() or p.is_symlink() or p.suffix not in ('.txt', '.spv'):
            continue
        if not any(h.lower() in p.name.lower() for h in hashes):
            continue
        st = p.stat()
        if st.st_mtime < started or st.st_size > 20*1024**2 or used+st.st_size > 60*1024**2:
            continue
        used += st.st_size
        target = evid/'fresh-shaders'/p.name
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(p, target)
        inventory.append({'file': p.name, 'sha256': u.sha(p), 'mtime': st.st_mtime})
    dump(evid/'fresh-shader-inventory.json', inventory)


def main(u=None, project: Path | None = None) -> int:
    if u is None:
        helper = Path(__file__).with_name('resume_c925_revalidation.py')
        if hashlib.sha256(helper.read_bytes()).hexdigest() != RUNTIME_SHA:
            raise RuntimeError('RUNTIME_HELPER_HASH_MISMATCH')
        spec = importlib.util.spec_from_file_location('bounded_runtime', helper)
        if spec is None or spec.loader is None:
            raise RuntimeError('RUNTIME_HELPER_LOAD_FAILED')
        u = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(u)
    project = project or Path.home()/'projects/shadps4'
    logs = project/'logs'
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evid = logs/('got-c925-finite-raw-resume-'+stamp)
    evid.mkdir(parents=True)
    cand = project/'GhostOfTsushima/experiments'/BASE
    source, binary = cand/'source', cand/'build/shadps4'
    result = {'schema_version': 2, 'record_id': evid.name, 'recorded_on': dt.date.today().isoformat(),
        'journal_scope': 'UNIVERSAL_SHADPS4', 'game_id': 'CUSA11456', 'tested_games': [], 'pr': 100,
        'build': 'REUSE_NATIVE_PASS_NO_REBUILD', 'source_changes': False, 'config_changes': False,
        'game': {'result': 'NOT_RUN'}, 'expected_binary_sha256': EXPECTED, 'resume_of': PREVIOUS,
        'main_promotion': False, 'graphics_fixed': 'UNVERIFIED'}
    u.emit('EVIDENCE_DIRECTORY='+str(evid)); u.emit('BUILD=SKIPPED_NATIVE_PR100_ALREADY_PASSED')
    locks, pending, originals = [], [], {}
    api = u.Ollama(evid)
    rc = 1
    try:
        for name in ('.got-pr95-scalar-bounds-build.lock', '.got-indexed-revalidation.lock', '.got-finite-raw-image.lock'):
            f = (logs/name).open('a+')
            try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except Exception:
                f.close(); raise
            locks.append(f)
        prior = json.loads((logs/PREVIOUS/'trial.json').read_text())
        if prior.get('build') != 'PASS' or prior.get('candidate_sha256') != EXPECTED:
            raise RuntimeError('NATIVE_BUILD_RECORD_MISMATCH')
        if u.sha(binary) != EXPECTED:
            raise RuntimeError('CANDIDATE_CHANGED_NO_GAME')
        verify_sources(u, source)
        result['source_sha256_verified'] = {**SOURCES, **KEPT}
        if u.emulator_pids():
            raise RuntimeError('ANOTHER_EMULATOR_RUNNING_NO_GAME')
        for proc in Path('/proc').glob('[0-9]*'):
            try:
                if b'ninja' in (proc/'cmdline').read_bytes() and (proc/'cwd').resolve() == cand/'build':
                    raise RuntimeError('CANDIDATE_BUILD_ACTIVE_NO_GAME')
            except OSError:
                pass
        profile = verified_profile(cand, json.loads((logs/PROFILE_TRIAL/'trial.json').read_text()))
        result['private_profile'] = str(profile)
        old_protection = json.loads((logs/PREVIOUS/'original-files-before.json').read_text())
        if not isinstance(old_protection, dict):
            raise RuntimeError('PROTECTED_FILE_MANIFEST_INVALID')
        protected = project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
        if not protected.is_file():
            raise RuntimeError('PROTECTED_EXECUTABLE_MISSING')
        originals = {p: u.sha(p) if Path(p).is_file() else None for p in old_protection}
        originals[str(protected)] = u.sha(protected)
        dump(evid/'original-files-before.json', originals)
        screen = os.environ.get('DISPLAY') or ':0'
        if not shutil.which('xdpyinfo') or not (shutil.which('import') or shutil.which('maim')):
            raise RuntimeError('DISPLAY_CAPTURE_TOOLS_MISSING')
        (evid/'display.log').write_bytes(u.command(['env', 'DISPLAY='+screen, 'xdpyinfo'], timeout=8))
        eboot = Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
        if not eboot.is_file():
            raise RuntimeError('GAME_FILE_MISSING')
        verified_headroom(u, api, evid, pending, result)
        # Never reduce the threshold because one previous check happened to pass.
        if u.emulator_pids() or u.sha(binary) != EXPECTED:
            raise RuntimeError('LAUNCH_PREFLIGHT_CHANGED')
        verify_sources(u, source)
        result['vram_immediately_before_launch_bytes'] = u.free_vram()
        if result['vram_immediately_before_launch_bytes'] < MIN_FREE:
            raise RuntimeError('VRAM_DROPPED_BEFORE_LAUNCH_NO_GAME')
        started = time.time()
        result['game'] = u.run_game(binary, eboot, profile.parent, evid, screen)
        result['tested_games'] = ['CUSA11456']
        text = (evid/'game.log').read_text(errors='replace')
        keys = ('FiniteRawImageRead:', 'FiniteRawImageSharp:', 'IndexedExplicitLodBound',
                'INDEXED_TABLE_REVALIDATE', 'INDEXED_MATERIAL_BIND_REJECT',
                'Sharp source was not flatenned', 'StencilReferenceExactMerged',
                'Stencil test requires', 'VK_ERROR_DEVICE_LOST', 'Device lost during submit')
        result['sample_limited_events'] = {key: text.count(key) for key in keys}
        capture_fresh(u, profile, evid, started)
        result['status'] = ('BOUNDED_MENU_CAPTURE_FINISHED_VISUAL_PENDING'
                            if result['game']['time_limit_reached'] else 'EARLY_GAME_EXIT')
        rc = 0 if result['game']['time_limit_reached'] or result['game']['exit_code'] == 0 else 1
    except (Exception, KeyboardInterrupt) as exc:
        result['status'] = str(exc) or type(exc).__name__
        (evid/'failure.txt').write_text(traceback.format_exc())
        u.emit('STOP='+result['status'])
        rc = 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        # Gather the blocked state BEFORE restoration masks it; never let a
        # diagnostic failure prevent restoration of models we attempted to unload.
        if result['game'].get('result') == 'NOT_RUN' and 'vram_before_bytes' in result:
            try: memory_evidence(u, evid)
            except Exception as exc: result['memory_evidence_error'] = str(exc)
        if pending:
            try:
                result['ollama_restore'] = u.restore_models(api, pending, evid)
                if any(x['result'] in ('RESTORE_FAILED', 'RESTORE_NOT_VERIFIED')
                       for x in result['ollama_restore']): rc = 1
            except Exception as exc:
                result['restore_error'] = str(exc); rc = 1
        try:
            result['original_files_changed'] = [p for p, h in originals.items()
                if (u.sha(p) if Path(p).is_file() else None) != h]
            if result['original_files_changed']: rc = 1
            if binary.is_file():
                result['candidate_after_sha256'] = u.sha(binary)
                if result['candidate_after_sha256'] != EXPECTED: rc = 1
        except Exception as exc:
            result['verification_error'] = str(exc); rc = 1
        result['runner_exit_code'] = rc
        dump(evid/'trial.json', result)
        archive = Path(str(evid)+'.tar.gz')
        with tarfile.open(archive, 'w:gz') as tf: tf.add(evid, arcname=evid.name)
        u.emit('RESULT='+result.get('status', 'UNKNOWN')); u.emit('EVIDENCE='+str(archive))
        for f in locks: f.close()
    return rc


if __name__ == '__main__':
    def terminated(signum, frame):
        raise KeyboardInterrupt('TERMINATED_BY_USER')
    signal.signal(signal.SIGTERM, terminated)
    sys.exit(main())
