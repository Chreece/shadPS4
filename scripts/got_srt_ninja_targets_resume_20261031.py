#!/usr/bin/env python3
"""Repair GoT SRT build preflight: query Ninja's graph, not an inoperative dry-run.

The verified original SRT patch is preserved, and a separate revised runner is
created. No game, emulator, or system process is stopped by this bootstrapper.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
import sys

PROJECT = Path.home() / 'projects/shadps4'
SCRIPTS = PROJECT / 'scripts'
ORIGINAL = SCRIPTS / 'got_srt_scalar_coherence_20261031.py'
REVISED = SCRIPTS / 'got_srt_scalar_coherence_ninjatargets_20261031.py'
BUILD = (PROJECT / 'GhostOfTsushima/experiments/'
         'c925-pr95-scalar-bounds-20261028T112756671630889Z/build')
BASELINE = BUILD / 'shadps4'
EXPECTED_BASELINE_SHA256 = '89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec'
EXPECTED_ORIGINAL_SHA256 = 'c4a08460c7134c4f5830222273d4f60bee811389dc6fece76e721453ea3daf87'

OLD = r'''        plan=sp.run(['ninja','-n','-C',str(B),'shadps4'],capture_output=True,text=True,timeout=60)
        (out/'ninja-dryrun.log').write_text(plan.stdout+'\n'+plan.stderr)
        need(plan.returncode==0,'NINJA_DRYRUN_FAILED')
        tracked=set()
        for line in plan.stdout.splitlines():
            match=re.search(r'(?:Building (?:C|CXX|ASM) object|Linking (?:CXX|C|Fortran) (?:static|shared|module) library) (.+?\.(?:o|a|so))\s*$',line)
            if match:
                rel=Path(match.group(1))
                need(not rel.is_absolute() and '..' not in rel.parts,'UNSAFE_NINJA_OUTPUT_PATH')
                tracked.add(rel)
        need(0<len(tracked)<=3000,'NINJA_PLAN_OBJECT_COUNT_OUT_OF_BOUNDS:'+str(len(tracked)))'''

# Query directly from the loaded Ninja graph. Unlike "ninja -n", this does not
# run CMake/glob regeneration, so the list remains usable after header edits.
NEW = r'''        plan=sp.run(['ninja','-C',str(B),'-t','targets','all'],
                    capture_output=True,text=True,timeout=90)
        (out/'ninja-build-targets.log').write_text(plan.stdout+'\n'+plan.stderr)
        need(plan.returncode==0,'NINJA_TARGET_GRAPH_QUERY_FAILED')
        tracked=set()
        for line in plan.stdout.splitlines():
            if ': ' not in line:
                continue
            target=line.split(': ',1)[0]
            if not (target.endswith(('.o','.a','.pch','.pcm','.so')) or
                    re.search(r'\.so\.[0-9]+(?:\.[0-9]+)*$',target)):
                continue
            rel=Path(target)
            need(not rel.is_absolute() and '..' not in rel.parts and rel.parts,
                 'UNSAFE_NINJA_OUTPUT_PATH:'+target)
            # Shared-library soname symlinks are not compiled outputs. Their
            # regular backing files are separately included in target listings.
            if (B/rel).is_symlink():
                continue
            tracked.add(rel)
        need(0<len(tracked)<=15000,'NINJA_GRAPH_ARTIFACT_COUNT_INVALID:'+str(len(tracked)))
        print('VERIFIED_BUILD_GRAPH_ARTIFACTS='+str(len(tracked)),flush=True)'''


def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def graph_artifacts(stdout: str, base: Path) -> set[Path]:
    """Local preflight replicating the replacement's target parsing."""
    tracked=set()
    for line in stdout.splitlines():
        if ': ' not in line:
            continue
        target=line.split(': ',1)[0]
        if not (target.endswith(('.o','.a','.pch','.pcm','.so')) or
                re.search(r'\.so\.[0-9]+(?:\.[0-9]+)*$',target)):
            continue
        rel=Path(target)
        if rel.is_absolute() or '..' in rel.parts or not rel.parts:
            raise RuntimeError('UNSAFE_NINJA_OUTPUT_PATH:'+target)
        if not (base/rel).is_symlink():
            tracked.add(rel)
    return tracked


def main() -> int:
    if not ORIGINAL.is_file() or ORIGINAL.is_symlink():
        raise RuntimeError('ORIGINAL_EXACT_SRT_RUNNER_MISSING')
    if sha(ORIGINAL) != EXPECTED_ORIGINAL_SHA256:
        raise RuntimeError('ORIGINAL_EXACT_SRT_RUNNER_SHA_MISMATCH')
    if not BASELINE.is_file() or sha(BASELINE) != EXPECTED_BASELINE_SHA256:
        raise RuntimeError('PRE_SRT_BASELINE_CHANGED')
    p=subprocess.run(['ninja','-C',str(BUILD),'-t','targets','all'],
                     capture_output=True,text=True,timeout=90)
    if p.returncode != 0:
        raise RuntimeError('NINJA_GRAPH_UNAVAILABLE:'+p.stderr[-600:])
    tracked=graph_artifacts(p.stdout,BUILD)
    if not 0<len(tracked)<=15000:
        raise RuntimeError('NINJA_GRAPH_HAS_INVALID_TARGET_COUNT:'+str(len(tracked)))
    if not any(x.suffix=='.o' for x in tracked):
        raise RuntimeError('NINJA_GRAPH_CONTAINS_NO_OBJECTS')
    print('NINJA_GRAPH_PREFLIGHT=PASS targets='+str(len(tracked)),flush=True)
    original=ORIGINAL.read_text()
    if original.count(OLD) != 1:
        raise RuntimeError('ORIGINAL_NINJA_DRYRUN_BLOCK_CHANGED:'+str(original.count(OLD)))
    updated=original.replace(OLD,NEW,1)
    if 'ninja-dryrun.log' in updated or 'NINJA_PLAN_OBJECT_COUNT_OUT_OF_BOUNDS' in updated:
        raise RuntimeError('OBSOLETE_DRY_RUN_GUARD_REMAINS')
    compile(updated,str(REVISED),'exec')
    if REVISED.is_symlink() or (REVISED.exists() and REVISED.read_text()!=updated):
        raise RuntimeError('EXISTING_TARGET_CHANGED')
    if not REVISED.exists():
        tmp=REVISED.with_name(REVISED.name+'.pending')
        if tmp.exists() or tmp.is_symlink():
            raise RuntimeError('PREVIOUS_PATCH_PENDING')
        try:
            tmp.write_text(updated)
            tmp.replace(REVISED)
        finally:
            if tmp.exists(): tmp.unlink()
    print('SOURCE_PATCH_GUARDED=PASS; STAGE=RUN_GPU_SRT_CORRECTNESS_CANDIDATE',flush=True)
    # No exec/SSH logout. The existing runner handles full backup and rollback.
    return subprocess.call([sys.executable,'-u',str(REVISED)])

if __name__=='__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print('STOP='+type(e).__name__+':'+str(e),file=sys.stderr,flush=True)
        raise
