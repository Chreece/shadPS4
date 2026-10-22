#!/usr/bin/env python3
"""Run the already-linked copy-range candidate. No build or emulator checkout writes.

Preserve the uploaded report and correct its stale source_parent only in a
separate launch record, after checking the exact three-commit chain and tree.
"""
from __future__ import annotations
import argparse
import ast
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import subprocess

BUILD = 'masked-wave-build-20261022-163329-2257544'
REV = '9d70b53b323f54a38bb2f77eed4435154679adfe'
PARENT = '0c551c269c657fa1be86def8d99e3eabef4d9a38'
OLDER_PARENT = '861066371a305800abd3ccf83bcdcb6897d7cb43'
TREE = 'e55a6ab5bf8460a14429744310b6da7facf0c7f0'
PARENT_TREE = '5e5b65f858ca8ed1195a2b8f765f5ab8195f554d'
BASE = '04d891dc33bb1c8c89789b5ff039049901f95a41'
BINARY_SHA = '0355d23f97c85c65f27d1a50f7a01e5587b39248829d440df7d535a1a235c10f'
REPORT_SHA = '6b4d2ec5b03a03c60495c386a482b2b0a152d70a449a8757f6c65c39cd22097a'
MANIFEST_SHA = '9f0601e73fc3bf42ae3a24780da873683ee2d6bd3001b078c171776091384eb3'
PREPARED_SHA = '0507a7a27945d50004ef8d7e88647af471d5166ffcf608925a15ca7670ead873'
ATOMIC_REV = '8329755a6da9c1827c3153de4f82d0638ee19ff8'
ATOMIC_SHA = '1aedac41041fd34210325729506b15336df0a5df9b1f5dbd1b358493f773a197'
FILE = 'src/video_core/renderer_vulkan/vk_runtime.cpp'
OLD_BLOB = '3940e0abb310f36efd35488709afb5c1c1a173a8'
NEW_BLOB = '3ab9edd8584be5793fa21c65243f8c328c29e062'
GIT_ENV = {k: v for k, v in os.environ.items()
           if k not in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR')}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('NOT_A_REGULAR_FILE=' + str(path))
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git(source, *args):
    return subprocess.check_output(
        ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'gc.auto=0',
         '-c', 'submodule.recurse=false', '-C', str(source), *args],
        env=GIT_ENV, stderr=subprocess.PIPE, timeout=30)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def verified_report(root):
    info = root / 'logs' / BUILD / 'info'
    report_path = info / 'build-result.json'
    if digest(report_path) != REPORT_SHA:
        raise RuntimeError('UPLOADED_BUILD_REPORT_CHANGED')
    report = json.loads(report_path.read_bytes())
    expected = dict(status='MASKED_WAVE_BUILD_PASS', revision=REV, tree=TREE,
                    source_parent=OLDER_PARENT, candidate_sha256=BINARY_SHA,
                    base=BASE, full_build='PASS', game_started=False, exit=0,
                    block_copy='CEIL_BLOCK_PITCH_AND_U64', spirv_validation='PASS',
                    fault_spirv_validation='PASS',
                    float_atomic_spirv_validation='PASS_8_MODULES_NEGATIVE_CONTROL_REJECTED')
    if any(report.get(k) != v for k, v in expected.items()):
        raise RuntimeError('BUILD_RESULT_NOT_EXPECTED')
    for name in ('source_restored', 'build_binary_restored', 'sirit_restored'):
        if report.get(name) is not True:
            raise RuntimeError('BUILD_RESTORATION_NOT_VERIFIED=' + name)
    source = root / 'clean-rebase-verified/source'
    candidate = root / f'clean-rebase-verified/candidates/{REV}-{BINARY_SHA}/shadps4'
    if Path(report['source']) != source or Path(report['candidate']) != candidate:
        raise RuntimeError('BUILD_PATH_MISMATCH')
    if digest(candidate) != BINARY_SHA or not os.access(candidate, os.X_OK):
        raise RuntimeError('BUILT_CANDIDATE_CHANGED')
    if digest(info / 'correction.json') != MANIFEST_SHA:
        raise RuntimeError('SOURCE_MANIFEST_CHANGED')
    if report.get('manifest_sha256') != MANIFEST_SHA:
        raise RuntimeError('REPORT_MANIFEST_IDENTITY_MISMATCH')
    if (info / 'prepared-runtime.sha256').read_text().strip() != PREPARED_SHA:
        raise RuntimeError('RECORDED_RUNTIME_CHANGED')
    checks = {
        REV + '^{tree}': TREE, REV + '^': PARENT,
        PARENT + '^{tree}': PARENT_TREE, PARENT + '^': OLDER_PARENT,
        PARENT + ':' + FILE: OLD_BLOB, REV + ':' + FILE: NEW_BLOB,
        'HEAD': BASE,
    }
    for ref, expected in checks.items():
        if git(source, 'rev-parse', ref).decode().strip() != expected:
            raise RuntimeError('SOURCE_PROVENANCE_MISMATCH=' + ref)
    if set(git(source, 'diff', '--name-only', PARENT, REV).decode().splitlines()) != {FILE}:
        raise RuntimeError('INCREMENTAL_CHANGESET_NOT_SINGLE_FILE')
    git(source, 'diff', '--exit-code', 'HEAD', '--')
    sirit = source / 'externals/sirit'
    if git(sirit, 'rev-parse', 'HEAD').decode().strip() != report['sirit_original']:
        raise RuntimeError('SIRIT_NOT_RESTORED')
    if git(sirit, 'status', '--porcelain', '--untracked-files=all').strip():
        raise RuntimeError('SIRIT_LOCAL_EDITS')
    # Correct a reporting field, not the candidate or its source tree. The
    # original report is preserved byte-for-byte in the launch and runtime logs.
    corrected = dict(report, source_parent=PARENT)
    return info, source, candidate, corrected


