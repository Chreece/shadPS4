#!/usr/bin/env python3
"""GoT: capture both native fatal stops and UMR, without relying on a hang callback.

Extension of the SHA-pinned, already verified live broker. No emulator build or
patch, no new privileged command shapes beyond two fixed wave-only checkpoints.
The game and GDB remain unprivileged. GPU waves are never explicitly halted.
"""
from __future__ import annotations
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import time

BASE_SHA = '2ffdc34ffc17f15331c0a26f4c3117831adcca11977462c95a3ab81554fd0fe0'
LABEL = 'got-umr-dual-visual'
TRIGGER = 'Compiling graphics pipeline 0x8fde0b626b436bc4'
MAX_CONSOLE = 24 * 1024 * 1024
CHECKPOINTS = ('pipeline', 'fatal')


def load_base():
    path = Path(__file__).resolve().with_name('got-umr-live-capture.py')
    if not path.is_file() or path.is_symlink():
        raise RuntimeError('Pinned live helper missing: ' + str(path))
    if hashlib.sha256(path.read_bytes()).hexdigest() != BASE_SHA:
        raise RuntimeError('Existing live helper changed; refusing an unknown privileged backend')
    spec = importlib.util.spec_from_file_location('got_umr_verified_backend', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_maps(text: str) -> list[dict]:
    records = []
    for line in text.splitlines():
        fields = line.split(None, 5)
        if len(fields) < 5:
            continue
        try:
            start, end = (int(v, 16) for v in fields[0].split('-'))
            offset = int(fields[2], 16)
        except ValueError:
            continue
        records.append({'start': start, 'end': end, 'file_offset': offset,
                        'permissions': fields[1], 'path': fields[5] if len(fields) == 6 else ''})
    return records


def classify_wave(answer: dict) -> dict:
    text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', answer.get('output', ''))
    pcs = re.findall(r'(?:ix|reg)?SQ_WAVE_PC_LO\s*[:=]\s*(?:0x)?([0-9a-f]{8})\b', text, re.I)
    errors = [line for line in text.splitlines() if re.search(
        r'\berror\b|failed|unsupported|not supported|permission denied|invalid argument|no such file',
        line, re.I)]
    if not answer.get('ok') or answer.get('exit_code') not in (0, None) or errors:
        verdict = 'ERROR_OR_TIMEOUT'
    elif any(int(value, 16) != 0 for value in pcs):
        verdict = 'WAVE_PC_PRESENT_NOT_YET_ATTRIBUTED_TO_GOT'
    else:
        verdict = 'NO_USABLE_PC_INCONCLUSIVE'
    return {'verdict': verdict, 'pc_records': len(pcs), 'errors': errors[:12],
            'game_fault_identified': False}


# Executed by GDB's Python interpreter. It records the native context before any
# blocking GPU query, including the interrupted frame under a signal trampoline.
GDB_BODY = r'''
import gdb, hashlib, json, os, socket, struct, time
from pathlib import Path
out = Path(SESSION) / 'native'
out.mkdir(exist_ok=True)
event = {'reason': 'UNKNOWN'}

def write(name, obj):
    (out / name).write_text(json.dumps(obj, indent=2) + '\n')

def stop(e):
    if isinstance(e, gdb.SignalEvent):
        event.update(reason='SIGNAL', signal=e.stop_signal)
    elif isinstance(e, gdb.BreakpointEvent):
        event.update(reason='BREAKPOINT', breakpoints=[b.number for b in e.breakpoints])
    else:
        event.update(reason=type(e).__name__)

def exited(e):
    event.update(reason='EXITED', exit_code=getattr(e, 'exit_code', None))

def thread_started(e):
    pid = gdb.selected_inferior().pid
    if pid:
        write('inferior.json', {'pid': pid, 'binary': gdb.current_progspace().filename})

def execute(command):
    try:
        return gdb.execute(command, to_string=True)
    except Exception as exc:
        return 'COMMAND_FAILED ' + command + ': ' + str(exc) + '\n'

def gpu_checkpoint():
    answer = None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(35)
            s.connect(str(Path(SESSION) / 'broker.sock'))
            request = {'op': 'umr', 'args': ['--got-checkpoint', 'fatal']}
            s.sendall(json.dumps(request).encode())
            s.shutdown(socket.SHUT_WR)
            data = bytearray()
            while True:
                part = s.recv(65536)
                if not part:
                    break
                data.extend(part)
                if len(data) > 16 * 1024 * 1024:
                    raise RuntimeError('GPU reply exceeded limit')
            answer = json.loads(data)
    except Exception as exc:
        answer = {'ok': False, 'error': str(exc)}
    (Path(SESSION) / 'fatal.json').write_text(json.dumps(answer, indent=2) + '\n')

def dump_native():
    inf = gdb.selected_inferior()
    meta = dict(event, pid=inf.pid, current_thread=str(gdb.selected_thread()),
                gpu_wave_halt_requested=False, frames=[])
    if not inf.pid or not inf.threads():
        write('stop.json', meta)
        return False
    try:
        maps = Path('/proc/%d/maps' % inf.pid).read_text()
        (out / 'maps-at-stop.txt').write_text(maps)
    except Exception as exc:
        maps = ''
        meta['maps_error'] = str(exc)
    frame = gdb.newest_frame()
    restore = frame
    after_signal = False
    for level in range(48):
        if frame is None:
            break
        row = {'level': level}
        try:
            pc = int(frame.pc())
            row.update(pc=hex(pc), name=frame.name(), frame_type=frame.type(),
                       module=gdb.solib_name(pc), interrupted_frame=after_signal)
            for line in maps.splitlines():
                fields = line.split(None, 5)
                if len(fields) < 5:
                    continue
                lo, hi = [int(x, 16) for x in fields[0].split('-')]
                if lo <= pc < hi:
                    offset = int(fields[2], 16)
                    row.update(mapping=line, mapping_relative_pc=hex(pc-lo),
                               object_relative_pc=hex(pc-lo+offset))
                    break
            if level == 0 or after_signal:
                frame.select()
                (out / ('frame-%02d-registers.txt' % level)).write_text(execute('info registers'))
                (out / ('frame-%02d-instructions.txt' % level)).write_text(execute('x/16i $pc'))
                try:
                    row['instruction_bytes_32'] = bytes(inf.read_memory(pc, 32)).hex()
                except Exception as exc:
                    row['instruction_bytes_error'] = str(exc)
            after_signal = frame.type() == gdb.SIGTRAMP_FRAME
            older = frame.older()
        except Exception as exc:
            row['error'] = str(exc)
            meta['frames'].append(row)
            break
        meta['frames'].append(row)
        frame = older
    if restore:
        restore.select()
    # Persist first: if a GPU read hangs or the enclosing time limit expires,
    # the native fault address/module/stack are already on disk.
    (out / 'fault-thread-bt.txt').write_text(execute('bt 48'))
    (out / 'shared-libraries.txt').write_text(execute('info sharedlibrary'))
    write('stop.json', meta)
    print('GOT_DUAL_NATIVE_CONTEXT_SAVED reason=' + str(meta.get('reason')), flush=True)
    gpu_checkpoint()
    (out / 'all-threads.txt').write_text(execute('thread apply all bt 18'))
    return True

for command in ('set pagination off', 'set confirm off', 'set print elements 32',
                'set print frame-arguments scalars', 'set print thread-events off',
                'set disable-randomization off', 'set auto-load off',
                'handle SIGSEGV nostop noprint pass', 'handle SIGBUS nostop noprint pass',
                'handle SIGILL nostop noprint pass', 'handle SIGUSR1 nostop noprint pass',
                'handle SIGABRT stop print pass'):
    gdb.execute(command)
# Never initiate downloads during this capture.
execute('set debuginfod enabled off')
gdb.events.stop.connect(stop)
gdb.events.exited.connect(exited)
gdb.events.new_thread.connect(thread_started)
try:
    bp = gdb.Breakpoint('assert_fail_impl', internal=True)
    if bp.pending:
        raise RuntimeError('Fatal assertion breakpoint unresolved; no game run')
    print('GOT_DUAL_NATIVE_BREAKPOINT=ARMED_BEFORE_SHUTDOWN', flush=True)
    gdb.execute('run')
    dump_native()
except Exception as exc:
    write('gdb-error.json', {'error': str(exc), 'event': event})
    print('GOT_DUAL_GDB_ERROR=' + str(exc), flush=True)
finally:
    # Only the inferior owned by this debugger, never an unrelated process.
    inf = gdb.selected_inferior()
    if inf.pid and inf.threads():
        execute('kill')
    print('GOT_DUAL_DEBUGGER_FINISHED', flush=True)
'''


def gdb_source(session: Path) -> str:
    return 'SESSION = ' + repr(str(session)) + '\n' + GDB_BODY


def supervise(base, session: Path, binary: Path, arguments: list[str]) -> int:
    """Run the unchanged game through GDB, trigger one early UMR scan from its log."""
    if os.geteuid() == 0:
        raise RuntimeError('Game/debugger must not run as root')
    project = session.parent.parent
    if session.parent != project / 'tools' or not session.name.startswith('umr-session-'):
        raise RuntimeError('Invalid diagnostic session')
    if binary != Path.cwd() / 'shadps4' or not binary.resolve().is_relative_to(project / 'tests'):
        raise RuntimeError('Not the isolated portable game binary')
    if not base.regular(binary) or base.sha(binary) != base.GAME_SHA:
        raise RuntimeError('Portable v37 executable hash mismatch')
    expected = ['--cpu-id-mode=native', '--game', '/mnt/roms-all/ps4-games/CUSA11456/eboot.bin',
                '--fullscreen', 'true']
    if arguments != expected:
        raise RuntimeError('Game arguments differ from the preserved v37 test')
    native = session / 'native'
    native.mkdir(exist_ok=True)
    payload = session / 'native-stop.py'
    payload.write_text(gdb_source(session))
    command = [shutil.which('gdb') or '/usr/bin/gdb', '-q', '-nx', '--batch',
               '-x', str(payload), '--args', str(binary)] + arguments
    base.store(native / 'invocation.json', {'args': command, 'binary_sha256': base.GAME_SHA,
                                          'started': base.now(), 'cpu_paused_only_on_fatal_stop': True})
    env = dict(os.environ, DEBUGINFOD_URLS='', LC_ALL='C')
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, env=env, start_new_session=True)
    cancelled = False
    def cancel(_sig, _frame):
        nonlocal cancelled
        cancelled = True
    old_handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = None
    trigger = {'trigger_seen': False, 'marker': TRIGGER}
    start = time.monotonic()
    tail = ''
    written = 0
    last_maps = 0.0
    def checkpoint():
        answer = base.rpc(session, {'op': 'umr', 'args': ['--got-checkpoint', 'pipeline']})
        base.store(session / 'pipeline.json', answer)
        return answer
    try:
        with selectors.DefaultSelector() as select, (native / 'debugger-console.log').open('wb') as log:
            select.register(process.stdout, selectors.EVENT_READ)
            while not cancelled and time.monotonic() - start < 105:
                events = select.select(0.1)
                for key, _ in events:
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        select.unregister(key.fileobj)
                        continue
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
                    if written < MAX_CONSOLE:
                        log.write(data[:MAX_CONSOLE-written]); log.flush()
                        written += len(data[:MAX_CONSOLE-written])
                    tail = (tail + data.decode(errors='replace'))[-4096:]
                    if not trigger['trigger_seen'] and TRIGGER in tail:
                        trigger.update(trigger_seen=True, detected=base.now(),
                                       reason='LAST_KNOWN_PIPELINE_BEFORE_HOST_FAULT')
                        future = pool.submit(checkpoint)
                        base.store(native / 'pipeline-trigger.json', trigger)
                # Keep a recent ASLR map even if the process dies without reaching
                # the assertion hook; no memory contents or profiles are copied.
                if time.monotonic() - last_maps >= 0.5:
                    last_maps = time.monotonic()
                    pidfile = native / 'inferior.json'
                    try:
                        inf = json.loads(pidfile.read_text())
                        pid = int(inf['pid'])
                        exe = Path('/proc') / str(pid) / 'exe'
                        if exe.resolve() == binary.resolve():
                            (native / 'maps-last.txt').write_text((exe.parent / 'maps').read_text())
                    except (OSError, ValueError, KeyError):
                        pass
                if process.poll() is not None and not select.get_map():
                    break
        if process.poll() is None:
            cancelled = True
        if future and not cancelled:
            wait_until = time.monotonic() + 35
            while not future.done() and not cancelled and time.monotonic() < wait_until:
                time.sleep(0.1)
            try:
                trigger['wave_summary'] = classify_wave(future.result(timeout=0))
            except Exception as exc:
                trigger['capture_error'] = str(exc) or 'Checkpoint reply not ready'

    finally:
        if process.poll() is None:
            # The debugger and its children have a NEW session created above.
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        if process.stdout is not None:
            process.stdout.close()
        pool.shutdown(wait=False, cancel_futures=True)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        trigger.update(debugger_exit=process.poll(), deadline_or_cancellation=cancelled,
                       finished=base.now())
        base.store(native / 'pipeline-trigger.json', trigger)
    # Report a diagnostic outcome, never call GDB's exit=0 successful gameplay.
    try:
        stop = json.loads((native / 'stop.json').read_text())
    except (OSError, ValueError):
        stop = {}
    modules = []
    paths = sorted({row.get('module') for row in stop.get('frames', []) if row.get('module')})
    for name in paths[:12]:
        p = Path(name)
        try:
            if not p.is_file() or p.stat().st_size > 200 * 1024 * 1024:
                continue
            item = {'path': str(p), 'resolved_path': str(p.resolve()), 'sha256': base.sha(p)}
            if shutil.which('readelf'):
                notes = subprocess.run(['readelf', '-n', str(p)], capture_output=True,
                                       text=True, timeout=5).stdout
                matches = re.findall(r'Build ID: ([0-9a-f]+)', notes)
                item['build_ids'] = matches
            modules.append(item)
        except (OSError, subprocess.TimeoutExpired) as exc:
            modules.append({'path': str(p), 'error': str(exc)})
    base.store(native / 'modules.json', modules)
    result = {'kind': 'DEBUGGER_CONTROLLED_RUN', 'debugger_exit': process.poll(),
              'native_stop_reason': stop.get('reason'), 'signal': stop.get('signal'),
              'gameplay_verified': False, 'finished': base.now()}
    base.store(native / 'result.json', result)
    return 124 if cancelled else 0 if stop else 2


