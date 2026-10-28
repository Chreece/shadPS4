#!/usr/bin/env python3
"""Compare protected GoW renderer source with PR94 without launching a game.
Archives only allowlisted emulator source and hashes, never binaries or saves.
"""
import datetime as dt
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path.home() / 'projects/shadps4'
CURRENT = ROOT / 'Upstream/tests/gow-hle-abi-20261027-194629-907086'
OLD = ROOT / 'GodOfWarRagnarok'
BINARIES = {
    'protected': (OLD / 'build/shadps4', '8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e'),
    'pr91': (CURRENT / 'old-binaries/pr91-46195c52-native-shadps4', '46195c52e9dd3fb3921b0b5c99a233edf4b484ecceb7794c12ae864626d6b3af'),
    'pr94': (CURRENT / 'build/shadps4', '65fafc436831d76380db349b89c89fe610d16325981870ac073d8b9ed0f010b9'),
}
PIN = 'bbe1a72840733afccba5f48fb4b5eb29a1c96128'
FILES = '''
src/core/memory.cpp
src/core/libraries/gnmdriver/gnmdriver.cpp
src/core/libraries/kernel/process.cpp
src/video_core/amdgpu/liverpool.cpp
src/video_core/amdgpu/resource.h
src/video_core/renderer_vulkan/vk_scheduler.cpp
src/video_core/renderer_vulkan/vk_rasterizer.cpp
src/video_core/renderer_vulkan/vk_rasterizer.h
src/video_core/renderer_vulkan/vk_runtime.cpp
src/video_core/renderer_vulkan/vk_pipeline_cache.cpp
src/video_core/renderer_vulkan/vk_instance.cpp
src/video_core/renderer_vulkan/vk_dispatch_helper.cpp
src/video_core/renderer_vulkan/vk_indirect_read_sync.h
src/video_core/buffer_cache/buffer_cache.cpp
src/video_core/texture_cache/texture_cache.cpp
src/video_core/texture_cache/types.h
src/video_core/host_shaders/prepare_dispatch.comp
src/shader_recompiler/info.h
src/shader_recompiler/resource.h
src/shader_recompiler/profile.h
src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp
src/shader_recompiler/ir/passes/resource_discover_pass.cpp
src/shader_recompiler/ir/passes/resource_patching_pass.cpp
src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp
src/shader_recompiler/backend/spirv/emit_spirv_image.cpp
src/shader_recompiler/backend/spirv/spirv_emit_context.cpp
'''.split()


def shell(*args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=12, check=False)
        return p.stdout.strip()[:5000] if p.returncode == 0 else f'EXIT={p.returncode}'
    except (OSError, subprocess.TimeoutExpired) as ex:
        return f'ERROR={ex}'


def filehash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()


def source_dir(path):
    try:
        r = path.resolve(strict=True)
        r.relative_to(ROOT.resolve(strict=True))
        return r if (r / 'src/video_core/renderer_vulkan/vk_rasterizer.cpp').is_file() else None
    except (OSError, ValueError):
        return None


def cache_home(path):
    try:
        for l in path.read_text(errors='replace').splitlines():
            if l.startswith('CMAKE_HOME_DIRECTORY:INTERNAL='):
                return l.split('=', 1)[1]
    except OSError:
        pass
    return None


