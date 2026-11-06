#!/usr/bin/env python3
"""Recover shader evidence from an existing run without starting the emulator."""
import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tarfile

COLLECTOR_SHA256 = 'ac31fdf2f5a991bef96ec870ea022e0fd0ed394838a3fdefa574a4690b3dc526'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024**2:
        raise RuntimeError('INVALID_RECORD_FILE: ' + str(path))
    return json.loads(path.read_text())


def contained(path, parent):
    if path.resolve() != path or not path.is_relative_to(parent):
        raise RuntimeError('EVIDENCE_PATH_OUTSIDE_PROJECT_OR_SYMLINK: ' + str(path))
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.home() / 'projects/shadps4')
    parser.add_argument('--record', required=True)
    parser.add_argument('--expected-source', required=True)
    parser.add_argument('--collector-commit', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'universal-graphics-\d{8}T\d{6}Z-\d+', args.record):
        parser.error('record must be the original universal-graphics directory name')
    if not all(re.fullmatch(r'[0-9a-f]{40}', value)
               for value in (args.expected_source, args.collector_commit)):
        parser.error('source and collector commits must be immutable 40-character SHAs')
    project = args.project.expanduser().resolve()
    logs = contained(project / 'logs', project)
    tag = 'universal-gpu-work-recovery-' + dt.datetime.now(dt.timezone.utc).strftime(
        '%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
    out = logs / tag
    out.mkdir(parents=True, exist_ok=False)
    result = {'schema_version': 2, 'record_id': tag, 'scope': 'UNIVERSAL_SHADPS4',
              'timestamp_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'source_commit': args.expected_source, 'collector_commit': args.collector_commit,
              'original_record': args.record, 'game_launched': False, 'tested_games': [],
              'graphics_verified': False, 'gpu_used': False, 'installed_binary_changed': False,
              'result': 'RECOVERY_FAILED', 'evidence': str(out) + '.tar.gz'}
    rc = 1
    try:
        prior = contained(logs / args.record, logs)
        runtime = contained(project / 'tests' / args.record, project / 'tests')
        native = read_json(prior / 'result.json')
        launch = read_json(contained(prior / 'capture/launch.json', prior))
        build_path = contained(Path(native['build_record']), logs)
        build = read_json(build_path)
        expected_binary = native['binary_sha256']
        if (native['source_commit'] != args.expected_source or
                build['source_commit'] != args.expected_source or
                build['result'] != 'PASS_NATIVE_BUILD_CPU_AND_STARTUP_TESTS_GPU_NOT_RUN' or
                build['binary_sha256'] != expected_binary or launch['sha256'] != expected_binary or
                native.get('native', {}).get('binary_sha256') != expected_binary):
            raise RuntimeError('ORIGINAL_SOURCE_OR_BINARY_RECORD_MISMATCH')
        result['binary_sha256'] = expected_binary
        result['original_tested_games'] = native.get('tested_games', [])
        result['original_gpu_failure'] = native.get('native', {}).get('gpu_failure')
        result['original_ollama_restore_leases'] = native.get('ollama_restore_leases', [])
        binary = contained(Path(build['candidate_binary']), project / 'tests')
        if binary.is_file():
            if digest(binary) != expected_binary:
                raise RuntimeError('RETAINED_BINARY_CHANGED')
            result['retained_binary_verification'] = 'SHA256_MATCH_NO_EXECUTION'
        else:
            result['retained_binary_verification'] = 'ABSENT_USING_RECORDED_PROVENANCE'
        dumps = contained(runtime / 'user/shader/dumps', runtime)
        if not dumps.is_dir():
            raise RuntimeError('RETAINED_SHADER_DUMPS_MISSING')
        collector = Path(__file__).resolve().with_name('run_universal_verified_graphics.py')
        if digest(collector) != COLLECTOR_SHA256:
            raise RuntimeError('COLLECTOR_HASH_MISMATCH')
        spec = importlib.util.spec_from_file_location('verified_shader_collector', collector)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        console = contained(prior / 'capture/full-console.log', prior)
        if not console.is_file():
            console = contained(prior / 'console-tail.log', prior)
        if not console.is_file():
            raise RuntimeError('ORIGINAL_CONSOLE_MISSING')
        copied = []
        for relative in ('result.json', 'capture/launch.json', 'capture/native-result.json',
                         'capture/gpu-work.log', 'capture/graphics-diagnostics.log',
                         'capture/vulkan-validation.log', 'ollama-restore.json'):
            path = contained(prior / relative, prior)
            if path.is_file() and path.stat().st_size <= 4 * 1024**2:
                target = out / 'original' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                copied.append({'file': relative, 'sha256': digest(target)})
        shutil.copyfile(build_path, out / 'original/build-result.json')
        validation = out / 'original/capture/vulkan-validation.log'
        if validation.is_file():
            (out / 'capture').mkdir(exist_ok=True)
            shutil.copyfile(validation, out / 'capture/vulkan-validation.log')
        result['original_files'] = copied
        result['console_sha256_before'] = digest(console)
        result['shader_diagnostics'] = module.collect_shader_diagnostics(
            runtime, out, console, native.get('ordered_gds_evidence', []))
        if digest(console) != result['console_sha256_before']:
            raise RuntimeError('ORIGINAL_CONSOLE_CHANGED_DURING_RECOVERY')
        selected = result['shader_diagnostics']
        if not selected['first_wait_window'] or not selected['first_wait_work_hashes']:
            raise RuntimeError('FIRST_PENDING_WAIT_HAS_NO_RECORDED_SHADERS')
        if selected['first_wait_hashes_truncated'] or selected['first_wait_missing_binary_hashes']:
            result['result'] = 'PARTIAL_FIRST_WAIT_SHADER_RECOVERY_GPU_NOT_RUN'
        else:
            result['result'] = 'FIRST_WAIT_SHADER_RECOVERY_COMPLETE_GPU_NOT_RUN'
            rc = 0
        result['limits'] = 'Shader hash may have multiple permutations; recovered modules are not proof of the executed permutation or root cause. No model restoration or GPU recovery attempted.'
        for path in (Path(__file__).resolve(), collector):
            shutil.copyfile(path, out / path.name)
    except Exception as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        result['exit_code'] = rc
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        journal = project / 'docs/emulator/TRIALS.jsonl'
        journal.parent.mkdir(parents=True, exist_ok=True)
        try:
            with journal.open('a') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                stream.write(json.dumps(result, separators=(',', ':')) + '\n')
        finally:
            with tarfile.open(str(out) + '.tar.gz', 'w:gz') as archive:
                archive.add(out, arcname=out.name)
            print('RESULT=' + result['result'], flush=True)
            print('UPLOAD_THIS=' + str(out) + '.tar.gz', flush=True)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
