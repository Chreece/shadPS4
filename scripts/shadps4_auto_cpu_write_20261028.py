#!/usr/bin/env python3
"""State-driven GPU->CPU write preservation; isolated native validation.
No compatibility flags, shader/title exceptions, services or live saves changed.
CPU-only extracted-method tests are not full Vulkan/gameplay verification.
"""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback

ROOT = Path.home() / 'projects/shadps4'
TREE = ROOT / 'Upstream/tests/gow-hle-abi-20261027-194629-907086'
SRC, BUILD = TREE / 'source', TREE / 'build'
BIN = BUILD / 'shadps4'
LOGS = ROOT / 'logs'
BASE = 'bbe1a72840733afccba5f48fb4b5eb29a1c96128'
BASE_ELF = '65fafc436831d76380db349b89c89fe610d16325981870ac073d8b9ed0f010b9'
LAST = 'd6eebb26bf0c1a739869a46adcacd74e72f917c5'
LAST_ELF = 'f4f8b7abd464c700d441f7a5060f76626a675871b81d9afb3ec6376a63aa52d9'
LAST_BIN = ROOT / 'GodOfWarRagnarok/tests/gow-permutation-owner-20261028T134122Z-1938747/candidate/shadps4'
UPSTREAM_OLD = '45837a89a617ac394c1844ec95a28471fb79ec08'
UPSTREAM = 'caaa5419726c912013ffd5154dd4a9bac461cd94'
PACKAGE = 'd52b7977fc75a7f8db12f3b1117f830d03beca5d'
PATCH_PATH = 'scripts/patchsets/automatic-cpu-write-coherence-20261028.patch'
TEST_PATH = 'tools/development/cpu_write_coherence_fixture.cpp.in'
PACKAGE_BLOBS = {PATCH_PATH: '1f43c0a21007c66a6d92448493d8bdb9f44d2968',
                 TEST_PATH: '6537d6945ad007867c897ea85a1c23bf131b2424'}
M = 'src/video_core/buffer_cache/memory_tracker.h'
B = 'src/video_core/buffer_cache/buffer_cache.cpp'
H = 'src/video_core/buffer_cache/cpu_write_coherence.h'
PREIMAGES = {M: '2546a2d54102aad759816adeee3f6e0cbe2cc06e',
             B: 'b33e51616c961b56ef5ef80c019064ac34e527cc'}
CHANGED = {M, B, H}
UPSTREAM_PATHS = set('''src/core/libraries/np/np_auth/np_auth.cpp
src/core/libraries/np/np_auth/np_auth_ctx.h
src/core/libraries/np/np_handler/np_handler.cpp
src/core/libraries/np/np_handler/np_handler.h
src/core/libraries/np/np_handler/np_handler_auth.cpp
src/shadnet/client.cpp
src/shadnet/client.h
src/shadnet/shadnet.proto'''.splitlines())
PROTECTED = ROOT / 'GodOfWarRagnarok/build/shadps4'
PROTECTED_SHA = '8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e'
PR91 = TREE / 'old-binaries/pr91-46195c52-native-shadps4'
PR91_SHA = '46195c52e9dd3fb3921b0b5c99a233edf4b484ecceb7794c12ae864626d6b3af'
TEMPLATE = LOGS / 'gow-gpu-submit-trace-20261028T111830Z-1380900-evidence/original-game-runner.sh'
TEMPLATE_BLOB = 'b773b5936e5bc72ae631c3c0f9ac20578fa9f7f8'
LOCKS = ('auto-cpu-write-coherence.lock', 'gow-upstream-got-build.lock',
         'shadps4-gow-srt-phi.lock', 'gow-hle-stack-build.lock')

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

