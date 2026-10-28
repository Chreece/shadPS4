#!/usr/bin/env python3
"""Capture the real input/output shaders of an immutable native candidate.

No rebuild, compiler patch, compatibility change, cache reuse or injected input.
Only GPU.dump_shaders is enabled in a private copied profile. Source worktree and
copied executable are isolated; the restored PR94 worktree is never checked out.
"""
from __future__ import annotations
import argparse
import ast
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import traceback

ROOT = Path.home() / 'projects/shadps4'
REPO = ROOT / 'Upstream/tests/gow-hle-abi-20261027-194629-907086/source'
LOGS = ROOT / 'logs'
BASE = 'bbe1a72840733afccba5f48fb4b5eb29a1c96128'
SOURCE = '60f87071d0c19c968253c9c25e3e5c318e756994'
ELF = '730b87e677e0b0c99947a78947c62904e6b7aa292455efa9e69b69f0f60afff4'
UPSTREAM = '0cd6e4f2cb6617af12eb8267cca848cc41ad20d7'
CANDIDATE = ROOT / 'GodOfWarRagnarok/tests/gow-readconst-restore-20261028T171639Z-3709330/candidate/shadps4'
TEMPLATE = LOGS / 'gow-readconst-restore-20261028T171639Z-3709330-evidence/native-game-runner.sh'
TEMPLATE_SHA = '3b6c7f946bdcfae9730fc2a43d9b02232b34baed9e6b0e247ffa921c33665856'
PROTECTED = {
    ROOT / 'GodOfWarRagnarok/build/shadps4': '8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e',
    REPO.parent / 'build/shadps4': '65fafc436831d76380db349b89c89fe610d16325981870ac073d8b9ed0f010b9',
    REPO.parent / 'old-binaries/pr91-46195c52-native-shadps4': '46195c52e9dd3fb3921b0b5c99a233edf4b484ecceb7794c12ae864626d6b3af',
    CANDIDATE: ELF,
}
LOCKS = ('real-shader-capture.lock', 'gow-upstream-got-build.lock',
         'shadps4-gow-srt-phi.lock', 'gow-hle-stack-build.lock')
SHADER_NAME = re.compile(r'^(?:cs|fs|vs|gs|hs|ls|es)_[0-9a-fA-Fx]+_[0-9]+[A-Za-z0-9_.-]*\.(?:spv|bin)$')
MAX_FILE = 16 * 1024 * 1024
MAX_BYTES = 128 * 1024 * 1024
MAX_COUNT = 2048
SOURCE_PATHS = ('src/shader_recompiler', 'src/video_core/renderer_vulkan',
                'src/video_core/buffer_cache', 'src/video_core/host_shaders',
                'src/video_core/amdgpu', 'src/common/types.h', 'src/common/serdes.h',
                'src/core/emulator_settings.h', 'src/video_core/cache_storage.cpp')


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def run(args, timeout=60):
    result = subprocess.run([str(x) for x in args], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=timeout, check=False)
    require(result.returncode == 0,
            'COMMAND_FAILED=' + str(args[0]) + '\n' + result.stderr.decode(errors='replace')[-4000:])
    return result.stdout


def git(*args):
    return run(['git', '-C', REPO, *args]).decode().strip()


def verify(path, wanted):
    require(path.is_file() and not path.is_symlink() and sha(path) == wanted,
            'PIN_MISMATCH=' + str(path))


