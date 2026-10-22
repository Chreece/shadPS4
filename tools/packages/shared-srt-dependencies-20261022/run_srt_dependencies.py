#!/usr/bin/env python3
"""Build-only shared SRT dependency restoration on the exact latest candidate.

The existing tested build/dependency restoration machinery is reused, but old
patches are NOT reconstructed: the complete 9d70b53b tree is the starting point.
No emulator launch or change to settings, saves, ES-DE, Sunshine or Ollama.
"""
from __future__ import annotations
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import types
import patch_srt_dependencies as patch

PARENT = '9d70b53b323f54a38bb2f77eed4435154679adfe'
TREE = 'e55a6ab5bf8460a14429744310b6da7facf0c7f0'
SIRIT = '471ebbd5f08047a9ece7f5f3766c848c37c8b580'
ATOMIC = '8329755a6da9c1827c3153de4f82d0638ee19ff8'
ATOMIC_SHA = '1aedac41041fd34210325729506b15336df0a5df9b1f5dbd1b358493f773a197'
LAUNCHER = 'e5db2b9967e4a69e76ff3fb7e8dcf1139ae058b3'
LAUNCHER_SHA = '6dffa58152ee047a52ed63e648b5b90a094de9a8d08101c094393d382b0ac63c'
PACKAGE = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_package(directory):
    m=directory/'MANIFEST.json'
    if m.is_symlink() or not m.is_file():raise RuntimeError('PACKAGE_MANIFEST_INVALID')
    manifest=json.loads(m.read_bytes())
    if not isinstance(manifest,dict) or not manifest:raise RuntimeError('PACKAGE_MANIFEST_INVALID')
    required={'run_srt_dependencies.py','patch_srt_dependencies.py','verify_srt_dependencies.py',
              'srt_dependency_schedule.h','srt_dependency_codegen.inc','test_srt_schedule.cpp',
              'test_srt_order.cpp','test_srt_dependency_codegen.cpp','pointer_functions.inc',
              'old_visit_fixture.inc','comparator_fixture.inc'}
    if not required.issubset(manifest):raise RuntimeError('INCOMPLETE_PACKAGE_MANIFEST')
    for name,expected in manifest.items():
        if not isinstance(name,str) or Path(name).name!=name or name.startswith('.'):
            raise RuntimeError('UNSAFE_MANIFEST_NAME')
        p=directory/name
        if p.is_symlink() or not p.is_file() or digest(p.read_bytes())!=expected:
            raise RuntimeError('PACKAGE_CHECKSUM_MISMATCH='+name)
        if name.endswith('.py'):ast.parse(p.read_bytes(),filename=name)


def install_delta(job,native,manifest,updates):
    """Only mutate Git objects, staged source and the new report until compile()."""
    if set(updates)!={patch.FLAT,patch.CACHE,patch.HEADER,patch.CODEGEN}:
        raise RuntimeError('SRT_CHANGESET_MISMATCH')
    if job.git('rev-parse',PARENT+'^{tree}').decode().strip()!=TREE:
        raise RuntimeError('RETAINED_PARENT_TREE_CHANGED')
    if job.git('rev-parse',PARENT+':'+native.SIRIT).decode().strip()!=SIRIT:
        raise RuntimeError('RETAINED_DEPENDENCY_CHANGED')
    for name,record in manifest['files'].items():
        if job.git('rev-parse',PARENT+':'+name).decode().strip()!=record['corrected_blob']:
            raise RuntimeError('RETAINED_SOURCE_MANIFEST_MISMATCH='+name)
    new_manifest=json.loads(json.dumps(manifest))
    env=dict(native.base.ENV,GIT_INDEX_FILE=str(job.run/'srt-dependency-index'))
    job.git('read-tree',PARENT,env=env)
    for name,data in updates.items():
        oid=job.git('hash-object','-w','--stdin',input_data=data).decode().strip()
        if oid!=patch.blob(data):raise RuntimeError('NEW_SOURCE_OBJECT_MISMATCH='+name)
        job.git('update-index','--add','--cacheinfo',f'100644,{oid},{name}',env=env)
        original=new_manifest['files'].get(name,{}).get('original_blob')
        new_manifest['files'][name]={'original_blob':original,'corrected_blob':oid,'sha256':digest(data)}
    tree=job.git('write-tree',env=env).decode().strip()
    revision=job.git('commit-tree',tree,'-p',PARENT,input_data=(
        'Resolve shared shader resource-table dependencies before declaring reads unavailable\n\n'
        'Retry only dependencies of real host-evaluable reads, keep GPU-dependent values on GPU, '
        'stop on no progress, reuse actual slots, retain codegen rollback, correct strict comparator, '
        'and reject flatbuffer slot wrap. No title/hash selectors or renderer options.\n').encode()).decode().strip()
    if set(job.git('diff','--name-only',PARENT,revision).decode().splitlines())!=set(updates):
        raise RuntimeError('INCREMENTAL_SRT_CHANGE_SET_CHANGED')
    if set(job.git('diff','--name-only',native.base.BASE,revision).decode().splitlines())!=set(new_manifest['files']):
        raise RuntimeError('RETAINED_FIX_SET_CHANGED')
    if job.git('rev-parse',revision+'^').decode().strip()!=PARENT:
        raise RuntimeError('NEW_PARENT_NOT_RETAINED_CANDIDATE')
    if job.git('rev-parse',revision+':'+native.SIRIT).decode().strip()!=SIRIT:
        raise RuntimeError('NEW_DEPENDENCY_NOT_RETAINED')
    new_manifest['parent']=PARENT
    new_manifest['srt_dependencies']={
        'parent_tree':TREE,'incremental_files':sorted(updates),'cache_version':'0x43520009',
        'cpu_uniformity_proof':'unchanged','guest_runtime_loop_limits':'none',
        'dynamic_resource_array_port':'not included','cpu_gpu_coherence_policy':'unchanged',
        'visual_recovery_verified':False}
    native.base.write_json(job.info/'correction.json',new_manifest)
    (job.info/'correction.patch').write_bytes(job.git('diff',native.base.BASE,revision,'--'))
    (job.info/'srt-dependency.patch').write_bytes(job.git('diff',PARENT,revision,'--'))
    for name,data in updates.items():
        out=job.stage/name;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(data)
    job.git('update-ref','refs/heads/local/srt-dependencies-'+revision[:12],revision)
    # Preserve the existing generated dependency commit, not a new timestamp-dependent copy.
    native.NEW_SIRIT=SIRIT
    job.revision=revision
    job.report.update(revision=revision,tree=tree,source_parent=PARENT,sirit_candidate=SIRIT,
        manifest_sha256=native.base.digest(job.info/'correction.json'),
        srt_dependencies='LOCAL_AND_CROSS_ROOT_REAL_READ_SCHEDULING',
        retained_candidate=PARENT,game_started=False,build_only=True)