def install_hooks(base):
    old_kind, old_args, old_transform, old_gather = (
        base.command_kind, base.umr_args, base.transform, base.gather)
    def command_kind(args):
        if (isinstance(args, list) and len(args) == 2 and args[0] == '--got-checkpoint'
                and isinstance(args[1], str) and args[1] in CHECKPOINTS):
            return args[1], 'gfx'
        return old_kind(args)
    def umr_args(binary, database, kind, ring):
        return old_args(binary, database, 'waves' if kind in CHECKPOINTS else kind, ring)
    def transform(source, script, session):
        text = old_transform(source, script, session)
        old = '      "$ready/shadps4" --cpu-id-mode=native --game "$GAME" --fullscreen true \\\n'
        new = ('      /usr/bin/python3 ' + shlex.quote(str(script)) + ' supervise ' +
               shlex.quote(str(session)) + ' "$ready/shadps4" --cpu-id-mode=native --game "$GAME" --fullscreen true \\\n')
        if text.count(old) != 1:
            raise RuntimeError('Actual v37 game command changed; no launch')
        text = text.replace(old, new)
        text = text.replace('timeout --signal=TERM --kill-after=5s 75s',
                            'timeout --signal=TERM --kill-after=8s 115s')
        text = text.replace('--property=RuntimeMaxSec=140s', '--property=RuntimeMaxSec=170s')
        text = text.replace('got-umr-live-portable-ready-', 'got-umr-dual-portable-ready-')
        text = text.replace("echo 'GAMEPLAY_LIMIT=75s'", "echo 'DEBUGGER_RUN_LIMIT=115s'")
        text = text.replace("echo 'UMR_CALLBACK=ARMED'", "echo 'UMR_TRIGGERS=PIPELINE+FATAL+RADV'")
        # Avoid representing GDB's exit status as an unobserved guest exit status.
        text = text.replace("printf 'GAME_EXIT_%s\\n' \"$game_rc\"", "printf 'DEBUGGER_EXIT_%s\\n' \"$game_rc\"")
        text = text.replace('echo "game_exit=$game_rc"', 'echo "debugger_wrapper_exit=$game_rc"')
        return text
    def gather(session, report):
        code = old_gather(session, report)
        results = {}
        for kind in (*CHECKPOINTS, 'waves', 'ring'):
            path = session / (kind + '.json')
            if base.regular(path) and path.stat().st_size <= base.MAX_REPLY:
                if kind in CHECKPOINTS:
                    shutil.copyfile(path, report / 'logs' / ('umr-' + kind + '.json'))
                if kind != 'ring':
                    results[kind] = classify_wave(json.loads(path.read_text()))
        directory = session / 'native'
        if directory.is_dir() and not directory.is_symlink():
            for p in sorted(directory.iterdir()):
                if base.regular(p) and p.suffix in ('.txt', '.json', '.log') and p.stat().st_size <= MAX_CONSOLE:
                    shutil.copyfile(p, report / 'logs' / ('native-' + p.name))
        base.store(report / 'info' / 'dual-capture.json', {'wave_results': results,
            'gameplay_verified': False, 'zero_captures_is_not_success': True})
        return code
    base.command_kind, base.umr_args = command_kind, umr_args
    base.transform, base.gather = transform, gather
    base.LABEL = LABEL
    # Base.main's detached broker/runner must dispatch through this extended entry.
    base.__file__ = str(Path(__file__).resolve())


