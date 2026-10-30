#!/usr/bin/env python3
"""Repair missing subprocess import in GoT's 95s hotpath profiler; validate real launcher."""
from __future__ import annotations
import ast
import hashlib
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path.home() / 'projects/shadps4'
SCRIPTS = ROOT / 'scripts'
ORIGINAL = SCRIPTS / 'got_pr5467_revert_hotpath_profile_7g_exactguard_20261030.py'
FIXED = SCRIPTS / 'got_pr5467_revert_hotpath_profile_7g_importfix_20261030.py'
ARCHIVE = ROOT / 'logs/got-pr5467-revert-hotpath-20261030T202846Z-4024979.tar.gz'
CAND = ROOT / 'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z'
ELF = CAND / 'build/shadps4'
BASE_SHA = '89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec'
CAPTURE_SHA = '4288d541444b8ec513cb906f7e4caf1feb1393a498aa4b97414d7791d5af7410'
IMPORT_SITE = "stage='INIT'\ntry:\n"
CALL = "subprocess.run(['bash','-n',str(generated)],check=True)"
START = 'python3 -u - "$HELP" <<\'PY\'\n'
END = '\nPY\nRUN\n'


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def transform(original: str) -> str:
    check(original.count(IMPORT_SITE) == 1, 'EXACT_PROFILE_ENTRY_CHANGED')
    check(original.count(CALL) == 1, 'EXACT_PROFILE_LAUNCHER_CHANGED')
    tree = ast.parse(original)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'make_runtime']
    check(len(functions) == 2, 'EXPECTED_BASE_AND_OVERRIDE_MISSING')
    # This declaration is inserted before the top-level experiment but after all function definitions.
    result = original.replace(IMPORT_SITE, 'import subprocess\n' + IMPORT_SITE, 1)
    parsed = ast.parse(result)
    check(any(isinstance(node, ast.Import) and any(alias.name == 'subprocess' and alias.asname is None
              for alias in node.names) for node in parsed.body), 'GLOBAL_SUBPROCESS_IMPORT_MISSING')
    compile(result, str(FIXED), 'exec')
    return result


def captured_shell() -> bytes:
    check(ARCHIVE.is_file() and not ARCHIVE.is_symlink(), 'UPLOADED_TRIAL_ARCHIVE_MISSING')
    with tarfile.open(ARCHIVE, 'r:gz') as tar:
        files = [m for m in tar.getmembers() if m.isfile() and m.name.endswith('/profile-runner.sh')]
        check(len(files) == 1 and files[0].size < 100_000, 'CAPTURED_LAUNCHER_NOT_UNIQUE')
        content = tar.extractfile(files[0]).read()
    check(sha256(content) == CAPTURE_SHA, 'CAPTURED_LAUNCHER_HASH_MISMATCH')
    return content


def literal_assignment(fn: ast.FunctionDef, name: str) -> str:
    values = [node.value for node in ast.walk(fn) if isinstance(node, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)]
    check(len(values) == 1, 'EXACT_OVERRIDE_VARIABLE_CHANGED:' + name)
    value = ast.literal_eval(values[0])
    check(isinstance(value, str), 'OVERRIDE_VARIABLE_NOT_STRING:' + name)
    return value


def preflight_make_runtime(patched: str) -> None:
    nodes = ast.parse(patched).body
    fn = [n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == 'make_runtime'][-1]
    before = captured_shell().decode()
    old_guard = literal_assignment(fn, 'old_guard')
    new_guard = literal_assignment(fn, 'new_guard')
    old_helper = literal_assignment(fn, 'old_helper')
    new_helper = literal_assignment(fn, 'new_helper')
    check(before.count(new_guard) == 1 and before.count(new_helper) == 1,
          'CAPTURED_LAUNCHER_NOT_IDENTICAL_TO_GENERATED_OVERRIDE')
    mock_before = before.replace(new_guard, old_guard, 1).replace(new_helper, old_helper, 1)
    with tempfile.TemporaryDirectory(prefix='.got-profile-preflight-', dir=SCRIPTS) as folder:
        dummy = Path(folder) / 'profile-runner.sh'
        dummy.write_text(mock_before)
        env = {'_base_make_runtime': lambda *_a, **_kw: dummy, 'subprocess': subprocess,
               'RuntimeError': RuntimeError, 'print': print, 'str': str}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'profile-make-runtime', 'exec'), env)
        result = env['make_runtime']('bb6ef526ab6a00deb02b484a88b38e017d526098da2906729f77bbe6373d12a9')
        check(result == dummy, 'MOCK_RUNNER_PATH_CHANGED')
        check(dummy.read_text() == before, 'GENERATED_LAUNCHER_DIFFERS_FROM_ACTUAL_ARCHIVE')
        check(before.count(START) == 1 and before.count(END) == 1, 'PROFILE_HEREDOC_CHANGED')
        compile(before.split(START, 1)[1].rsplit(END, 1)[0], 'profile-runner-python', 'exec')
        subprocess.run(['bash', '-n', str(dummy)], check=True)
    print('REAL_CAPTURED_LAUNCHER_PREFLIGHT=PASS', flush=True)


def main() -> int:
    check(ORIGINAL.is_file() and not ORIGINAL.is_symlink(), 'GENERATED_PROFILE_NOT_FOUND')
    check(ELF.is_file() and sha256(ELF.read_bytes()) == BASE_SHA,
          'ORIGINAL_PRE_SRT_ELF_NOT_PRESERVED')
    if subprocess.run(['pgrep', '-ix', 'shadps4'], stdout=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError('ANOTHER_EMULATOR_IS_RUNNING')
    repaired = transform(ORIGINAL.read_text())
    preflight_make_runtime(repaired)
    check(not FIXED.is_symlink(), 'REPAIRED_TARGET_SYMLINK')
    if FIXED.exists():
        check(FIXED.read_text() == repaired, 'REPAIRED_TARGET_CHANGED')
    else:
        pending = FIXED.with_name(FIXED.name + '.pending')
        check(not pending.exists() and not pending.is_symlink(), 'REPAIRED_PENDING_TARGET_EXISTS')
        pending.write_text(repaired)
        pending.chmod(stat.S_IMODE(ORIGINAL.stat().st_mode))
        pending.replace(FIXED)
    print('STAGE=RUN_VERIFIED_95S_HOTPATH_PROFILE', flush=True)
    return subprocess.call([sys.executable, '-u', str(FIXED)])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print('STOP=' + type(e).__name__ + ':' + str(e), file=sys.stderr, flush=True)
        raise
