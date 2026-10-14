#!/usr/bin/env python3
"""Finish GoT's post-movie Options test without generating another shader dump.

Keeps the v46 executable and private driver, v48 automatic setup and v49's single
PID/focus-verified Options press. Recover compact shader binaries from the already
completed v49 run BEFORE launch; never dump every shader again or delete evidence.
No build, package installation, sudo, game-memory edit, or movement/combat input.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import types

LABEL = 'got-v50-postmovie-visual'
READY_PREFIX = 'got-v50-postmovie-ready-'
V49_SHA = 'f2d62a9e82a19c6e161ef9f9ad964afea087d659e7d28f62887f1873a549658c'
V49_URL = 'https://raw.githubusercontent.com/Chreece/shadPS4/5f16131836ac344e924f881cde50ad09aa3268d0/tools/diagnostics/got-v49-postmovie-render-capture.py'
EVIDENCE_RUN = 'got-v49-postmovie-render-visual-20261014-142855-ucbqod4b'
EVIDENCE_READY = 'got-v49-postmovie-render-ready-20261014-142855-mb2lrlmd'
BINARY_SHA = 'a22d65b9b958f743154bc1e9d2b3019498336ee5530ab480e52fe3c66a575e76'
BINARY_BUDGET = 64 * 1024 * 1024
TEXT_BUDGET = 32 * 1024 * 1024
TEXT_TARGETS = {0x5e89a8b4, 0x8e743c8e, 0x551d6087, 0x142dead8}


def digest(p: Path) -> str:
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def read_regular(p: Path, limit: int = 100_000) -> bytes:
    if not p.is_file() or p.is_symlink() or p.stat().st_size > limit:
        raise RuntimeError('Missing/unsafe file: ' + str(p))
    return p.read_bytes()


def replace_once(s: str, old: str, new: str) -> str:
    if s.count(old) != 1:
        raise RuntimeError('Pinned v49 source changed at ' + repr(old[:90]))
    return s.replace(old, new)


def safe_relative(name: str) -> PurePosixPath:
    rel = PurePosixPath(name)
    if rel.is_absolute() or '..' in rel.parts or len(rel.parts) < 3:
        raise ValueError('Unsafe archived shader path')
    if rel.parts[0] not in ('shader', 'shaders') or rel.parts[1] != 'dumps':
        raise ValueError('Not an archived shader dump')
    if rel.suffix not in ('.bin', '.spv', '.txt'):
        raise ValueError('Not an allowed shader file')
    return rel


def read_evidence_json(project: Path, relative: str, limit: int) -> dict:
    """Read a named member only; never unpack archives into the user's project."""
    logs = project.parent / 'logs'
    root = logs / EVIDENCE_RUN
    file = root / relative
    if file.exists():
        if not file.resolve().is_relative_to(root.resolve()) or root.is_symlink():
            raise RuntimeError('Evidence path escaped its report')
        raw = read_regular(file, limit)
    else:
        archive = logs / (EVIDENCE_RUN + '.tar.gz')
        if not archive.is_file() or archive.is_symlink() or archive.stat().st_size > 512*1024*1024:
            raise RuntimeError('The exact v49 report/archive is unavailable')
        with tarfile.open(archive, 'r:gz') as tar:
            member = tar.getmember(relative)
            if not member.isfile() or member.size > limit:
                raise RuntimeError('Unsafe evidence member: ' + relative)
            with tar.extractfile(member) as f:
                raw = f.read(limit + 1)
    if len(raw) > limit:
        raise RuntimeError('Evidence member too large')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError('Expected JSON object: ' + relative)
    return value


