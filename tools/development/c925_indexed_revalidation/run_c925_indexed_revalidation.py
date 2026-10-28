#!/usr/bin/env python3
"""Apply the reviewed C925 adapter, test/build incrementally, run at most 118+2s.
No upstream rebase, main promotion, title-specific renderer flags or injected input.
"""
from __future__ import annotations
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess as sp
import sys
import tarfile
import time
import traceback
import urllib.request

BASE_NAME = 'c925-pr95-scalar-bounds-20261028T112756671630889Z'
EXPECTED_ELF = '75788b566f0b53b1e342d7a0eca121cc034d64eb165e0474884af4d12e998527'
EXPECTED_HEAD = 'c925523b49ef39434024b16b2b014d120471ee1a'
UPSTREAM_OBSERVED = '45837a89a617ac394c1844ec95a28471fb79ec08'
MIN_VRAM = 8 * 1024**3
PROFILE_LIMIT = 2 * 1024**3
API = 'http://127.0.0.1:11434'
SKIP_PROFILE_DIRS = {'cache', 'shader', 'log', 'temp', 'screenshots', 'captures', 'download'}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def emit(message: str) -> None:
    print(time.strftime('%H:%M:%S') + ' ' + message, flush=True)


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n')


def stop_owned(proc: sp.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=2)
    except sp.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
    except ProcessLookupError:
        pass


def logged(args: list[str], log: Path, label: str, *, cwd: Path | None = None,
           limit: float | None = None, env: dict | None = None, checked: bool = True) -> int:
    emit('STAGE=' + label + ' LOG=' + str(log))
    start = last_beat = last_line = time.monotonic()
    proc = None
    try:
        with log.open('wb') as out, log.open('r', errors='replace') as reader:
            proc = sp.Popen(args, cwd=cwd, stdout=out, stderr=sp.STDOUT,
                            env=env, start_new_session=True)
            while proc.poll() is None:
                now = time.monotonic()
                if limit is not None and now - start >= limit:
                    stop_owned(proc)
                    raise RuntimeError(label + '_TIME_LIMIT')
                lines = reader.read().splitlines()
                if lines and now - last_line >= 3:
                    emit(label + ' ' + lines[-1][:260])
                    last_line = now
                if now - last_beat >= 15:
                    emit(f'{label}_ACTIVE elapsed={now-start:.0f}s pid={proc.pid} log_bytes={log.stat().st_size}')
                    last_beat = now
                time.sleep(0.2)
            rc = proc.returncode
        emit(f'{label}_EXIT={rc}')
        if rc and checked:
            for line in log.read_text(errors='replace').splitlines()[-35:]:
                print(line, flush=True)
            raise RuntimeError(label + '_FAILED_' + str(rc))
        return rc
    finally:
        stop_owned(proc)


def emulator_pids() -> list[int]:
    found = []
    for entry in Path('/proc').glob('[0-9]*'):
        try:
            comm = (entry / 'comm').read_text().strip().lower()
            if comm == 'shadps4':
                found.append(int(entry.name))
        except (OSError, ValueError):
            pass
    return found


def free_vram() -> int:
    devices = []
    for total_path in Path('/sys/class/drm').glob('card*/device/mem_info_vram_total'):
        try:
            total = int(total_path.read_text())
            used = int(total_path.with_name('mem_info_vram_used').read_text())
            if total >= MIN_VRAM and 0 <= used <= total:
                devices.append((total, total - used))
        except (OSError, ValueError):
            pass
    return max(devices)[1] if devices else -1