def json_write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def enable_dumps(config, evidence):
    """Executed in the inner runner only after it has made the private profile."""
    require(config.is_file() and not config.is_symlink(), 'PRIVATE_CONFIG_MISSING')
    settings = json.loads(config.read_text())
    require(isinstance(settings, dict), 'CONFIG_NOT_OBJECT')
    before = copy.deepcopy(settings)
    gpu, vulkan = settings.get('GPU'), settings.get('Vulkan')
    require(isinstance(gpu, dict) and isinstance(vulkan, dict), 'CONFIG_GROUP_MISSING')
    require(gpu.get('patch_shaders', False) is False, 'SHADER_PATCHES_ENABLED_REFUSE')
    require(gpu.get('null_gpu', False) is False, 'NULL_GPU_ENABLED_REFUSE')
    require(vulkan.get('pipeline_cache_enabled', False) is False, 'CACHE_REUSE_ENABLED_REFUSE')
    require(gpu.get('dump_shaders', False) is False, 'DUMP_SETTING_CHANGED_SINCE_REFERENCE')
    gpu['dump_shaders'] = True
    reconstructed = copy.deepcopy(settings)
    if 'dump_shaders' in before['GPU']:
        reconstructed['GPU']['dump_shaders'] = before['GPU']['dump_shaders']
    else:
        del reconstructed['GPU']['dump_shaders']
    require(reconstructed == before, 'NON_DIAGNOSTIC_CONFIG_CHANGE_REFUSED')
    json_write(config, settings)
    json_write(evidence, {
        'changed': [{'key': 'GPU.dump_shaders', 'before': False, 'after': True}],
        'pipeline_cache_enabled': False,
        'shader_patching_enabled': False,
        'compatibility_settings_changed': False,
        'private_profile_only': True,
    })
    print('PRIVATE_PROFILE_SHADER_DUMP=ON_ONLY_CHANGE', flush=True)


def derive_runtime(original, runtime_root, self_path):
    require(hashlib.sha256(original).hexdigest() == TEMPLATE_SHA, 'RUNTIME_TEMPLATE_CHANGED')
    text = original.decode()
    old = 'CANDIDATE="/home/chreece/projects/shadps4/Upstream/tests/gow-hle-abi-20261027-194629-907086"'
    require(text.count(old) == 1, 'CANDIDATE_TEMPLATE_CHANGED')
    text = text.replace(old, 'CANDIDATE=' + __import__('shlex').quote(str(runtime_root)))
    require(text.count('gow-readconst-restore-game-') == 2, 'RUN_TAG_TEMPLATE_CHANGED')
    text = text.replace('gow-readconst-restore-game-', 'gow-real-shaders-game-')
    text = text.replace('RESTORED_AUTOMATIC_DYNAMIC_READCONST_GOW', 'GENERATED_SHADER_EVIDENCE_SAME_ELF')
    anchor = '\nPYPROFILE\nSTATE="RUN_REAL_GOW_CUSA34384_OFFICIAL_PR5479_PR88"'
    require(text.count(anchor) == 1, 'PRIVATE_PROFILE_ANCHOR_CHANGED')
    command = '\nPYPROFILE\npython3 -B ' + __import__('shlex').quote(str(self_path)) + \
              ' --enable-private-dumps "$WORK/user/config.json" "$EVIDENCE/diagnostic-config-change.json"\n' + \
              'STATE="RUN_REAL_GOW_CUSA34384_OFFICIAL_PR5479_PR88"'
    text = text.replace(anchor, command)
    require('--seconds 118' in text and 'shutil.copy2(old,dest)' in text and
            'GOW_COHERENCE_PARENT_LOCKS' in text and SOURCE in text and ELF in text,
            'RUNTIME_GUARDS_MISSING')
    return text