def cmd(args, log=None, timeout=120, env=None):
    """Bounded subprocess, heartbeat, and cleanup of only its own process group."""
    args = [str(x) for x in args]
    with (Path(log).open('w+b') if log else tempfile.TemporaryFile(dir=LOGS)) as output:
        child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=output,
                                 stderr=subprocess.STDOUT, env=env, start_new_session=True)
        began = last = time.monotonic()
        try:
            while child.poll() is None:
                now = time.monotonic()
                if now - began >= timeout:
                    raise TimeoutError('COMMAND_TIMEOUT=' + args[0])
                if log and now - last >= 15:
                    output.flush()
                    with Path(log).open('rb') as reader:
                        reader.seek(max(0, os.fstat(reader.fileno()).st_size - 1200))
                        lines = reader.read().decode(errors='replace').splitlines()
                    print('BUILD_PROGRESS=' + (lines[-1][-350:] if lines else 'command running') +
                          ' elapsed_s=' + str(int(now-began)), flush=True)
                    last = now
                time.sleep(0.15)
        except BaseException:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            raise
        output.seek(0)
        data = output.read()
    require(child.returncode == 0, 'COMMAND_FAILED=' + args[0] + '\n' + data[-7000:].decode(errors='replace'))
    return data

def git_bytes(*args, **kwargs):
    return cmd(['git', '-C', SRC, *args], **kwargs)

def git(*args, **kwargs):
    return git_bytes(*args, **kwargs).decode().strip()

def clean():
    return not git('status', '--porcelain', '--untracked-files=no')

def verify_file(path, wanted):
    require(path.is_file() and not path.is_symlink() and sha(path) == wanted,
            'IMMUTABLE_FILE_MISMATCH=' + str(path))

def append_diary(row):
    path = ROOT / 'diary/trials.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(row, separators=(',', ':')) + '\n')
        f.flush()
        os.fsync(f.fileno())

def extract_method(text):
    start = text.index('    void InvalidateRegion(')
    end = text.index('    /// Call ', start)
    return text[start:end]

def cpu_tests(old_header, test_template, work, evidence):
    require(test_template.count('// EXACT_INVALIDATE_REGION\n') == 1, 'FIXTURE_MARKER_CHANGED')
    for name, source, args in (
        ('baseline', old_header, ['--baseline']),
        ('corrected', (SRC / M).read_text(), []),
    ):
        source_file = evidence / (name + '-cpu.cpp')
        source_file.write_text(test_template.replace('// EXACT_INVALIDATE_REGION\n', extract_method(source)))
        exe = work / (name + '-cpu')
        cmd(['g++', '-std=c++23', '-O2', '-DNDEBUG', '-Wall', '-Wextra', '-Werror',
             '-pedantic', '-I', SRC / 'src/video_core/buffer_cache', source_file, '-o', exe],
            evidence / (name + '-cpu-build.log'))
        output = cmd([exe, *args], evidence / (name + '-cpu-result.txt'), timeout=30).decode()
        expected = 'BASELINE_DISABLED_GPU_DATA_LOSS=REPRODUCED' if args else 'CPU_WRITE_COHERENCE=PASS'
        require(expected in output, 'CPU_REGRESSION_FAILED=' + name)
    print('CPU_REGRESSION=OLD_DATA_LOSS_REPRODUCED_NEW_PRESERVATION_PASS', flush=True)

