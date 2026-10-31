#!/usr/bin/env python3
"""Resume the exact GoT GPU-SRT fix after Ninja reconfigures its build graph."""
from pathlib import Path
import hashlib
import subprocess
import sys

ROOT = Path.home() / 'projects/shadps4/scripts'
SOURCE = ROOT / 'got_srt_scalar_coherence_20261031.py'
TARGET = ROOT / 'got_srt_scalar_coherence_regen_20261031.py'
ORIGINAL_SHA256 = 'c4a08460c7134c4f5830222273d4f60bee811389dc6fece76e721453ea3daf87'
OLD = "        plan=sp.run(['ninja','-n','-C',str(B),'shadps4'],capture_output=True,text=True,timeout=60)"
NEW = """        # Regenerate CMake's Ninja graph *without compiling* first.
        # A Ninja -n run stops at 'Re-running CMake' and cannot enumerate objects.
        print('STAGE=REGENERATE_NINJA_GRAPH_ONLY',flush=True)
        regenerated=sp.run(['ninja','-C',str(B),'build.ninja'],
                           capture_output=True,text=True,timeout=240)
        (out/'ninja-graph-regen.log').write_text(regenerated.stdout+'\\n'+regenerated.stderr)
        need(regenerated.returncode==0,'NINJA_GRAPH_REGEN_FAILED')
""" + OLD

def patched_source(original: str) -> str:
    if original.count(OLD) != 1:
        raise RuntimeError('EXACT_NINJA_DRYRUN_ANCHOR_CHANGED')
    if original.count('NINJA_PLAN_OBJECT_COUNT_OUT_OF_BOUNDS') != 1:
        raise RuntimeError('EXACT_NINJA_PLAN_GUARD_CHANGED')
    updated = original.replace(OLD, NEW, 1)
    compile(updated, str(TARGET), 'exec')
    return updated

def main() -> int:
    if not SOURCE.is_file() or SOURCE.is_symlink():
        raise RuntimeError('VERIFIED_COHERENCE_SOURCE_MISSING')
    raw = SOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ORIGINAL_SHA256:
        raise RuntimeError('ORIGINAL_COHERENCE_RUNNER_SHA_CHANGED')
    updated = patched_source(raw.decode('utf-8'))
    if TARGET.is_symlink() or (TARGET.exists() and TARGET.read_text() != updated):
        raise RuntimeError('RESUME_RUNNER_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT')
    if not TARGET.exists():
        pending = TARGET.with_name(TARGET.name+'.pending')
        if pending.exists() or pending.is_symlink():
            raise RuntimeError('RESUME_RUNNER_PENDING_FILE_EXISTS')
        try:
            pending.write_text(updated)
            pending.replace(TARGET)
        finally:
            if pending.exists():
                pending.unlink()
    print('SOURCE_PATCH=PASS NINJA_BUILD_GRAPH_REGEN=ENABLED',flush=True)
    return subprocess.call([sys.executable,'-u',str(TARGET)])

if __name__ == '__main__':
    sys.exit(main())
