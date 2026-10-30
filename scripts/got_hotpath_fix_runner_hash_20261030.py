#!/usr/bin/env python3
"""Fail-closed repair: compare Git blob SHA-1 to Git blob SHA-1, not SHA-256."""
from __future__ import annotations
import ast
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE_SHA='4068ec8178569f90139739eeaa94a39a568c7016fbd7b1c2457728bd84d02b47'
GIT_BLOB='c4dc6034e47413ef274a77c262c9ee792b89c58d'
ROOT=Path.home()/'projects'/'shadps4'/'scripts'
SOURCE=ROOT/'got_pr5467_revert_hotpath_resume_20261030.py'
TARGET=ROOT/'got_pr5467_revert_hotpath_resume_gitblob_20261030.py'
RUNNER=ROOT/'got_presrt_persistent_cache_118s.sh'

def verified_source(s: str) -> str:
    old="def sha(p:Path) -> str:\n    return hashlib.sha256(p.read_bytes()).hexdigest()\n"
    new=(old+"\ndef git_blob_sha(p:Path) -> str:\n"
         "    data=p.read_bytes()\n"
         "    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\\0'+data).hexdigest()\n")
    assert s.count(old)==1, 'PROFILER_SHA_FUNCTION_CHANGED'
    s=s.replace(old,new,1)
    old="check(sha(RUNNER)==BASE_RUNNER_SHA,'BASELINE_RUNNER_CHANGED')"
    new="check(git_blob_sha(RUNNER)==BASE_RUNNER_SHA,'BASELINE_RUNNER_CHANGED')"
    assert s.count(old)==1, 'PROFILER_RUNNER_SHA_CHECK_CHANGED'
    s=s.replace(old,new,1)
    return s

def test_make_runtime(s:str,runner:Path, expected_sha:str):
    parsed=ast.parse(s)
    methods={'sha','git_blob_sha','check','unique','make_runtime'}
    functions=[f for f in parsed.body if isinstance(f,ast.FunctionDef) and f.name in methods]
    if {f.name for f in functions} != methods:
        raise RuntimeError('PROFILE_METHOD_SET_CHANGED')
    with tempfile.TemporaryDirectory(prefix='.got-hotpath-preflight-',dir=ROOT) as tmp:
        ns={'__builtins__':__builtins__,'hashlib':hashlib,'Path':Path,
            'BASE_RUNNER_SHA':expected_sha,
            'BASELINE_SHA':'89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec',
            'RUNNER':runner,'EVID':Path(tmp)}
        module=ast.fix_missing_locations(ast.Module(body=functions,type_ignores=[]))
        exec(compile(module,'profile-runner-adapter','exec'),ns)
        generated=ns['make_runtime']('1'*64)
        subprocess.run(['bash','-n',str(generated)],check=True)
        if "old='"+'1'*64+"'" not in generated.read_text():
            raise RuntimeError('PROFILING_EXECUTABLE_HASH_NOT_BOUND')
        print('PROFILER_ADAPTER_DRY_RUN=PASS',flush=True)

def main():
    if not SOURCE.is_file() or SOURCE.is_symlink():
        raise RuntimeError('PROFILER_SOURCE_MISSING_OR_SYMLINK')
    original=SOURCE.read_bytes()
    if hashlib.sha256(original).hexdigest()!=SOURCE_SHA:
        raise RuntimeError('PROFILER_SOURCE_SHA256_CHANGED')
    if not RUNNER.is_file() or RUNNER.is_symlink():
        raise RuntimeError('BASELINE_RUNNER_MISSING_OR_SYMLINK')
    data=RUNNER.read_bytes()
    git_sha=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    if git_sha != GIT_BLOB:
        raise RuntimeError('BASELINE_RUNNER_GIT_BLOB_CHANGED:'+git_sha)
    fixed=verified_source(original.decode('utf-8'))
    compile(fixed,str(TARGET),'exec')
    test_make_runtime(fixed,RUNNER,GIT_BLOB)
    if TARGET.exists() or TARGET.is_symlink():
        if TARGET.is_symlink() or TARGET.read_text()!=fixed:
            raise RuntimeError('EXISTING_FIXED_PROFILER_CHANGED')
    else:
        tmp=TARGET.with_name(TARGET.name+'.pending')
        if tmp.exists() or tmp.is_symlink():
            raise RuntimeError('PROFILER_PENDING_FILE_EXISTS')
        tmp.write_text(fixed)
        tmp.replace(TARGET)
    print('PROFILER_GIT_BLOB_FIX=PASS',flush=True)
    return subprocess.call([sys.executable,'-u',str(TARGET)])

if __name__=='__main__':
    sys.exit(main())
