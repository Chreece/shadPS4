#!/usr/bin/env python3
"""Complete the missed PR107 SPIR-V checks and collect an existing GoW error.
No emulator launch/build, source/settings writes, package installation or GPU access.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess as sp
import sys
import tarfile
import time
import traceback

PRIOR = 'got-c925-indirect-materials-20261029T053938612839Z'
ARCHIVE_SHA = '3a3599725a7ba284ecc5e5b22bc9029ba31b6b3a39d17500f9822042de102569'
ELF_SHA = '3b0695880f0f2b34390236fa8e2f96dc821c204dfa362691a2eb0c866b5bbb6f'
PACKAGE_SHA = '2cc272ef1340cf0b47de6c5fcccaf91574d7a8f4061abb718d6c639463e85aab'
VALIDATOR_SHA = 'f386e67676bc134802b6bfec6d96fff0f9437d0f60e4c97ee0afa59ab4f1c4ea'
GOW_PREFIX = 'gow-vulkan-validation-20261029T053642Z-2451872'
GOW_GAME = 'gow-vulkan-validation-game-20261029-053642-2451873.log'
MAX_FILE = 16 * 1024**2
MAX_TOTAL = 64 * 1024**2
MODES = {
    'universal': ['--target-env', 'spv1.6'],
    'vulkan13-strict': ['--target-env', 'vulkan1.3'],
    'vulkan13-maintenance8-scalar': ['--target-env', 'vulkan1.3',
                                   '--scalar-block-layout', '--allow-offset-texture-operand'],
}

def emit(message):
    print(time.strftime('%H:%M:%S') + ' ' + message, flush=True)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()

def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')

def run(args, timeout=15):
    """Only signal/reap the child created here; never run through a shell."""
    child = sp.Popen([str(x) for x in args], stdout=sp.PIPE, stderr=sp.STDOUT,
                     stdin=sp.DEVNULL, start_new_session=True)
    try:
        output, _ = child.communicate(timeout=timeout)
        return child.returncode, output.decode(errors='replace')
    finally:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=2)
            except sp.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=3)
            except ProcessLookupError:
                pass
        if child.stdout is not None:
            child.stdout.close()

def safe_member(member):
    p = PurePosixPath(member.name)
    return (member.isfile() and not p.is_absolute() and '..' not in p.parts
            and 0 <= member.size <= MAX_FILE)

def locate_validator(project, evidence):
    """The earlier runner searched got-spirv-offline.*, not the extraction directory."""
    scripts = (project / 'scripts').resolve()
    candidates = []
    private = scripts / ('spirv-tools-private-' + PACKAGE_SHA[:16])
    candidates += [(p, 'checksum-pinned-private-package')
                   for p in sorted(private.glob('unpacked-*/usr/bin/spirv-val'))]
    # Also honor the extraction path recorded by the existing successful audit.
    metadata = project / 'logs/got-c925-spirv-offline-20261028T175021642972Z/validator-package.json'
    if metadata.is_file() and metadata.stat().st_size < 64 * 1024:
        item = json.loads(metadata.read_text())
        if item.get('sha256') == PACKAGE_SHA and item.get('extracted_to'):
            candidates.append((Path(item['extracted_to']) / 'usr/bin/spirv-val',
                               'previous-audit-extraction'))
    installed = shutil.which('spirv-val')
    if installed:
        candidates.append((Path(installed), 'installed-PATH-tool'))
    seen, attempts = set(), []
    for path, origin in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        row = {'path': str(path), 'origin': origin}
        if not path.is_file() or not os.access(path, os.X_OK):
            row['result'] = 'MISSING_OR_NOT_EXECUTABLE'; attempts.append(row); continue
        if origin != 'installed-PATH-tool':
            if path.is_symlink() or not resolved.is_relative_to(scripts):
                row['result'] = 'PRIVATE_PATH_REFUSED'; attempts.append(row); continue
        row['sha256'] = sha(path)
        if origin != 'installed-PATH-tool' and row['sha256'] != VALIDATOR_SHA:
            row['result'] = 'PRIVATE_BINARY_HASH_MISMATCH'; attempts.append(row); continue
        try:
            code, version = run([path, '--version'], timeout=5)
            if code or 'SPIRV-Tools' not in version:
                raise RuntimeError('VALIDATOR_VERSION_CHECK_FAILED')
            code, help_text = run([path, '--help'], timeout=5)
            if code or '--allow-offset-texture-operand' not in help_text:
                raise RuntimeError('VALIDATOR_LACKS_REQUIRED_SEPARATE_FEATURE_MODE')
            row.update(result='SELECTED', version=version)
            attempts.append(row); save(evidence/'validator-discovery.json', attempts)
            return path, row
        except Exception as exc:
            row.update(result='TOOL_ERROR', error=str(exc)); attempts.append(row)
    save(evidence/'validator-discovery.json', attempts)
    raise RuntimeError('VALIDATOR_UNAVAILABLE_NO_INSTALL_NO_GAME')

def snapshot_modules(archive, evidence):
    unique, sources, total = {}, [], 0
    output = evidence / 'modules'; output.mkdir(exist_ok=True)
    with tarfile.open(archive, 'r:gz') as tf:
        for member in tf:
            p = PurePosixPath(member.name)
            if not safe_member(member) or 'fresh-shaders' not in p.parts or p.suffix != '.spv':
                continue
            if len(sources) >= 256 or total + member.size > MAX_TOTAL:
                raise RuntimeError('SHADER_INPUT_BUDGET_EXCEEDED')
            stream = tf.extractfile(member)
            if stream is None:
                raise RuntimeError('SHADER_MEMBER_UNREADABLE')
            with stream:
                data = stream.read(MAX_FILE+1)
            if len(data) != member.size or len(data) < 20 or len(data) % 4 or data[:4] != b'\x03\x02#\x07':
                raise RuntimeError('INVALID_SPIRV_CONTAINER:' + str(p))
            h = hashlib.sha256(data).hexdigest(); total += len(data)
            sources.append({'archive_member': member.name, 'sha256': h, 'bytes': len(data)})
            if h not in unique:
                (output/(h+'.spv')).write_bytes(data)
                unique[h] = {'sha256': h, 'bytes': len(data), 'origins': []}
            unique[h]['origins'].append(member.name)
    save(evidence/'shader-origins.json', sources)
    save(evidence/'unique-shaders.json', list(unique.values()))
    if not unique:
        raise RuntimeError('NO_CAPTURED_SHADER_MODULES')
    return list(unique.values()), len(sources)

def collect_existing_gow(project, evidence):
    """Only the exact already-recorded run. No launch, no unrelated log sweep."""
    logs = project/'logs'; dest = evidence/'gow-existing'; dest.mkdir(exist_ok=True)
    records, total = [], 0
    for name in (GOW_PREFIX+'.log', GOW_GAME):
        path = logs/name
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_FILE:
            data = path.read_bytes(); (dest/name).write_bytes(data); total += len(data)
            records.append({'source': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
                            'bytes': len(data), 'copied': name})
        else:
            records.append({'source': str(path), 'result': 'MISSING_OR_NOT_BOUNDED_REGULAR_FILE'})
    archive = logs/(GOW_PREFIX+'.tar.gz')
    if archive.is_file() and not archive.is_symlink():
        archive_meta = {'source': str(archive), 'bytes': archive.stat().st_size,
                        'sha256': sha(archive), 'extracted_text_only': True}
        records.append(archive_meta)
        with tarfile.open(archive, 'r:gz') as tf:
            for member in tf:
                p = PurePosixPath(member.name)
                if not safe_member(member) or p.suffix not in ('.log','.json','.txt'):
                    continue
                if total + member.size > MAX_TOTAL or len(records) >= 100:
                    records.append({'result':'TEXT_BUDGET_REACHED'}); break
                stream = tf.extractfile(member)
                if stream is None: continue
                with stream: data = stream.read(MAX_FILE+1)
                if len(data) != member.size: continue
                name = f'archive-{len(records):03d}-' + p.name
                (dest/name).write_bytes(data); total += len(data)
                records.append({'source': str(archive)+'::'+member.name, 'copied': name,
                                'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
    else:
        records.append({'source': str(archive), 'result': 'ARCHIVE_NOT_PRESENT'})
    save(evidence/'gow-evidence-manifest.json', records)
    return records

def audit_modules(validator, modules, evidence):
    results = []
    for item in modules:
        for mode, args in MODES.items():
            emit('VALIDATING='+item['sha256'][:12]+' MODE='+mode)
            row = {'module_sha256': item['sha256'], 'mode': mode, 'arguments': args}
            try:
                code, text = run([validator, *args, evidence/'modules'/(item['sha256']+'.spv')], 15)
                (evidence/(item['sha256']+'.'+mode+'.log')).write_text(text)
                row.update(exit_code=code, result='PASS' if code == 0 else 'DIAGNOSTIC_REQUIRES_REVIEW')
            except Exception as exc:
                row.update(result='VALIDATOR_EXECUTION_FAILED', error=type(exc).__name__+':'+str(exc))
            results.append(row)
            save(evidence/'validation-results.json', results)
    return results

def main(project=None):
    project = Path(project) if project else Path.home()/'projects/shadps4'
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evid = project/'logs'/('got-c925-indirect-validation-'+stamp); evid.mkdir(parents=True)
    archive = project/'logs'/(PRIOR+'.tar.gz')
    binary = project/'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z/build/shadps4'
    result = {'schema_version':2, 'record_id':evid.name, 'journal_scope':'UNIVERSAL_SHADPS4',
              'related_prs':[107], 'build':'NOT_RUN', 'tested_games':[], 'game':'NOT_RUN',
              'gpu_access':False, 'ollama_access':False, 'system_install':False,
              'emulator_source_changes':False, 'input_archive':str(archive),
              'graphics_fixed':'UNVERIFIED', 'screenshot_equality_interpretation':'NOT_USED'}
    before = sha(binary) if binary.is_file() else None
    result['current_binary_before'] = before
    emit('EVIDENCE_DIRECTORY='+str(evid)); emit('MODE=OFFLINE_ONLY NO_REBUILD_NO_GAME')
    code = 1
    try:
        # Collect existing GoW failure evidence even if a separate GoT check fails.
        collect_existing_gow(project,evid)
        if not archive.is_file() or sha(archive) != ARCHIVE_SHA:
            raise RuntimeError('PR107_EVIDENCE_ARCHIVE_CHANGED_OR_MISSING')
        result['input_archive_sha256'] = ARCHIVE_SHA
        modules, origins = snapshot_modules(archive,evid)
        result.update(captured_module_files=origins, distinct_module_bytes=len(modules))
        validator, meta = locate_validator(project,evid)
        result['validator'] = meta
        results = audit_modules(validator,modules,evid)
        result['validation_results'] = results
        result['status'] = 'OFFLINE_VALIDATION_FINISHED'
        code = 0 if all(r['result']=='PASS' for r in results) else 2
        result['interpretation'] = ('Separate strict and feature-aware diagnostics; passing does not prove '
            'GPU resource-binding correctness or menu material recovery. The current executable is not run.')
    except (Exception,KeyboardInterrupt) as exc:
        result['status'] = str(exc) or type(exc).__name__
        (evid/'failure.txt').write_text(traceback.format_exc()); emit('STOP='+result['status'])
        code = 130 if isinstance(exc,KeyboardInterrupt) else 1
    finally:
        after = sha(binary) if binary.is_file() else None
        result.update(current_binary_after=after, binary_unchanged=after==before, runner_exit_code=code)
        if after != before:
            result['concurrent_binary_change_detected']=True
            code=1;result['runner_exit_code']=code
        save(evid/'trial.json',result)
        output = Path(str(evid)+'.tar.gz')
        with tarfile.open(output,'w:gz') as tf: tf.add(evid,arcname=evid.name)
        emit('RESULT='+result.get('status','UNKNOWN'));emit('EVIDENCE='+str(output))
    return code

if __name__ == '__main__':
    sys.exit(main())
