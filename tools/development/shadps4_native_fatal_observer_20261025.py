#!/usr/bin/env python3
"""One-session generic shadPS4 fatal-call-stack recorder.

Uses the exact freshly built *native* emulator CPU identity path and only
the standard fullscreen/FPS options. No DynamoRIO or alternative CPU runtime. It does not patch/replace/skip guest
instructions, alter game files, or modify the emulator source. GDB observes a
genuine fatal call to unreachable_impl(), after regular signal handling.
The debugged process group is stopped at 120 seconds at the latest
(with a few seconds reserved for orderly owned-process cleanup).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + '\n')


def preexisting_emulators() -> list[int]:
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            state = (proc / 'stat').read_text().rsplit(') ', 1)[1][0]
            if state in ('Z', 'X'):
                continue
            exe = Path(os.readlink(proc / 'exe')).name
            if exe.startswith('shadps4'):
                found.append(int(proc.name))
        except (OSError, ValueError, IndexError):
            continue
    return found


def gdb_commands() -> str:
    return '''
set confirm off
set pagination off
set print pretty on
set print frame-arguments all
set print elements 80
set print repeats 3
set debuginfod enabled off
set breakpoint pending on
set follow-exec-mode same
set follow-fork-mode child
set detach-on-fork off
# No external CPU runtime or intermediate exec. Native CPU trapping stays in
# the emulator and this breakpoint remains valid in the linked ELF.
handle SIGSEGV nostop noprint pass
handle SIGBUS nostop noprint pass
handle SIGILL nostop noprint pass
handle SIGSYS nostop noprint pass
break unreachable_impl
commands
  silent
  printf "\\n=== REAL_FATAL_CALL_STACK_BEGIN ===\\n"
  bt full 48
  printf "\\n=== ALL_THREADS_BACKTRACE_BEGIN ===\\n"
  thread apply all bt 8
  printf "\\n=== FATAL_REGISTER_CONTEXT ===\\n"
  info registers
  printf "\\n=== REAL_FATAL_CALL_STACK_END ===\\n"
  quit 0
end
run
# If GDB stops on a fatal trap without reaching the breakpoint, capture the
# actual caller stack at that stop instead of exiting with no trace.
printf "\\n=== NATIVE_SIGTRAP_FALLBACK_STACK_BEGIN ===\\n"
info program
bt full 48
thread apply all bt 8
info registers
info proc mappings
printf "\\n=== NATIVE_SIGTRAP_FALLBACK_STACK_END ===\\n"
quit 0
'''


def terminate_own_process_group(child: subprocess.Popen, output: Path) -> None:
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=7)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)
    with output.open('a') as stream:
        stream.write('\nOBSERVER_ENDED_OWN_GDB_PROCESS_GROUP_AFTER_120_SECONDS=YES\n')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--game', required=True, help='Ordinary game ID or executable path')
    parser.add_argument('--cwd', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=120)
    args = parser.parse_args()

    if args.seconds != 120:
        parser.error('The generic fatal-capture test has a strict two-minute limit')
    if not args.game or args.game.startswith('-') or '\x00' in args.game:
        parser.error('Invalid ordinary game selector')
    if not re.fullmatch('[0-9a-f]{64}', args.sha256):
        parser.error('Invalid candidate ELF SHA256')
    if not shutil.which('gdb'):
        parser.error('GDB not installed; no game started')
    if not args.binary.is_file() or args.binary.is_symlink():
        parser.error('Expected regular emulator binary is unavailable')
    if args.binary.resolve() != args.binary:
        parser.error('The pinned emulator must have an absolute canonical path')
    if file_sha256(args.binary) != args.sha256:
        parser.error('Candidate ELF hash differs from reviewed crash build')
    if not args.cwd.is_dir() or not (args.cwd/'user').is_dir():
        parser.error('Private portable game profile missing')
    other = preexisting_emulators()
    if other:
        parser.error(f'Another shadPS4 instance is running: {other}')
    out = args.report
    out.mkdir(parents=True, exist_ok=False)
    commands = out / 'gdb-commands.txt'
    commands.write_text(gdb_commands())
    log_file = out / 'gdb-fatal-trace.log'

    # Preserves the same standard game launch arguments in the actual emulator.
    game_command = [str(args.binary), args.game, '--fullscreen', 'true', '--show-fps']
    debugger_command = ['gdb', '--nx', '--quiet', '--batch', '-x', str(commands), '--args',
                        *game_command]
    save(out / 'launch-command.json',
         {'emulator_argv': game_command,
          'debugger_argv': debugger_command,
          'cpu_identity': 'native emulator-owned CPUID/RDTSCP/RDPID - no translator',
          'memory_tracking': 'normal global setting',
          'gamepad_input': 'user only', 'process_duration_limit_seconds': 120})

    env = os.environ.copy()
    # Remove old test-only overrides without adding replacement compatibility modes.
    for key in list(env):
        if key.startswith(('SHADPS4_GOW_', 'SHADPS4_GOT_', 'SHADPS4_CPU_ID_')):
            env.pop(key, None)
    if not env.get('XDG_RUNTIME_DIR'):
        env['XDG_RUNTIME_DIR'] = f'/run/user/{os.getuid()}'
    if not env.get('DBUS_SESSION_BUS_ADDRESS'):
        env['DBUS_SESSION_BUS_ADDRESS'] = (
            f'unix:path={env["XDG_RUNTIME_DIR"]}/bus')

    print('REAL_GDB_FATAL_STACK_TEST=STARTED', flush=True)
    print('EMULATOR_COMPATIBILITY_FLAGS=NONE; STANDARD_FULLSCREEN_AND_FPS=YES', flush=True)
    print('TWO_MINUTE_HARD_GAME_PROCESS_LIMIT=YES', flush=True)
    start = time.monotonic()
    child = None
    reason = 'not_started'
    try:
        with log_file.open('wb', buffering=0) as log:
            child = subprocess.Popen(
                debugger_command, cwd=args.cwd, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
            print(f'GDB_PROCESS_GROUP_PID={child.pid}', flush=True)
            try:
                child.wait(timeout=args.seconds)
                reason = 'debugger_process_exited'
            except subprocess.TimeoutExpired:
                reason = 'two_minute_deadline'
    except Exception as error:
        reason = f'failed_to_launch_debugger_{type(error).__name__}: {error}'
        print('GDB_OBSERVER_ERROR=' + reason, flush=True)
    finally:
        if child is not None:
            terminate_own_process_group(child, log_file)

    result = {'elapsed_seconds': round(time.monotonic()-start,2),
              'reason': reason, 'debugger_returncode': None if child is None else child.poll(),
              'candidate_sha256': args.sha256, 'modified_emulator_source': False,
              'emulator_compatibility_options': [],
              'normal_display_options': ['--fullscreen', 'true', '--show-fps'],
              'hard_limit_seconds': 120}
    if log_file.is_file():
        raw = log_file.read_bytes()
        result['fatal_breakpoint_reached'] = b'REAL_FATAL_CALL_STACK_BEGIN' in raw
        result['fatal_breakpoint_completed'] = b'REAL_FATAL_CALL_STACK_END' in raw
        result['fatal_sigtrap_fallback_completed'] = (
            b'NATIVE_SIGTRAP_FALLBACK_STACK_BEGIN' in raw and
            b'NATIVE_SIGTRAP_FALLBACK_STACK_END' in raw)
        result['fatal_stack_complete'] = (
            result['fatal_breakpoint_completed'] or
            (result['fatal_sigtrap_fallback_completed'] and
             b'SIGTRAP' in raw and b'Unhandled access violation' in raw))
        result['debugger_trace_bytes'] = len(raw)
        interesting = []
        lines = raw.decode('utf-8','replace').splitlines()
        for index,line in enumerate(lines):
            if any(item in line for item in ('REAL_FATAL_CALL_STACK_BEGIN', 'REAL_FATAL_CALL_STACK_END',
                                           'shadps4.cpp:', 'fmt::', 'SignalHandler',
                                           'unreachable_impl', 'SIGSEGV', 'NATIVE_SIGTRAP_FALLBACK_STACK_BEGIN',
                                           'NATIVE_SIGTRAP_FALLBACK_STACK_END', 'SIGTRAP',
                                           'No symbol table',
                                           'Could not insert breakpoint', 'ptrace:',
                                           'Warning:', 'warning:')):
                interesting.append(line[:500])
        (out/'gdb-selected-stack-lines.txt').write_text(
            '\n'.join(interesting[:150]) + '\n', encoding='utf-8')
        # Keep the first and last 256 KiB only; no huge terminal dump.
        if len(raw) > 512*1024:
            (out/'gdb-fatal-trace.log').write_bytes(
                raw[:256*1024] + b'\n...[middle omitted]...\n' + raw[-256*1024:])
    save(out/'fatal-result.json', result)
    print('FATAL_BACKTRACE_CAPTURED=' +
          ('YES' if result.get('fatal_stack_complete') else 'NO'), flush=True)
    print(f'GDB_REPORT={out}', flush=True)
    print('GDB_RETURN_CODE=' + str(result['debugger_returncode']), flush=True)
    print('EMULATOR_PROCESS_GROUP_CLEANED_UP=YES', flush=True)
    print('OLLAMA_RELOAD_SAFE_AFTER_PROCESS_EXIT=YES', flush=True)
    return 0 if result.get('fatal_stack_complete') else 1


if __name__ == '__main__':
    raise SystemExit(main())