def prepare_runtime(root, source):
    path = root / 'scripts' / ('typed-float-atomic-' + ATOMIC_REV + '.py')
    if digest(path) != ATOMIC_SHA:
        raise RuntimeError('VERIFIED_ATOMIC_RUNNER_CHANGED')
    atomic = load_module(path, 'verified_runtime_atomic')
    fault = atomic.load_previous(root)
    dma = fault.load_previous(root)
    descriptor = dma.load_descriptor(root)
    fmt = descriptor.load_format(Path(descriptor.__file__).with_name('restore_ubnorm_nz_20261022.py'))
    native = fmt.load_native(root / 'scripts' / ('native-masked-minimum-' + descriptor.NATIVE_PACKAGE_REV))
    validator = root / ('tools/spirv-validator-' + atomic.TOOLS[:12] + '/build/tools/spirv-val')
    if digest(validator) != atomic.VALIDATOR_SHA or not os.access(validator, os.X_OK):
        raise RuntimeError('VALIDATOR_CHANGED')
    adapter = native.base.adapter
    raw = git(source, 'show', adapter.RUNTIME_REF + ':scripts/got-world-graphics-runtime-20261021.py')
    text = atomic.validated_runtime(
        fault.capture_runtime(descriptor.harden_runtime(adapter.prepare_runtime(raw)), adapter),
        validator)
    if sha(text.encode()) != PREPARED_SHA:
        raise RuntimeError('RUNTIME_DIFFERS_FROM_BUILD_PREFLIGHT')
    # Only extend metadata collection. Rendering, timing, controls, environment,
    # process cleanup, display checks and Ollama handling remain unchanged.
    anchor = 'for name in ("build-result.json","correction.json"'
    replacement = ('for name in ("build-result.original.json","launch-provenance-correction.json",'
                   '"build-result.json","correction.json"')
    if text.count(anchor) != 1:
        raise RuntimeError('RUNTIME_METADATA_ANCHOR_NOT_UNIQUE')
    text = text.replace(anchor, replacement, 1)
    ast.parse(text)
    return text, native.base


