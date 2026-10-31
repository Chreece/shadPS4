#!/usr/bin/env python3
"""Use Ollama's model API to make VRAM available for a generic shadPS4 run.

The game uses its ordinary global configuration; this wrapper never adds CPU,
GPU, memory, audio, or per-game compatibility options. It never stops either
Ollama's server or shadPS4. When a manual play session outlives the observer,
model restoration waits for the actual candidate process to exit.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.request import Request, urlopen

OLLAMA = 'http://127.0.0.1:7869'
GIB = 1024 ** 3
POLL_INTERVAL = 3


def atomic_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.pending')
    temp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + '\n')
    temp.replace(path)


def request_json(route: str, payload: dict | None = None, timeout: int = 15) -> dict:
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    req = Request(OLLAMA + route, data=data, headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=timeout) as reply:
        response = json.load(reply)
    if not isinstance(response, dict):
        raise RuntimeError(f'Unexpected Ollama {route} response')
    return response


def models_loaded() -> list[dict]:
    models = request_json('/api/ps', timeout=8).get('models')
    if not isinstance(models, list) or not all(isinstance(x, dict) for x in models):
        raise RuntimeError('OLLAMA_PS_INVALID')
    return models


def gpu_memory() -> dict:
    devices = []
    for candidate in Path('/sys/class/drm').glob('card[0-9]*/device'):
        try:
            if (candidate / 'vendor').read_text().strip().lower() != '0x1002':
                continue
            total = int((candidate / 'mem_info_vram_total').read_text())
            used = int((candidate / 'mem_info_vram_used').read_text())
            if total > 0 and 0 <= used <= total:
                devices.append({'device': str(candidate), 'total': total, 'used': used,
                                'free': total - used})
        except (OSError, ValueError):
            pass
    if not devices:
        raise RuntimeError('AMD_GPU_VRAM_TELEMETRY_UNAVAILABLE')
    return max(devices, key=lambda x: x['total'])


def print_memory(label: str, gpu: dict) -> None:
    print(f'{label}_FREE_GIB={gpu["free"] / GIB:.3f}', flush=True)


def snapshot(models: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for model in models:
        name = model.get('name') or model.get('model')
        if not isinstance(name, str) or not name or name in seen:
            raise RuntimeError('OLLAMA_LOADED_MODEL_NAME_INVALID_OR_DUPLICATED')
        seen.add(name)
        remaining = 600
        expiration = model.get('expires_at')
        if isinstance(expiration, str):
            try:
                expiry = dt.datetime.fromisoformat(expiration.replace('Z', '+00:00'))
                if expiry.tzinfo is not None:
                    delta = (expiry - dt.datetime.now(dt.timezone.utc)).total_seconds()
                    if delta > 365 * 86400:
                        remaining = -1
                    elif delta > 0:
                        remaining = max(1, round(delta))
            except (ValueError, OverflowError):
                pass
        length = model.get('context_length')
        if not isinstance(length, int) or isinstance(length, bool) or not 512 <= length <= 131072:
            length = None
        out.append({'name': name, 'context_length': length,
                    'keep_alive_seconds': remaining,
                    'original_size_vram': model.get('size_vram')})
    return out


def unload(model: dict) -> None:
    name = model['name']
    request_json('/api/generate', {'model': name, 'prompt': '',
                                   'stream': False, 'keep_alive': 0}, timeout=65)
    print(f'OLLAMA_MODEL_UNLOADED={name}', flush=True)


def restore(model: dict) -> None:
    name = model['name']
    ttl = model['keep_alive_seconds']
    args = {'model': name, 'prompt': '', 'stream': False,
            'keep_alive': -1 if ttl == -1 else f'{ttl}s'}
    if model['context_length'] is not None:
        args['options'] = {'num_ctx': model['context_length']}
    response = request_json('/api/generate', args, timeout=180)
    if response.get('done') is False:
        raise RuntimeError(f'OLLAMA_MODEL_RESTORE_NOT_DONE={name}')
    print(f'OLLAMA_MODEL_RESTORED={name} CTX={model["context_length"]}', flush=True)


def is_active_process(state: str) -> bool:
    return bool(state) and state[0] not in ('Z', 'X')


def active_candidate_processes(binary: Path) -> list[int]:
    """Protect the running candidate, including its automatic DynamoRIO wrapper."""
    expected = str(binary)
    active = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            line = (proc / 'stat').read_text().rsplit(') ', 1)[1]
            if not is_active_process(line):
                continue
            exe = os.readlink(proc / 'exe')
            base = Path(exe).name.lower()
            if exe == expected or (base in {'drrun', 'drrun.exe'} and
                                   expected.encode() in (proc / 'cmdline').read_bytes()):
                active.append(int(proc.name))
        except (OSError, ValueError, IndexError):
            pass
    return active


def run(args: argparse.Namespace) -> int:
    state = {'candidate': str(args.binary), 'state': 'preflight',
             'original_loaded_models': [], 'unloaded': False,
             'restored': False, 'observed_code': None, 'events': []}
    changed = []
    last_watch = 0.0
    exit_code = 0
    atomic_json(args.state, state)

    def save(stage: str, event: str = '') -> None:
        state['state'] = stage
        if event:
            state['events'].append(event)
        atomic_json(args.state, state)

    def unload_reloaded_models() -> None:
        nonlocal last_watch
        if not changed or time.monotonic() - last_watch < 20:
            return
        last_watch = time.monotonic()
        try:
            current = {x.get('name') or x.get('model') for x in models_loaded()}
            for model in changed:
                if model['name'] in current:
                    print(f'OLLAMA_RELOADED_WHILE_GAME_RUNNING={model["name"]}; unloading again',
                          flush=True)
                    unload(model)
                    save(state['state'], f'Model reloaded during game: {model["name"]}')
        except Exception as error:
            print(f'OLLAMA_VRAM_WATCH_WARNING={type(error).__name__}: {error}', flush=True)

    try:
        if active_candidate_processes(args.binary):
            raise RuntimeError('SHADPS4_ALREADY_RUNNING_NO_OLLAMA_CHANGES')
        before = gpu_memory()
        state['gpu_before'] = before
        print_memory('VRAM_BEFORE', before)
        target = args.minimum_gib * GIB
        if before['free'] < target:
            print('VRAM_BELOW_TARGET_TRY_OLLAMA_API_UNLOAD=YES', flush=True)
            available = models_loaded()
            state['original_loaded_models'] = snapshot(available)
            if not available:
                raise RuntimeError('LOW_VRAM_BUT_NO_LOADED_OLLAMA_MODELS; other process owns VRAM')
            save('unloading', 'Ollama models snapshotted before any changes')
            for model in state['original_loaded_models']:
                changed.append(model)
                unload(model)
            state['unloaded'] = True
            save('unloaded', 'Ollama models requested to unload')
            print('OLLAMA_SERVER_STOPPED=NO', flush=True)
            deadline = time.monotonic() + 70
            while time.monotonic() < deadline:
                current = gpu_memory()
                names = {x.get('name') or x.get('model') for x in models_loaded()}
                if current['free'] >= target and not any(m['name'] in names for m in changed):
                    break
                time.sleep(2)
            after = gpu_memory()
            state['gpu_after_unload'] = after
            print_memory('VRAM_AFTER_OLLAMA_UNLOAD', after)
            if after['free'] < target:
                raise RuntimeError(f'VRAM_STILL_BELOW_{args.minimum_gib}_GIB_AFTER_OLLAMA_UNLOAD')
        else:
            print('OLLAMA_UNLOAD_NOT_NEEDED=YES', flush=True)

        save('observing', 'Launching generic observer using ordinary shadPS4 flags')
        invocation = [sys.executable, '-B', str(args.observer), '--binary', str(args.binary),
                      '--sha256', args.sha256, '--game', args.game, '--cwd', str(args.cwd),
                      '--report', str(args.report), '--seconds', str(args.seconds)]
        observer = subprocess.Popen(invocation, stdin=subprocess.DEVNULL)
        while observer.poll() is None:
            unload_reloaded_models()
            time.sleep(POLL_INTERVAL)
        exit_code = observer.returncode
        state['observed_code'] = exit_code
        print(f'OBSERVATION_EXIT_CODE={exit_code}', flush=True)
    except BaseException as error:
        state['failure'] = f'{type(error).__name__}: {error}'
        print('VRAM_SESSION_ERROR=' + state['failure'], flush=True)
        exit_code = 10 if exit_code == 0 else exit_code
    finally:
        if changed:
            # The observer may stop observing without stopping the game.
            # Do not reload models until the real candidate and translator exit.
            # A GPU task stuck in uninterruptible kernel sleep may ignore
            # SIGKILL. Don't hang the user's SSH command indefinitely while
            # trying to restore memory that the still-live candidate owns.
            # Keep the model snapshot in the state file and return clearly.
            deadline = time.monotonic() + 90
            last_notice = 0.0
            while True:
                live = active_candidate_processes(args.binary)
                if not live:
                    break
                if state['state'] != 'waiting_for_game_exit':
                    save('waiting_for_game_exit',
                         'Deferring Ollama restoration until owned game exits')
                    print(f'OLLAMA_RESTORE_DEFERRED_GAME_RUNNING_PIDS={live}', flush=True)
                unload_reloaded_models()
                if time.monotonic() >= deadline:
                    save('restoration_deferred_unreaped_game',
                         f'Owned emulator remained alive after bounded wait: {live}')
                    state['restored'] = False
                    state['restore_pending'] = True
                    state['unreaped_game_pids'] = live
                    state['exit_code'] = 12
                    atomic_json(args.state, state)
                    print(f'OLLAMA_RESTORE_PENDING_GAME_STILL_ALIVE_PIDS={live}',
                          flush=True)
                    print('NO_UNRELATED_PROCESS_KILLED=YES',flush=True)
                    return 12
                if time.monotonic() - last_notice > 30:
                    print(f'WAITING_FOR_OWNED_GAME_EXIT_PIDS={live}',flush=True)
                    last_notice = time.monotonic()
                time.sleep(POLL_INTERVAL)
            save('restoring', 'Game fully exited, restoring original Ollama models')
            errors = []
            for model in changed:
                last_error = None
                for attempt in range(1, 4):
                    try:
                        restore(model)
                        last_error = None
                        break
                    except Exception as error:
                        last_error = error
                        print(f'OLLAMA_RESTORE_ATTEMPT_FAILED={model["name"]} '
                              f'ATTEMPT={attempt}: {type(error).__name__}: {error}', flush=True)
                        if attempt < 3:
                            time.sleep(3)
                if last_error is not None:
                    errors.append(f'{model["name"]}: {type(last_error).__name__}: {last_error}')
            try:
                loaded_now = {x.get('name') or x.get('model') for x in models_loaded()}
                errors.extend(f'Missing restored model {m["name"]}' for m in changed
                              if m['name'] not in loaded_now)
            except Exception as error:
                errors.append(f'Unable to verify restoration: {error}')
            state['restored'] = not errors
            state['restore_errors'] = errors
            save('complete' if not errors else 'restore_failed')
            print('OLLAMA_MODELS_RESTORED=' + ('YES' if not errors else 'NO'), flush=True)
            if errors:
                for error in errors:
                    print('OLLAMA_RESTORE_ERROR=' + error, flush=True)
                if exit_code == 0:
                    exit_code = 11
        else:
            state['restored'] = True  # no model was ever unloaded
            save('complete')
        state['exit_code'] = exit_code
        atomic_json(args.state, state)
        try:
            now = gpu_memory()
            print_memory('VRAM_AFTER_SESSION', now)
        except Exception:
            pass
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--observer', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--game', required=True)
    parser.add_argument('--cwd', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=240)
    parser.add_argument('--minimum-gib', type=int, default=8)
    args = parser.parse_args()
    if args.minimum_gib < 1 or not 45 <= args.seconds <= 360:
        parser.error('Invalid VRAM threshold or observer duration')
    if not args.binary.is_file() or args.binary.is_symlink():
        parser.error('Candidate emulator must be a regular file')
    if not args.observer.is_file() or args.observer.is_symlink():
        parser.error('Generic observer unavailable')
    if not re.fullmatch('[0-9a-f]{64}', args.sha256):
        parser.error('Invalid expected binary SHA256')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
