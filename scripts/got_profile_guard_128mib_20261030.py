#!/usr/bin/env python3
"""Resume the native GoT hotpath probe with a 128-MiB VRAM telemetry tolerance."""
from pathlib import Path
import ast
import hashlib
import re
import subprocess
import sys
import tempfile

ROOT = Path.home() / "projects/shadps4/scripts"
ORIGINAL = ROOT / "got_pr5467_revert_hotpath_resume_gitblob_20261030.py"
TARGET = ROOT / "got_pr5467_revert_hotpath_resume_vram_margin_20261030.py"

def transform(source):
    for marker in (
        "def make_runtime(",
        "def git_blob_sha(",
        "check(git_blob_sha(RUNNER)==BASE_RUNNER_SHA,'BASELINE_RUNNER_CHANGED')",
    ):
        if source.count(marker) != 1:
            raise RuntimeError("EXPECTED_PROFILER_SOURCE_CHANGED:" + marker)
    pattern = r"(?m)^if __name__\s*==\s*['\"]__main__['\"]\s*:"
    positions = list(re.finditer(pattern, source))
    if len(positions) != 1:
        raise RuntimeError("PROFILER_MAIN_GUARD_CHANGED")
    injection = """
# Only the generated, isolated test launcher is modified. Native source is unchanged.
_base_make_runtime = make_runtime
def make_runtime(*args, **kwargs):
    generated = _base_make_runtime(*args, **kwargs)
    original = generated.read_text()
    old = " u.MIN_VRAM=8*1024**3\\n result['required_free_vram_gib']=8"
    new = (" u.MIN_VRAM=8*1024**3-128*1024**2\\n"
           " result['required_free_vram_gib']=7.875\\n"
           " result['vram_guard_tolerance_mib']=128")
    if original.count(old) != 1:
        raise RuntimeError('GENERATED_PROFILE_VRAM_GUARD_CHANGED')
    amended = original.replace(old, new, 1)
    compile(amended.split("python3 -u - \\"$HELP\\" <<'PY'\\n", 1)[1].rsplit('\\nPY\\nRUN\\n', 1)[0],
            'adjusted-guest-profile', 'exec')
    generated.write_text(amended)
    print('VRAM_GUARD_TOLERANCE_MIB=128; ORIGINAL_8GIB_POLICY_UNCHANGED_OUTSIDE_PROFILE', flush=True)
    return generated

"""
    return source[:positions[0].start()] + injection + source[positions[0].start():]

def local_test():
    with tempfile.TemporaryDirectory(prefix="got-profile-guard-") as temp:
        sample = Path(temp) / "profile-runner.sh"
        sample.write_text(
            "bash <<'RUN'\npython3 -u - \"$HELP\" <<'PY'\n"
            "if True:\n u.MIN_VRAM=8*1024**3\n result['required_free_vram_gib']=8\n"
            "PY\nRUN\n"
        )
        # Execute just the wrapper with a simulated baseline function.
        source = transform("def make_runtime(*a, **kw):\n    return None\n"
                           "def git_blob_sha(p):\n    return None\n"
                           "def check(a,b):\n    return None\n"
                           "check(git_blob_sha(RUNNER)==BASE_RUNNER_SHA,'BASELINE_RUNNER_CHANGED')\n"
                           "if __name__ == '__main__':\n    pass\n")
        module = ast.parse(source)
        assignment_index = next(i for i, f in enumerate(module.body)
            if isinstance(f, ast.Assign) and any(isinstance(t, ast.Name) and t.id=='_base_make_runtime'
                                                for t in f.targets))
        body = module.body[assignment_index:assignment_index + 2]
        env = {'make_runtime': lambda *a, **kw: sample}
        exec(compile(ast.Module(body=body, type_ignores=[]),'profile-fixture','exec'),env)
        result = env['make_runtime']()
        assert result == sample
        assert 'u.MIN_VRAM=8*1024**3-128*1024**2' in sample.read_text()
        assert "result['vram_guard_tolerance_mib']=128" in sample.read_text()
        assert sample.read_text().count('u.MIN_VRAM=')==1
        print('LOCAL_GUARD_ADAPTATION_TEST=PASS')

def main():
    local_test()
    if not ORIGINAL.is_file() or ORIGINAL.is_symlink():
        raise RuntimeError('VERIFIED_PROFILER_SCRIPT_NOT_FOUND')
    original = ORIGINAL.read_text()
    changed = transform(original)
    compile(changed, str(TARGET), 'exec')
    if TARGET.is_symlink() or (TARGET.exists() and TARGET.read_text()!=changed):
        raise RuntimeError('PROFILE_TARGET_CHANGED')
    if not TARGET.exists():
        pending = TARGET.with_name(TARGET.name + '.pending')
        if pending.exists() or pending.is_symlink():
            raise RuntimeError('PROFILE_TARGET_PENDING_EXISTS')
        pending.write_text(changed)
        pending.replace(TARGET)
    print('STAGE=REBUILD_TIMED_DIAGNOSTIC_WITH_128MIB_GUARD_TOLERANCE',flush=True)
    return subprocess.call([sys.executable, '-u', str(TARGET)])

if __name__ == '__main__':
    sys.exit(main())