def derive_runtime(target, source_pin, binary_sha):
    raw = TEMPLATE.read_bytes()
    require(blob(raw) == TEMPLATE_BLOB, 'GAME_TEMPLATE_CHANGED')
    s = raw.decode()
    replacements = [
        ('gow-official-indirect-', 'gow-auto-coherence-game-', 2),
        ('CANDIDATE="$ROOT/Upstream/tests/shadps4-indirect-hazards-20261027-170922-3777629"', f'CANDIDATE="{TREE}"', 1),
        ('43d7d22360dee7a545ef9e81557fb8ae9109f7a4', source_pin, 2),
        ('3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75', binary_sha, 2),
        ('OFFICIAL_MAIN_PR5479_PR88_NATIVE_GOW', 'AUTOMATIC_CPU_WRITE_COHERENCE_GOW', 1),
        ('"recorded_at":"2026-10-27"', '"recorded_at":"2026-10-28"', 1),
    ]
    for before, after, count in replacements:
        require(s.count(before) == count, 'TEMPLATE_ANCHOR_CHANGED=' + before[:60])
        s = s.replace(before, after)
    locks = '''exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/shadps4-official-indirect-game.lock"
flock -n 9 && flock -n 8 && flock -n 7 || {
    echo "CONCURRENT_GAME_BUILD_LOCK_UNAVAILABLE_NO_ACTION"; exit 3;
}'''
    require(s.count(locks) == 1, 'NESTED_LOCK_TEMPLATE_CHANGED')
    s = s.replace(locks, '''[[ "$GOW_COHERENCE_PARENT_LOCKS" == YES ]] || {
    echo "PARENT_LOCK_GUARD_MISSING"; exit 3;
}''')
    s = s.replace('-d "$SRC/.git"', '-e "$SRC/.git"')
    require('--seconds 118' in s and 'shutil.copy2(old,dest)' in s and 'GAME="CUSA34384"' in s,
            'RUNTIME_LIMIT_OR_SAVE_GUARD_CHANGED')
    target.write_text(s)
    cmd(['bash', '-n', target])

def run_game(runner, game_tag, game_log):
    env = os.environ.copy()
    env['GOW_COHERENCE_PARENT_LOCKS'] = 'YES'
    with game_log.open('wb') as f:
        return subprocess.run(['bash', str(runner), '--worker', game_tag], env=env,
                              stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT).returncode

