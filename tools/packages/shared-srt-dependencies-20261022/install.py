#!/usr/bin/env python3
"""Materialize the complete pinned package from existing Git objects. No build yet."""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
PREFIX='tools/packages/shared-srt-dependencies-20261022'
REQUIRED={'run_srt_dependencies.py','patch_srt_dependencies.py','verify_srt_dependencies.py',
          'srt_dependency_schedule.h','srt_dependency_codegen.inc','test_srt_schedule.cpp',
          'test_srt_order.cpp','test_srt_dependency_codegen.cpp','pointer_functions.inc',
          'old_visit_fixture.inc','comparator_fixture.inc'}

def install(source,revision,destination):
    if re.fullmatch('[0-9a-f]{40}',revision) is None:raise RuntimeError('INVALID_REVISION')
    env={k:v for k,v in os.environ.items() if k not in ('GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_COMMON_DIR')}
    def get(path):return subprocess.check_output(['git','-C',str(source),'show',revision+':'+PREFIX+'/'+path],env=env,timeout=30)
    raw=get('MANIFEST.json');manifest=json.loads(raw)
    if not isinstance(manifest,dict) or not REQUIRED.issubset(manifest):raise RuntimeError('INCOMPLETE_MANIFEST')
    contents={}
    for name,expected in manifest.items():
        if not isinstance(name,str) or Path(name).name!=name or name.startswith('.') or name=='MANIFEST.json':
            raise RuntimeError('UNSAFE_PACKAGE_PATH')
        if not isinstance(expected,str) or re.fullmatch('[0-9a-f]{64}',expected) is None:raise RuntimeError('INVALID_PACKAGE_HASH')
        data=get(name)
        if hashlib.sha256(data).hexdigest()!=expected:raise RuntimeError('PACKAGE_HASH_MISMATCH='+name)
        if name.endswith('.py'):ast.parse(data,filename=name)
        contents[name]=data
    contents['MANIFEST.json']=raw
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():raise RuntimeError('DESTINATION_OCCUPIED')
        for name,data in contents.items():
            p=destination/name
            if p.is_symlink() or not p.is_file() or p.read_bytes()!=data:raise RuntimeError('INSTALLED_PACKAGE_CHANGED='+name)
    else:
        destination.parent.mkdir(parents=True,exist_ok=True)
        temp=Path(tempfile.mkdtemp(prefix='.shared-srt-install-',dir=destination.parent))
        try:
            for name,data in contents.items():(temp/name).write_bytes(data)
            os.rename(temp,destination)
        finally:
            if temp.exists():shutil.rmtree(temp)
    print('COMPLETE_SRT_PACKAGE=VERIFIED files='+str(len(manifest)))
    print('PACKAGE='+str(destination))
    print('GAME_LAUNCHED=NO; BUILD_NOT_STARTED=YES')

if __name__=='__main__':
    if len(sys.argv)!=4:raise SystemExit('Usage: install.py SOURCE COMMIT DIRECTORY')
    try:install(Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3]))
    except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError) as e:
        print('INSTALL_FAILED='+str(e),file=sys.stderr);raise SystemExit(2)
