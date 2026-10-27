#!/usr/bin/env python3
"""Read-only recovery of the October 2026 proven, general SRT planner source."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import sys

REL = Path('src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp')
GOOD_SHA = 'b70df7890134f497e9eb7efd2c717d6cf09afa784a007ca7dac59f50a26f6651'
GOOD_ELF_SHA = 'b7db5807b4b04920b0e8f72d94a19fa74a29c70787c60264d8dfd06f1104ea79'
DEFAULT_ROOT = Path.home() / 'projects/shadps4'

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def safe_file(path, root):
    try:
        return path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False

def run(root):
    tests = root / 'GodOfWarRagnarok' / 'tests'
    logs = root / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    label = f'shadps4-proven-srt-source-{timestamp}-{os.getpid()}'
    bundle = logs / (label + '.tar.gz')
    report = {'kind': 'PROTECTED_GOW_SRT_SOURCE_RECOVERY', 'test_game': 'NONE',
              'source_role': 'general shader dependency ordering, no game workarounds',
              'expected_source_sha256': GOOD_SHA, 'expected_binary_sha256': GOOD_ELF_SHA,
              'source_status': 'NOT_FOUND', 'checked_source_paths': 0,
              'current_source_status': 'NOT_FOUND', 'production_changed': False,
              'sunshine_touched': False, 'emulator_launched': False}
    candidates = []
    snapshot = tests / 'gow91-build-20261014-112830-2480722'
    manifest = snapshot / 'manifest.json'
    if safe_file(manifest, root):
        try:
            data = json.loads(manifest.read_text())
            for key, value in data.get('replacement_objects', {}).items():
                if not str(key).endswith(str(REL) + '.o'):
                    continue
                path = Path(str(value))
                for ancestor in list(path.parents)[:13]:
                    candidates.append(ancestor / 'overlay' / REL)
        except (OSError, ValueError, TypeError) as exc:
            report['manifest_error'] = type(exc).__name__
    if tests.is_dir():
        for pattern in ('gow*/source/' + str(REL), 'gow*/overlay/' + str(REL),
                        'gow*/source/overlay/' + str(REL), 'gow*/source-fixes/' + str(REL)):
            candidates.extend(tests.glob(pattern))
        candidates.extend((tests / 'gow84-build-20261013').glob('**/' + str(REL)))
    found = None
    seen = set()
    for candidate in candidates[:2000]:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if not safe_file(candidate, root):
            continue
        try:
            raw = candidate.read_bytes()
        except OSError:
            continue
        report['checked_source_paths'] += 1
        if digest(raw) == GOOD_SHA:
            found = (candidate, raw)
            break
    if found:
        report['source_status'] = 'VERIFIED_EXACT_SHA256'
        report['source_path'] = str(found[0])
        report['source_bytes'] = len(found[1])
        report['old_source_has_root_order_option'] = b'SHADPS4_GOW_SRT_AUTO_ROOTS' in found[1]
        report['old_source_has_topological_plan'] = b'SRT_TOPO_PLAN' in found[1]
    elf = snapshot / 'shadps4'
    if safe_file(elf, root):
        with elf.open('rb') as stream:
            h = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(chunk)
        report['good_elf_verification'] = 'MATCH' if h.hexdigest() == GOOD_ELF_SHA else 'MISMATCH'
        report['good_elf_sha256'] = h.hexdigest()
    else:
        report['good_elf_verification'] = 'NOT_PRESENT'
    current_dir = tests / 'shadps4-srt-findilsb-20261026-160302-916073' / 'source'
    git_dir = current_dir / REL
    current_bytes = None
    if safe_file(git_dir, root):
        current_bytes = git_dir.read_bytes()
        report['current_source_status'] = 'CAPTURED_READ_ONLY'
        report['current_source_sha256'] = digest(current_bytes)
        proc = subprocess.run(['git', '-C', str(current_dir), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, timeout=5, check=False)
        if proc.returncode == 0:
            report['current_source_commit'] = proc.stdout.strip()
    info = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    with tarfile.open(bundle, 'w:gz') as tar:
        from io import BytesIO
        for name, contents in [('source-recovery.json', info.encode()),
                               ('proven-srt-source.cpp', found[1] if found else None),
                               ('current-srt-source.cpp', current_bytes)]:
            if contents is None:
                continue
            metadata = tarfile.TarInfo(name)
            metadata.size = len(contents)
            metadata.mode = 0o600
            tar.addfile(metadata, BytesIO(contents))
    diary = root / 'diary' / 'trials.jsonl'
    try:
        diary.parent.mkdir(parents=True, exist_ok=True)
        with diary.open('a') as out:
            out.write(json.dumps({'kind': 'GENERAL_SRT_SOURCE_RECOVERY',
                                  'source_sha256': GOOD_SHA, 'status': report['source_status'],
                                  'no_game_run': True, 'artifact': bundle.name}) + '\n')
        print('DIARY_APPENDED=' + str(diary))
    except OSError as exc:
        print('DIARY_WRITE_FAILED=' + type(exc).__name__)
    print('OLD_SRT_SOURCE=' + report['source_status'])
    print('OLD_ELF=' + report['good_elf_verification'])
    print('CURRENT_SRT_SOURCE=' + report['current_source_status'])
    print('UPLOAD_THIS=' + str(bundle))
    print('UNCHANGED=SUNSHINE_XORG_ESDE_SAVES_EXECUTABLE_NETWORK_SSH')
    return 0 if found else 4

if __name__ == '__main__':
    if len(sys.argv) != 1:
        raise SystemExit('Run with no arguments: this collector does not accept paths or overrides')
    raise SystemExit(run(DEFAULT_ROOT))