def collect_shaders(dump_root, destination):
    """Copy only bounded regular shader files, never a whole user profile."""
    inventory = {'files': [], 'rejected': [], 'total_bytes': 0}
    if not dump_root.is_dir() or dump_root.is_symlink():
        inventory['status'] = 'DUMP_DIRECTORY_MISSING'
        return inventory
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(dump_root.iterdir()):
        if path.is_symlink() or not path.is_file() or not SHADER_NAME.fullmatch(path.name):
            inventory['rejected'].append({'name': path.name, 'reason': 'not_allowlisted_regular_shader'})
            continue
        size = path.stat().st_size
        if size <= 0 or size > MAX_FILE or inventory['total_bytes'] + size > MAX_BYTES or len(inventory['files']) >= MAX_COUNT:
            inventory['rejected'].append({'name': path.name, 'reason': 'capture_size_limit', 'size': size})
            continue
        # An O_NOFOLLOW descriptor prevents replacing a checked path with a symlink.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            require(stat.S_ISREG(st.st_mode) and st.st_size == size, 'SHADER_CHANGED_DURING_READ')
            with os.fdopen(fd, 'rb', closefd=False) as f:
                data = f.read(MAX_FILE + 1)
            require(len(data) == size, 'SHADER_SIZE_CHANGED')
        finally:
            os.close(fd)
        if path.suffix == '.spv' and (len(data) < 20 or len(data) % 4 or data[:4] != b'\x03\x02\x23\x07'):
            inventory['rejected'].append({'name': path.name, 'reason': 'invalid_spirv_header', 'size': size})
            continue
        (destination / path.name).write_bytes(data)
        inventory['files'].append({'name': path.name, 'size': size, 'sha256': hashlib.sha256(data).hexdigest()})
        inventory['total_bytes'] += size
    inventory['spv_count'] = sum(x['name'].endswith('.spv') for x in inventory['files'])
    inventory['guest_binary_count'] = sum(x['name'].endswith('.bin') for x in inventory['files'])
    inventory['status'] = 'CAPTURED' if inventory['spv_count'] else 'NO_SPIRV_CAPTURED'
    return inventory


def validate_shaders(folder, evidence):
    validator = shutil.which('spirv-val')
    result = {'tool': validator, 'status': 'NOT_INSTALLED' if validator is None else 'AVAILABLE', 'files': []}
    if validator is None:
        return result
    try:
        result['version'] = run([validator, '--version'], timeout=10).decode(errors='replace')[:3000]
        help_text = run([validator, '--help'], timeout=10).decode(errors='replace')
        if 'vulkan1.3' not in help_text or '--scalar-block-layout' not in help_text:
            result['status'] = 'TOOL_LACKS_REQUIRED_OPTIONS'
            return result
        (evidence / 'validation').mkdir(exist_ok=True)
        deadline = time.monotonic() + 120
        for path in sorted(folder.glob('*.spv')):
            if time.monotonic() >= deadline:
                result['status'] = 'PARTIAL_TIME_LIMIT'
                break
            args = [validator, '--target-env', 'vulkan1.3', '--scalar-block-layout', str(path)]
            try:
                p = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=8, check=False)
                output = (p.stdout + p.stderr).decode(errors='replace')[:65536]
                (evidence / 'validation' / (path.name + '.txt')).write_text(output)
                result['files'].append({'shader': path.name, 'returncode': p.returncode,
                                        'classification': 'VALID' if p.returncode == 0 else 'VALIDATOR_REJECTED_REVIEW_VERSION_AND_DIAGNOSTIC'})
            except subprocess.TimeoutExpired:
                result['files'].append({'shader': path.name, 'classification': 'VALIDATOR_TIMEOUT'})
        result['limitations'] = 'Structural/API validation does not prove shader semantics, valid runtime resources, or fault causality.'
    except (OSError, subprocess.SubprocessError, RuntimeError) as ex:
        result['status'] = 'VALIDATOR_ERROR'
        result['error'] = str(ex)
    return result


def append_diary(row):
    path = ROOT / 'diary/trials.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(row, separators=(',', ':')) + '\n')
        f.flush()
        os.fsync(f.fileno())