def launch_record(root, folder, info, corrected, text):
    for src in info.iterdir():
        if src.is_symlink():
            raise RuntimeError('SYMLINK_IN_BUILD_EVIDENCE=' + src.name)
        if src.is_file():
            if src.stat().st_size > 16 * 1024 * 1024:
                raise RuntimeError('BUILD_EVIDENCE_TOO_LARGE=' + src.name)
            shutil.copyfile(src, folder / src.name)
    shutil.copyfile(info / 'build-result.json', folder / 'build-result.original.json')
    (folder / 'build-result.json').write_text(json.dumps(corrected, indent=2) + '\n')
    (folder / 'launch-provenance-correction.json').write_text(json.dumps({
        'original_report': str(info / 'build-result.json'),
        'original_report_sha256': REPORT_SHA,
        'field_changed': 'source_parent', 'before': OLDER_PARENT, 'after': PARENT,
        'verified_candidate': REV, 'candidate_tree': TREE, 'candidate_sha256': BINARY_SHA,
        'parent_tree': PARENT_TREE, 'incremental_changed_files': [FILE],
        'runtime_preflight_sha256': PREPARED_SHA,
        'runtime_with_evidence_collection_sha256': sha(text.encode()),
        'recompiled': False, 'source_checkout_modified': False,
        'original_build_evidence_modified': False,
    }, indent=2) + '\n')
    scripts = root / 'scripts'
    runtime = scripts / ('verified-copy-runtime-' + sha(text.encode())[:20] + '.py')
    if runtime.exists() or runtime.is_symlink():
        if digest(runtime) != sha(text.encode()):
            raise RuntimeError('EXISTING_RUNTIME_CHANGED')
    else:
        fd, name = tempfile.mkstemp(prefix='.copy-runtime-', dir=scripts)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(text)
            os.link(name, runtime)
        finally:
            Path(name).unlink(missing_ok=True)
    return runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    if not 40 <= args.seconds <= 300:
        parser.error('seconds must be 40..300')
    root = Path.home() / 'projects/shadps4'
    folder = Path(tempfile.mkdtemp(prefix='copy-range-launch-', dir=root / 'logs'))
    try:
        with (root / 'clean-rebase-verified/.build.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info, source, candidate, corrected = verified_report(root)
            text, base = prepare_runtime(root, source)
            runtime_path = launch_record(root, folder, info, corrected, text)
            runtime = load_module(runtime_path, 'verified_copy_candidate_runtime')
            runtime.verify_host(folder / 'build-result.json', candidate, REV)
            baseline = root / f'clean-rebase-verified/candidates/{BASE}-{base.BASE_SHA256}/shadps4'
            if digest(baseline) != base.BASE_SHA256:
                raise RuntimeError('PRESERVED_BASELINE_CHANGED')
            print('CANDIDATE_VERIFIED=' + REV, flush=True)
            print('REPORT_PARENT_CORRECTION=VERIFIED; ORIGINAL_REPORT=PRESERVED', flush=True)
            print('REBUILD=NO; EMULATOR_SOURCE_CHANGES=NONE', flush=True)
            if args.check_only:
                print('RUNTIME_PREFLIGHT=PASS; GAME_LAUNCHED=NO', flush=True)
                return 0
            sys.argv = [str(runtime_path), '--candidate', str(candidate),
                        '--source-revision', REV, '--host-report', str(folder / 'build-result.json'),
                        '--baseline-binary', str(baseline),
                        '--baseline-profile', str(Path.home() / '.local/share/shadPS4'),
                        '--game', '/mnt/roms-all/ps4-games/CUSA11456/eboot.bin',
                        '--seconds', str(args.seconds)]
            return runtime.main()
    except (Exception, KeyboardInterrupt) as error:
        (folder / 'launch-error.txt').write_text(type(error).__name__ + ': ' + str(error) + '\n')
        archive = folder.with_suffix('.tar.gz')
        with tarfile.open(archive, 'w:gz') as packed:
            packed.add(folder, arcname='launch')
        print('LAUNCH_FAILED=' + str(error), file=sys.stderr, flush=True)
        print('UPLOAD_THIS=' + str(archive), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
