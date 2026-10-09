#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Watch the exact Compute6 progress-table slot while capturing first underflow.

Extends the tested, pinned Ghost ProxySetSync collector with ONE additional
hardware write watchpoint. Uses only the verified private Compute6 executable.
No builds, source changes, guest writes, game save changes or other emulators.
"""
from __future__ import annotations
import argparse
import ast
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

GITHUB_REV = "3296bd475654328de25ee2663e619ec38c02eac6"
SCRIPT_BLOB = "30f83ebaab15fcaa814ea7996f1b966299f88f12"
BASE_NAME = "ghost-proxy-progress-first-negative-20261009.py"
REPO = "Chreece/shadPS4"
SLOT = 0x11000000B8
UNDERFLOW = 0x3FB64E4
HOME = Path.home()

# GDB Python code: the previous full hardware underflow watchpoint is retained.
# Only the slot watch is additional, and both hooks have return False for
# ordinary writes so the game can progress normally after each observation.
EXTRA = '''
progress_slot_addr = 0x11000000b8
progress_slot_prior = val(progress_slot_addr, 64)
progress_slot_writes = 0
progress_slot_regressions = 0
class ProgressSlotWatch(gdb.Breakpoint):
    def __init__(self):
        super().__init__("*(unsigned long long*)0x11000000b8",
                         type=gdb.BP_WATCHPOINT, wp_class=gdb.WP_WRITE)
        self.silent = True
    def stop(self):
        global progress_slot_prior, progress_slot_writes, progress_slot_regressions
        progress_slot_writes += 1
        current = val(progress_slot_addr, 64)
        previous = progress_slot_prior
        progress_slot_prior = current
        thread = gdb.selected_thread()
        name = thread.name if thread else "unknown"
        tid = thread.ptid[1] if thread else -1
        pc = int(gdb.parse_and_eval("$pc"))
        changed = current != previous
        regressed = (previous is not None and current is not None and current < previous)
        if regressed:
            progress_slot_regressions += 1
        if changed or progress_slot_writes <= 3:
            print("GHOST_PROGRESS_SLOT_WRITE",
                  "count=", progress_slot_writes,
                  "previous=", hex(previous) if previous is not None else "unmapped",
                  "current=", hex(current) if current is not None else "unmapped",
                  "regressed=", regressed,
                  "pc=", hex(pc), "tid=", tid, "thread=", name,
                  "middle_count=", val(0x3fb64e4),
                  "middle_state=", val(0x3fb64e0), flush=True)
        if regressed:
            print("GHOST_PROGRESS_SLOT_REGRESSION",
                  "previous=",previous,"current=",current,
                  "pc=",hex(pc),"thread=",name,flush=True)
            inspect("info registers rip rax rbx rcx rdx rsi rdi rsp rbp eflags")
            inspect("bt 9")
            inspect("x/10gx 0x11000000a8")
        return False
ProgressSlotWatch()
print("GHOST_PROGRESS_SLOT_WATCH_ARMED",
      "slot=",hex(progress_slot_addr),
      "initial=", hex(progress_slot_prior) if progress_slot_prior is not None else "unmapped",
      flush=True)
'''


def with_slot_watch(original: str) -> str:
    """Insert one GDB-Python watch after the established middle watch arms."""
    anchor = 'MiddleWatch()\nprint("GHOST_MIDDLE_HW_WATCH_ARMED"'
    if original.count(anchor) != 1:
        raise ValueError("Unexpected original middle watch command layout")
    if "GHOST_PROGRESS_SLOT_WATCH_ARMED" in original:
        raise ValueError("Slot watch already installed")
    result = original.replace(anchor, 'MiddleWatch()\n' + EXTRA + '\nprint("GHOST_MIDDLE_HW_WATCH_ARMED"', 1)
    assert result.splitlines().count('ProgressSlotWatch()') == 1
    assert result.splitlines().count('MiddleWatch()') == 1
    assert result.count('detach\nquit') == 1
    return result


def python_blocks(gdb_script: str) -> list[str]:
    """Parse actual gdb 'python'...'end' blocks without starting debugger."""
    blocks = re.findall(r'(?m)^python\n(.*?)\nend\s*$', gdb_script, re.DOTALL | re.MULTILINE)
    if len(blocks) != 2:
        raise ValueError(f"Expected two Python blocks, found {len(blocks)}")
    for block in blocks:
        ast.parse(block)
    return blocks


def slot_watch_summary(log: str) -> dict:
    """Never confuse one working watchpoint with both being available."""
    writes = [line for line in log.splitlines()
              if line.startswith('GHOST_PROGRESS_SLOT_WRITE ')]
    regressions = [line for line in log.splitlines()
                   if line.startswith('GHOST_PROGRESS_SLOT_REGRESSION ')]
    return {
        'middle_hardware_present': bool(re.search(r'Hardware watchpoint\s+1:', log)),
        'slot_hardware_present': bool(re.search(r'Hardware watchpoint\s+2:', log)),
        'slot_armed': 'GHOST_PROGRESS_SLOT_WATCH_ARMED' in log,
        'slot_write_count': len(writes),
        'regression_count': len(regressions),
        'first_write': writes[0] if writes else None,
        'last_write': writes[-1] if writes else None,
        'regressions': regressions[:15],
        'middle_negative_captured': 'GHOST_MIDDLE_FIRST_NEGATIVE_DETECTED' in log
    }


def selftest() -> None:
    fixture = '''set pagination off
python
import gdb
hits = 0
class FakeWatch:
    pass
def val(*args):
    return 0
def inspect(*args):
    pass
def MiddleWatch():
    return None
MiddleWatch()
print("GHOST_MIDDLE_HW_WATCH_ARMED")
end
python
try:
    gdb.execute("continue")
except Exception:
    pass
end
detach
quit
'''
    commands = with_slot_watch(fixture)
    blocks = python_blocks(commands)
    assert len(blocks) == 2
    assert commands.count('*(unsigned long long*)0x11000000b8') == 1
    assert commands.count('gdb.BP_WATCHPOINT') == 1
    assert commands.count('gdb.WP_WRITE') == 1
    assert 'return False' in EXTRA
    assert commands.count('GHOST_PROGRESS_SLOT_REGRESSION') == 1
    assert commands.count('GHOST_PROGRESS_SLOT_WRITE') == 1
    assert 'val(0x3fb64e4)' in commands
    assert SLOT == 0x1100000000 + 23*8
    assert UNDERFLOW == 0x3FB64E0 + 4
    assert GITHUB_REV != '' and len(SCRIPT_BLOB) == 40
    sample = ('Hardware watchpoint 1: middle\nHardware watchpoint 2: slot\n'
              'GHOST_PROGRESS_SLOT_WATCH_ARMED\n'
              'GHOST_PROGRESS_SLOT_WRITE count=1 previous=0x1 current=0xb\n'
              'GHOST_PROGRESS_SLOT_REGRESSION previous=11 current=1\n'
              'GHOST_MIDDLE_FIRST_NEGATIVE_DETECTED\n')
    meta=slot_watch_summary(sample)
    assert meta['middle_hardware_present'] and meta['slot_hardware_present']
    assert meta['slot_write_count']==1 and meta['regression_count']==1
    assert meta['middle_negative_captured']
    assert not slot_watch_summary('Hardware watchpoint 1: middle')['slot_hardware_present']
    try:
        with_slot_watch(commands)
    except ValueError:
        pass
    else:
        raise AssertionError('Duplicate watchpoint was accepted')
    try:
        with_slot_watch(fixture.replace('MiddleWatch()\nprint("GHOST_MIDDLE_HW_WATCH_ARMED"', 'no original anchor'))
    except ValueError:
        pass
    else:
        raise AssertionError('Incorrect GDB layout was accepted')
    print('SELFTEST PASS: GDB Python syntax, 64-bit slot, 32-bit underflow, 2 hardware watches, all guards')


def obtain_base() -> tuple[object, Path, tempfile.TemporaryDirectory]:
    temp = tempfile.TemporaryDirectory(prefix='ghost-slot-script-')
    path = Path(temp.name) / BASE_NAME
    url = f'https://raw.githubusercontent.com/{REPO}/{GITHUB_REV}/scripts/{BASE_NAME}'
    try:
        subprocess.run(['curl', '-fsSL', '--retry', '3', '--connect-timeout', '15',
                        '--max-time', '45', url, '-o', str(path)], check=True, timeout=70)
        blob = subprocess.check_output(['git', 'hash-object', str(path)], text=True, timeout=10).strip()
        if blob != SCRIPT_BLOB:
            raise RuntimeError(f'Pinned base script checksum mismatch: {blob}')
        compile(path.read_bytes(), str(path), 'exec')
        spec = importlib.util.spec_from_file_location('ghost_slot_pinned_base', path)
        if spec is None or spec.loader is None:
            raise RuntimeError('Could not create pinned base module')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, path, temp
    except BaseException:
        temp.cleanup()
        raise


def run_test() -> int:
    selftest()
    base, path, temp = obtain_base()
    try:
        # Unlike the failed last run, this base already has the corrected
        # post-capture 'payload' count; also verify that exact regression.
        source = path.read_text(errors='strict')
        if 'key:payload.count(key) for key in' not in source:
            raise RuntimeError('Base still contains the prior post-capture NameError')
        base.selftest()  # full proven base fixture test before any game launch
        original = base.middle_watch_commands
        def updated_commands() -> str:
            code = with_slot_watch(original())
            python_blocks(code)
            return code
        base.middle_watch_commands = updated_commands
        original_finish = base.finish_middle_watch
        def updated_finish(proc, pid, why):
            original_finish(proc, pid, why)
            trace = (base.WORK / 'middle-underflow-watch.log')
            log = trace.read_text(errors='replace') if trace.is_file() else ''
            summary = slot_watch_summary(log)
            (base.WORK / 'progress-slot-summary.json').write_text(
                json.dumps(summary, indent=2) + '\n')
            print('GHOST_PROGRESS_SLOT_SUMMARY', json.dumps(summary), flush=True)
        base.finish_middle_watch = updated_finish
        # Make archive naming unambiguous and never overlap old captures.
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        base.WORK = HOME / '.cache' / ('ghost-slot-writers-' + stamp)
        base.OUT = HOME / ('ghost-slot-writers-' + stamp + '.tar.gz')
        print('PINNED_BASE_VERIFIED', SCRIPT_BLOB, flush=True)
        print('PRIVATE_BINARY', base.BIN, flush=True)
        old_argv = sys.argv
        try:
            sys.argv = [str(path), '--test-only']
            result = base.main()
            summary_path = base.WORK / 'progress-slot-summary.json'
            if summary_path.is_file():
                summary = json.loads(summary_path.read_text())
                if not (summary['slot_hardware_present'] and summary['slot_armed']):
                    print('SLOT_WATCH_NOT_VERIFIED: upload the report; no fix inferred', flush=True)
                    return 1
            return result
        finally:
            sys.argv = old_argv
    finally:
        temp.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--test-only', action='store_true')
    args = parser.parse_args()
    if args.self_test and args.test_only:
        parser.error('Choose one mode')
    if args.self_test:
        selftest()
        return 0
    if not args.test_only:
        parser.error('Run --self-test first, then --test-only')
    return run_test()


if __name__ == '__main__':
    raise SystemExit(main())