def worker(tag):
    os.umask(0o077)
    evidence = LOGS / (tag + '-evidence')
    evidence.mkdir(exist_ok=False)
    runtime_root = ROOT / 'GodOfWarRagnarok/tests' / tag
    runtime_root.mkdir(parents=True, exist_ok=False)
    archive = LOGS / (tag + '.tar.gz')
    record = {'schema_version': 2, 'record_id': tag, 'recorded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'record_type': 'EXACT_GENERATED_SHADER_EVIDENCE_NO_REBUILD', 'source_commit': SOURCE,
              'binary_sha256': ELF, 'upstream_commit': UPSTREAM, 'game_id': 'CUSA34384',
              'tested_games': [], 'gameplay': 'NOT_VERIFIED', 'archive': str(archive),
              'renderer_semantic_changes': False, 'stage': 'PREFLIGHT', 'decision': 'DIAGNOSTIC_ONLY'}
    held = []
    game_work = None
    worktree_created = False
    rc = 1
    try:
        for lock in LOCKS:
            f = (LOGS / lock).open('a+')
            held.append(f)
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(git('rev-parse', 'HEAD') == BASE and not git('status', '--porcelain', '--untracked-files=no'),
                'RESTORED_BASELINE_CHANGED_REFUSE')
        for path, expected in PROTECTED.items():
            verify(path, expected)
        require(shutil.disk_usage(runtime_root).free >= 3 * 1024**3, 'NEED_3_GIB_FREE')
        busy = subprocess.run(['pgrep', '-x', 'shadps4'], capture_output=True, check=False)
        require(busy.returncode == 1, 'EMULATOR_ACTIVE_OR_PROCESS_CHECK_FAILED')
        git('cat-file', '-e', SOURCE + '^{commit}')
        git('merge-base', '--is-ancestor', UPSTREAM, SOURCE)
        verify(TEMPLATE, TEMPLATE_SHA)
        # No checkout/reset/patch on the original build worktree.
        record['stage'] = 'ISOLATED_SOURCE_AND_IDENTICAL_BINARY'
        git('-c', 'core.hooksPath=/dev/null', '-c', 'submodule.recurse=false',
            'worktree', 'add', '--detach', runtime_root / 'source', SOURCE)
        worktree_created = True
        (runtime_root / 'build').mkdir()
        shutil.copy2(CANDIDATE, runtime_root / 'build/shadps4')
        verify(runtime_root / 'build/shadps4', ELF)
        run(['git', '-C', REPO, 'archive', '--format=tar.gz', '--output=' + str(evidence / 'compiler-source.tar.gz'),
             SOURCE, *SOURCE_PATHS], timeout=120)
        script = evidence / 'native-game-runner.sh'
        script.write_text(derive_runtime(TEMPLATE.read_bytes(), runtime_root, Path(__file__).resolve()))
        run(['bash', '-n', script])
        game_tag = 'gow-real-shaders-game-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())
        game_work = ROOT / 'GodOfWarRagnarok/tests' / ('playtest-' + game_tag)
        require(not game_work.exists(), 'GAME_OUTPUT_PATH_COLLISION')
        game_log = LOGS / (game_tag + '.log')
        record['stage'] = 'ONE_SAME_BINARY_SHADER_CAPTURE'
        print('REBUILD_REQUIRED=NO\nSOURCE_SWITCH_ON_BASELINE=NO\nSAME_BINARY_SHA256=' + ELF, flush=True)
        print('GAME_MONITOR=' + str(game_log), flush=True)
        env = os.environ.copy()
        env['GOW_COHERENCE_PARENT_LOCKS'] = 'YES'
        with game_log.open('xb') as output:
            completed = subprocess.run(['bash', str(script), '--worker', game_tag], env=env,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, check=False)
        record['observer_rc'] = completed.returncode
        shutil.copy2(game_log, evidence / 'game-runner.log')
        game_evidence = game_work / 'evidence'
        if game_evidence.is_dir():
            shutil.copytree(game_evidence, evidence / 'game-evidence', symlinks=False)
        log_text = game_log.read_text(errors='replace')
        if 'NATIVE_GENERIC_GAME_LAUNCHED=YES' in log_text:
            record['tested_games'] = ['CUSA34384']
        # This is the exact directory used by PipelineCache::DumpShader.
        inventory = collect_shaders(game_work / 'user/shader/dumps', evidence / 'shaders')
        console_path = game_evidence / 'game/full-console.log'
        if console_path.is_file():
            console = console_path.read_text(errors='replace')
            active = sorted({int(x, 16) for x in re.findall(r'AutoReadConst: shader=(0x[0-9a-fA-F]+)', console)})
            inventory['automatic_readconst_shaders'] = [hex(x) for x in active]
            inventory['automatic_shader_spv_coverage'] = {
                hex(x): [v['name'] for v in inventory['files']
                         if v['name'].endswith('.spv') and f'0x{x:016x}' in v['name'].lower()]
                for x in active}
            inventory['dump_flag_confirmed_in_log'] = 'GPU shouldDumpShaders: true' in console
        json_write(evidence / 'shader-manifest.json', inventory)
        record['shader_spv_count'] = inventory.get('spv_count', 0)
        record['shader_guest_binary_count'] = inventory.get('guest_binary_count', 0)
        record['shader_capture_status'] = inventory['status']
        result = validate_shaders(evidence / 'shaders', evidence)
        json_write(evidence / 'spirv-validation.json', result)
        record['validator_status'] = result['status']
        native = game_evidence / 'game/native-result.json'
        if native.is_file():
            record['native_result'] = json.loads(native.read_text())
        record['stage'] = 'SHADERS_CAPTURED_REVIEW_REQUIRED' if record['shader_spv_count'] else 'NO_SHADERS_CAPTURED'
        print('SPIRV_CAPTURED=' + str(record['shader_spv_count']), flush=True)
        print('GUEST_SHADER_INPUTS_CAPTURED=' + str(record['shader_guest_binary_count']), flush=True)
        # Evidence capture can succeed even when the tested game crashes.
        record['evidence_capture_success'] = record['shader_spv_count'] > 0
        rc = 0 if record['evidence_capture_success'] else 1
    except BaseException as ex:
        record['failure'] = str(ex)
        (evidence / 'failure.txt').write_text(traceback.format_exc())
        print('CAPTURE_FAILED=' + str(ex), flush=True)
    finally:
        # Retain our isolated read-only source and candidate for exact attribution.
        # Never remove/reset any worktree, kill another emulator, or restart services.
        record['isolated_source_worktree_created'] = worktree_created
        record['original_source_was_switched'] = False
        unchanged = {}
        for path, expected in PROTECTED.items():
            try:
                unchanged[str(path)] = sha(path) == expected
            except OSError:
                unchanged[str(path)] = False
        record['protected_hashes_unchanged'] = unchanged
        try:
            record['original_source_unchanged'] = git('rev-parse', 'HEAD') == BASE and not git('status', '--porcelain', '--untracked-files=no')
        except Exception:
            record['original_source_unchanged'] = False
        if not all(unchanged.values()) or not record['original_source_unchanged']:
            rc = 1
        record['collector_exit_code'] = rc
        json_write(evidence / 'result.json', record)
        try:
            with tarfile.open(archive, 'w:gz') as t:
                t.add(evidence, arcname=evidence.name, recursive=True)
                worker_log = LOGS / (tag + '.log')
                if worker_log.is_file():
                    t.add(worker_log, arcname=worker_log.name)
            record['archive_sha256'] = sha(archive)
            append_diary(record)
            print('UPLOAD_THIS=' + str(archive), flush=True)
        except Exception as ex:
            print('ARCHIVE_OR_DIARY_FAILED=' + str(ex), flush=True)
            rc = 1
        for f in held:
            f.close()
        print('SSH_SESSION=OPEN', flush=True)
    return rc


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker')
    p.add_argument('--enable-private-dumps', nargs=2)
    args = p.parse_args()
    if args.enable_private_dumps:
        config, evidence = map(Path, args.enable_private_dumps)
        config.resolve(strict=True).relative_to((ROOT / 'GodOfWarRagnarok/tests').resolve())
        require(config.name == 'config.json' and config.parent.name == 'user' and
                config.parent.parent.name.startswith('playtest-gow-real-shaders-game-'),
                'NOT_A_PRIVATE_CAPTURE_PROFILE')
        enable_dumps(config, evidence)
        return 0
    LOGS.mkdir(parents=True, exist_ok=True)
    if args.worker:
        require(re.fullmatch(r'gow-real-shaders-\d{8}T\d{6}Z-\d+', args.worker), 'INVALID_TAG')
        return worker(args.worker)
    tag = 'gow-real-shaders-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
    with (LOGS / (tag + '.log')).open('xb') as log:
        child = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), '--worker', tag],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
    print('WORKER_PID=' + str(child.pid))
    print("MONITOR=tail -F '" + str(LOGS / (tag + '.log')) + "'")
    print('UPLOAD_THIS=' + str(LOGS / (tag + '.tar.gz')))
    print('SSH_SESSION=OPEN')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
