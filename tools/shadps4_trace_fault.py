#!/usr/bin/env python3
"""Trace one existing shadPS4 launch without changing the emulator or launcher."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

PREFIX = "@@SHADPS4_TRACE@@"
MIB = 1024 * 1024
HARNESS = r'''
#define _GNU_SOURCE
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>
static void *page;
static volatile sig_atomic_t high;
static void handler(int signal, siginfo_t *info, void *context) {
    if (info->si_addr == page) {
        ++high; mprotect(page, 4096, PROT_READ | PROT_WRITE);
        const char text[] = "HIGH_SIGNAL_DELIVERED\n";
        write(1, text, sizeof(text)-1); return;
    }
    if ((uintptr_t)info->si_addr == 0x28 && high == 1) {
        const char text[] = "LOW_SIGNAL_DELIVERED_AFTER_DETACH\n";
        write(1, text, sizeof(text)-1); _exit(42);
    }
    _exit(99);
}
int main(void) {
    struct sigaction action = {.sa_sigaction = handler, .sa_flags = SA_SIGINFO};
    sigemptyset(&action.sa_mask); sigaction(SIGSEGV, &action, NULL);
    page = mmap(NULL, 4096, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (page == MAP_FAILED) return 95;
    char start; if (read(0, &start, 1) != 1) return 91;
    volatile unsigned char value = *(volatile unsigned char *)page;
    volatile uintptr_t address = 0x28;
    __asm__ volatile("mov (%0), %%eax" : : "r"(address) : "eax", "memory");
    return value + 98;
}
'''

GDB_PYTHON = r'''
import gdb, json, os, re, signal, threading, time
from pathlib import Path
def event(name, **data):
    print("@@SHADPS4_TRACE@@" + json.dumps(dict(event=name, **data)), flush=True)
event('DEBUGGER', pid=os.getpid())
def inspect(pid, attached=False):
    proc = Path('/proc') / str(pid)
    status = dict(line.split(':', 1) for line in (proc/'status').read_text().splitlines())
    if int(status['Uid'].split()[0]) != cfg['uid']:
        raise RuntimeError('Candidate belongs to another user')
    if int(status['TracerPid']) != (os.getpid() if attached else 0):
        raise RuntimeError('Candidate already has another tracer')
    stat = (proc/'exe').stat()
    identity = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
    if identity != cfg['identity'] or not os.path.samefile(proc/'exe', cfg['binary']):
        raise RuntimeError('Executable identity changed or does not match')
    args = (proc/'cmdline').read_bytes().split(b'\0')
    if not cfg.get('test_pid') and not any(args[i:i+2] == [b'--game', cfg['game'].encode()] for i in range(len(args)-1)):
        raise RuntimeError('Candidate is not the requested game')
    raw = (proc/'stat').read_text()
    return (pid, raw[raw.rfind(')')+2:].split()[19], tuple(identity))
def command(text):
    try:
        gdb.execute(text)
        return True
    except gdb.error as exc:
        print('COMMAND_ERROR: ' + text + ': ' + str(exc), flush=True)
        return False
stop = threading.Event()
state = {'deadline': None, 'reason': None}
def watchdog():
    while not stop.wait(0.1):
        reason = ('Cancelled by user' if Path(cfg['cancel']).exists() else
                  'Capture deadline reached' if state['deadline'] and time.monotonic() >= state['deadline'] else None)
        if reason:
            state['reason'] = reason
            os.kill(os.getpid(), signal.SIGINT)  # Interrupt GDB itself, never signal the game directly.
            return
previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGCHLD})
try:
    threading.Thread(target=watchdog, daemon=True).start()
finally:
    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
fault_thread = None
try:
    for setting in ('set pagination off', 'set confirm off', 'set print thread-events off',
                    'set print elements 24', 'set print repeats 4', 'set print max-depth 3',
                    'handle SIGINT stop print nopass',
                    'handle SIGSEGV SIGILL SIGBUS SIGUSR1 SIGUSR2 SIGPIPE nostop noprint pass'):
        gdb.execute(setting)
    command('info signals SIGINT')
    command('info files')
    command('info functions scePadResetOrientation')
    if cfg.get('test_pid'):
        pid = cfg['test_pid']
        identity = inspect(pid)
    else:
        event('ARMED', game=cfg['game'], seconds=cfg['wait'])
        deadline = time.monotonic() + cfg['wait']
        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError('No new matching launch within the waiting deadline')
            latest = (Path(cfg['logs'])/'latest').resolve()
            try:
                if str(latest) != cfg['previous']:
                    meta = (latest/'session.meta').read_text()[:16384]
                    match = re.search(r'^launcher_pid=(\d+)$', meta, re.M)
                    if match:
                        pid = int(match[1]); identity = inspect(pid)
                        event('SESSION', path=str(latest), pid=pid)
                        break
            except (OSError, ValueError, RuntimeError):
                pass  # The launcher may still be executing its shell setup.
            time.sleep(0.05)
    if inspect(pid) != identity:
        raise RuntimeError('PID identity changed before attach')
    state['deadline'] = time.monotonic() + (20 if cfg.get('timeout_check') else cfg['capture'])
    gdb.execute('attach ' + str(pid))
    if inspect(pid, attached=True) != identity:
        raise RuntimeError('PID identity changed during attach')
    gdb.execute('catch signal SIGSEGV')
    number = gdb.breakpoints()[-1].number
    gdb.execute('condition %d (unsigned long)$_siginfo._sifields._sigfault.si_addr == %d' % (number, cfg['fault']))
    state['deadline'] = time.monotonic() + cfg['capture']
    event('ATTACHED', pid=pid)
    event('CONTINUING')
    gdb.execute('continue')
    if state['reason']:
        raise RuntimeError(state['reason'])
    if not gdb.selected_inferior().pid:
        raise RuntimeError('Process exited before the requested fault was captured')
    address = int(gdb.parse_and_eval('$_siginfo._sifields._sigfault.si_addr'))
    if int(gdb.parse_and_eval('$_siginfo.si_signo')) != signal.SIGSEGV or address != cfg['fault']:
        raise RuntimeError('Debugger stopped for a different event; see debugger.log')
    fault_thread = gdb.selected_thread()
    event('FAULT', address=hex(address), pc=str(gdb.parse_and_eval('$pc')))
    complete = True
    for text in ('p $_siginfo', 'info registers', 'info symbol $pc', 'x/16i $pc', 'x/96bx $pc-32',
                 'bt full 32', 'info proc mappings', 'info sharedlibrary', 'thread apply all bt 16'):
        complete = command(text) and complete
    if not complete:
        raise RuntimeError('A debugger command failed; partial capture retained')
    event('CAPTURE_COMPLETE')
except BaseException as exc:
    if cfg.get('timeout_check') and state['reason'] == 'Capture deadline reached':
        event('TIMEOUT_CHECK')
    else:
        event('ERROR', message=state['reason'] or str(exc) or type(exc).__name__)
finally:
    stop.set()
    if gdb.selected_inferior().pid:
        try:
            if fault_thread is not None and fault_thread.is_valid():
                fault_thread.switch()
                gdb.execute('queue-signal SIGSEGV')
            elif state['reason']:
                try:
                    interrupted = int(gdb.parse_and_eval('$_siginfo.si_signo')) == signal.SIGINT
                except gdb.error:
                    interrupted = False
                if interrupted:
                    gdb.execute('queue-signal 0')
            gdb.execute('detach')
            event('DETACHED')
        except BaseException as exc:
            event('DETACH_ERROR', message=str(exc))
event('DONE')
'''


def binary_info(path):
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
    identity = lambda st: [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]
    if identity(before) != identity(after) or identity(after) != identity(path.stat()):
        raise RuntimeError('Binary changed during hashing')
    return {'path': str(path.resolve()), 'sha256': digest, 'identity': identity(after)}


class DebuggerStillRunning(RuntimeError):
    def __init__(self, pid):
        self.pid = pid
        super().__init__(f'Debugger PID {pid} did not confirm exit after cancellation; capture files retained')


@contextmanager
def workspace(home, result):
    path = Path(tempfile.mkdtemp(prefix='shadps4-trace-', dir=home))
    try:
        yield path
    finally:
        if not result.get('unresolved_debugger_pid'):
            shutil.rmtree(path)


def run_gdb(gdb, prefix, config, report, label, start=None):
    cancel = report / (label + '.cancel')
    config = dict(config, cancel=str(cancel))
    script = report / (label + '.gdb')
    script.write_text('python\ncfg = ' + repr(config) + '\n' + GDB_PYTHON + '\nend\n')
    args = prefix + [gdb, '-q', '-nx', '-batch', '-iex', 'set auto-load off', '-iex',
                     'set debuginfod enabled off', '-x', str(script), config['binary']]
    events, deferred_error, pending, cancelled = [], None, b'', None
    deadline = time.monotonic() + 20  # Bound debugger startup before its own watchdog exists.
    unresolved = False
    with (report / (label + '.log')).open('w') as log:
        child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, process_group=0)
        with (report / (label + '.log')).open('rb') as reader:
            while True:
                try:
                    code = child.poll()
                    if time.monotonic() >= deadline and cancelled is None:
                        cancelled = time.monotonic(); cancel.touch()
                    if cancelled is not None and time.monotonic() - cancelled >= 8 and code is None:
                        unresolved = True
                        break
                    chunk = reader.read(65536); pending += chunk
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        if not line.startswith(PREFIX.encode()):
                            continue
                        item = json.loads(line[len(PREFIX):]); events.append(item)
                        if item['event'] in ('ARMED', 'ATTACHED'):
                            deadline = time.monotonic() + config['wait' if item['event'] == 'ARMED' else 'capture'] + 10
                        if item['event'] == 'ATTACHED' and start:
                            start()
                        if label == 'debugger':
                            if item['event'] == 'ARMED':
                                print(f"ARMED: launch {config['game']} normally from ES-DE / Moonlight (within {config['wait']} seconds).", flush=True)
                            elif item['event'] in ('ATTACHED', 'FAULT', 'DETACHED', 'ERROR', 'DETACH_ERROR'):
                                print(item['event'] + ': ' + json.dumps({k:v for k,v in item.items() if k != 'event'}), flush=True)
                    code = child.poll()
                    if code is not None and not chunk:
                        break
                    time.sleep(0.05)
                except (KeyboardInterrupt, Exception) as exc:
                    deferred_error = exc
                    if cancelled is None:
                        cancelled = time.monotonic(); cancel.touch()
                        print('Cancellation requested; waiting for the debugger to detach.', flush=True)
                    code = child.poll()
                    if code is not None:
                        break
    (report / (label + '-events.json')).write_text(json.dumps({'exit_code': code, 'events': events}, indent=2))
    if unresolved:
        raise DebuggerStillRunning(next((e['pid'] for e in events if e['event'] == 'DEBUGGER'), child.pid))
    names = {e['event'] for e in events}
    required = {'ATTACHED', 'CONTINUING', 'DETACHED'} | ({'TIMEOUT_CHECK'} if config.get('timeout_check') else {'FAULT', 'CAPTURE_COMPLETE'})
    if deferred_error or code or names & {'ERROR', 'DETACH_ERROR'} or not required <= names:
        raise RuntimeError(label + ' did not complete a fault capture and detach; see its log')
    return events


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', default='CUSA36843')
    parser.add_argument('--fault-address', type=lambda s: int(s, 0), default=0x28)
    parser.add_argument('--expected-sha', required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r'CUSA\d{5}', args.game) or not re.fullmatch(r'[a-fA-F0-9]{64}', args.expected_sha):
        parser.error('Use a CUSA title ID and a complete SHA-256 hash')
    home = Path.home(); binary = home / 'Applications/shadps4/shadps4'
    logs = home / '.local/state/shadps4-playtest-logs'
    result, events = {'game': args.game, 'fault_address': hex(args.fault_address)}, []
    print('Checking the installed binary and native debugger support...', flush=True)
    with workspace(home, result) as temporary:
        report = Path(temporary); fixture = None
        try:
            info = binary_info(binary); result['binary'] = info
            if info['sha256'] != args.expected_sha.lower():
                raise RuntimeError('Installed binary SHA-256 differs from the requested build; capture not started')
            gdb = shutil.which('gdb')
            if not gdb:
                raise RuntimeError('gdb is not installed; no host packages were changed')
            version = subprocess.run([gdb, '--version'], capture_output=True, text=True, timeout=10, check=True)
            (report/'gdb-version.txt').write_text(version.stdout + version.stderr)
            yama = Path('/proc/sys/kernel/yama/ptrace_scope')
            scope = int(yama.read_text()) if yama.exists() else 0
            result['ptrace_scope'] = scope
            if scope == 3:
                raise RuntimeError('ptrace_scope=3 prohibits tracing; system setting preserved')
            prefix = []
            if scope in (1, 2) and os.geteuid() != 0:
                print(f'Linux ptrace_scope={scope} requires elevated GDB to attach to the normal ES-DE launch. Sudo is used for GDB only.', flush=True)
                if subprocess.run(['sudo', '-n', 'true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                    with open('/dev/tty', 'r+') as tty:
                        subprocess.run(['sudo', '-v'], stdin=tty, stdout=tty, stderr=tty, check=True, timeout=120)
                prefix = ['sudo', '-n', '--']
            config = dict(binary=str(binary), identity=info['identity'], uid=os.getuid(), game=args.game,
                          fault=args.fault_address, logs=str(logs), previous=str((logs/'latest').resolve()), wait=120, capture=60)
            compiler = shutil.which('cc') or shutil.which('gcc')
            if not compiler:
                raise RuntimeError('No C compiler available for the harmless native debugger self-check; not armed')
            source = report/'native-check.c'; test_binary = report/'native-check'
            source.write_text(HARNESS)
            build = subprocess.run([compiler, '-O0', '-g', '-fno-omit-frame-pointer', '-o', str(test_binary), str(source)],
                                   capture_output=True, text=True, timeout=30)
            (report/'native-check-build.log').write_text(build.stdout + build.stderr)
            if build.returncode:
                raise RuntimeError('Native debugger self-check compilation failed; not armed')
            with (report/'native-check-runtime.log').open('wb') as output:
                fixture = subprocess.Popen([str(test_binary)], stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT)
                test = binary_info(test_binary)
                test_config = dict(config, binary=str(test_binary), identity=test['identity'], test_pid=fixture.pid, fault=0x28, capture=15)
                run_gdb(gdb, prefix, dict(test_config, capture=2, timeout_check=True), report, 'native-timeout')
                status = dict(line.split(':', 1) for line in (Path('/proc')/str(fixture.pid)/'status').read_text().splitlines())
                if fixture.poll() is not None or int(status['TracerPid']) != 0:
                    raise RuntimeError('Debugger timeout self-check did not leave the fixture alive and untraced; not armed')
                run_gdb(gdb, prefix, test_config, report, 'native-check', lambda: (fixture.stdin.write(b'go\n'), fixture.stdin.flush()))
                fixture_code = fixture.wait(timeout=5)
            if fixture_code != 42 or b'LOW_SIGNAL_DELIVERED_AFTER_DETACH' not in (report/'native-check-runtime.log').read_bytes():
                raise RuntimeError('Native self-check did not preserve the original signal handler; not armed')
            result['native_self_check'] = 'passed'
            print('Native debugger checks passed: timeout detached safely, ordinary faults passed through, and the captured fault resumed after detach.', flush=True)
            config['previous'] = str((logs/'latest').resolve())
            events = run_gdb(gdb, prefix, config, report, 'debugger')
            result['status'] = 'fault_captured'
        except BaseException as exc:
            result['status'] = 'incomplete'; result['error'] = str(exc) or type(exc).__name__
            if isinstance(exc, DebuggerStillRunning):
                result['unresolved_debugger_pid'] = exc.pid
                result['retained_work_directory'] = str(report)
            print('CAPTURE_INCOMPLETE: ' + result['error'], flush=True)
        finally:
            if fixture is not None and fixture.poll() is None:
                try:
                    fixture.terminate(); fixture.wait(timeout=3)  # Only our disposable self-check process.
                except subprocess.TimeoutExpired:
                    try:
                        fixture.kill(); fixture.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        result['self_check_cleanup_error'] = str(exc)
                except OSError as exc:
                    result['self_check_cleanup_error'] = str(exc)
            if (report/'debugger-events.json').exists():
                events = json.loads((report/'debugger-events.json').read_text())['events']
            for name, command in (('head', ['rev-parse', 'HEAD']), ('status', ['status', '--porcelain', '--untracked-files=no'])):
                try:
                    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
                    got = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(home/'.cache/shadps4-clean-verified-main/source')] + command,
                                         capture_output=True, timeout=10, env=env)
                    (report/('source-' + name + '.txt')).write_bytes(got.stdout + got.stderr)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    result['source_' + name + '_error'] = str(exc)
            session = next((e['path'] for e in events if e['event'] == 'SESSION'), str((logs/'latest').resolve()))
            result['session'] = session
            for name in ('session.meta', 'runtime.log'):
                try:
                    with (Path(session)/name).open('rb') as source:
                        size = os.fstat(source.fileno()).st_size
                        source.seek(max(0, size - 16*MIB))
                        data = (b'[EARLIER LOG BYTES OMITTED; FINAL 16 MiB]\n' if size > 16*MIB else b'') + source.read(min(size, 16*MIB))
                    (report/name).write_bytes(data)
                except OSError as exc:
                    result[name + '_error'] = str(exc)
            (report/'result.json').write_text(json.dumps(result, indent=2))
            stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
            descriptor, path = tempfile.mkstemp(prefix='shadps4-rdr-trace-' + stamp + '-', suffix='.tar.gz', dir=home)
            os.close(descriptor)
            with tarfile.open(path, 'w:gz') as archive:
                for item in report.iterdir():
                    if item.name != 'native-check':
                        archive.add(item, arcname='shadps4-rdr-trace/' + item.name)
            print('ARCHIVE=' + path, flush=True)
    return 0 if result.get('status') == 'fault_captured' else 2


if __name__ == '__main__':
    raise SystemExit(main())