def api(path: str, data: dict | None = None, timeout: int = 20) -> dict:
    raw = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(API + path, data=raw,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        result = json.load(response)
    if not isinstance(result, dict) or result.get('error'):
        raise RuntimeError('OLLAMA_API_ERROR:' + str(result)[:200])
    return result


def expiry(model: dict) -> dt.datetime:
    when = dt.datetime.fromisoformat(str(model['expires_at']).replace('Z', '+00:00'))
    if when.tzinfo is None:
        raise ValueError('Ollama expiry lacks timezone')
    return when


def headroom(evid: Path, unloaded: list[dict], result: dict) -> None:
    current = free_vram()
    result['vram_before_bytes'] = current
    emit(f'VRAM_FREE_BYTES={current} REQUIRED={MIN_VRAM}')
    if current < 0:
        raise RuntimeError('VRAM_COUNTER_UNAVAILABLE')
    if current < MIN_VRAM:
        data = api('/api/ps')
        models = data.get('models', [])
        write_json(evid / 'ollama-before.json', data)
        # Never unload a model when its context/expiry cannot be restored.
        for model in models:
            name = model.get('name') or model.get('model')
            context = model.get('context_length')
            if not name or not isinstance(context, int) or context <= 0:
                raise RuntimeError('OLLAMA_CONTEXT_UNKNOWN_NO_MODELS_UNLOADED')
            expiry(model)
        for model in models:
            if free_vram() >= MIN_VRAM:
                break
            emit('OLLAMA_UNLOAD=' + (model.get('name') or model['model']))
            # Register first, so a timeout after successful unloading still restores it.
            unloaded.append(model)
            api('/api/generate', {'model': model.get('name') or model['model'],
                                 'keep_alive': 0, 'stream': False}, 25)
            for _ in range(10):
                if free_vram() >= MIN_VRAM:
                    break
                time.sleep(0.5)
    current = free_vram()
    result['vram_at_launch_bytes'] = current
    emit(f'VRAM_FINAL_BYTES={current}')
    if current < MIN_VRAM:
        raise RuntimeError('INSUFFICIENT_8GIB_VRAM_NO_GAME_LAUNCHED')


def restore_models(unloaded: list[dict], evid: Path) -> list[dict]:
    results = []
    for model in unloaded:
        name = model.get('name') or model['model']
        record = {'name': name, 'context_length': model['context_length']}
        try:
            now = dt.datetime.now(dt.timezone.utc)
            remaining = (expiry(model) - now).total_seconds()
            if remaining <= 0:
                record['result'] = 'ORIGINAL_EXPIRY_ELAPSED_NOT_RELOADED'
            else:
                keep_alive = -1 if remaining > 365 * 86400 else int(remaining) + 1
                emit('OLLAMA_RESTORE=' + name)
                api('/api/generate', {'model': name, 'stream': False,
                    'keep_alive': keep_alive, 'options': {'num_ctx': model['context_length']}}, 60)
                loaded = api('/api/ps').get('models', [])
                ok = any((m.get('name') or m.get('model')) == name and
                         m.get('context_length') == model['context_length'] for m in loaded)
                record['result'] = 'RESTORED_VERIFIED' if ok else 'RESTORE_NOT_VERIFIED'
        except Exception as exc:
            record['result'] = 'RESTORE_FAILED'
            record['error'] = str(exc)
        results.append(record)
        write_json(evid / 'ollama-restore.json', results)
    return results


def profile_files(root: Path) -> list[Path]:
    files = []
    total = 0
    for base, dirs, names in os.walk(root, followlinks=True):
        base = Path(base)
        rel = base.relative_to(root)
        if not rel.parts:
            dirs[:] = [d for d in dirs if d not in SKIP_PROFILE_DIRS]
        if len(rel.parts) > 24:
            raise RuntimeError('PROFILE_NESTING_OR_SYMLINK_CYCLE')
        for name in names:
            path = base / name
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode):
                raise RuntimeError('PROFILE_SPECIAL_FILE:' + str(path))
            total += path.stat().st_size
            files.append(path)
            if total > PROFILE_LIMIT or len(files) > 100000:
                raise RuntimeError('PROFILE_COPY_LIMIT_EXCEEDED_NO_GAME')
    return files


def relocate_strings(value, original: Path, destination: Path):
    if isinstance(value, str):
        prefix = str(original)
        if value == prefix or value.startswith(prefix + '/'):
            return str(destination) + value[len(prefix):]
        return value
    if isinstance(value, list):
        return [relocate_strings(x, original, destination) for x in value]
    if isinstance(value, dict):
        return {k: relocate_strings(v, original, destination) for k, v in value.items()}
    return value


