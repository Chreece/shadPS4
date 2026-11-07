#!/usr/bin/env python3
"""ES-DE launch guard: reserve 8 GiB of AMD GPU VRAM by temporarily unloading Ollama models.

Install by wrapping the existing ES-DE AppImage *at its original path*. The real
AppImage is retained alongside it as ``.real``. No Sunshine, emulator or Docker
configuration is changed, and Ollama itself is never stopped.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

HOME = Path.home()
PROJECT = Path(os.environ.get('ESDE_GUARD_PROJECT', HOME / 'projects/shadps4'))
APPIMAGE = Path(os.environ.get('ESDE_GUARD_APPIMAGE', HOME / 'apps/es/ES-DE_x64.AppImage'))
REAL = APPIMAGE.with_name(APPIMAGE.name + '.real')
SCRIPT = PROJECT / 'scripts/esde-ollama-vram-guard.py'
LOGS = PROJECT / 'logs'
SYSFS = Path(os.environ.get('ESDE_GUARD_DRM_ROOT', '/sys/class/drm'))
API = os.environ.get('ESDE_GUARD_OLLAMA_API', 'http://127.0.0.1:11434').rstrip('/')
REQUIRED = int(float(os.environ.get('ESDE_GUARD_MIN_FREE_GIB', '8')) * 1024 ** 3)
MARKER = '# CHREECE_ESDE_OLLAMA_VRAM_GUARD_V1'


def emit(log: Path, event: str, **items: object) -> None:
    entry = {'utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'event': event, **items}
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    print('ESDE_GUARD ' + line, flush=True)
    if log:
        with log.open('a', encoding='utf-8') as file:
            file.write(line + '\n')


def vram() -> tuple[int, int, str] | None:
    """Read the largest discrete AMD device, avoiding total/free from different GPUs."""
    devices = []
    for card in SYSFS.glob('card[0-9]*'):
        if re.fullmatch(r'card\d+', card.name) is None:
            continue
        device = card / 'device'
        try:
            total = int((device / 'mem_info_vram_total').read_text().strip())
            used = int((device / 'mem_info_vram_used').read_text().strip())
            if not (0 <= used <= total and total > 0):
                continue
            devices.append((total, used, card.name))
        except (OSError, ValueError):
            continue
    if not devices:
        return None
    return max(devices, key=lambda item: item[0])


def request(method: str, endpoint: str, payload: dict | None = None, timeout: int = 20) -> dict:
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(
        API + endpoint, data=data, method=method,
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        value = json.loads(response.read(4 * 1024 * 1024))
    if not isinstance(value, dict):
        raise RuntimeError('Ollama returned invalid JSON')
    return value


def loaded_models() -> list[dict]:
    value = request('GET', '/api/ps', timeout=5)
    models = value.get('models', [])
    if not isinstance(models, list):
        raise RuntimeError('Ollama /api/ps response has no models list')
    return [item for item in models if isinstance(item, dict) and isinstance(item.get('name'), str)]


def duration_for_restore(item: dict) -> int:
    """Preserve remaining keep-alive (min 5 minutes) or indefinite retention."""
    expires = item.get('expires_at')
    if isinstance(expires, str):
        try:
            date = dt.datetime.fromisoformat(expires.replace('Z', '+00:00'))
            if date.year >= 9000:
                return -1
            if date.tzinfo:
                remaining = int((date - dt.datetime.now(dt.timezone.utc)).total_seconds())
                return max(300, remaining)
        except (ValueError, OverflowError):
            pass
    return 300


def api_model(name: str, keep_alive: int) -> dict:
    return request('POST', '/api/generate', {
        'model': name, 'prompt': '', 'stream': False, 'keep_alive': keep_alive,
    }, timeout=90 if keep_alive != 0 else 30)


def free_bytes() -> int | None:
    usage = vram()
    if usage is None:
        return None
    return usage[0] - usage[1]


def unload_for_vram(log: Path, unloaded: list[tuple[str, int]]) -> None:
    usage = vram()
    if usage is None:
        emit(log, 'VRAM_UNAVAILABLE', detail='Unknown AMD GPU VRAM; leaving Ollama untouched')
        return
    total, used, gpu = usage
    emit(log, 'VRAM_BEFORE', gpu=gpu, total_gib=round(total / 1024 ** 3, 2),
         free_gib=round((total - used) / 1024 ** 3, 2), required_gib=round(REQUIRED / 1024 ** 3, 2))
    if total - used >= REQUIRED:
        emit(log, 'ENOUGH_VRAM', detail='No models unloaded')
        return
    try:
        models = loaded_models()
    except (OSError, ValueError, RuntimeError) as exc:
        emit(log, 'OLLAMA_UNAVAILABLE', error=str(exc))
        return
    candidates = sorted(
        (m for m in models if int(m.get('size_vram') or 0) > 0),
        key=lambda m: int(m.get('size_vram') or 0), reverse=True,
    )
    for model in candidates:
        if (free_bytes() or 0) >= REQUIRED:
            break
        name = model['name']
        ttl = duration_for_restore(model)
        try:
            # Model-specific API call, never kill/restart the Ollama server.
            api_model(name, 0)
            unloaded.append((name, ttl))
            emit(log, 'MODEL_UNLOADED', model=name, restore_keep_alive=ttl)
        except (OSError, ValueError, RuntimeError) as exc:
            emit(log, 'UNLOAD_FAILED', model=name, error=str(exc))
            continue
        for _ in range(10):
            if (free_bytes() or 0) >= REQUIRED:
                break
            time.sleep(0.25)
    result = free_bytes()
    emit(log, 'VRAM_AFTER', free_gib=round(result / 1024 ** 3, 2) if result is not None else None,
         target_reached=result is not None and result >= REQUIRED,
         unloaded=[name for name, _ in unloaded])
    return


def restore(log: Path, unloaded: list[tuple[str, int]]) -> None:
    if not unloaded:
        emit(log, 'NOTHING_TO_RESTORE')
        return
    try:
        present = {item['name'] for item in loaded_models()}
    except (OSError, ValueError, RuntimeError) as exc:
        emit(log, 'RESTORE_LIST_FAILED', error=str(exc))
        present = set()
    for name, ttl in unloaded:
        if name in present:
            emit(log, 'ALREADY_RELOADED', model=name)
            continue
        try:
            api_model(name, ttl)
            emit(log, 'MODEL_RESTORED', model=name, keep_alive=ttl)
        except (OSError, ValueError, RuntimeError) as exc:
            emit(log, 'MODEL_RESTORE_FAILED', model=name, error=str(exc))


def run(binary: Path, args: list[str]) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    (PROJECT / 'tests').mkdir(parents=True, exist_ok=True)
    log = LOGS / ('esde-vram-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-'
                  + str(os.getpid()) + '.jsonl')
    if not binary.is_file() or not os.access(binary, os.X_OK):
        emit(log, 'MISSING_REAL_ESDE_APPIMAGE', path=str(binary))
        return 2
    lock = PROJECT / 'tests/.esde-vram-guard.lock'
    with lock.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit(log, 'ALREADY_RUNNING', detail='Another guarded ES-DE session is active')
            return 2
        unloaded: list[tuple[str, int]] = []
        child: subprocess.Popen | None = None
        def on_signal(signum: int, _frame: object) -> None:
            raise InterruptedError(f'Signal {signum}')
        old_term = signal.signal(signal.SIGTERM, on_signal)
        old_int = signal.signal(signal.SIGINT, on_signal)
        exit_code = 1
        try:
            try:
                unload_for_vram(log, unloaded)
            except Exception as exc:
                emit(log, 'PREFLIGHT_UNEXPECTED_ERROR', error=repr(exc))
                # ES-DE should remain usable even if diagnostics or Ollama fail.
            emit(log, 'START_ESDE', executable=str(binary), args=args)
            child = subprocess.Popen([str(binary), *args])
            exit_code = child.wait()
            emit(log, 'ESDE_EXITED', exit_code=exit_code)
        except (InterruptedError, KeyboardInterrupt) as exc:
            emit(log, 'ESDE_INTERRUPTED', signal=str(exc))
            if child is not None and child.poll() is None:
                child.terminate()  # Only the child AppImage, never Sunshine or its process group.
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            exit_code = 128 + signal.SIGTERM
        except OSError as exc:
            emit(log, 'ESDE_EXEC_FAILED', error=str(exc))
            exit_code = 1
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)  # finish restoration even on late TERM
            restore(log, unloaded)
            signal.signal(signal.SIGTERM, old_term)
            signal.signal(signal.SIGINT, old_int)
        return exit_code


def is_wrapper() -> bool:
    try:
        with APPIMAGE.open('rb') as source:
            return MARKER.encode() in source.read(1024)
    except OSError:
        return False


def install() -> None:
    if not SCRIPT.is_file() or not os.access(SCRIPT, os.R_OK):
        raise RuntimeError('Install the verified Python script at ' + str(SCRIPT))
    if not APPIMAGE.parent.is_dir():
        raise RuntimeError('ES-DE directory not found: ' + str(APPIMAGE.parent))
    if is_wrapper():
        if not REAL.is_file():
            raise RuntimeError('Guard wrapper present but real AppImage missing: ' + str(REAL))
        print('ESDE_GUARD_ALREADY_INSTALLED=' + str(APPIMAGE))
        return
    if REAL.exists() or REAL.is_symlink():
        raise RuntimeError('Refusing to overwrite an existing .real AppImage: ' + str(REAL))
    if APPIMAGE.is_symlink() or not APPIMAGE.is_file() or not os.access(APPIMAGE, os.X_OK):
        raise RuntimeError('ES-DE AppImage must be a regular executable file: ' + str(APPIMAGE))
    with APPIMAGE.open('rb') as source:
        if source.read(4) != b'\x7fELF':
            raise RuntimeError('ES-DE AppImage is not an ELF binary; leaving it untouched')
    content = ('#!/usr/bin/env bash\n' + MARKER + '\n' +
               'exec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(SCRIPT)) +
               ' run -- ' + shlex.quote(str(REAL)) + ' "$@"\n')
    temporary = APPIMAGE.parent / ('.esde-guard-' + uuid.uuid4().hex)
    try:
        temporary.write_text(content)
        temporary.chmod(0o755)
        APPIMAGE.rename(REAL)
        try:
            os.replace(temporary, APPIMAGE)
        except Exception:
            REAL.rename(APPIMAGE)
            raise
    finally:
        temporary.unlink(missing_ok=True)
    print('ESDE_GUARD_INSTALLED=' + str(APPIMAGE))
    print('ESDE_REAL_APPIMAGE=' + str(REAL))
    print('VRAM_TARGET_GIB=' + str(REQUIRED // 1024 ** 3))
    print('OLLAMA_API=' + API)
    print('SUNSHINE_AND_SHADPS4_UNCHANGED=YES')


def uninstall() -> None:
    if not is_wrapper() or not REAL.is_file():
        raise RuntimeError('No managed ES-DE wrapper and preserved original detected')
    temporary = APPIMAGE.parent / ('.esde-guard-previous-' + uuid.uuid4().hex)
    APPIMAGE.rename(temporary)
    try:
        REAL.rename(APPIMAGE)
    except Exception:
        temporary.rename(APPIMAGE)
        raise
    temporary.unlink(missing_ok=True)
    print('ESDE_GUARD_REMOVED=' + str(APPIMAGE))


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    if command == 'install' and len(sys.argv) == 2:
        install()
        return 0
    if command == 'uninstall' and len(sys.argv) == 2:
        uninstall()
        return 0
    if command == 'status' and len(sys.argv) == 2:
        usage = vram()
        print('ESDE_WRAPPED=' + str(is_wrapper()))
        print('REAL_APPIMAGE=' + str(REAL) + ' exists=' + str(REAL.is_file()))
        if usage is None:
            print('VRAM_STATUS=UNAVAILABLE')
        else:
            print('VRAM_FREE_GIB=%.2f' % ((usage[0] - usage[1]) / 1024 ** 3))
        return 0
    if command == 'run' and len(sys.argv) >= 4 and sys.argv[2] == '--':
        return run(Path(sys.argv[3]), sys.argv[4:])
    print('Usage: guard.py install|uninstall|status|run -- ORIGINAL_APPIMAGE [args...]', file=sys.stderr)
    return 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print('ESDE_GUARD_ERROR=' + str(error), file=sys.stderr)
        sys.exit(1)
