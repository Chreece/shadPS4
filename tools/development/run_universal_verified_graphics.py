#!/usr/bin/env python3
"""Observe the host-verified candidate with an isolated profile and no input."""
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request

PIN = os.environ.get('SHADPS4_TEST_SOURCE_COMMIT', 'ed0fe5be055ee712756351479ac5411366e5385b')
ELF = '2866fdd415a3e3a82c9fa216530acb26fd4a4f4ef388e0aeec2b7f3ce8abc548'
MIN_FREE = 8 * 1024**3


def digest(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def save(p, value):
    p.write_text(json.dumps(value, indent=2) + '\n')


def diagnostic_command(argv, timeout):
    started = time.monotonic()
    result = {'command': argv, 'timed_out': False}
    try:
        completed = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                   timeout=timeout)
        result['exit_code'] = completed.returncode
        stdout, stderr = completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        result.update(timed_out=True, exit_code=None)
        stdout, stderr = exc.stdout or b'', exc.stderr or b''
    except OSError as exc:
        result.update(exit_code=None, error=str(exc))
        stdout, stderr = b'', b''
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    result['stdout'] = stdout[-128 * 1024:].decode('utf-8', 'replace')
    result['stderr'] = stderr[-16 * 1024:].decode('utf-8', 'replace')
    return result


def collect_display_health(out, display):
    authority = Path(os.environ.get('XAUTHORITY') or str(Path.home() / '.Xauthority'))
    health = {'display': display, 'xauthority_path': str(authority),
              'xauthority_exists': authority.is_file(),
              'local_x_sockets': sorted(str(p) for p in Path('/tmp/.X11-unix').glob('X*')),
              'processes': []}
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            name = (process / 'comm').read_text().strip()
            if name not in ('X', 'Xorg', 'Xwayland', 'sunshine') and not name.lower().startswith('shadps4'):
                continue
            state = {'pid': int(process.name), 'name': name}
            for line in (process / 'status').read_text().splitlines():
                key, _, value = line.partition(':')
                if key in ('State', 'PPid', 'Uid', 'Threads', 'VmRSS'):
                    state[key] = value.strip()
            try:
                state['wait_channel'] = (process / 'wchan').read_text().strip()
            except OSError:
                pass
            health['processes'].append(state)
        except OSError:
            continue
    save(out / 'display-host-state.json', health)
    save(out / 'display-xset.json', diagnostic_command(['xset', '-display', display, 'q'], 3))
    save(out / 'kernel-journal.json', diagnostic_command(
        ['journalctl', '-k', '-b', '--no-pager', '-n', '600', '-o', 'short-monotonic'], 5))
    match = re.fullmatch(r':(\d+)(?:\.\d+)?', display)
    if match:
        name = 'Xorg.' + match.group(1) + '.log'
        for index, directory in enumerate((Path('/var/log'), Path.home() / '.local/share/xorg')):
            path = directory / name
            try:
                with path.open('rb') as f:
                    f.seek(max(0, path.stat().st_size - 128 * 1024))
                    (out / ('display-xorg-' + str(index) + '.log')).write_bytes(f.read())
            except OSError:
                pass


def verify_display(out, record, display):
    probe = diagnostic_command(['xdpyinfo', '-display', display], 8)
    save(out / 'display-preflight.json', probe)
    record['display'] = display
    record['display_preflight'] = {'exit_code': probe['exit_code'],
                                   'timed_out': probe['timed_out']}
    if probe['exit_code'] == 0:
        return
    record['preflight_failure'] = ('DISPLAY_UNRESPONSIVE' if probe['timed_out']
                                   else 'DISPLAY_UNAVAILABLE')
    record['stop_remaining_tests'] = True
    try:
        collect_display_health(out, display)
    except Exception as exc:
        record['display_diagnostic_error'] = str(exc)
    raise RuntimeError(record['preflight_failure'] + ' display=' + display)


def free_vram():
    cards = []
    for device in Path('/sys/class/drm').glob('card[0-9]*/device'):
        try:
            total = int((device / 'mem_info_vram_total').read_text())
            used = int((device / 'mem_info_vram_used').read_text())
            cards.append((total, total - used))
        except (OSError, ValueError):
            pass
    if not cards:
        raise RuntimeError('VRAM_TELEMETRY_UNAVAILABLE')
    return max(cards)[1]