def preflight_gdb(base):
    if not shutil.which('gdb'):
        raise RuntimeError('GDB missing; no game scheduled')
    project = Path.home() / 'projects/shadps4/GhostOfTsushima'
    binary = project / 'tests/got-v37-guest-image-safety-20261013/build/shadps4'
    if not base.regular(binary) or base.sha(binary) != base.GAME_SHA:
        raise RuntimeError('Preserved v37 binary changed')
    command = ['gdb', '-q', '-nx', '--batch', '-ex', 'set auto-load off',
               '-ex', 'set debuginfod enabled off', '-ex',
               'python import socket,json; print("GOT_GDB_PYTHON_OK")',
               '-ex', 'info address assert_fail_impl', '--args', str(binary)]
    run = subprocess.run(command, capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, DEBUGINFOD_URLS=''))
    output = run.stdout + run.stderr
    if run.returncode or 'GOT_GDB_PYTHON_OK' not in output or not re.search(r'0x[0-9a-fA-F]+', output):
        raise RuntimeError('GDB Python/fatal-symbol preflight failed (game not started): ' + output[-1500:])
    print('GDB_PYTHON_AND_FATAL_SYMBOL=VERIFIED; GAME_NOT_STARTED_YET', flush=True)


def main():
    base = load_base()
    install_hooks(base)
    args = sys.argv[1:]
    if not args:
        new = Path.home() / 'projects/shadps4/GhostOfTsushima/scripts' / (LABEL + '.sh')
        if base.regular(new):
            base.status(Path.home()); return 0
        preflight_gdb(base)
        return base.main()
    if args == ['status']:
        base.status(Path.home()); return 0
    if args[0] == 'broker' and len(args) == 3:
        return base.broker(int(args[1]), Path(args[2]))
    if args[0] == 'client' and len(args) >= 3:
        return base.client(Path(args[1]), args[2:])
    if args[0] == 'gather' and len(args) == 3:
        return base.gather(Path(args[1]), Path(args[2]))
    if args[0] == 'ping' and len(args) == 2:
        answer = base.rpc(Path(args[1]), {'op': 'ping'})
        print(json.dumps(answer)); return 0 if answer.get('ok') else 1
    if args[0] == 'supervise' and len(args) >= 4:
        return supervise(base, Path(args[1]), Path(args[2]), args[3:])
    raise RuntimeError('Invalid helper arguments')


if __name__ == '__main__':
    os.umask(0o077)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        sys.exit(1)