def collect():
    os.umask(0o077)
    logs = ROOT / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / 'gow-pr94-source-parity.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        checks = {}
        for name, (path, expect) in BINARIES.items():
            got = filehash(path) if path.is_file() and not path.is_symlink() else 'MISSING'
            checks[name] = {'file': str(path), 'expected': expect, 'actual': got, 'match': got == expect}
        if not all(x['match'] for x in checks.values()):
            raise RuntimeError('EXACT_BINARY_GUARD_FAILED ' + json.dumps(checks))
        pr94src = CURRENT / 'source'
        if shell('git', '-C', str(pr94src), 'rev-parse', 'HEAD') != PIN:
            raise RuntimeError('EXACT_PR94_SOURCE_GUARD_FAILED')
        candidates = [pr94src, OLD, OLD / 'source', OLD / 'shadPS4']
        cache_hints = []
        for cache in (OLD / 'build/CMakeCache.txt', CURRENT / 'build/CMakeCache.txt'):
            h = cache_home(cache)
            cache_hints.append({'cache': str(cache), 'source': h})
            if h:
                candidates.append(Path(h))
        for base in (OLD / 'tests', ROOT / 'Upstream/tests'):
            if base.is_dir():
                for entry in sorted(base.iterdir(), key=lambda p:p.name, reverse=True)[:100]:
                    if entry.is_dir() and not entry.is_symlink():
                        candidates.append(entry / 'source')
        found = []
        for candidate in candidates:
            s = source_dir(candidate)
            if s and s not in found:
                found.append(s)
        if not found or found[0] != pr94src.resolve():
            raise RuntimeError('PR94_SOURCE_NOT_FOUND')
        tag = 'gow-pr94-proven-source-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        out = logs / tag
        out.mkdir()
        manifest = {'source_commit': PIN, 'game': 'CUSA34384', 'gpu_trial': 'gow-pr94-gpu-20261028-112822-1190205',
                    'game_failure': 'RADV_DEVICE_LOST_22.236S_NO_REAL_FRAME', 'binaries': checks,
                    'cache_hints_are_not_binary_source_proof': cache_hints, 'sources': [],
                    'actual_protected_binary_source_proven': False, 'no_games_or_builds_or_gpu': True}
        raw = {}
        for index, s in enumerate(found[:8]):
            label = f'candidate{index:02d}'
            entry = {'label': label, 'source': str(s),
                     'head': shell('git', '-C', str(s), 'rev-parse', 'HEAD'),
                     'status': shell('git', '-C', str(s), 'status', '--porcelain', '--untracked-files=no', '--', 'src'),
                     'hashes': {}}
            source_data = {}
            for rel in FILES:
                p = s / rel
                if not p.is_file() or p.is_symlink() or p.stat().st_size > 2000000:
                    continue
                try:
                    b = p.read_bytes()
                except OSError:
                    continue
                entry['hashes'][rel] = hashlib.sha256(b).hexdigest()
                source_data[rel] = b
                dst = out / 'code' / label / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(b)
            raw[label] = source_data
            manifest['sources'].append(entry)
        base = manifest['sources'][0]['hashes']
        report = []
        for entry in manifest['sources'][1:]:
            label = entry['label']
            different = [rel for rel in FILES if entry['hashes'].get(rel) != base.get(rel)]
            entry['differs_from_pr94'] = different
            report.append(f'{label} {entry["source"]}: {len(different)} differences')
            report.extend('  ' + rel for rel in different)
        (out / 'source-differences.txt').write_text('\n'.join(report) + '\n')
        if len(raw) > 1:
            diff = []
            old = raw.get('candidate01', {})
            for rel in FILES:
                if rel in raw['candidate00'] and rel in old and raw['candidate00'][rel] != old[rel]:
                    patch = list(difflib.unified_diff(raw['candidate00'][rel].decode('utf8','replace').splitlines(keepends=True),
                                old[rel].decode('utf8','replace').splitlines(keepends=True),
                                fromfile='pr94/' + rel, tofile='protected-source-candidate/' + rel))
                    if len(diff) + len(patch) > 18000:
                        diff.append('DIFF_LIMIT\n')
                        break
                    diff.extend(patch)
            (out / 'bounded-comparison.patch').write_text(''.join(diff))
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        archive = logs / (tag + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as t:
            t.add(out, arcname=tag)
        diary = ROOT / 'diary/trials.jsonl'
        diary.parent.mkdir(parents=True, exist_ok=True)
        row = {'schema_version': 2, 'record_id': tag, 'recorded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
               'record_type': 'GOW_PR94_PROVEN_LINEAGE_READONLY', 'game_id': 'CUSA34384', 'tested_games': [],
               'pr94_source': PIN, 'runtime_result': 'RADV_DEVICE_LOST_22.236_SECONDS', 'gameplay': 'NOT_OBSERVED',
               'protected_elf': BINARIES['protected'][1], 'archive': archive.name,
               'decision': 'NO_RERUN_PR94_UNCHANGED;_COMPARE_PROVEN_RENDERER_SOURCE', 'no_game_test': True}
        with diary.open('a') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(row, separators=(',', ':')) + '\n')
            f.flush(); os.fsync(f.fileno())
        print('PROTECTED_GOW_BINARY_VERIFIED=YES')
        print('PR94_GPU_FAILED_BINARY_VERIFIED=YES')
        print(f'SOURCE_CANDIDATES={len(found)} CAPTURED={len(manifest["sources"])}')
        print('NO_GAMES_NO_BUILD_NO_GPU_NO_DISPLAY_CHANGES=YES')
        print(f'UPLOAD_THIS={archive}')
        print('SSH_SESSION=OPEN')


if __name__ == '__main__':
    try:
        collect()
    except Exception as exc:
        print('GOW_SOURCE_PARITY_FAILURE=' + str(exc), file=sys.stderr)
        sys.exit(1)