def spirv_envelope(data: bytes) -> dict:
    """Instruction framing only, NOT Vulkan semantic or GPU-feature validation."""
    if len(data) < 20 or len(data) % 4:
        return {'result': 'INVALID_HEADER_OR_SIZE'}
    words = struct.unpack('<%dI' % (len(data)//4), data)
    if words[0] != 0x07230203 or not words[3] or words[4] != 0:
        return {'result': 'INVALID_HEADER'}
    cursor = 5; instructions = 0
    while cursor < len(words):
        count = words[cursor] >> 16
        if count == 0 or cursor + count > len(words):
            return {'result': 'INVALID_INSTRUCTION_LENGTH', 'word': cursor}
        instructions += 1; cursor += count
    return {'result': 'INSTRUCTION_FRAMING_OK_NOT_SEMANTIC_VALIDATION',
            'version': hex(words[1]), 'instructions': instructions}


def recover_existing_shaders(base, project: Path, report: Path) -> dict:
    """Recover all SPV/GCN first. Large text can no longer starve the binaries."""
    result = {'source_run': EVIDENCE_RUN, 'new_shader_dumps': False,
              'original_files_deleted': False, 'source_is_previous_run_not_current': True,
              'files': []}
    root = project/'tests'/EVIDENCE_READY
    try:
        session = read_evidence_json(project, 'info/session.json', 20_000)
        if session.get('helper_sha256') != V49_SHA or session.get('ready') != str(root):
            raise RuntimeError('v49 session identity differs from the uploaded report')
        if not root.is_dir() or root.is_symlink():
            raise RuntimeError('The original private v49 dump directory no longer exists')
        if (root/'user').is_symlink() or not (root/'user').is_dir():
            raise RuntimeError('Unsafe original private profile directory')
        if (root/'shadps4').is_symlink():
            raise RuntimeError('Symlinked original runtime binary')
        if digest(root/'shadps4') != BINARY_SHA:
            raise RuntimeError('The v49 dump directory is not the verified v46 runtime')
        manifest = read_evidence_json(project, 'info/shader-manifest.json', 5_000_000)
        entries = manifest.get('files')
        if not isinstance(entries, list) or len(entries) > 20_000:
            raise RuntimeError('Unexpected shader manifest')
        binaries = []; texts = []; seen = set()
        for entry in entries:
            rel = safe_relative(entry['file'])
            name = str(rel)
            if name in seen:
                raise RuntimeError('Duplicate shader path in manifest')
            seen.add(name)
            size = entry.get('bytes')
            if not isinstance(size, int) or size < 0:
                raise RuntimeError('Invalid shader size in manifest')
            if rel.suffix in ('.bin', '.spv'):
                binaries.append((rel, size, entry.get('sha256')))
            else:
                match = re.search(r'0x([0-9a-fA-F]+)', rel.name)
                if match and int(match.group(1), 16) in TEXT_TARGETS and size:
                    texts.append((rel, size, entry.get('sha256')))
        result['binary_files_expected'] = len(binaries)
        result['binary_bytes_expected'] = sum(size for _,size,_ in binaries)
        if result['binary_bytes_expected'] > BINARY_BUDGET:
            raise RuntimeError('Existing binary set exceeds 64 MiB export budget; no partial binary selection')
        if shutil.disk_usage(report).free < result['binary_bytes_expected'] + TEXT_BUDGET + 512*1024*1024:
            raise RuntimeError('Insufficient disk reserve for offline shader export')
        binary_bytes = 0; text_bytes = 0; counts = {'.spv': 0, '.bin': 0, '.txt': 0}
        for rel, size, expected in sorted(binaries) + sorted(texts):
            record = {'file': str(rel), 'bytes': size, 'copied': False}
            if rel.suffix == '.txt' and text_bytes + size > TEXT_BUDGET:
                record['reason'] = 'bounded_target_text_budget'; result['files'].append(record); continue
            source = root/'user'/rel
            # Validate all ancestors inside the private directory, not merely the leaf.
            if any((root/'user'/Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts)+1)) or not source.resolve().is_relative_to((root/'user').resolve()):
                raise RuntimeError('Unsafe source dump: ' + str(rel))
            if not source.is_file() or source.stat().st_size != size or size > 16*1024*1024:
                record['reason'] = 'source_missing_or_size_changed'; result['files'].append(record); continue
            data = read_regular(source, 16*1024*1024)
            actual = hashlib.sha256(data).hexdigest()
            if expected is not None and actual != expected:
                record['reason'] = 'previously_recorded_checksum_changed'; result['files'].append(record); continue
            destination = report/'info/v49-shaders'/rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                raise RuntimeError('Offline export destination already exists')
            destination.write_bytes(data)
            record.update(copied=True, sha256=actual,
                          previous_checksum_available=expected is not None)
            if rel.suffix == '.spv':
                record['spirv_structure'] = spirv_envelope(data)
            counts[rel.suffix] += 1
            if rel.suffix == '.txt': text_bytes += size
            else: binary_bytes += size
            result['files'].append(record)
        result.update(result='EXISTING_BINARY_SET_EXPORTED' if counts['.spv']+counts['.bin']==len(binaries)
                      else 'EXISTING_BINARY_SET_PARTIAL', counts=counts,
                      copied_binary_bytes=binary_bytes, copied_target_text_bytes=text_bytes,
                      semantic_spirv_validation=False)
    except Exception as exc:
        # The already-unfinished UI test is still useful if old raw dumps were cleaned.
        # Do not enable new dumps or pretend the omitted files were collected.
        result.update(result='OFFLINE_EXPORT_INCOMPLETE_UI_TEST_STILL_ALLOWED', error=str(exc))
    base.store(report/'info/v49-shader-recovery.json', result)
    print('V49_SHADER_RECOVERY='+result['result'], flush=True)
    return result


def configure_profile(base, ready: Path, report: Path) -> None:
    changes = []
    configs = [ready/'user/config.json'] + sorted((ready/'user/custom_configs').glob('*.json'))
    for file in configs:
        config = base.read_json(file)
        before = config.get('GPU', {}).get('dump_shaders')
        config.setdefault('GPU', {})['dump_shaders'] = False
        vulkan = config.get('Vulkan', {})
        if vulkan.get('pipeline_cache_enabled', False) or vulkan.get('pipeline_cache_archived', False):
            raise RuntimeError('Unexpected enabled pipeline cache; baseline not changed')
        base.store(file, config)
        changes.append({'file': str(file.relative_to(ready)), 'dump_shaders_before': before,
                        'dump_shaders_after': False})
    for name in ('default.ini', 'CUSA11456.ini'):
        file = ready/'user/input_config'/name
        data = read_regular(file).decode()
        if 'cross = n' not in data or 'options =' in data:
            raise RuntimeError('Unknown private input bindings')
        file.write_text(data+'\n# One PID/focus-verified post-movie Options press.\noptions = enter\n')
    base.store(report/'info/render-settings.json', {
        'changes': changes, 'new_shader_dumps': False,
        'previous_v49_dumps_reused': True, 'emulator_rebuilt': False,
        'driver_patched': False, 'movement_input': False,
        'options_input_limit': 1, 'automatic_setup_keeps_displayed_defaults': True})


def load_v49(script: Path):
    worker = len(sys.argv)>1 and sys.argv[1]=='worker'
    if worker:
        path = script.parent/'v49-base.py'
    else:
        directory = script.parent.parent/'tools/capture-helpers'
        if directory.is_symlink(): raise RuntimeError('Unsafe helper-cache directory')
        directory.mkdir(parents=True, exist_ok=True)
        path = directory/'v49-5f16131.py'
        if not path.exists() and not path.is_symlink():
            part = path.with_name(path.name+'.part')
            if part.is_symlink(): raise RuntimeError('Unsafe download path')
            subprocess.run(['curl','-4fsSL','--retry','2','--connect-timeout','10','--max-time','60',
                            V49_URL,'-o',str(part)], check=True, timeout=180)
            if hashlib.sha256(read_regular(part)).hexdigest()!=V49_SHA:
                raise RuntimeError('Pinned v49 download hash mismatch')
            os.replace(part,path)
    data = read_regular(path)
    if hashlib.sha256(data).hexdigest()!=V49_SHA:
        raise RuntimeError('Pinned v49 helper checksum differs')
    source = replace_once(data.decode(), "LABEL = 'got-v49-postmovie-render-visual'", 'LABEL = '+repr(LABEL))
    source = replace_once(source, "READY_PREFIX = 'got-v49-postmovie-render-ready-'", 'READY_PREFIX = '+repr(READY_PREFIX))
    source = replace_once(source,
        'CAPTURE=POST_CINEMATIC_RENDER; BUILD=UNCHANGED_V46; DUMPS=PRIVATE_ONLY',
        'CAPTURE=POST_MOVIE_OPTIONS; BUILD=UNCHANGED_V46; NEW_DUMPS=OFF')
    source = replace_once(source, 'GOT49_OPTIONS=ONE_PRESS; NO_FURTHER_GAME_INPUT',
                          'GOT50_OPTIONS=ONE_PRESS; NO_FURTHER_GAME_INPUT')
    module = types.ModuleType('got50_pinned_v49'); module.__file__ = str(script)
    exec(compile(source, str(path), 'exec'), module.__dict__)
    module.PINNED_V49_PATH = path
    return module


def install(v49):
    v49.configure_profile = configure_profile
    # This run must not repack/recreate v49's oversized textual dump set.
    def no_new_dumps(base, ready, report, error_shaders):
        files = list(v49.dump_files(ready))
        base.store(report/'info/current-shader-dumps.json', {
            'enabled': False, 'unexpected_files': len(files),
            'bytes': sum(p.stat().st_size for _,p,_ in files),
            'previous_source_run': EVIDENCE_RUN,
            'current_shader_outputs_not_captured': True})
    v49.capture_shaders = no_new_dumps
    original_install = v49.install
    def install_capture(base, v48):
        base = original_install(base, v48)
        original_copy = base.copy_profile
        def copy_profile(source, ready):
            result = original_copy(source, ready)
            project = ready.parent.parent
            report = base.latest(project)
            if report is None or base.read_json(report/'info/session.json').get('ready') != str(ready):
                raise RuntimeError('Frozen helper/report identity mismatch')
            shutil.copyfile(v49.PINNED_V49_PATH, report/'info/v49-base.py')
            result['render_capture'] = 'no new dumps; finish one post-movie Options test'
            recover_existing_shaders(base, project, report)
            return result
        base.copy_profile = copy_profile
        original_collect = base.collect
        def collect(report, ready):
            try:
                original_collect(report, ready)
            finally:
                result_path = report/'info/result.json'
                if base.regular(result_path, 1_000_000):
                    result = base.read_json(result_path)
                    if result.get('result') == 'USER_STOP_REQUEST':
                        if base.regular(report/'info/render-budget-stop.json'):
                            result.update(result='DIAGNOSTIC_DISK_RESERVE_STOP_NOT_GAME_CRASH',
                                          stop_requested_by='helper_disk_guard')
                        elif base.regular(report/'info/diagnostic-complete.json'):
                            result.update(result='POST_MOVIE_OBSERVATION_COMPLETE_NOT_GAME_CRASH',
                                          stop_requested_by='helper_observation_window')
                    base.store(result_path, result)
        base.collect = collect
        return base
    v49.install = install_capture
    return v49


def main() -> int:
    os.umask(0o077)
    if os.geteuid() == 0: raise RuntimeError('Run without sudo')
    script = Path(__file__).resolve()
    worker = len(sys.argv)>1 and sys.argv[1]=='worker'
    if not worker and (script.parent.name!='scripts' or
            script.parent.parent!=Path.home()/'projects/shadps4/GhostOfTsushima'):
        raise RuntimeError('Place helper under ~/projects/shadps4/GhostOfTsushima/scripts')
    return install(load_v49(script)).main()


if __name__ == '__main__':
    try: sys.exit(main())
    except BlockingIOError: print('CAPTURE_SETUP_ALREADY_IN_PROGRESS');sys.exit(0)
    except KeyboardInterrupt: sys.exit(130)
    except Exception as exc: print('STOP: '+str(exc),file=sys.stderr);sys.exit(1)