def job_class(native, parent):
    class SharedSrtJob(parent):
        def preflight(self):
            super().preflight()
            raw=self.git('show',LAUNCHER+':scripts/run_verified_copy_candidate_20261022.py')
            if digest(raw)!=LAUNCHER_SHA:raise RuntimeError('PINNED_PARENT_VERIFIER_CHANGED')
            ast.parse(raw)
            module=types.ModuleType('retained_srt_parent_verifier')
            exec(compile(raw,'verified_copy_candidate.py','exec'),module.__dict__)
            info,source,candidate,report=module.verified_report(self.root)
            if source!=self.source or report['revision']!=PARENT or report['tree']!=TREE:
                raise RuntimeError('LATEST_BUILT_SOURCE_NOT_EXPECTED')
            self.retained_manifest=json.loads((info/'correction.json').read_bytes())
            self.retained_report=report
            (self.info/'retained-parent-build-result.json').write_bytes((info/'build-result.json').read_bytes())
            (self.info/'retained-parent-source-manifest.json').write_bytes((info/'correction.json').read_bytes())
            print('LATEST_PARENT_VERIFIED='+PARENT,flush=True)

        def prepare(self):
            # Deliberately do not reconstruct or reapply the older correction stack.
            before={name:self.git('show',PARENT+':'+name) for name in patch.EXPECTED}
            updates=patch.package_updates(PACKAGE,before)
            install_delta(self,native,self.retained_manifest,updates)
            self.command([sys.executable,'-B',PACKAGE/'verify_srt_dependencies.py',
                          '--compiler',self.compiler,'--output',self.run/'srt-model-tests'],capture=False)
            print('SRT_DEPENDENCY_DELTA=APPLIED existing_fixes=PRESERVED',flush=True)

        def verify_native_library(self):
            # Execute real generated machine code from the exact checked-out source.
            result=self.command([sys.executable,'-B',PACKAGE/'verify_srt_dependencies.py',
                         '--compiler',self.compiler,'--output',self.run/'srt-native-tests',
                         '--source',self.source])
            with (self.info/'native-tests.log').open('ab') as log:log.write(result)
            print(result.decode(),end='',flush=True)
            self.report['srt_native_dependency_regressions']='PINNED_XBYAK_EXECUTED'
            super().verify_native_library()
            self.command(['nice','-n','10','ninja','-C',self.build,'-j','2',
                          'CMakeFiles/shadps4.dir/'+patch.FLAT+'.o',
                          'CMakeFiles/shadps4.dir/'+patch.CACHE+'.o'],capture=False)

        def finish(self,code):
            for folder in ('srt-model-tests','srt-native-tests'):
                path=self.run/folder/'verification.log'
                if path.is_file():
                    (self.info/(folder+'.log')).write_bytes(path.read_bytes())
            return super().finish(code)
    return SharedSrtJob


def main():
    verify_package(PACKAGE)
    root=Path.home()/'projects/shadps4'
    path=root/'scripts'/('typed-float-atomic-'+ATOMIC+'.py')
    if path.is_symlink() or not path.is_file() or digest(path.read_bytes())!=ATOMIC_SHA:
        raise RuntimeError('VERIFIED_ATOMIC_WORKFLOW_MISSING_OR_CHANGED')
    spec=importlib.util.spec_from_file_location('retained_srt_atomic_workflow',path)
    atomic=importlib.util.module_from_spec(spec);sys.modules[spec.name]=atomic;spec.loader.exec_module(atomic)
    original=atomic.job_class
    atomic.job_class=lambda native,parent,validator:job_class(native,original(native,parent,validator))
    # This continuation NEVER invokes a game. No implicit runtime profile reset.
    if any(x!='--build-only' for x in sys.argv[1:]):raise RuntimeError('ONLY_BUILD_ONLY_IS_SUPPORTED')
    sys.argv=[sys.argv[0],'--build-only']
    return atomic.main()

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError) as error:
        print('SRT_DEPENDENCY_PREFLIGHT_ERROR='+str(error),file=sys.stderr,flush=True)
        root=Path.home()/'projects/shadps4/logs';root.mkdir(parents=True,exist_ok=True)
        folder=Path(tempfile.mkdtemp(prefix='srt-dependency-preflight-',dir=root))
        (folder/'error.txt').write_text(type(error).__name__+': '+str(error)+'\nGAME_LAUNCHED=NO\n')
        archive=folder.with_suffix('.tar.gz')
        with tarfile.open(archive,'w:gz') as out:out.add(folder/'error.txt',arcname='info/error.txt')
        print('UPLOAD_THIS='+str(archive),flush=True)
        raise SystemExit(2)
