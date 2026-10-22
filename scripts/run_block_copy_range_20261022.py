#!/usr/bin/env python3
"""Protected build of general compressed buffer-image copy-range accounting."""
import hashlib, importlib.util, json, subprocess, sys
from pathlib import Path
ROOT=Path.home()/'projects/shadps4'
PARENT='8329755a6da9c1827c3153de4f82d0638ee19ff8'
PARENT_SHA='1aedac41041fd34210325729506b15336df0a5df9b1f5dbd1b358493f773a197'
PATCH='822a68d8aae409b5a29759de462eb43329e52753'
TEST='ff6cdac368b847ecb67dfc64d74367c2ce7a9446'
TEST_BLOB='abad3b121c540358b7505a88750e37123398853a'
FILE='src/video_core/renderer_vulkan/vk_runtime.cpp'
OLD='3940e0abb310f36efd35488709afb5c1c1a173a8'
NEW='3ab9edd8584be5793fa21c65243f8c328c29e062'
def sha(data):return hashlib.sha256(data).hexdigest()
def blob(data):return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
runner=ROOT/'scripts'/('typed-float-atomic-'+PARENT+'.py')
if runner.is_symlink() or not runner.is_file() or sha(runner.read_bytes())!=PARENT_SHA:
    raise SystemExit('VERIFIED_ATOMIC_RUNNER_REQUIRED')
spec=importlib.util.spec_from_file_location('verified_previous_emulator_workflow',runner)
atomic=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=atomic
spec.loader.exec_module(atomic)
original_class=atomic.job_class
def class_factory(native,parent,validator):
    Original=original_class(native,parent,validator)
    class CheckedCopyRangeJob(Original):
        def prepare(self):
            super().prepare()
            old_revision=self.revision
            source=self.git('show',old_revision+':'+FILE)
            fixed=self.git('show',PATCH+':'+FILE)
            if blob(source)!=OLD or blob(fixed)!=NEW:
                raise RuntimeError('UNEXPECTED_COPY_RANGE_SOURCE')
            env=dict(native.base.ENV,GIT_INDEX_FILE=str(self.run/'copy-range-index'))
            self.git('read-tree',old_revision,env=env)
            oid=self.git('hash-object','-w','--stdin',input_data=fixed).decode().strip()
            if oid!=NEW:raise RuntimeError('WRITTEN_COPY_BLOB_CHANGED')
            self.git('update-index','--add','--cacheinfo',
                     f'100644,{oid},{FILE}',env=env)
            tree=self.git('write-tree',env=env).decode().strip()
            revision=self.git('commit-tree',tree,'-p',old_revision,input_data=(
                'Correct shared Vulkan copy-range bounds for compressed blocks and 64-bit sizes\n\n'
                'Preserve all other emulator, shader, game, and driver behavior.\n').encode()
            ).decode().strip()
            diff=set(self.git('diff','--name-only',old_revision,revision).decode().splitlines())
            if diff!={FILE}:raise RuntimeError('COPY_CHANGESET_NOT_SINGLE_FILE')
            manifest_file=self.info/'correction.json'
            manifest=json.loads(manifest_file.read_text())
            if FILE in manifest['files']:raise RuntimeError('COPY_FILE_ALREADY_CHANGED')
            manifest['files'][FILE]={
                'original_blob':OLD,'corrected_blob':NEW,'sha256':sha(fixed)}
            native.base.write_json(manifest_file,manifest)
            with (self.info/'correction.patch').open('ab') as f:
                f.write(b'\n# Generic Vulkan copy range correction\n')
                f.write(self.git('diff',old_revision,revision,'--',FILE))
            dest=self.stage/FILE
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(fixed)
            self.git('update-ref','refs/heads/local/copy-range-'+revision[:12],revision)
            self.revision=revision
            self.report.update(revision=revision,tree=tree,source_parent=old_revision,
                manifest_sha256=native.base.digest(manifest_file),
                block_copy='CEIL_BLOCK_PITCH_AND_U64')
            test=self.git('show',TEST+':scripts/validate_block_copy_range_20261022.py')
            if blob(test)!=TEST_BLOB:raise RuntimeError('INVALID_REGRESSION_SOURCE')
            test_path=self.run/'block-copy-test.py'
            test_path.write_bytes(test)
            print('CHECKING_REAL_BLOCK_COPY_FUNCTION',flush=True)
            self.command([sys.executable,'-B',str(test_path),
                          '--source',str(self.source),'--revision',revision,
                          '--compiler',self.compiler],capture=False)
            print('COPY_RANGE_FIX=APPLIED shared_files=1',flush=True)
        def verify_native_library(self):
            super().verify_native_library()
            print('COMPILING_REAL_VULKAN_RUNTIME_OBJECT',flush=True)
            self.command(['nice','-n','10','ninja','-C',self.build,'-j','2',
                'CMakeFiles/shadps4.dir/'+FILE+'.o'],capture=False)
    return CheckedCopyRangeJob
atomic.job_class=class_factory
try:
    raise SystemExit(atomic.main())
except (RuntimeError,OSError,ValueError,subprocess.CalledProcessError) as exc:
    print('COPY_RANGE_ERROR='+str(exc),file=sys.stderr)
    raise SystemExit(2)
