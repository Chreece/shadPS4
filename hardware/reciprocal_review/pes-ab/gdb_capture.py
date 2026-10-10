# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Record crash signals and an optional fault-address catchpoint without suppressing signals."""
import json
import os
from pathlib import Path
import struct
import time
import traceback

import gdb

stage = Path(os.environ['PES_GDB_STAGE'])
target = json.loads((stage / 'debug-target.json').read_text())
proc = Path('/proc') / str(target['pid'])
seen = set()
loaded = set()
sequence = 0
last_signal = None
segv_catchpoint = None


def save(name, value):
    temporary = stage / (name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(stage / name)


def execute(command):
    try:
        return gdb.execute(command, to_string=True)
    except gdb.error as exc:
        return 'GDB: ' + str(exc) + '\n'


def load_symbols(maps):
    for line in maps.splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6 or int(fields[2], 16) != 0:
            continue
        path = Path(fields[5])
        if not path.is_relative_to(target['build_dir']):
            continue
        base = int(fields[0].split('-')[0], 16)
        key = (str(path), base)
        if key in loaded:
            continue
        try:
            with path.open('rb') as stream:
                header = stream.read(64)
                if header[:6] != b'\x7fELF\x02\x01':
                    continue
                phoff = struct.unpack_from('<Q', header, 32)[0]
                size, count = struct.unpack_from('<HH', header, 54)
                stream.seek(phoff)
                segments = [stream.read(size) for _ in range(count)]
            vaddr = next(struct.unpack_from('<Q', seg, 16)[0] for seg in segments
                         if struct.unpack_from('<I', seg)[0] == 1 and
                         struct.unpack_from('<Q', seg, 8)[0] == 0)
            gdb.execute('add-symbol-file ' + json.dumps(str(path)) + ' -o ' + hex(base-vaddr),
                        to_string=True)
            loaded.add(key)
        except (OSError, StopIteration, ValueError, gdb.error) as exc:
            print('Symbol loading: ' + str(exc), flush=True)


def stopped(event):
    global last_signal
    last_signal = event.stop_signal if isinstance(event, gdb.SignalEvent) else None
    if segv_catchpoint is not None and segv_catchpoint in getattr(event, 'breakpoints', ()):
        last_signal = 'SIGSEGV'


def capture_signal():
    global sequence
    sequence += 1
    inferior = gdb.selected_inferior()
    maps = (proc / 'maps').read_text()
    load_symbols(maps)
    pc = int(gdb.parse_and_eval('$pc'))
    sp = int(gdb.parse_and_eval('$sp'))
    key = (last_signal, pc)
    record = {'sequence': sequence, 'signal': last_signal, 'pc': hex(pc), 'sp': hex(sp),
              'thread': list(gdb.selected_thread().ptid), 'monotonic_ns': time.monotonic_ns(),
              'siginfo': execute('p $_siginfo')}
    if last_signal == 'SIGSEGV':
        record['fault_address'] = hex(int(gdb.parse_and_eval('$_siginfo._sifields._sigfault.si_addr')))
    first = key not in seen and len(seen) < 16
    if first:
        seen.add(key)
    prefix = 'signal-' + str(sequence).zfill(3) if first else 'signal-last'
    parts = []
    for command in ('info registers', 'x/24i $pc', 'x/64gx $sp', 'bt 32',
                    'info threads', 'thread apply all bt 8'):
        parts.append(command + '\n' + execute(command))
    guest_pc = target.get('guest_pc')
    if guest_pc is not None:
        command = 'x/24i ' + hex(guest_pc)
        parts.append(command + '\n' + execute(command))
        parts.append('info all-registers\n' + execute('info all-registers'))
    (stage / (prefix + '.txt')).write_text('\n'.join(parts))
    (stage / (prefix + '.maps')).write_text(maps)
    regions = [('code', max(0, pc-64), 256), ('stack', sp, 4096)]
    if guest_pc is not None:
        record['guest_pc'] = hex(guest_pc)
        regions.append(('guest-code', max(0, guest_pc-64), 256))
    for label, address, length in regions:
        try:
            (stage / (prefix + '.' + label + '.bin')).write_bytes(
                bytes(inferior.read_memory(address, length)))
        except (gdb.error, MemoryError) as exc:
            record[label + '_error'] = str(exc)
    record['evidence_prefix'] = prefix
    save('signal-last.json', record)
    if sequence <= 512:
        with (stage / 'signals.jsonl').open('a') as output:
            output.write(json.dumps(record) + '\n')
    if first:
        save(prefix + '.json', record)


def main():
    global segv_catchpoint
    for command in ('set pagination off', 'set confirm off', 'set auto-load off',
                    'set debuginfod enabled off', 'set print thread-events off',
                    'set disable-randomization off',
                    'handle SIGILL SIGSEGV SIGUSR1 SIGUSR2 SIGFPE SIGPIPE SIGTERM SIGTRAP SIGSYS nostop noprint pass',
                    'handle SIGBUS SIGABRT stop print pass'):
        gdb.execute(command, to_string=True)
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    if fields[19] != target['start'] or proc.stat().st_uid != os.getuid():
        raise RuntimeError('Target process identity changed; refusing debugger attach')
    gdb.execute('attach ' + str(target['pid']), to_string=True)
    if target.get('fault_address') is not None:
        existing = {bp.number for bp in (gdb.breakpoints() or ())}
        gdb.execute('catch signal SIGSEGV', to_string=True)
        segv_catchpoint = next(bp for bp in gdb.breakpoints() if bp.number not in existing)
        gdb.execute('condition ' + str(segv_catchpoint.number) +
                    ' $_siginfo._sifields._sigfault.si_addr == (void*)' +
                    hex(target['fault_address']), to_string=True)
    gdb.events.stop.connect(stopped)
    load_symbols((proc / 'maps').read_text())
    save('debug-ready.json', {'pid': target['pid'], 'monotonic_ns': time.monotonic_ns()})
    while gdb.selected_inferior().pid and not (stage / 'debug-stop').exists():
        gdb.execute('continue', to_string=True)
        if gdb.selected_inferior().pid and last_signal in ('SIGBUS', 'SIGABRT', 'SIGSEGV'):
            capture_signal()


try:
    main()
except KeyboardInterrupt:
    pass
except BaseException:
    (stage / 'debug-error.txt').write_text(traceback.format_exc())
finally:
    if gdb.selected_inferior().pid:
        execute('detach')
    save('debug-finished.json', {'signal_events': sequence,
                                'error': (stage / 'debug-error.txt').exists()})
