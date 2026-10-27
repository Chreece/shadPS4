#!/usr/bin/env python3
"""Single guarded inspection of the actual faulting RADV CPU register frame.

No emulator source modifications or recompilation. Stops on the native fatal
handler before shutdown, preserving the original GPU thread backtrace.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import traceback

HOME = Path.home()
SCRIPT = HOME / 'projects/shadps4/scripts/got-c925-integrated-native-ebea63b.py'
SCRIPT_BLOB = '5ec32ffdccd7e2a4dba29b56f8fe8f53343b348b'
GAME_ELF_SHA = 'ed9cda903dd3094a7ac0c0edb845fa86e7eeb56480c7b4a0444540ecbeeb4022'
PREVIOUS_HEADER_SHA = 'fd5e730ab62b37062d7abdcd52f6c897e65f9f33893ea480490008f1875e8a33'


def original_runner():
    if not SCRIPT.is_file() or SCRIPT.is_symlink():
        raise RuntimeError('Original pinned native runner missing/linked')
    blob = subprocess.check_output(['git', 'hash-object', str(SCRIPT)], text=True).strip()
    if blob != SCRIPT_BLOB:
        raise RuntimeError('Native runner Git blob hash differs from proven candidate')
    spec = importlib.util.spec_from_file_location('c925_pinned_runner', SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot inspect pinned runner')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gdb_script(out: Path) -> Path:
    """Emit debugger commands with zero quoted printf strings.

    GDB echo uses a literal backslash-n for its line separator. Preserve the
    escape in the generated file; do not write multi-line quoted strings.
    """
    path = out / 'original-driver-fault-frame.gdb'
    lines = [
        'set pagination off', 'set confirm off', 'set print thread-events off',
        'set print frame-arguments all', 'set disable-randomization off',
        'set breakpoint pending off',
        'handle SIGSEGV nostop noprint pass',
        'handle SIGBUS nostop noprint pass',
        'handle SIGILL nostop noprint pass',
        'handle SIGVTALRM nostop noprint pass',
        'handle SIGUSR1 nostop noprint pass',
        'handle SIGPIPE nostop noprint pass',
        'break unreachable_impl',
        'commands', 'silent',
        r'echo GOT_ORIGINAL_DRIVER_FAULT_FRAME_CAPTURED\n',
        'bt 22',
        r'echo ORIGINAL_FAULT_FRAME_INDEX_4\n',
        'frame 4', 'info frame', 'info registers', 'info symbol $pc',
        'x/24i $pc-48',
        r'echo RADV_DESCRIPTOR_CALLER_FRAME_INDEX_7\n',
        'frame 7', 'info registers', 'info symbol $pc',
        'x/22i $pc-40',
        r'echo EMULATOR_PIPELINE_BIND_FRAME_INDEX_8\n',
        'frame 8', 'info registers', 'info symbol $pc',
        'x/28i $pc-56',
        r'echo FAULTING_DRIVER_AND_RENDERER_MAPPINGS\n',
        'info proc mappings',
        'quit', 'end', 'run',
    ]
    body = '\n'.join(lines) + '\n'
    required = ('GOT_ORIGINAL_DRIVER_FAULT_FRAME_CAPTURED',
                'ORIGINAL_FAULT_FRAME_INDEX_4',
                'RADV_DESCRIPTOR_CALLER_FRAME_INDEX_7',
                'EMULATOR_PIPELINE_BIND_FRAME_INDEX_8',
                'FAULTING_DRIVER_AND_RENDERER_MAPPINGS')
    if ('printf' in body or body.count('\ncommands\n') != 1 or
            body.count('\nend\nrun\n') != 1 or
            any(body.count('echo ' + marker + '\\n\n') != 1 for marker in required)):
        raise RuntimeError('GDB script generation failed structural validation')
    if any(line.count('"') % 2 for line in lines):
        raise RuntimeError('Unterminated debugger command quote')
    path.write_text(body)
    return path


def native_gdb_self_test(out: Path) -> Path:
    """Run this exact GDB command file against a harmless 10-frame C fixture.

    The historical 14:52 debugger regression printed a GDB error only *after*
    the game crashed and the breakpoint fired. A real synthetic inferior is
    therefore mandatory; checking the Python syntax alone is insufficient.
    """
    import hashlib
    if not shutil.which('cc') or not shutil.which('gdb'):
        raise RuntimeError('Missing cc or gdb for debugger preflight')
    source = out / 'gdb-native-fixture.c'
    binary = out / 'gdb-native-fixture'
    fragments = [
        '#include <stdint.h>',
        '__attribute__((noinline)) void unreachable_impl(void) { '
        'volatile uintptr_t x = 3; (void)x; }',
    ]
    for level in range(9):
        callee = 'unreachable_impl' if level == 0 else f'hop{level - 1}'
        fragments.append(f'__attribute__((noinline)) void hop{level}(void) '
                         f'{{ {callee}(); }}')
    fragments.append('int main(void) { hop8(); return 0; }')
    source.write_text('\n'.join(fragments) + '\n')
    with (out / 'gdb-selftest-compile.log').open('w') as stream:
        compile_run = subprocess.run(
            ['cc', '-O0', '-g', '-fno-omit-frame-pointer',
             '-fno-optimize-sibling-calls', str(source), '-o', str(binary)],
            stdout=stream, stderr=subprocess.STDOUT, timeout=30, check=False)
    if compile_run.returncode:
        raise RuntimeError('GDB self-test fixture compilation failed')
    cmdfile = gdb_script(out)
    command = ['gdb', '--nx', '--quiet', '--batch', '-x', str(cmdfile),
               '--args', str(binary)]
    with (out / 'gdb-selftest-native.log').open('w') as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                timeout=45, check=False)
    transcript = (out / 'gdb-selftest-native.log').read_text(errors='replace')
    for marker in ('GOT_ORIGINAL_DRIVER_FAULT_FRAME_CAPTURED',
                   'ORIGINAL_FAULT_FRAME_INDEX_4',
                   'RADV_DESCRIPTOR_CALLER_FRAME_INDEX_7',
                   'EMULATOR_PIPELINE_BIND_FRAME_INDEX_8',
                   'FAULTING_DRIVER_AND_RENDERER_MAPPINGS'):
        if marker not in transcript:
            raise RuntimeError('GDB native fixture missing marker: ' + marker)
    if (result.returncode != 0 or 'Error in sourced command file' in transcript or
            'Bad format string' in transcript or
            'unreachable_impl' not in transcript or
            'rip ' not in transcript):
        raise RuntimeError('GDB native fixture exited with invalid frame capture')
    (out / 'gdb-selftest-sha256.txt').write_text(
        'gdb_script_sha256=' + hashlib.sha256(cmdfile.read_bytes()).hexdigest() + '\n'
        + 'result=GDB_NATIVE_FIXTURE_PASS\n')
    print('GDB_NATIVE_COMMAND_SELF_TEST=PASS', flush=True)
    return cmdfile


def run(m) -> None:
    root = m.ROOT / 'experiments/c925-integrated-gpu-20261027-122701'
    m.EXP, m.SRC, m.BUILD = root, root / 'source', root / 'build'
    elf = m.BUILD / 'shadps4'
    m.STATE.update({
        'record_type': 'EXACT_C925_NATIVE_GPU_UNHANDLED_NULL_CRASH_DEBUG',
        'mode': 'GDB_PRESERVE_ORIGINAL_DRIVER_FAULT_FRAME',
        'binary_sha256': GAME_ELF_SHA,
        'source_changes': 'NONE', 'rebuild': False, 'game_exit': None,
        'no_automatic_game_input': True, 'tested_games': [],
    })
    m.stage('VERIFY_SOURCE_BUILD_PORTABLE_SAVES_PROTECTED_BINARY_AND_8GIB_VRAM')
    if not shutil.which('gdb') or not shutil.which('nm'):
        raise m.Abort('GDB_OR_NM_NOT_INSTALLED', 2)
    if any(p.is_symlink() for p in (m.BUILD, m.SRC, elf, m.BUILD / 'user')):
        raise m.Abort('SYMLINKED_EXPERIMENT_PATH', 2)
    if not (m.BUILD / 'build.ninja').is_file() or not (m.BUILD / 'user').is_dir():
        raise m.Abort('ORIGINAL_NATIVE_BUILD_OR_PORTABLE_PROFILE_MISSING', 2)
    if not elf.is_file() or m.sha256(elf) != GAME_ELF_SHA:
        raise m.Abort('C925_TESTED_BINARY_SHA_MISMATCH', 2)
    if not m.PROVEN.is_file() or m.sha256(m.PROVEN) != m.MENU_ELF:
        raise m.Abort('PROTECTED_PREVIOUS_MENU_ELF_MISMATCH', 2)
    if not m.GAME.is_file():
        raise m.Abort('GAME_EXECUTABLE_NOT_FOUND', 2)
    if m.sha256(m.SRC / 'src/video_core/renderer_vulkan/vk_instance.h') != PREVIOUS_HEADER_SHA:
        raise m.Abort('C925_REPAIRED_NATIVE_SOURCE_CHANGED', 2)
    if subprocess.run(['pgrep', '-x', 'shadps4'], capture_output=True).returncode == 0:
        raise m.Abort('ANOTHER_SHADPS4_GAME_RUNNING', 2)
    sym = subprocess.run(['nm', '-C', str(elf)], capture_output=True,
                         text=True, timeout=40)
    if sym.returncode or 'unreachable_impl()' not in sym.stdout:
        raise m.Abort('REQUIRED_HOST_FATAL_BREAKPOINT_SYMBOL_MISSING', 2)
    m.stage('GDB_NATIVE_COMMAND_SELF_TEST_ON_HARMLESS_INFERIOR')
    script = native_gdb_self_test(m.OUT)
    m.STATE['gdb_native_fixture_pass'] = True
    m.offload_if_needed()
    m.stage('NATIVE_GPU_GDB_CAPTURE_ONE_GAME_MAX_118S')
    env = dict(os.environ)
    if not env.get('DISPLAY'):
        sock = sorted(Path('/tmp/.X11-unix').glob('X[0-9]*'))
        if sock:
            env['DISPLAY'] = ':' + sock[0].name[1:]
    env['XDG_DATA_HOME'] = str(root / 'xdg-data')
    cmd = ['gdb', '--nx', '--quiet', '--batch', '-x', str(script), '--args',
           str(elf), '--game', str(m.GAME), '--fullscreen', 'true', '--show-fps']
    (m.OUT / 'gdb-command.json').write_text(json.dumps(cmd) + '\n')
    start = time.monotonic()
    with (m.OUT / 'game-gdb.log').open('w') as stream:
        proc = subprocess.Popen(cmd, cwd=m.BUILD, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, start_new_session=True)
        m.STATE['tested_games'] = ['CUSA11456']
        for sec in (5, 10, 17, 26, 36, 55, 85, 110):
            while proc.poll() is None and time.monotonic() - start < sec:
                time.sleep(0.3)
            if proc.poll() is not None:
                break
            m.capture_screen(sec, env)
        while proc.poll() is None and time.monotonic() - start < 118:
            time.sleep(0.3)
        if proc.poll() is None:
            m.STATE['duration_limit_hit'] = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
        m.STATE['gdb_exit_code'] = proc.returncode
    m.STATE['elapsed_seconds'] = round(time.monotonic() - start, 2)
    console = (m.OUT / 'game-gdb.log').read_text(errors='replace')
    required = ('GOT_ORIGINAL_DRIVER_FAULT_FRAME_CAPTURED',
                'ORIGINAL_FAULT_FRAME_INDEX_4',
                'RADV_DESCRIPTOR_CALLER_FRAME_INDEX_7',
                'EMULATOR_PIPELINE_BIND_FRAME_INDEX_8',
                'FAULTING_DRIVER_AND_RENDERER_MAPPINGS')
    m.STATE['fatal_backtrace_captured'] = (
        all(marker in console for marker in required) and
        'Error in sourced command file' not in console and
        'Bad format string' not in console)
    m.STATE['native_breakpoint_resolved'] = 'Breakpoint 1 at' in console
    m.STATE['screenshots'] = {p.name: m.sha256(p) for p in m.OUT.glob('menu-*.png')}
    m.STATE['previous_menu_binary_unchanged'] = m.PROVEN.is_file() and m.sha256(m.PROVEN) == m.MENU_ELF
    m.stage('GDB_ORIGINAL_DRIVER_FRAME_CAPTURED' if m.STATE['fatal_backtrace_captured']
            else 'GDB_RUN_FINISHED_NO_NATIVE_BACKTRACE')
    print('NATIVE_CRASH_BACKTRACE=' + str(m.STATE['fatal_backtrace_captured']), flush=True)
    if not m.STATE['fatal_backtrace_captured']:
        raise m.Abort('GDB_NATIVE_FAULT_FRAME_MISSING', 10)


def main() -> int:
    module = None
    status = 0
    try:
        module = original_runner()
        run(module)
    except BaseException as e:
        status = 5
        if module is not None:
            module.STATE['status'] = 'NATIVE_DEBUGGER_ERROR'
            (module.OUT / 'debugger-error.txt').write_text(traceback.format_exc())
        print('GDB_TRACE_ERROR=' + str(e), file=sys.stderr, flush=True)
    finally:
        if module is not None:
            module.finish(status)
    return status


if __name__ == '__main__':
    sys.exit(main())