def prepare_profile(work: Path, evid: Path) -> tuple[Path, dict[str, str]]:
    original = Path.cwd() / 'user'
    if not original.is_dir():
        original = Path(os.environ.get('XDG_DATA_HOME') or (Path.home() / '.local/share')) / 'shadPS4'
    original = original.resolve()
    if not (original / 'config.json').is_file():
        raise RuntimeError('PROFILE_CONFIG_JSON_MISSING_NO_GAME')
    # Refuse a save location outside the copied profile, instead of writing original saves.
    for config in (original / 'config.json', original / 'custom_configs/CUSA11456.json'):
        if config.is_file():
            obj = json.loads(config.read_text())
            home = obj.get('General', {}).get('home_dir', '')
            if home and not Path(home).expanduser().resolve().is_relative_to(original):
                raise RuntimeError('EXTERNAL_SAVE_ROOT_NOT_ISOLATED_NO_GAME')
    files = profile_files(original)
    user = work / 'user'
    user.mkdir(parents=True)
    protected = {}
    last = time.monotonic()
    for i, path in enumerate(files):
        rel = path.relative_to(original)
        target = user / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target, follow_symlinks=True)
        if rel.parts[0] == 'home' or path.suffix == '.json':
            protected[str(path)] = digest(path)
        if path.suffix == '.json' and path.stat().st_size <= 4 * 1024**2:
            try:
                before = json.loads(target.read_text())
            except (ValueError, UnicodeError):
                continue
            after = relocate_strings(before, original, user)
            if before != after:
                write_json(target, after)
        if time.monotonic() - last >= 15:
            emit(f'COPYING_PRIVATE_PROFILE files={i+1}/{len(files)}')
            last = time.monotonic()
    write_json(evid / 'profile-protection.json', {'source': str(original),
        'isolated': str(user), 'files_copied': len(files), 'protected_sha256': protected,
        'graphics_settings_changed': False, 'relocation': 'absolute paths inside copied profile only'})
    return original, protected


def game_run(binary: Path, eboot: Path, work: Path, evid: Path, screen: str) -> dict:
    emit('STAGE=MENU_ONLY_TEST duration=118s+2s NO_INPUT=YES')
    env = dict(os.environ, DISPLAY=screen)
    frames = []
    captures = []
    proc = None
    started = time.monotonic()
    timed = False
    marks = iter((40, 55, 85, 112))
    next_mark = next(marks, None)
    last_beat = started
    try:
        with (evid / 'game.log').open('wb') as log, (evid / 'capture.log').open('ab') as caplog:
            proc = sp.Popen([str(binary), '--game', str(eboot), '--fullscreen', 'true', '--show-fps'],
                cwd=work, env=env, stdout=log, stderr=sp.STDOUT, start_new_session=True)
            while proc.poll() is None:
                now = time.monotonic()
                elapsed = now - started
                if elapsed >= 118:
                    timed = True
                    stop_owned(proc)
                    break
                if next_mark is not None and elapsed >= next_mark:
                    target = evid / f'menu-{next_mark}.png'
                    cmd = ['import', '-window', 'root', str(target)] if shutil.which('import') else ['maim', str(target)]
                    cap = sp.Popen(cmd, env=env, stdout=caplog, stderr=sp.STDOUT, start_new_session=True)
                    item = {'requested_seconds': next_mark, 'started_seconds': elapsed, 'file': target.name}
                    captures.append((cap, now, item))
                    next_mark = next(marks, None)
                for cap, capstart, item in captures[:]:
                    if cap.poll() is not None or now - capstart > 6:
                        stop_owned(cap)
                        item['exit_code'] = cap.returncode
                        item['finished_seconds'] = time.monotonic() - started
                        item['game_alive_at_finish'] = proc.poll() is None
                        frames.append(item)
                        captures.remove((cap, capstart, item))
                if now - last_beat >= 15:
                    emit(f'MENU_TEST_ACTIVE elapsed={elapsed:.0f}s pid={proc.pid}')
                    last_beat = now
                time.sleep(0.05)
            rc = proc.wait(timeout=3)
    finally:
        stop_owned(proc)
        for cap, capstart, item in captures:
            stop_owned(cap)
            item['exit_code'] = cap.returncode
            item['game_alive_at_finish'] = False
            frames.append(item)
        write_json(evid / 'capture-times.json', frames)
    return {'exit_code': rc, 'duration_seconds': round(time.monotonic()-started, 3),
            'time_limit_reached': timed, 'input_injected': False,
            'result': 'BOUNDED_TEST_FINISHED' if timed else 'EARLY_PROCESS_EXIT',
            'visual_result': 'PENDING_REVIEW_NOT_AUTOMATICALLY_PASS'}