def ollama(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request('http://127.0.0.1:11434/api/' + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=15) as response:
        return json.load(response)


def unload_models(out, record):
    attempts = []
    record['ollama_attempts'] = attempts
    print('VRAM_LOW_TRYING_LOCAL_OLLAMA_API', flush=True)
    try:
        models = ollama('ps').get('models', [])
        save(out / 'ollama-before.json', models)
        for model in models:
            name = model.get('name') or model.get('model')
            if name:
                ollama('generate', {'model': name, 'keep_alive': 0, 'stream': False})
                record['ollama_unloaded'].append(name)
        attempts.append({'transport': 'direct-local-http', 'result': 'SUCCESS'})
        return
    except Exception as exc:
        attempts.append({'transport': 'direct-local-http', 'error': str(exc)})
        print('LOCAL_API_FAILED_TRYING_CONTAINER_CLI', flush=True)

    def command(argv, label):
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            attempts.append({'transport': label, 'error': str(exc)})
            return None
        (out / (label + '.txt')).write_text(result.stdout + result.stderr)
        if result.returncode:
            attempts.append({'transport': label, 'exit_code': result.returncode})
            return None
        return result.stdout

    if not shutil.which('docker'):
        attempts.append({'transport': 'docker', 'error': 'CLI_NOT_INSTALLED'})
        return
    listing = command(['docker', 'ps', '--format', '{{.ID}}\t{{.Image}}\t{{.Names}}'],
                      'docker-running-containers')
    if listing is None:
        return
    for row in listing.splitlines():
        fields = row.split('\t')
        if len(fields) != 3 or 'ollama' not in (fields[1] + ' ' + fields[2]).lower():
            continue
        container = fields[0]
        if not container or any(c not in '0123456789abcdef' for c in container):
            continue
        prefix = ['docker', 'exec', container, 'ollama']
        before = command(prefix + ['ps'], 'ollama-' + container + '-before')
        if before is None:
            continue
        rows = [line.split() for line in before.splitlines() if line.strip()]
        if not rows or rows[0][0] != 'NAME':
            attempts.append({'transport': 'docker:' + container, 'error': 'UNRECOGNIZED_PS_OUTPUT'})
            continue
        names = [row[0] for row in rows[1:] if len(row) >= 2 and not row[0].startswith('-')]
        for index, name in enumerate(names):
            print('UNLOADING_CONTAINER_MODEL=' + name, flush=True)
            stopped = command(prefix + ['stop', name], 'ollama-' + container + '-stop-' + str(index))
            if stopped is not None:
                record['ollama_unloaded'].append(name)
        after = command(prefix + ['ps'], 'ollama-' + container + '-after')
        attempts.append({'transport': 'docker:' + container, 'listed_models': names,
                         'post_unload_query_succeeded': after is not None})


def profile_source(project):
    candidates = [project / 'user', project / 'build/user',
                  Path.home() / '.local/share/shadPS4', project / 'GhostOfTsushima/user']
    for base in [project / 'GhostOfTsushima/experiments', project / 'tests']:
        candidates.extend(sorted(base.glob('*/user'), key=lambda p: p.stat().st_mtime,
                                 reverse=True))
        candidates.extend(sorted(base.glob('*/runtime/user'), key=lambda p: p.stat().st_mtime,
                                 reverse=True))
    for p in candidates:
        if (p / 'sys_modules').is_dir() and any((p / 'sys_modules').glob('*.sprx')):
            return p.resolve()
    raise RuntimeError('NO_EXISTING_PROFILE_WITH_SYSTEM_MODULES')


def copy_profile(old, new):
    allowed = {'sys_modules', 'custom_modules', 'fonts', 'licenses', 'home',
               'savedata', 'game_data', 'trophy', 'config.json'}
    files = []
    for name in allowed:
        src = old / name
        if not src.exists():
            continue
        paths = [src] if src.is_file() else list(src.rglob('*'))
        for p in paths:
            if p.is_symlink():
                raise RuntimeError('PROFILE_SYMLINK_REFUSED:' + str(p))
            if p.is_file():
                files.append(p)
    if sum(p.stat().st_size for p in files) > 2 * 1024**3:
        raise RuntimeError('PROFILE_COPY_EXCEEDS_2_GIB')
    new.mkdir(parents=True)
    for p in files:
        target = new / p.relative_to(old)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    config = new / 'config.json'
    if config.is_file():
        data = json.loads(config.read_text())
        def relocate(value):
            if isinstance(value, dict):
                return {k: relocate(v) for k, v in value.items()}
            if isinstance(value, list):
                return [relocate(v) for v in value]
            if isinstance(value, str) and (value == str(old) or value.startswith(str(old) + '/')):
                return str(new) + value[len(str(old)):]
            return value
        data = relocate(data)
        data.setdefault('General', {})['home_dir'] = str(new / 'home')
        data.setdefault('GPU', {})['dump_shaders'] = True
        save(config, data)
    if not config.is_file():
        save(config, {'GPU': {'dump_shaders': True}})
    return len(files)


def recover_incomplete_captures(project, out, observer):
    recovered = []
    for prior in sorted((project / 'logs').glob('universal-graphics-*/result.json'), reverse=True):
        capture = prior.parent / 'capture'
        console = capture / 'full-console.log'
        if prior.parent == out or (capture / 'native-result.json').exists() or not console.is_file():
            continue
        if len(recovered) == 4:
            break
        target = out / 'recovered-captures' / prior.parent.name
        target.mkdir(parents=True)
        try:
            summary = observer.summarize(console, target)
            summary['original_result'] = json.loads(prior.read_text())
            summary['recovered_from'] = str(console)
            save(target / 'recovered-summary.json', summary)
            recovered.append(prior.parent.name)
        except (OSError, ValueError) as exc:
            save(target / 'recovery-error.json', {'error': str(exc)})
    return recovered


def main():
    os.umask(0o077)
    project = Path.home() / 'projects/shadps4'
    tag = 'universal-graphics-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
    out = project / 'logs' / tag
    out.mkdir(parents=True)
    runtime = project / 'tests' / tag
    base = project / 'tests' / ('universal-upstream-' + PIN[:12])
    source = base / 'source'
    binary = base / 'build/shadps4'
    record = {'schema_version': 2, 'record_id': tag, 'scope': 'UNIVERSAL_SHADPS4',
              'timestamp_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'source_commit': PIN, 'binary_sha256': ELF, 'game_launched': False,
              'graphics_verified': False, 'installed_binary_changed': False,
              'evidence': str(out) + '.tar.gz', 'result': 'PREFLIGHT_FAILED'}
    record['runner_commit'] = os.environ.get('SHADPS4_TEST_RUNNER_COMMIT', PIN)
    record['runner_sha256'] = digest(Path(__file__))
    rc = 1
    game = Path(os.environ.get('SHADPS4_TEST_GAME', '/mnt/roms-all/ps4-games/CUSA11456/eboot.bin'))
    record['context_games'] = [game.parent.name]
    expected_elf = ELF
    module = None
    lock = (project / 'tests/.universal-upstream-build.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not re.fullmatch(r'[0-9a-f]{40}', PIN):
            raise RuntimeError('INVALID_SOURCE_PIN')
        if PIN != 'ed0fe5be055ee712756351479ac5411366e5385b':
            matches = []
            for result_path in (project / 'logs').glob('universal-upstream-*/result.json'):
                try:
                    build_record = json.loads(result_path.read_text())
                except (OSError, ValueError):
                    continue
                if (build_record.get('source_commit') == PIN and
                    build_record.get('result') == 'PASS_NATIVE_BUILD_CPU_AND_STARTUP_TESTS_GPU_NOT_RUN' and
                    build_record.get('candidate_binary') == str(binary) and
                    re.fullmatch(r'[0-9a-f]{64}', build_record.get('binary_sha256', ''))):
                    matches.append((result_path.stat().st_mtime, result_path, build_record))
            if not matches:
                raise RuntimeError('NO_SUCCESSFUL_BUILD_RECORD_FOR_SOURCE')
            _, result_path, build_record = max(matches)
            expected_elf = build_record['binary_sha256']
            record['build_record'] = str(result_path)
            record['binary_sha256'] = expected_elf
        if digest(binary) != expected_elf:
            raise RuntimeError('VERIFIED_BINARY_CHANGED')
        head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if head != PIN:
            raise RuntimeError('VERIFIED_SOURCE_CHANGED')
        observer = source / 'tools/development/shadps4_native_120_observer_20261026.py'
        committed = subprocess.check_output(['git', '-C', str(source), 'show', PIN + ':' + str(observer.relative_to(source))])
        if observer.read_bytes() != committed:
            raise RuntimeError('OBSERVER_CHANGED')
        spec = importlib.util.spec_from_file_location('observer', observer)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        record['recovered_captures'] = recover_incomplete_captures(project, out, module)
        if module.existing_games():
            raise RuntimeError('AN_EMULATOR_IS_ALREADY_RUNNING')
        game = Path(os.environ.get('SHADPS4_TEST_GAME', '/mnt/roms-all/ps4-games/CUSA11456/eboot.bin'))
        if not game.is_file():
            raise RuntimeError('GAME_FILE_MISSING:' + str(game))
        display = os.environ.get('DISPLAY') or ':0'
        os.environ['DISPLAY'] = display
        if not shutil.which('import'):
            raise RuntimeError('SCREENSHOT_TOOL_IMPORT_MISSING')
        verify_display(out, record, display)
        old = profile_source(project)
        record['profile_source'] = str(old)
        record['profile_files_copied'] = copy_profile(old, runtime / 'user')
        record['profile_policy'] = 'isolated saves; global config; no custom title configs, patches, cheats or old shader cache'
        record['vram_free_before'] = free_vram()
        record['ollama_unloaded'] = []
        if record['vram_free_before'] < MIN_FREE:
            unload_models(out, record)
            until = time.monotonic() + 20
            while free_vram() < MIN_FREE and time.monotonic() < until:
                time.sleep(.5)
        record['vram_free_at_launch'] = free_vram()
        if record['vram_free_at_launch'] < MIN_FREE:
            raise RuntimeError('LESS_THAN_8_GIB_VRAM_FREE')
        sys.argv = [str(observer), '--binary', str(binary.resolve()), '--sha256', expected_elf,
                    '--game', str(game), '--cwd', str(runtime), '--report', str(out / 'capture'),
                    '--seconds', '118']
        print('STARTING_VERIFIED_CANDIDATE NO_INPUT MAXIMUM_120_SECONDS', flush=True)
        rc = module.main()
        native = json.loads((out / 'capture/native-result.json').read_text())
        record['game_launched'] = (out / 'capture/launch.json').is_file()
        record['native'] = native
        record['tested_games'] = [game.parent.name] if record['game_launched'] else []
        if rc == 0 and len(native.get('screenshots', [])) < 2:
            rc = 1
            record['result'] = 'INSUFFICIENT_SCREENSHOTS'
        else:
            record['result'] = 'CAPTURE_COMPLETE_VISUAL_REVIEW_REQUIRED' if rc == 0 else 'GAME_OR_OBSERVER_FAILED'
    except (Exception, KeyboardInterrupt) as exc:
        record['error'] = str(exc) or type(exc).__name__
        print('STOP=' + record['error'], flush=True)
    finally:
        record['game_launched'] = (out / 'capture/launch.json').is_file()
        record['tested_games'] = [game.parent.name] if record['game_launched'] else []
        if record['game_launched'] and record['result'] == 'PREFLIGHT_FAILED':
            record['result'] = 'GAME_OR_OBSERVER_FAILED'
        native_path = out / 'capture/native-result.json'
        console = out / 'capture/full-console.log'
        if record['game_launched'] and 'native' not in record:
            try:
                if native_path.is_file():
                    record['native'] = json.loads(native_path.read_text())
                elif module is not None and console.is_file():
                    record['native'] = module.summarize(console, console.parent)
            except (OSError, ValueError) as exc:
                record['capture_error'] = str(exc)
        record['stop_remaining_tests'] = bool(record.get('stop_remaining_tests') or
            record['game_launched'] and (
            record.get('native', {}).get('gpu_failure') or
            not record.get('native', {}).get('owned_child_terminated', False)))
        logs = runtime / 'user/log'
        if logs.is_dir():
            for log in logs.glob('*.txt'):
                if log.is_file() and not log.is_symlink():
                    with log.open('rb') as f:
                        f.seek(max(0, log.stat().st_size - 2 * 1024**2))
                        (out / ('emulator-' + log.name)).write_bytes(f.read())
        console = out / 'capture/full-console.log'
        blocked = set()
        current_hash = None
        if console.is_file():
            with console.open(errors='replace') as f:
                for line in f:
                    match = re.search(r'Compiling \w+ shader (0x[0-9a-fA-F]+)', line)
                    if match:
                        current_hash = format(int(match.group(1), 16), '016x')
                    if current_hash and 'Unexpected instruction for offset computation' in line:
                        blocked.add(current_hash)
        saved_ir = []
        total_ir = 0
        for path in sorted((runtime / 'user/shader/dumps').glob('*irprogram.txt')):
            if path.is_symlink() or not any(h in path.name for h in blocked):
                continue
            size = path.stat().st_size
            if len(saved_ir) >= 48 or total_ir + size > 20 * 1024**2:
                continue
            target = out / 'blocked-shader-ir' / path.name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(path, target)
            saved_ir.append(path.name)
            total_ir += size
        record['shader_diagnostics'] = {'dump_enabled': True, 'blocked_hashes': sorted(blocked),
                                        'saved_ir': saved_ir, 'saved_bytes': total_ir,
                                        'limit_bytes': 20 * 1024**2, 'limit_files': 48}
        record['exit_code'] = rc
        save(out / 'result.json', record)
        for folder in [project / 'docs/emulator', project / 'games' / game.parent.name / 'docs/emulator']:
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / 'TRIALS.jsonl').open('a') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.write(json.dumps(record) + '\n')
                f.flush()
                os.fsync(f.fileno())
        with tarfile.open(str(out) + '.tar.gz', 'w:gz') as tar:
            for p in out.rglob('*'):
                if p.is_file() and p.name != 'full-console.log':
                    tar.add(p, arcname=str(Path(tag) / p.relative_to(out)))
        print('RESULT=' + record['result'], flush=True)
        print('EVIDENCE=' + str(out) + '.tar.gz', flush=True)
        lock.close()
    return rc


if __name__ == '__main__':
    def terminate(signum, frame):
        raise KeyboardInterrupt('TERMINATED_BY_USER')
    signal.signal(signal.SIGTERM, terminate)
    sys.exit(main())