def worker(tag):
    os.umask(0o077)
    LOGS.mkdir(parents=True, exist_ok=True)
    work = ROOT / 'GodOfWarRagnarok/tests' / tag
    e = LOGS / (tag + '-evidence')
    work.mkdir(parents=True, exist_ok=False)
    e.mkdir(exist_ok=False)
    archive = LOGS / (tag + '.tar.gz')
    snapshot = TREE / 'old-binaries/pr94-65fafc43-native-shadps4'
    status = {'schema_version': 2, 'record_id': tag, 'recorded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'record_type': 'AUTOMATIC_GPU_CPU_WRITE_COHERENCE', 'game_id': 'CUSA34384', 'tested_games': [],
              'base_commit': BASE, 'retained_candidate': LAST, 'upstream_commit': UPSTREAM,
              'stage': 'PREFLIGHT', 'game_rc': None, 'source_commit': None,
              'archive': str(archive), 'gameplay': 'NOT_VERIFIED',
              'scope': 'Mandatory GPU-dirty buffer preservation on CPU write; read/backend/image options unchanged',
              'decision': 'ISOLATED_CANDIDATE_RUNTIME_PENDING'}
    held, owned = [], set()
    switched = patched = False
    rc = 1
    patch_path = e / 'automatic-cpu-write.patch'
    try:
        for name in LOCKS:
            f = (LOGS / name).open('a+')
            held.append(f)
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(git('rev-parse', 'HEAD') == BASE and clean(), 'PR94_SOURCE_CHANGED_REFUSE')
        require((BUILD / 'build.ninja').is_file(), 'EXISTING_NINJA_BUILD_MISSING')
        cache = (BUILD / 'CMakeCache.txt').read_text()
        require('CMAKE_HOME_DIRECTORY:INTERNAL=' + str(SRC) in cache and
                'CMAKE_BUILD_TYPE:STRING=Release' in cache, 'BUILD_CACHE_MISMATCH')
        for path, wanted in ((BIN, BASE_ELF), (PROTECTED, PROTECTED_SHA), (PR91, PR91_SHA), (LAST_BIN, LAST_ELF)):
            verify_file(path, wanted)
        require(subprocess.run(['pgrep', '-x', 'shadps4'], capture_output=True).returncode == 1,
                'EMULATOR_RUNNING_OR_PROCESS_CHECK_FAILED')
        require(shutil.disk_usage(TREE).free >= 3 * 1024**3, 'NEED_3_GIB_FREE_FOR_PRESERVATION')
        require(TEMPLATE.is_file() and blob(TEMPLATE.read_bytes()) == TEMPLATE_BLOB, 'RUNTIME_TEMPLATE_MISSING')
        require(not snapshot.is_symlink(), 'SNAPSHOT_SYMLINK_REFUSED')
        if not snapshot.exists():
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(BIN, snapshot)
        verify_file(snapshot, BASE_ELF)
        git('cat-file', '-e', LAST + '^{commit}')
        git('merge-base', '--is-ancestor', BASE, LAST)
        print('PROTECTED_PR94_PR91_GAMEPLAY_AND_METADATA_CANDIDATE=VERIFIED', flush=True)
        status['stage'] = 'FETCH_REVIEWED_PACKAGE_AND_UPSTREAM'
        print('STAGE=' + status['stage'], flush=True)
        for pin, repo in ((PACKAGE, 'Chreece'), (UPSTREAM, 'shadps4-emu')):
            present = subprocess.run(['git', '-C', str(SRC), 'cat-file', '-e', pin + '^{commit}'], capture_output=True).returncode == 0
            if not present:
                git('-c', 'http.version=HTTP/1.1', 'fetch', '--no-tags', '--no-recurse-submodules',
                    f'https://github.com/{repo}/shadPS4.git', pin,
                    log=e / ('fetch-' + pin[:8] + '.log'), timeout=600)
        data = {}
        for path, wanted in PACKAGE_BLOBS.items():
            data[path] = git_bytes('show', PACKAGE + ':' + path)
            require(blob(data[path]) == wanted, 'PACKAGE_HASH_MISMATCH=' + path)
        patch_path.write_bytes(data[PATCH_PATH])
        git('merge-base', '--is-ancestor', UPSTREAM_OLD, UPSTREAM)
        require(set(git('diff', '--name-only', UPSTREAM_OLD, UPSTREAM).splitlines()) == UPSTREAM_PATHS,
                'UNREVIEWED_UPSTREAM_DELTA')
        env = os.environ.copy()
        env.update(GIT_AUTHOR_NAME='shadPS4 isolated experiment', GIT_COMMITTER_NAME='shadPS4 isolated experiment',
                   GIT_AUTHOR_EMAIL='experiment@localhost', GIT_COMMITTER_EMAIL='experiment@localhost')
        merged_tree = git('merge-tree', '--write-tree', LAST, UPSTREAM).splitlines()[0]
        require(re.fullmatch('[0-9a-f]{40}', merged_tree) is not None, 'MERGE_CONFLICT_REFUSE')
        merged = git('commit-tree', merged_tree, '-p', LAST, '-p', UPSTREAM,
                     '-m', 'Isolated upstream integration retaining verified metadata source', env=env)
        require(set(git('diff', '--name-only', LAST, merged).splitlines()) == UPSTREAM_PATHS,
                'MERGE_CHANGED_UNRELATED_SOURCE')
        for path, wanted in PREIMAGES.items():
            require(blob(git_bytes('show', merged + ':' + path)) == wanted, 'SOURCE_PREIMAGE_MISMATCH=' + path)
        old_header = git_bytes('show', merged + ':' + M).decode()
        (e / 'baseline-memory-tracker.h').write_text(old_header)
        owned.add(merged)
        git('switch', '--detach', merged)
        switched = True
        require(not (SRC / H).exists(), 'NEW_HEADER_COLLISION')
        git('apply', '--check', '--index', patch_path)
        git('apply', '--index', patch_path)
        patched = True
        require(set(git('diff', '--cached', '--name-only').splitlines()) == CHANGED, 'PATCH_SCOPE_CHANGED')
        git('diff', '--cached', '--check')
        status['stage'] = 'EXACT_METHOD_CPU_TESTS'
        print('STAGE=' + status['stage'], flush=True)
        cpu_tests(old_header, data[TEST_PATH].decode(), work, e)
        git('-c', 'core.hooksPath=/dev/null', 'commit', '-m',
            'memory: state-driven preservation of GPU-owned bytes before CPU writes', env=env)
        candidate = git('rev-parse', 'HEAD')
        owned.add(candidate)
        patched = False
        require(clean(), 'CANDIDATE_DIRTY')
        git('update-ref', 'refs/heads/experiments/' + tag, candidate, '0'*40)
        git('merge-base', '--is-ancestor', UPSTREAM, candidate)
        status['source_commit'] = candidate
        (e / 'full-source-delta.patch').write_bytes(git_bytes('diff', BASE, candidate))
        (e / 'source-commit.txt').write_text(git('show', '-s', '--format=fuller', candidate) + '\n')
        for path in CHANGED:
            dest = e / 'source' / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((SRC / path).read_bytes())
        status['stage'] = 'NATIVE_BUFFER_CACHE_OBJECT'
        print('STAGE=' + status['stage'], flush=True)
        cmd(['nice', '-n', '10', 'ninja', '-C', BUILD, '-j', '2',
             'CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o'],
            e / 'native-object.log', timeout=2400)
        status['stage'] = 'NATIVE_FULL_INCREMENTAL_LINK'
        print('STAGE=' + status['stage'], flush=True)
        cmd(['nice', '-n', '10', 'cmake', '--build', BUILD, '--target', 'shadps4', '--parallel', '2'],
            e / 'native-link.log', timeout=2400)
        new_sha = sha(BIN)
        require(new_sha not in (BASE_ELF, LAST_ELF), 'BINARY_NOT_REBUILT')
        status['binary_sha256'] = new_sha
        require(b'not found' not in cmd(['ldd', BIN], e / 'ldd.txt'), 'MISSING_SHARED_LIBRARY')
        kept = work / 'candidate/shadps4'
        kept.parent.mkdir()
        shutil.copy2(BIN, kept)
        verify_file(kept, new_sha)
        status['preserved_candidate'] = str(kept)
        warnings = []
        for log in (e / 'native-object.log', e / 'native-link.log'):
            warnings += [log.name + ': ' + line for line in log.read_text(errors='replace').splitlines() if 'warning:' in line]
        (e / 'compiler-warnings.txt').write_text('\n'.join(warnings))
        print('NATIVE_BUILD_PASS=' + new_sha, flush=True)
        runner = e / 'native-game-runner.sh'
        derive_runtime(runner, candidate, new_sha)
        game_tag = 'gow-auto-coherence-game-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())
        game_log = LOGS / (game_tag + '.log')
        status['stage'] = 'ONE_BOUNDED_GOW_TEST'
        print('STAGE=' + status['stage'] + '\nGAME_MONITOR=' + str(game_log), flush=True)
        game_rc = run_game(runner, game_tag, game_log)
        status['game_rc'] = game_rc
        if game_log.exists() and 'NATIVE_GENERIC_GAME_LAUNCHED=YES' in game_log.read_text(errors='replace'):
            status['tested_games'] = ['CUSA34384']
        game_archive = LOGS / (game_tag + '.tar.gz')
        if game_archive.is_file():
            shutil.copy2(game_archive, e / 'game-runtime.tar.gz')
        result_dir = ROOT / 'GodOfWarRagnarok/tests' / ('playtest-' + game_tag) / 'evidence/game'
        console = result_dir / 'full-console.log'
        if console.is_file() and console.stat().st_size <= 64 * 1024**2:
            shutil.copy2(console, e / 'full-console.log')
            text = console.read_text(errors='replace')
            (e / 'coherence-events.txt').write_text('\n'.join(line for line in text.splitlines()
                if 'CpuWriteCoherence:' in line or 'Device lost during submit' in line or 'GPU_QUEUE_FAILURE' in line))
        if (result_dir / 'native-result.json').is_file():
            status['native_result'] = json.loads((result_dir / 'native-result.json').read_text())
        status['stage'] = 'BUILD_PASS_RUNTIME_CAPTURED' if game_rc == 0 else 'BUILD_PASS_RUNTIME_FAILED'
        status['decision'] = 'REVIEW_VISIBLE_GAMEPLAY_AND_CPU_WRITE_COHERENCE_EVIDENCE_NO_PROMOTION'
        rc = 0 if game_rc == 0 else 1
    except BaseException as ex:
        status['failure'] = str(ex)
        (e / 'failure.txt').write_text(traceback.format_exc())
        print('EXPERIMENT_FAILED=' + str(ex), flush=True)
    finally:
        restore = []
        if switched:
            safe_restore = False
            try:
                require(git('rev-parse', 'HEAD') in owned, 'SOURCE_CHANGED_EXTERNALLY_REFUSE')
                if patched:
                    require(not git('diff', '--name-only'), 'WORKTREE_EDITED_EXTERNALLY_REFUSE')
                    require(set(git('diff', '--cached', '--name-only').splitlines()) == CHANGED,
                            'INDEX_CHANGED_EXTERNALLY_REFUSE')
                    git('apply', '--check', '--reverse', '--index', patch_path)
                    git('apply', '--reverse', '--index', patch_path)
                require(clean(), 'SOURCE_DIRTY_REFUSE_RESTORE')
                git('switch', '--detach', BASE)
                safe_restore = True
                restore.append('SOURCE_RESTORED')
            except Exception as ex:
                restore.append('SOURCE_RESTORE_FAILED: ' + str(ex))
            if safe_restore:
                try:
                    verify_file(snapshot, BASE_ELF)
                    temp = BIN.with_name('restore-' + tag)
                    require(not temp.exists() and not temp.is_symlink(), 'RESTORE_PATH_COLLISION')
                    shutil.copy2(snapshot, temp)
                    verify_file(temp, BASE_ELF)
                    os.replace(temp, BIN)
                    verify_file(BIN, BASE_ELF)
                    restore.append('PR94_ELF_RESTORED')
                except Exception as ex:
                    restore.append('BINARY_RESTORE_FAILED: ' + str(ex))
        status['restore'] = restore or ['NOT_CHANGED']
        for label, path, wanted in (('protected_gameplay', PROTECTED, PROTECTED_SHA), ('pr91', PR91, PR91_SHA),
                                    ('pr94', BIN, BASE_ELF), ('metadata_candidate', LAST_BIN, LAST_ELF)):
            try:
                status[label + '_hash_unchanged'] = sha(path) == wanted
            except OSError:
                status[label + '_hash_unchanged'] = False
        (e / 'result.json').write_text(json.dumps(status, indent=2) + '\n')
        try:
            with tarfile.open(archive, 'w:gz') as tf:
                tf.add(e, arcname=e.name)
                if (LOGS / (tag + '.log')).is_file():
                    tf.add(LOGS / (tag + '.log'), arcname=tag + '.log')
            status['archive_sha256'] = sha(archive)
            append_diary(status)
        except Exception as ex:
            print('ARCHIVE_OR_DIARY_FAILED=' + str(ex), flush=True)
            rc = 1
        print('RESTORE=' + ','.join(status['restore']), flush=True)
        if archive.is_file():
            print('UPLOAD_THIS=' + str(archive), flush=True)
        print('SSH_SESSION=OPEN', flush=True)
        for f in held:
            f.close()
        if any('FAILED' in s for s in status['restore']) or not all(status.get(k + '_hash_unchanged', False)
                for k in ('protected_gameplay', 'pr91', 'pr94', 'metadata_candidate')):
            rc = 1
    return rc

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker')
    args = p.parse_args()
    LOGS.mkdir(parents=True, exist_ok=True)
    if args.worker:
        require(re.fullmatch(r'gow-auto-cpu-write-\d{8}T\d{6}Z-\d+', args.worker), 'INVALID_RUN_TAG')
        return worker(args.worker)
    tag = 'gow-auto-cpu-write-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
    log = LOGS / (tag + '.log')
    with log.open('xb') as f:
        child = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), '--worker', tag],
                                 stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT,
                                 start_new_session=True)
    print('WORKER_PID=' + str(child.pid))
    print("MONITOR=tail -F '" + str(log) + "'")
    print('UPLOAD_THIS=' + str(LOGS / (tag + '.tar.gz')))
    print('SSH_SESSION=OPEN')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
