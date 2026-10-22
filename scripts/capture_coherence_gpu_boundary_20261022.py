#!/usr/bin/env python3
"""Collect a native RADV hang report for the exact verified coherence candidate.

Diagnostic only: RADV's recorder inserts trace points AND synchronization. A
successful instrumented run is not evidence of a rendering fix. No compilation,
shader substitution, permanent option, GPU reset or privilege change is performed.
The existing protected runner owns launch, fullscreen/FPS, saves, cleanup and Ollama.
"""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import types

PARENT_REV = 'f1ad2d69162795d0162e828e15ca782f00c4b1b1'
PARENT_FILE = 'scripts/run_verified_coherence_20261022.py'
PARENT_SHA = '9b1db80d44f031967bdcd4ef866d1282e38aaa6d3481d0ecb96c6215c1710f7a'
SOURCE_REV = 'c938fd009e936dd4f59bf673890dda47d18ac409'
RUNTIME_SHA = 'b688234b4dfbd690399f0fec2f4c55351379cfd71d6cebb4484318e83175510f'
DIAGNOSTIC = 'hang,nocache,noumr'
BUDGET = 96 * 1024**2
FILE_LIMIT = 64 * 1024**2
MAX_FILES = 2048
VALIDATOR_SHA = '8493755cf657810e0155b4cecd50106acf6d05e4cda687369bae2c0329808750'


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('EXPECTED_REGULAR_FILE=' + str(path))
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def once(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError('PINNED_RUNTIME_ANCHOR_CHANGED=' + before[:90])
    return text.replace(before, after, 1)


def report_directories(home, pid, before):
    """Only this owned emulator PID's new reports, never other applications' dumps."""
    if type(pid) is not int or pid <= 0:
        return []
    pattern = re.compile(r'radv_dumps_' + str(pid) + r'_[0-9.\-_]+\Z')
    result = []
    for path in home.iterdir():
        if pattern.fullmatch(path.name) and path.name not in before:
            if path.is_symlink() or not path.is_dir():
                continue
            result.append(path)
    return sorted(result)


def collect_reports(home, pid, before, destination, budget=BUDGET, file_limit=FILE_LIMIT):
    """Copy bounded regular evidence; leave all originals intact and mark omissions."""
    directories = report_directories(home, pid, before)
    entries = []
    candidates = []
    enumeration_limited = False
    for folder in directories:
        for current, dirs, files in os.walk(folder, followlinks=False):
            for name in sorted(dirs[:]):
                path = Path(current) / name
                if path.is_symlink():
                    dirs.remove(name)
                    entries.append({'path':str(path.relative_to(home)), 'status':'REJECTED_SYMLINK_DIRECTORY'})
            for name in sorted(files):
                if len(candidates) >= MAX_FILES:
                    enumeration_limited = True
                    break
                path = Path(current) / name
                relative = path.relative_to(home)
                mode = path.lstat().st_mode
                if not stat.S_ISREG(mode):
                    entries.append({'path':str(relative), 'status':'REJECTED_NONREGULAR'})
                    continue
                priority = 0 if path.suffix == '.spv' else 1 if name in (
                    'trace.log','pipeline.log','gpu_info.log','app_info.log','registers.log',
                    'vm_fault.log','bo_ranges.log','addr_binding_report.log') else 2
                candidates.append((priority, str(relative), path))
            if enumeration_limited:
                break
        if enumeration_limited:
            break
    copied = 0
    for _, relative, path in sorted(candidates):
        size = path.stat().st_size
        entry = {'path':relative, 'bytes':size}
        if size > file_limit or copied + size > budget:
            entry['status'] = 'OMITTED_BUDGET_ORIGINAL_RETAINED'
        else:
            # O_NOFOLLOW and descriptor identity checks also reject a swapped file.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                initial = os.fstat(fd)
                if not stat.S_ISREG(initial.st_mode) or initial.st_size != size:
                    raise RuntimeError('DRIVER_FILE_CHANGED_BEFORE_READ=' + relative)
                with os.fdopen(fd, 'rb', closefd=False) as source:
                    data = source.read(file_limit + 1)
                final = os.fstat(fd)
                if len(data) != size or (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
                    entry['status'] = 'CHANGED_DURING_READ_ORIGINAL_RETAINED'
                else:
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    copied += size
                    entry.update(status='COPIED', sha256=hashlib.sha256(data).hexdigest())
            finally:
                os.close(fd)
        entries.append(entry)
    complete = bool(directories) and not enumeration_limited and all(x['status'] == 'COPIED' for x in entries)
    return {'owned_pid':pid, 'report_directories':[str(p) for p in directories],
            'status':'COMPLETE' if complete else 'NOT_PRODUCED' if not directories else 'INCOMPLETE',
            'copied_bytes':copied,'budget_bytes':budget,'file_limit_bytes':file_limit,
            'enumeration_limited':enumeration_limited,'files':entries,
            'originals_modified':False, 'other_process_reports_collected':False,
            'interpretation':'BOUND_PIPELINE_AND_TRACE_ARE_EVIDENCE_NOT_PROOF_OF_SINGLE_FAULTING_INSTRUCTION'}


def validate_bound_shaders(root, evidence, output):
    shaders = sorted(evidence.rglob('*.spv')) if evidence.is_dir() else []
    validator = root / 'tools/spirv-validator-7651d9234622/build/tools/spirv-val'
    result = {'target_env':'vulkan1.3','layout_relaxations':False, 'shaders':[]}
    if not shaders:
        result['status'] = 'NOT_RUN_NO_DRIVER_SPIRV'
    elif digest(validator) != VALIDATOR_SHA or not os.access(validator, os.X_OK):
        raise RuntimeError('PINNED_SPIRV_VALIDATOR_CHANGED')
    else:
        for path in shaders:
            try:
                p = subprocess.run([str(validator),'--target-env','vulkan1.3',str(path)],
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=30)
                item = {'path':str(path.relative_to(evidence)), 'sha256':digest(path),
                        'exit':p.returncode, 'diagnostic':p.stdout[:16384]}
            except (OSError,subprocess.TimeoutExpired) as error:
                item = {'path':str(path.relative_to(evidence)), 'tool_error':str(error)}
            result['shaders'].append(item)
        result['status'] = 'PASS' if all(x.get('exit') == 0 for x in result['shaders']) else 'FINDINGS_OR_TOOL_ERROR'
    output.write_text(json.dumps(result,indent=2)+'\n')
    return result['status']


def install_hooks(parent):
    previous_adapt, previous_configure = parent.adapt_runtime, parent.configure_runtime
    # A different copied test session avoids changing the existing test/reference saves.
    parent.SESSION = 'got-coherence-driver-trace'

    def adapt(text):
        if hashlib.sha256(text.encode()).hexdigest() != RUNTIME_SHA:
            raise RuntimeError('PROTECTED_RUNTIME_IDENTITY_CHANGED')
        text = previous_adapt(text)
        text = once(text, 'f"shadps4-automatic-coherence-runtime-{stamp}-{uuid.uuid4().hex[:6]}"',
                          'f"shadps4-coherence-driver-trace-{stamp}-{uuid.uuid4().hex[:6]}"')
        text = once(text, '                if self.log.fault.is_set():\n                    raise RuntimeError("GPU_FAULT_ABORT")',
                          '                if self.log.fault.is_set():\n                    self.driver_fault_wait(elapsed)')
        text = once(text, 'self.meta["TEST_POLICY"] = "NORMAL_RENDERER_NO_DRIVER_OVERRIDES"',
                          'self.meta["TEST_POLICY"] = "DIAGNOSTIC_RADV_HANG_SYNCHRONIZES_NOT_A_GAMEPLAY_FIX"')
        ast.parse(text)
        return text

    def configure(module, root, info):
        previous_configure(module, root, info)
        Original = module.Runtime
        class TraceRuntime(Original):
            def environment(self):
                env = super().environment()
                self.driver_home = Path(env.get('HOME') or str(Path.home()))
                if self.driver_home.resolve() != Path.home().resolve():
                    raise RuntimeError('UNEXPECTED_CHILD_HOME')
                self.driver_before = {p.name for p in self.driver_home.glob('radv_dumps_*')}
                # These are diagnostics only; no nodcc, fullsync, skip or shader replacements.
                env['RADV_DEBUG'] = DIAGNOSTIC
                self.meta.update(DIAGNOSTIC_MODE='RADV_HANG_NOCACHE_NO_UMR',
                    RENDER_STATE='UNCHANGED_EMULATOR_DRIVER_INSTRUMENTED',
                    EXPENSIVE_DIAGNOSTICS='DRIVER_TRACE_POINTS_AND_SYNCHRONIZATION',
                    RENDERING_FIX_CLAIMED='NO', UMR='DISABLED_NO_PRIVILEGE_CHANGES',
                    DRIVER_REPORT_HOME=str(self.driver_home), SHADER_DUMPING='OFF')
                (self.run/'info/render-environment.json').write_text(json.dumps({
                    k:v for k,v in env.items() if k.startswith(('SHADPS4_','RADV_','VK_','MESA_','AMD_DEBUG'))
                },indent=2)+'\n')
                self.say('DRIVER_TRACE=ENABLED_FOR_THIS_PROCESS_ONLY; NOT_A_RENDERING_FIX')
                self.say('Driver instrumentation adds synchronization; a successful run is diagnostic, not a normal-runtime PASS.')
                return env

            def driver_fault_wait(self, elapsed):
                if not hasattr(self, 'driver_fault_at'):
                    self.driver_fault_at = elapsed
                    self.meta['DRIVER_FAULT_FIRST_SECONDS'] = str(elapsed)
                    self.say('GPU fault recorded; allowing a bounded 20-second report-writing grace before owned-process cleanup.')
                if elapsed - self.driver_fault_at >= 20:
                    raise RuntimeError('GPU_FAULT_DRIVER_REPORT_GRACE_EXPIRED')

            def collect_indirect_snapshot(self):
                # The inherited finish() already stops only the owned emulator and
                # restores Ollama BEFORE this collector; collection cannot block that.
                try:
                    super().collect_indirect_snapshot()
                finally:
                    if self.proc is not None and hasattr(self, 'driver_home'):
                        folder = self.run/'info/driver-evidence'
                        summary = collect_reports(self.driver_home,self.proc.pid,self.driver_before,folder)
                        (self.run/'info/driver-capture.json').write_text(json.dumps(summary,indent=2)+'\n')
                        self.meta['DRIVER_CAPTURE'] = summary['status']
                        self.meta['DRIVER_CAPTURE_BYTES'] = str(summary['copied_bytes'])
                        self.meta['DRIVER_BOUND_SPIRV_VALIDATION'] = validate_bound_shaders(
                            root,folder,self.run/'info/driver-bound-spirv-validation.json')
                    else:
                        self.meta['DRIVER_CAPTURE'] = 'NOT_STARTED'
                    self.meta['EXPENSIVE_DIAGNOSTICS'] = 'DRIVER_TRACE_POINTS_AND_SYNCHRONIZATION'
                    self.meta['RENDERING_FIX_CLAIMED'] = 'NO'
        module.Runtime = TraceRuntime
    parent.adapt_runtime, parent.configure_runtime = adapt, configure


def load_parent(raw):
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA:
        raise RuntimeError('PINNED_COHERENCE_LAUNCHER_CHANGED')
    ast.parse(raw)
    module = types.ModuleType('verified_coherence_trace_parent')
    module.__file__ = 'pinned_run_verified_coherence_20261022.py'
    sys.modules[module.__name__] = module
    exec(compile(raw,module.__file__,'exec'),module.__dict__)
    if module.REV != SOURCE_REV:
        raise RuntimeError('WRONG_EMULATOR_CANDIDATE')
    install_hooks(module)
    return module


def main():
    source = Path.home()/'projects/shadps4/clean-rebase-verified/source'
    env = {k:v for k,v in os.environ.items() if k not in ('GIT_DIR','GIT_WORK_TREE','GIT_COMMON_DIR','GIT_INDEX_FILE')}
    raw = subprocess.check_output(['git','-c','core.hooksPath=/dev/null','-C',str(source),
        'show',PARENT_REV+':'+PARENT_FILE],env=env,stderr=subprocess.PIPE,timeout=30)
    return load_parent(raw).main()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError,RuntimeError,ValueError,subprocess.CalledProcessError) as error:
        print('DRIVER_TRACE_PREFLIGHT_FAILED='+str(error),file=sys.stderr,flush=True)
        root = Path.home()/'projects/shadps4/logs';root.mkdir(parents=True,exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='coherence-trace-preflight-',dir=root))
        (folder/'failure.txt').write_text(type(error).__name__+': '+str(error)+'\nGAME_LAUNCHED=NO\n')
        archive = folder.with_suffix('.tar.gz')
        with tarfile.open(archive,'w:gz') as packed:
            packed.add(folder/'failure.txt',arcname='info/failure.txt')
        print('UPLOAD_THIS='+str(archive),flush=True)
        raise SystemExit(2)