def main() -> int:
    project = Path.home() / 'projects/shadps4'
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evid = project / 'logs' / ('got-c925-indexed-revalidation-' + stamp)
    evid.mkdir(parents=True)
    candidate = project / 'GhostOfTsushima/experiments' / BASE_NAME
    source = candidate / 'source'
    binary = candidate / 'build/shadps4'
    protected = project / 'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
    checkpoint = candidate / 'checkpoints' / ('pr95-before-revalidation-' + stamp)
    work = candidate / 'runs' / ('indexed-revalidation-' + stamp)
    result = {'schema_version': 2, 'record_id': evid.name,
        'source_base': EXPECTED_HEAD, 'upstream_checked_not_rebased': UPSTREAM_OBSERVED,
        'change': 'Retain rejected indexed table dependency and specialize on bounded capacity',
        'build': 'NOT_RUN', 'game': {'result': 'NOT_RUN'}, 'decision': 'ISOLATED_NOT_PROMOTED',
        'evidence': str(evid), 'candidate': str(candidate), 'graphics_fixed': 'UNVERIFIED'}
    emit('EVIDENCE_DIRECTORY=' + str(evid))
    locks = []
    unloaded = []
    originals = {}
    patched = False
    native_ok = False
    backup_binary = checkpoint / 'shadps4'
    protected_before = None
    rc = 1
    try:
        for name in ('.got-pr95-scalar-bounds-build.lock', '.got-indexed-revalidation.lock'):
            lock = (project / 'logs' / name).open('a+')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locks.append(lock)
        emit('STAGE=VERIFYING_EXISTING_PR95')
        if not binary.is_file() or digest(binary) != EXPECTED_ELF:
            raise RuntimeError('PR95_CANDIDATE_CHANGED_NO_SOURCE_MODIFIED')
        if not protected.is_file():
            raise RuntimeError('PROTECTED_ELF_MISSING')
        protected_before = digest(protected)
        result['protected_before_sha256'] = protected_before
        result['pr95_before_sha256'] = digest(binary)
        head = sp.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if head != EXPECTED_HEAD:
            raise RuntimeError('C925_BASE_CHANGED')
        for entry in Path('/proc').glob('[0-9]*'):
            try:
                args = (entry / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
                if 'ninja' in args and (entry / 'cwd').resolve() == candidate / 'build':
                    raise RuntimeError('CANDIDATE_ALREADY_BUILDING')
            except OSError:
                pass
        scripts = Path(__file__).resolve().parent
        adapter = scripts / 'apply_indexed_table_revalidation.py'
        tests = scripts / 'guest_indexed_table_revalidation.cpp'
        spec = importlib.util.spec_from_file_location('table_adapter', adapter)
        if spec is None or spec.loader is None:
            raise RuntimeError('ADAPTER_IMPORT_FAILED')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Fail on any missing source context BEFORE saving/replacing the executable.
        module.transform({p: (source/p).read_text() for p in module.PATHS})
        checkpoint.mkdir(parents=True)
        logged(['cp', '--reflink=auto', '--preserve=mode,timestamps', str(binary), str(backup_binary)],
               evid / 'checkpoint.log', 'PRESERVING_PR95', limit=120)
        if digest(backup_binary) != EXPECTED_ELF:
            raise RuntimeError('CHECKPOINT_VERIFY_FAILED')
        result['pr95_checkpoint'] = str(backup_binary)
        # Set first so a partial I/O failure still attempts restoration from the manifest.
        patched = True
        module.apply(source, evid)
        compilers = []
        for cxx in ('g++', 'clang++'):
            if not shutil.which(cxx):
                continue
            name = cxx.replace('+', 'p')
            exe = work / ('unit-' + name)
            work.mkdir(parents=True, exist_ok=True)
            logged([cxx, '-std=c++20', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic',
                    '-I'+str(source/'src'), str(tests), '-o', str(exe)],
                   evid/(name+'-compile.log'), 'UNIT_COMPILE_'+name, limit=120)
            logged([str(exe)], evid/(name+'-run.log'), 'UNIT_RUN_'+name, limit=20)
            compilers.append(cxx)
        if not compilers:
            raise RuntimeError('NO_CXX_COMPILER')
        result['unit_compilers_passed'] = compilers
        logged(['cmake', '--build', str(candidate/'build'), '--target', 'shadps4',
                '--parallel', '5', '--', '-k', '0'], evid/'build.log', 'INCREMENTAL_BUILD')
        if not binary.is_file():
            raise RuntimeError('NO_LINKED_BINARY')
        native_ok = True
        result['build'] = 'PASS'
        result['candidate_sha256'] = digest(binary)
        for p in module.PATHS:
            dest = evid / 'source-after' / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source/p, dest)
        if digest(protected) != protected_before:
            raise RuntimeError('PROTECTED_CHANGED_EXTERNALLY_NO_GAME')
        if emulator_pids():
            raise RuntimeError('OTHER_SHADPS4_RUNNING_NO_GAME')
        if not shutil.which('xdpyinfo') or not (shutil.which('import') or shutil.which('maim')):
            raise RuntimeError('DISPLAY_OR_CAPTURE_TOOL_UNAVAILABLE_NO_GAME')
        screen = os.environ.get('DISPLAY') or ':0'
        logged(['xdpyinfo'], evid/'display.log', 'VERIFYING_DISPLAY', limit=8,
               env=dict(os.environ, DISPLAY=screen))
        emit('STAGE=COPYING_PRIVATE_PROFILE no_original_save_writes=YES')
        original, originals = prepare_profile(work, evid)
        result['original_profile'] = str(original)
        result['isolated_profile'] = str(work/'user')
        headroom(evid, unloaded, result)
        eboot = Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
        if not eboot.is_file() or emulator_pids():
            raise RuntimeError('LAUNCH_PREFLIGHT_CHANGED_NO_GAME')
        result['game'] = game_run(binary, eboot, work, evid, screen)
        log = (evid/'game.log').read_text(errors='replace')
        keys = ('INDEXED_TABLE_REVALIDATE', 'INDEXED_MATERIAL_BIND_REJECT',
                'Sharp source was not flatenned', 'GuestIndexedImageDimensions',
                'VK_ERROR_DEVICE_LOST', 'Device lost during submit')
        result['sample_limited_log_counts'] = {key: log.count(key) for key in keys}
        if result['game']['time_limit_reached']:
            result['status'] = 'BUILD_AND_BOUNDED_CAPTURE_FINISHED_VISUAL_PENDING'
            rc = 0
        else:
            result['status'] = 'GAME_EXITED_BEFORE_TIME_LIMIT'
            rc = 0 if result['game']['exit_code'] == 0 else 1
    except (Exception, KeyboardInterrupt) as exc:
        result['status'] = str(exc) or type(exc).__name__
        (evid/'failure.txt').write_text(traceback.format_exc())
        emit('STOP=' + result['status'])
        rc = 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        if unloaded:
            restored = restore_models(unloaded, evid)
            result['ollama_restore'] = restored
            if any(x['result'] in ('RESTORE_FAILED', 'RESTORE_NOT_VERIFIED') for x in restored):
                rc = 1
        if patched and not native_ok:
            emit('STAGE=RESTORING_PR95_AFTER_FAILED_BUILD')
            try:
                backups = evid/'source-before'
                for path in backups.rglob('*'):
                    if path.is_file():
                        shutil.copy2(path, source/path.relative_to(backups))
                if backup_binary.is_file() and digest(backup_binary) == EXPECTED_ELF:
                    shutil.copy2(backup_binary, binary)
                result['rollback'] = 'SOURCE_AND_ELF_RESTORED' if digest(binary) == EXPECTED_ELF else 'VERIFY_FAILED'
            except Exception as exc:
                result['rollback'] = 'FAILED:' + str(exc)
        if protected_before is not None:
            result['protected_after_sha256'] = digest(protected) if protected.is_file() else None
            result['protected_unchanged'] = result['protected_after_sha256'] == protected_before
            if not result['protected_unchanged']:
                rc = 1
        if originals:
            changed = [p for p, sha in originals.items() if not Path(p).is_file() or digest(Path(p)) != sha]
            result['original_save_and_json_files_changed'] = changed
            if changed:
                rc = 1
        result['runner_exit_code'] = rc
        write_json(evid/'trial.json', result)
        archive = Path(str(evid) + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as tf:
            tf.add(evid, arcname=evid.name)
        emit('RESULT=' + result.get('status', 'UNKNOWN'))
        emit('EVIDENCE=' + str(archive))
        emit('NO_MAIN_OR_LAUNCHER_PROMOTION=YES')
        for lock in locks:
            lock.close()
    return rc


if __name__ == '__main__':
    def on_term(signum, frame):
        raise KeyboardInterrupt('TERMINATED_BY_USER')
    signal.signal(signal.SIGTERM, on_term)
    sys.exit(main())
