#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""One bounded native shadPS4 test for any title, without title-specific flags.

The actual emulator receives only the normal game selector and common
--fullscreen true --show-fps options. No CPU/GPU overrides, DynamoRIO or
automatic input. At 118 seconds SIGTERM only its owned child process group;
after two seconds SIGKILL only that group. Keep full console on the host and
write a bounded, source-attributed archive for the shared emulator diary.
"""

from __future__ import annotations
import argparse
import bisect
import struct
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ERROR_TERMS = (
    "Unhandled access violation", "Fatal", "SIGSEGV", "SIGABRT",
    "VK_ERROR_DEVICE_LOST", "GpuComm", "sharp source was not flattened",
    "SRT", "SrtDmaSource", "UniformBufferStore", "Ordered GDS", "PR117_", "AUDIO_", "Loading libSce",
)
GIB = 1024 ** 3
GPU_FAILURE_TERMS = ("VK_ERROR_DEVICE_LOST", "ErrorDeviceLost", "context is lost",
                     "guilty of a soft recovery", "GPU fault detected")
ANSI_ESCAPE = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
VALIDATION_ID = re.compile(r'\b(?:VUID-|SYNC-HAZARD-|UNASSIGNED-)[A-Za-z0-9_-]+')


def validation_failure(text):
    text = ANSI_ESCAPE.sub('', text)
    if 'Enabled instance layers:' in text and 'VK_LAYER_KHRONOS_validation' not in text:
        return 'REQUESTED_VALIDATION_LAYER_NOT_ACTIVE: ' + text
    if VALIDATION_ID.search(text) and ('<Error>' in text or 'Validation Error' in text):
        return text[:4096]
    return None


def summarize_validation(console, out, requested=False):
    enabled = False
    counts = {}
    first_error = None
    saved_bytes = 0
    truncated = False
    in_message = False
    with console.open(errors='replace') as stream, (out / 'vulkan-validation.log').open('w') as target:
        for raw in stream:
            line = ANSI_ESCAPE.sub('', raw)
            if 'Enabled instance layers:' in line:
                enabled |= 'VK_LAYER_KHRONOS_validation' in line
            ids = set(VALIDATION_ID.findall(line))
            for message_id in ids:
                if message_id in counts or len(counts) < 1024:
                    counts[message_id] = counts.get(message_id, 0) + 1
                else:
                    truncated = True
            if requested or ids:
                first_error = first_error or validation_failure(line)
            if re.match(r'^\[.+?\] <\w+>', line):
                in_message = 'DebugUtilsCallback:' in line
            if in_message or ids or 'Enabled instance layers:' in line:
                size = len(line.encode('utf-8'))
                if saved_bytes + size <= 8 * 1024**2:
                    target.write(line)
                    saved_bytes += size
                else:
                    truncated = True
    return {'validation_layer_active': enabled, 'validation_message_ids': counts,
            'first_validation_failure': first_error, 'validation_log_bytes': saved_bytes,
            'validation_evidence_truncated': truncated}


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as fd:
        for blob in iter(lambda:fd.read(1024*1024),b''):
            h.update(blob)
    return h.hexdigest()


def existing_games(proc=Path('/proc')):
    found=[]
    for p in proc.iterdir():
        if not p.name.isdecimal():
            continue
        try:
            if p.stat().st_uid!=os.getuid():
                continue
        except OSError:
            continue
        names=[]
        try:
            names.append(Path(os.readlink(p/'exe')).name.lower())
        except OSError:
            pass
        try:
            names.append((p/'comm').read_text().strip().lower())
        except OSError:
            pass
        if not any(name.startswith('shadps4') for name in names):
            continue
        try:
            tasks=list((p/'task').iterdir())
            busy=not tasks
            for task in tasks:
                try:
                    state=(task/'stat').read_text().rsplit(') ',1)[1][0]
                    busy=busy or state not in ('X','Z')
                except (OSError,IndexError,ValueError):
                    busy=busy or task.exists()
        except OSError:
            busy=True
        if busy and p.exists():
            found.append(int(p.name))
    return found


def save(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str)+'\n')


def take_picture(report,second,display):
    if shutil.which('import') is None:
        return False
    output=report/f'frame-{second:03d}s.png'
    try:
        result=subprocess.run(['import','-display',display,'-window','root',str(output)],
                              stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL,timeout=3,check=False)
        return result.returncode==0 and output.is_file()
    except (OSError,subprocess.TimeoutExpired):
        return False


def term_owned(child,phase,deadline=None):
    deadline=min(deadline if deadline is not None else float('inf'), time.monotonic()+2.0)
    if child.poll() is not None:
        return None
    try:
        try:
            os.killpg(child.pid,signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=max(0.0,min(1.5,deadline-time.monotonic())))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=max(0.0,deadline-time.monotonic()))
    except (OSError,subprocess.TimeoutExpired) as error:
        if child.poll() is None:
            return f'{type(error).__name__}: {error}'
    print('OWNED_GAME_PROCESS_TERMINATED='+phase,flush=True)
    return None


def capture_owned_stop_state(pid, out, proc=Path('/proc'),
                             filename='owned-process-stop-state.json'):
    state={'pid':pid,'threads':[]}
    process=proc/str(pid)
    try:
        tasks=sorted((process/'task').iterdir(),key=lambda p:int(p.name))
        state['thread_count']=len(tasks)
        state['truncated']=len(tasks)>128
        for task in tasks[:128]:
            item={'tid':int(task.name)}
            for name in ('status','wchan','stat','schedstat','syscall'):
                try:
                    item[name]=(task/name).read_text(errors='replace')[:8192]
                except OSError as error:
                    item[name+'_error']=str(error)
            state['threads'].append(item)
    except OSError as error:
        state['error']=str(error)
    save(out/filename,state)


def prepare_live_symbols(binary):
    result = {'binary': str(binary), 'functions': [], 'sampler': {}}
    try:
        with binary.open('rb') as stream:
            header = stream.read(64)
            if header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != 62:
                raise ValueError('ELF64_LITTLE_ENDIAN_X86_64_REQUIRED')
            kind = struct.unpack_from('<H', header, 16)[0]
            offset = struct.unpack_from('<Q', header, 32)[0]
            size, count = struct.unpack_from('<HH', header, 54)
            if kind not in (2, 3) or size != 56 or count > 128:
                raise ValueError('UNSUPPORTED_ELF_LAYOUT')
            stream.seek(offset)
            segments = [struct.unpack('<IIQQQQQQ', stream.read(size)) for _ in range(count)]
            initial = next(s for s in segments if s[0] == 1 and s[2] == 0)
            result['initial_vaddr'] = initial[3]
        symbols = subprocess.run(['nm', '-S', '-n', '-C', str(binary)],
                                 capture_output=True, text=True, timeout=10, check=True).stdout
        prefix = 'Common::PerformanceDiagnostics::(anonymous namespace)::'
        expected = {'samples': 32768 * 152, 'sample_count': 4, 'started': 16}
        for line in symbols.splitlines():
            fields = line.split(maxsplit=3)
            if len(fields) != 4:
                continue
            address, length, kind, name = fields
            if name == 'Common::PerformanceDiagnostics::counters' and int(length, 16) == 13 * 48:
                result['counters'] = int(address, 16)
            if name.startswith(prefix) and name[len(prefix):] in expected:
                key = name[len(prefix):]
                if int(length, 16) != expected[key]:
                    raise ValueError('UNSUPPORTED_SAMPLER_LAYOUT: ' + key)
                result['sampler'][key] = int(address, 16)
            if kind in 'tTwW':
                result['functions'].append((int(address, 16), int(length, 16), name))
        result['function_addresses'] = [f[0] for f in result['functions']]
        if set(result['sampler']) != set(expected):
            result['sampler_error'] = 'SAMPLER_SYMBOLS_UNAVAILABLE'
    except (OSError, ValueError, StopIteration, struct.error, subprocess.SubprocessError) as error:
        result['error'] = str(error)
    return result


def anonymous_executable(region):
    return bool(region and 'x' in region['permissions'] and (
        not region['path'] or region['path'].startswith(('/memfd:', '[anon:'))))


def capture_reverse_context(read, mapping, threads, deadline):
    result = {'blocks': [], 'seeds': [], 'references': [], 'references_are_candidates': True,
              'maximum_scan_bytes': 32 * 1024 * 1024, 'maximum_seeds': 16,
              'maximum_code_pages': 64, 'maximum_references': 256, 'scanned_bytes': 0}
    regions, seeds, pages = {}, {}, set()
    for thread in threads:
        for sample in thread['samples']:
            regs = sample['registers']
            region = mapping(regs['rip'])
            if not anonymous_executable(region):
                continue
            regions[region['start']] = region
            for name, address in regs.items():
                target = mapping(address)
                if name in ('rip', 'rsp', 'rbp') or abs(address - regs['rsp']) < 65536:
                    continue
                if target is None or 'r' not in target['permissions'] or 'x' in target['permissions']:
                    continue
                if len(seeds) >= result['maximum_seeds']:
                    result['seeds_truncated'] = True
                    break
                start = max(target['start'], address // 512 * 512)
                seeds[start] = min(start + 512, target['end'])
                raw = read(address, 64)
                for offset in range(0, len(raw) - 7, 8):
                    value = int.from_bytes(raw[offset:offset + 8], 'little')
                    if anonymous_executable(mapping(value)) and len(seeds) < result['maximum_seeds']:
                        seeds[value] = value + 1
    result['seeds'] = [{'start': start, 'end': end} for start, end in sorted(seeds.items())]
    starts = sorted(seeds)
    if not starts:
        return result
    pattern = re.compile(rb'(?=[\x89\x8b\x8d\xc6\xc7][\x05\x0d\x15\x1d\x25\x2d\x35\x3d])')
    for region in regions.values():
        position = region['start']
        while position < region['end']:
            if time.monotonic() >= deadline:
                result['budget_exhausted'] = True
                return result
            available = result['maximum_scan_bytes'] - result['scanned_bytes']
            if available <= 0:
                result['scan_truncated'] = True
                return result
            size = min(1024 * 1024, region['end'] - position, available)
            raw = read(position, min(size + 16, region['end'] - position))
            result['scanned_bytes'] += min(size, len(raw))
            for match in pattern.finditer(raw):
                if time.monotonic() >= deadline:
                    result['budget_exhausted'] = True
                    return result
                offset = match.start()
                if offset >= size or offset + 6 > len(raw):
                    continue
                immediate = 4 if raw[offset] == 0xc7 else 1 if raw[offset] == 0xc6 else 0
                if offset + 6 + immediate > len(raw):
                    continue
                target = (position + offset + 6 + immediate +
                          struct.unpack_from('<i', raw, offset + 2)[0]) & ((1 << 64) - 1)
                index = bisect.bisect_right(starts, target) - 1
                if index < 0 or target >= seeds[starts[index]]:
                    continue
                if len(result['references']) >= result['maximum_references']:
                    result['references_truncated'] = True
                    return result
                instruction = position + offset
                result['references'].append({'instruction': instruction, 'target': target,
                                              'opcode_byte': raw[offset]})
                page = max(region['start'], instruction // 4096 * 4096)
                if page in pages:
                    continue
                if len(pages) >= result['maximum_code_pages']:
                    result['code_truncated'] = True
                    continue
                pages.add(page)
                result['blocks'].append({'address': page, 'code': True,
                    'hex': read(page, min(4096, region['end'] - page)).hex()})
            if not raw:
                result['read_incomplete'] = True
                break
            position += size
    return result


def capture_instruction_context(read, mapping, pointers, deadline):
    result = {'blocks': [], 'references_are_candidates': True,
              'maximum_code_pages': 128, 'maximum_data_blocks': 1536,
              'maximum_data_blocks_per_depth': 512,
              'maximum_code_reference_depth': 2, 'maximum_data_pointer_depth': 2}
    if shutil.which('objdump') is None:
        result['error'] = 'OBJDUMP_UNAVAILABLE'
        return result
    pages, data_blocks = {}, {}
    pending_data = []
    data_depth_counts = [0, 0, 0]

    def queue(address, origin, depth, data_depth=0):
        region = mapping(address)
        if region is None or not region['permissions'].startswith('r'):
            return
        code = 'x' in region['permissions']
        if code and depth > result['maximum_code_reference_depth']:
            return
        if code and region['path'] and not region['path'].startswith(('/memfd:', '[anon:')):
            return
        size = 4096 if code else 1024
        start = max(region['start'], address // size * size)
        collection = pages if code else data_blocks
        if start in collection:
            return
        if not code and data_depth_counts[data_depth] >= result['maximum_data_blocks_per_depth']:
            result['data_truncated'] = True
            return
        if len(collection) >= result['maximum_code_pages' if code else 'maximum_data_blocks']:
            result['code_truncated' if code else 'data_truncated'] = True
            return
        collection[start] = {'address': start, 'origin': origin,
                             'depth': depth if code else data_depth,
                             'code': code, 'size': min(size, region['end'] - start)}
        if not code:
            data_depth_counts[data_depth] += 1
            pending_data.append(collection[start])

    for item in pointers:
        if item['code'] and item['depth'] == 0:
            queue(item['pointer'], item['origins'][0], 0)
    for depth in range(result['maximum_code_reference_depth'] + 1):
        batch = [item for item in pages.values() if item['depth'] == depth]
        if not batch:
            break
        chunks = []
        for item in batch:
            if time.monotonic() >= deadline:
                result['budget_exhausted'] = True
                break
            raw = read(item['address'], item['size'])
            result['blocks'].append(dict(item, hex=raw.hex()))
            chunks.append(raw.ljust(4096, b'\x00'))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result['budget_exhausted'] = True
            break
        try:
            with tempfile.TemporaryDirectory(prefix='shadps4-code-context-') as directory:
                binary = Path(directory) / 'context.bin'
                binary.write_bytes(b''.join(chunks))
                decoded = subprocess.run(
                    ['objdump', '-D', '-b', 'binary', '-m', 'i386:x86-64',
                     '--no-show-raw-insn', str(binary)],
                    capture_output=True, text=True, timeout=remaining, check=True).stdout
        except (OSError, subprocess.SubprocessError) as error:
            result['error'] = type(error).__name__
            result['budget_exhausted'] = time.monotonic() >= deadline
            break
        for line in decoded.splitlines():
            if time.monotonic() >= deadline:
                result['budget_exhausted'] = True
                break
            match = re.match(r'^\s*([0-9a-f]+):\s+(\S+)\s*(.*)$', line)
            if not match:
                continue
            offset, mnemonic, operands = int(match[1], 16), match[2], match[3]
            index = offset // 4096
            if index >= len(batch):
                continue
            item = batch[index]
            adjustment = item['address'] - index * 4096
            origin = f'instruction:{adjustment + offset:#x}'
            if depth < result['maximum_code_reference_depth'] and (
                    mnemonic.startswith('j') or mnemonic in ('call', 'callq', 'loop', 'loope', 'loopne')):
                target = re.match(r'0x([0-9a-f]+)(?:\s|$)', operands)
                if target:
                    queue((int(target[1], 16) + adjustment) & ((1 << 64) - 1), origin, depth + 1)
            relative = re.search(r'#\s*0x([0-9a-f]+)', operands) if '(%rip)' in operands else None
            if relative:
                queue((int(relative[1], 16) + adjustment) & ((1 << 64) - 1), origin, depth + 1)
            for immediate in re.findall(r'\$0x([0-9a-f]+)', operands):
                queue(int(immediate, 16), origin, depth + 1)
    for item in pending_data:
        if time.monotonic() >= deadline:
            result['budget_exhausted'] = True
            break
        raw = read(item['address'], item['size'])
        result['blocks'].append(dict(item, hex=raw.hex()))
        if item['depth'] < result['maximum_data_pointer_depth']:
            for offset in range(0, len(raw) - 7, 8):
                queue(int.from_bytes(raw[offset:offset + 8], 'little'),
                      f'data:{item["address"] + offset:#x}',
                      result['maximum_code_reference_depth'] + 1, item['depth'] + 1)
    result['bytes'] = sum(len(item['hex']) // 2 for item in result['blocks'])
    result['code_pages_captured'] = sum(item['code'] for item in result['blocks'])
    result['data_blocks_captured'] = len(result['blocks']) - result['code_pages_captured']
    return result


def capture_owned_live_memory(pid, out, second, symbols):
    record = {'pid': pid, 'threads': [], 'method': 'owned-child-proc-memory-read-only',
              'coherent_snapshot': False, 'signals_sent': False,
              'registers_are_prior_profiler_samples': True,
              'stack_code_candidates_are_not_unwound_frames': True,
              'maximum_threads': 128, 'maximum_sample_age_ms': 2000,
              'maximum_data_blocks': 512, 'maximum_code_blocks': 128,
              'maximum_pointer_depth': 2, 'pointer_context': []}
    began = time.monotonic()
    deadline = began + 1.0
    mem = None
    try:
        process = Path('/proc') / str(pid)
        stat = (process / 'stat').read_text().rsplit(') ', 1)[1].split()
        if int(stat[1]) != os.getpid():
            raise RuntimeError('NOT_OWNED_DIRECT_CHILD')
        if Path(os.readlink(process / 'exe')).resolve() != Path(symbols['binary']).resolve():
            raise RuntimeError('OWNED_EXECUTABLE_CHANGED')
        maps = []
        for line in (process / 'maps').read_text().splitlines():
            fields = line.split(maxsplit=5)
            start, end = (int(value, 16) for value in fields[0].split('-'))
            maps.append({'start': start, 'end': end, 'permissions': fields[1],
                         'offset': int(fields[2], 16),
                         'path': fields[5] if len(fields) > 5 else ''})
        record['maps'] = maps
        map_starts = [r['start'] for r in maps]
        initial = next(r for r in maps if r['path'] == symbols['binary'] and r['offset'] == 0)
        bias = initial['start'] - symbols.get('initial_vaddr', 0)
        record['load_bias'] = bias
        mem = os.open(process / 'mem', os.O_RDONLY | os.O_CLOEXEC)

        def mapping(address):
            index = bisect.bisect_right(map_starts, address) - 1
            if index >= 0 and address < maps[index]['end']:
                return maps[index]
            return None

        def read(address, size):
            if time.monotonic() >= deadline:
                record['budget_exhausted'] = True
                return b''
            region = mapping(address)
            if region is None or not region['permissions'].startswith('r'):
                return b''
            try:
                return os.pread(mem, min(size, region['end'] - address), address)
            except (OSError, OverflowError):
                return b''

        def location(address):
            region = mapping(address)
            if region is None or 'x' not in region['permissions']:
                return None
            entry = {'address': address, 'module': region['path'],
                     'file_offset': address - region['start'] + region['offset']}
            if region['path'] == symbols['binary']:
                relative = address - bias
                index = bisect.bisect_right(symbols.get('function_addresses', []), relative) - 1
                if index >= 0:
                    start, length, name = symbols['functions'][index]
                    if start <= relative < start + length:
                        entry.update(symbol=name, symbol_offset=relative - start)
            return entry

        pending = []
        contexts = {}

        def queue_pointer(address, origin, depth=0):
            region = mapping(address)
            if region is None or not region['permissions'].startswith('r'):
                return
            is_code = 'x' in region['permissions']
            start = max(region['start'], (address // 128) * 128 - 128) if is_code else (
                max(region['start'], (address // 1024) * 1024))
            key = (is_code, start)
            if key in contexts:
                item = contexts[key]
                if len(item['origins']) < 4 and origin not in item['origins']:
                    item['origins'].append(origin)
                return
            if len(pending) >= 2048:
                record['pointer_queue_truncated'] = True
                return
            item = {'address': start, 'pointer': address, 'code': is_code,
                    'depth': depth, 'origins': [origin]}
            contexts[key] = item
            pending.append(item)

        def memory(regs, origin):
            stack = read(regs['rsp'], 2048)
            entry = {'code': {'address': max(0, regs['rip'] - 16),
                              'hex': read(max(0, regs['rip'] - 16), 96).hex()},
                     'instruction_location': location(regs['rip']),
                     'stack': {'address': regs['rsp'], 'hex': stack.hex()},
                     'register_memory': {key: {'address': value, 'hex': data.hex()}
                        for key, value in regs.items() if key not in ('rip', 'rsp')
                        and (data := read(value, 64))},
                     'stack_code_candidates': []}
            for key, value in regs.items():
                queue_pointer(value, origin + ':' + key)
            for offset in range(0, len(stack) - 7, 8):
                address = int.from_bytes(stack[offset:offset + 8], 'little')
                candidate = location(address)
                if candidate:
                    entry['stack_code_candidates'].append(dict(candidate, stack_offset=offset))
                if offset < 512:
                    queue_pointer(address, origin + ':stack+' + str(offset))
            return entry

        if 'counters' in symbols:
            raw = read(bias + symbols['counters'], 13 * 48)
            if len(raw) == 13 * 48:
                names = ('draw dispatch ordered-ds bind-buffers bind-textures shader-compile '
                         'pipeline-lookup image-readback buffer-readback page-protect submit '
                         'gpu-wait buffer-clamp').split()
                fields = 'calls inclusive_ns self_ns max_ns bytes last_ns'.split()
                record['live_performance_regions'] = [dict(name=name,
                    **dict(zip(fields, struct.unpack_from('<6Q', raw, index * 48))))
                    for index, name in enumerate(names)]
            else:
                record['live_performance_error'] = 'COUNTER_MEMORY_UNAVAILABLE'

        latest = {}
        anonymous_sample_tids = set()
        sampler = symbols.get('sampler', {})
        if symbols.get('error') or symbols.get('sampler_error'):
            record['sampler_error'] = symbols.get('error') or symbols['sampler_error']
        elif set(sampler) == {'samples', 'sample_count', 'started'}:
            try:
                count = struct.unpack('<I', read(bias + sampler['sample_count'], 4))[0]
                seconds, nanoseconds = struct.unpack('<qq', read(bias + sampler['started'], 16))
                now_ms = (time.monotonic_ns() - seconds * 10**9 - nanoseconds) // 10**6
                record.update(samples_attempted=count, sampler_now_ms=now_ms,
                              sampler_capacity_exceeded=count > 32768)
                names = 'rip rsp rbp rax rbx rcx rdx rsi rdi r8 r9 r10 r11 r12 r13 r14 r15'.split()
                sample_bytes = min(count, 32768) * 152
                buffer = bytearray()
                while len(buffer) < sample_bytes:
                    chunk = read(bias + sampler['samples'] + len(buffer), sample_bytes - len(buffer))
                    if not chunk:
                        break
                    buffer.extend(chunk)
                record['samples_read'] = len(buffer) // 152
                for index in range(len(buffer) // 152 - 1, -1, -1):
                    raw = buffer[index * 152:(index + 1) * 152]
                    if len(raw) != 152 or raw[0] != 1:
                        continue
                    ready, tid, timestamp, *values = struct.unpack('<B3xIQ17Q', raw)
                    age = now_ms - timestamp
                    if not 0 <= age <= 2000:
                        continue
                    entries = latest.setdefault(tid, [])
                    is_anonymous = anonymous_executable(mapping(values[0]))
                    if (len(entries) < 3 or (is_anonymous and tid not in anonymous_sample_tids)) and not any(
                            x['registers']['rip'] == values[0] for x in entries):
                        entries.append({'sample_index': index, 'sample_ms': timestamp,
                                        'age_ms': age, 'registers': dict(zip(names, values))})
                        if is_anonymous:
                            anonymous_sample_tids.add(tid)
                    if time.monotonic() >= deadline:
                        break
            except struct.error:
                record['sampler_error'] = 'SAMPLER_MEMORY_UNAVAILABLE'
        tasks = []
        for task in (process / 'task').iterdir():
            try:
                runtime = int((task / 'schedstat').read_text().split()[0])
                tasks.append((runtime, int(task.name), (task / 'comm').read_text().strip()))
            except (OSError, ValueError):
                continue
        record['thread_count'] = len(tasks)
        record['threads_truncated'] = len(tasks) > record['maximum_threads']
        for runtime, tid, name in sorted(tasks, reverse=True)[:record['maximum_threads']]:
            if time.monotonic() >= deadline:
                record['budget_exhausted'] = True
                break
            item = {'tid': tid, 'name': name, 'runtime_ns': runtime, 'samples': []}
            record['threads'].append(item)
            for sample in latest.get(tid, []):
                item['samples'].append(dict(sample,
                    memory=memory(sample['registers'], f'{tid}:sample-{sample["sample_index"]}')))
            try:
                syscall = (process / 'task' / str(tid) / 'syscall').read_text().split()
                item['syscall'] = syscall
                if len(syscall) >= 3 and syscall[0] != 'running':
                    regs = dict(zip(('rsp', 'rip'), (int(v, 0) for v in syscall[-2:])))
                    for index, value in enumerate(syscall[1:-2]):
                        queue_pointer(int(value, 0), f'{tid}:syscall-arg-{index}')
                    item['blocked_syscall_memory'] = dict(registers=regs,
                        memory=memory(regs, f'{tid}:syscall'))
            except (OSError, ValueError) as error:
                item['syscall_error'] = str(error)
        record['threads_captured'] = len(record['threads'])
        data_count = code_count = 0
        for item in pending:
            if time.monotonic() >= deadline:
                record['budget_exhausted'] = True
                break
            if item['code']:
                if code_count >= record['maximum_code_blocks']:
                    record['code_context_truncated'] = True
                    continue
                code_count += 1
            else:
                if data_count >= record['maximum_data_blocks']:
                    record['data_context_truncated'] = True
                    continue
                data_count += 1
            raw = read(item['address'], 512 if item['code'] else 2048)
            record['pointer_context'].append(dict(item, hex=raw.hex()))
            if not item['code'] and item['depth'] < record['maximum_pointer_depth']:
                for offset in range(0, len(raw) - 7, 8):
                    queue_pointer(int.from_bytes(raw[offset:offset + 8], 'little'),
                                  f'{item["address"]:#x}+{offset}', item['depth'] + 1)
        record['pointer_context_bytes'] = sum(len(item['hex']) // 2
                                               for item in record['pointer_context'])
        record['reverse_context'] = capture_reverse_context(read, mapping, record['threads'], deadline)
        record['instruction_context'] = capture_instruction_context(read, mapping, pending, deadline)
    except (OSError, RuntimeError, ValueError, StopIteration) as error:
        record['error'] = str(error)
    finally:
        if mem is not None:
            os.close(mem)
        record['elapsed_seconds'] = time.monotonic() - began
        save(out / f'owned-live-memory-{second:03d}s.json', record)


def summarize(console,out):
    total=0
    important=[]
    warnings=0
    errors=0
    gpu_failure=None
    srt_rejections={}
    gpu_memory_fault_reports=0
    gpu_memory_fault_pages={}
    srt_source_preparations=0
    srt_source_blocks={}
    memory_histograms_truncated=False
    graphics_diagnostics=[]
    graphics_diagnostic_bytes=0
    graphics_diagnostics_truncated=False
    graphics_event_counts={}
    graphics_diagnostics_active=False
    gpu_work=[]
    gpu_work_bytes=0
    gpu_work_entries=0
    gpu_work_truncated=False
    gpu_work_overwritten=0
    performance=[]
    performance_bytes=0
    performance_truncated=False
    with console.open('rb') as stream:
        for raw in stream:
            total+=1
            text=raw.decode('utf-8','replace').strip()
            if text.startswith('PERF_'):
                line=text[:8192]+'\n'
                encoded_size=len(line.encode('utf-8'))
                if performance_bytes+encoded_size<=512*1024:
                    performance.append(line)
                    performance_bytes+=encoded_size
                else:
                    performance_truncated=True
            if text.startswith('GRAPHICS_WORK'):
                gpu_work_entries+=int(text.startswith('GRAPHICS_WORK seq='))
                gpu_work_truncated|=text.startswith('GRAPHICS_WORK_TRUNCATED')
                overwritten=re.search(r'\boverwritten=(\d+)\b',text)
                if overwritten:
                    gpu_work_overwritten=max(gpu_work_overwritten,int(overwritten.group(1)))
                line=text[:4096]+'\n'
                size=len(line.encode('utf-8'))
                if gpu_work_bytes+size<=8*1024**2:
                    gpu_work.append(line)
                    gpu_work_bytes+=size
                else:
                    gpu_work_truncated=True
            diagnostic=re.search(r'GRAPHICS_DIAG ms=\d+ event=([a-z-]+) count=(\d+)\b',text)
            if diagnostic:
                event=diagnostic.group(1)
                graphics_diagnostics_active=graphics_diagnostics_active or event=='enabled'
                if event in graphics_event_counts or len(graphics_event_counts)<64:
                    graphics_event_counts[event]=max(graphics_event_counts.get(event,0),
                                                     int(diagnostic.group(2)))
                line=text[:4096]+'\n'
                size=len(line.encode('utf-8'))
                if graphics_diagnostic_bytes+size<=4*1024**2:
                    graphics_diagnostics.append(line)
                    graphics_diagnostic_bytes+=size
                else:
                    graphics_diagnostics_truncated=True
            fault=re.search(r'Accessed non-GPU cached memory at (0x[0-9a-fA-F]+)',text)
            prepared=re.search(r'SrtDmaSource: .*first_block=(\d+) last_block=(\d+)',text)
            if fault:
                gpu_memory_fault_reports+=1
                page=hex(int(fault.group(1),16))
                if page in gpu_memory_fault_pages or len(gpu_memory_fault_pages)<4096:
                    gpu_memory_fault_pages[page]=gpu_memory_fault_pages.get(page,0)+1
                else:
                    memory_histograms_truncated=True
            if prepared:
                srt_source_preparations+=1
                blocks=prepared.group(1)+':'+prepared.group(2)
                if blocks in srt_source_blocks or len(srt_source_blocks)<4096:
                    srt_source_blocks[blocks]=srt_source_blocks.get(blocks,0)+1
                else:
                    memory_histograms_truncated=True
            if len(text)>1500:
                text=text[:1500]
            if ' <Error>' in text:
                errors+=1
            if ' <Warning>' in text:
                warnings+=1
            if any(term in text for term in GPU_FAILURE_TERMS) and gpu_failure is None:
                gpu_failure=text
            match=re.search(r'Unexpected instruction for offset computation, (\w+)', text)
            if match:
                opcode=match.group(1)
                srt_rejections[opcode]=srt_rejections.get(opcode, 0)+1
            if any(term in text for term in ERROR_TERMS + GPU_FAILURE_TERMS):
                if len(important)<420:
                    important.append(text)
    (out/'selected-console-diagnostics.txt').write_text(
        '\n'.join(important)+'\n',encoding='utf-8')
    (out/'graphics-diagnostics.log').write_text(''.join(graphics_diagnostics),encoding='utf-8')
    (out/'gpu-work.log').write_text(''.join(gpu_work),encoding='utf-8')
    (out/'performance.log').write_text(''.join(performance),encoding='utf-8')
    with console.open('rb') as stream:
        size=console.stat().st_size
        if size>2*1024*1024:
            first=stream.read(512*1024)
            stream.seek(-1024*1024,os.SEEK_END)
            tail=stream.read(1024*1024)
            excerpt=first+b'\n...[middle omitted, original full log retained on server]...\n'+tail
        else:
            excerpt=stream.read()
    (out/'console-excerpt.log').write_bytes(excerpt)
    return {'gpu_failure':gpu_failure,'srt_rejections':srt_rejections,'console_lines':total,'warning_lines':warnings,'error_lines':errors,
            'performance_records':len(performance),
            'performance_bytes':performance_bytes,
            'performance_truncated':performance_truncated,
            'gpu_work_entries':gpu_work_entries,
            'gpu_work_bytes':gpu_work_bytes,
            'gpu_work_truncated':gpu_work_truncated,
            'gpu_work_overwritten':gpu_work_overwritten,
            'graphics_diagnostics_active':graphics_diagnostics_active,
            'graphics_event_sampled_max_counts':graphics_event_counts,
            'graphics_diagnostics_bytes':graphics_diagnostic_bytes,
            'graphics_diagnostics_truncated':graphics_diagnostics_truncated,
            'selected_diagnostic_lines':len(important),
            'full_console_bytes':size,
            'console_excerpt_complete':size<=2*1024*1024,
            'gpu_memory_fault_reports':gpu_memory_fault_reports,
            'gpu_memory_fault_pages':gpu_memory_fault_pages,
            'srt_source_preparations':srt_source_preparations,
            'srt_source_blocks':srt_source_blocks,
            'memory_histograms_truncated':memory_histograms_truncated}


def capture_native_mappings(pid, out):
    # Read-only /proc evidence. Single overwritten snapshot, never a huge trace.
    try:
        content=Path(f'/proc/{pid}/maps').read_text(errors='replace')
        if len(content)<1024*1024:
            (out/'last-process-mappings.txt').write_text(content)
    except (OSError, UnicodeError):
        pass


def capture_performance_usage(pid, out, elapsed, proc=Path('/proc'),
                              drm=Path('/sys/class/drm')):
    record={'elapsed_seconds':round(elapsed,3),
            'monotonic_seconds':round(time.monotonic(),6),'gpus':[]}
    try:
        record['process_stat']=(proc/str(pid)/'stat').read_text()[:4096]
    except OSError as error:
        record['process_stat_error']=str(error)
    record['drm_clients']=[]
    seen=set()
    try:
        descriptors=sorted((proc/str(pid)/'fdinfo').iterdir(),key=lambda p:int(p.name))
    except OSError as error:
        descriptors=[]
        record['drm_client_error']=str(error)
    record['drm_descriptors_truncated']=len(descriptors)>512
    for descriptor in descriptors[:512]:
        try:
            fields={}
            with descriptor.open() as stream:
                for line in stream.read(16384).splitlines():
                    key,separator,value=line.partition(':')
                    if separator and key.startswith('drm-'):
                        fields[key]=value.strip()
            if 'drm-driver' not in fields:
                continue
            identity=(fields.get('drm-pdev'),fields['drm-driver'],fields.get('drm-client-id'))
            if identity[2] is not None and identity in seen:
                continue
            seen.add(identity)
            record['drm_clients'].append({'fd':descriptor.name,'fields':fields})
        except OSError:
            continue
    cards=sorted(p for p in drm.glob('card*') if re.fullmatch(r'card\d+',p.name))
    for card in cards[:8]:
        gpu={'card':card.name,'device':(card/'device').resolve().name}
        for metric in ('gpu_busy_percent','mem_busy_percent','mem_info_vram_used',
                       'mem_info_vram_total','mem_info_gtt_used'):
            try:
                value=(card/'device'/metric).read_text()[:128].strip()
                if value.isdecimal():
                    gpu[metric]=int(value)
            except OSError:
                pass
        record['gpus'].append(gpu)
    with (out/'performance-usage.jsonl').open('a') as stream:
        stream.write(json.dumps(record)+'\n')
    return record


def gpu_memory_headroom_failure(usage):
    devices={client['fields'].get('drm-pdev') for client in usage['drm_clients']}
    cards=[gpu for gpu in usage['gpus'] if gpu.get('mem_info_vram_total',0)>0 and
           'mem_info_vram_used' in gpu]
    selected=[gpu for gpu in cards if gpu['device'] in devices]
    if not selected and cards:
        selected=[max(cards,key=lambda gpu:gpu['mem_info_vram_total'])]
    for gpu in selected:
        free=gpu['mem_info_vram_total']-gpu['mem_info_vram_used']
        if free<8*GIB:
            return {'card':gpu['card'],'device':gpu['device'],'free_bytes':free,
                    'minimum_free_bytes':8*GIB,'elapsed_seconds':usage['elapsed_seconds']}
    return None


def collect_gpu_clients(proc=Path('/proc'), max_seconds=0.25,
                        max_processes=4096, max_descriptors=65536,
                        kfd=Path('/sys/class/kfd/kfd/proc')):
    began = time.monotonic()
    record = {'monotonic_seconds': began, 'clients': [], 'processes_scanned': 0,
              'descriptors_scanned': 0, 'unreadable_processes': 0,
              'unreadable_descriptors': 0, 'truncated': False,
              'scope': 'readable DRM fdinfo and KFD counters; shared clients may have multiple '
                       'holders; counters must not be summed across interfaces',
              'effective_uid': os.geteuid(), 'kfd_processes': [],
              'kfd_available': False}
    try:
        for process in kfd.iterdir():
            record['kfd_available'] = True
            if not process.name.isdecimal():
                continue
            if time.monotonic() - began >= max_seconds or len(record['kfd_processes']) >= 128:
                record['truncated'] = True
                break
            item = {'pid': int(process.name), 'vram_bytes_by_gpu': {}}
            try:
                for counter in process.iterdir():
                    if (time.monotonic() - began >= max_seconds or
                            len(item['vram_bytes_by_gpu']) >= 32):
                        record['truncated'] = True
                        break
                    if not re.fullmatch(r'vram_\d+', counter.name):
                        continue
                    with counter.open() as stream:
                        value = stream.read(128).strip()
                    if value.isdecimal():
                        item['vram_bytes_by_gpu'][counter.name[5:]] = int(value)
                for name in ('comm', 'cgroup'):
                    with (proc / process.name / name).open() as stream:
                        item[name] = stream.read(4096).strip()
            except OSError as error:
                item['read_error'] = str(error)
            record['kfd_processes'].append(item)
        record['kfd_available'] = True
    except OSError as error:
        record['kfd_error'] = str(error)
    try:
        for process in proc.iterdir():
            if not process.name.isdecimal():
                continue
            if (time.monotonic() - began >= max_seconds or
                    record['processes_scanned'] >= max_processes):
                record['truncated'] = True
                break
            record['processes_scanned'] += 1
            try:
                uid = process.stat().st_uid
                with (process / 'comm').open() as stream:
                    name = stream.read(256).strip()
                cgroup = None
                seen = set()
                for descriptor in (process / 'fdinfo').iterdir():
                    if (time.monotonic() - began >= max_seconds or
                            record['descriptors_scanned'] >= max_descriptors):
                        record['truncated'] = True
                        break
                    record['descriptors_scanned'] += 1
                    try:
                        with descriptor.open() as stream:
                            data = stream.read(16384)
                    except OSError:
                        record['unreadable_descriptors'] += 1
                        continue
                    fields = {}
                    for line in data.splitlines():
                        key, separator, value = line.partition(':')
                        if separator and key.startswith('drm-'):
                            fields[key] = value.strip()
                    if 'drm-driver' not in fields:
                        continue
                    identity = (fields.get('drm-pdev'), fields['drm-driver'],
                                fields.get('drm-client-id'))
                    if identity[2] is not None and identity in seen:
                        continue
                    seen.add(identity)
                    if cgroup is None:
                        try:
                            with (process / 'cgroup').open() as stream:
                                cgroup = stream.read(4096).strip()
                        except OSError:
                            cgroup = 'UNAVAILABLE'
                    record['clients'].append({'pid': int(process.name), 'uid': uid,
                                              'name': name, 'fd': descriptor.name,
                                              'cgroup': cgroup, 'fields': fields})
                    if len(record['clients']) >= 128:
                        record['truncated'] = True
                        break
            except OSError:
                record['unreadable_processes'] += 1
            if record['truncated']:
                break
    except OSError as error:
        record['error'] = str(error)
    record['elapsed_seconds'] = round(time.monotonic() - began, 6)
    return record


def capture_gpu_clients(out, label, timeout=0.6):
    privileged = os.environ.get('SHADPS4_KERNEL_JOURNAL_SUDO') == '1'
    command = [sys.executable, str(Path(__file__).resolve()), '--gpu-client-snapshot']
    if privileged:
        collector = ('import json, os, re, time\nfrom pathlib import Path\n' +
                     inspect.getsource(collect_gpu_clients) +
                     '\nprint(json.dumps(collect_gpu_clients()))\n')
        command = ['sudo', '-n', '--', '/usr/bin/python3', '-I', '-c', collector]
    try:
        captured = subprocess.run(
            command,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
        if captured.returncode:
            record = {'error': 'GPU_CLIENT_SNAPSHOT_FAILED', 'exit_code': captured.returncode}
        else:
            record = json.loads(captured.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        record = {'error': type(error).__name__}
    record['privileged_requested'] = privileged
    save(out / f'gpu-clients-{label}.json', record)


def classify_native_fault(console, out, binary):
    # A fault RIP in a PIE binary must be corrected by the mapping file
    # offset; don't mistake a generic SIGABRT for the true preceding SIGSEGV.
    maps=out/'last-process-mappings.txt'
    if not maps.is_file():
        return {"fault_mapping":"NOT_OBSERVED"}
    fault=None
    with console.open('rb') as stream:
        for raw in stream:
            line=raw.decode('utf-8','replace')
            match=re.search(
                r'Unhandled access violation at code address (0x[0-9a-fA-F]+)',line)
            if match:
                fault=int(match.group(1),16)
    if fault is None:
        return {"fault_mapping":"NO_UNHANDLED_ACCESS_REPORTED"}
    for row in maps.read_text(errors='replace').splitlines():
        parts=row.split(maxsplit=5)
        if len(parts)<5 or '-' not in parts[0]:
            continue
        try:
            begin,end=(int(x,16) for x in parts[0].split('-',1))
            offset=int(parts[2],16)
        except ValueError:
            continue
        if begin<=fault<end:
            result={"fault_rip":hex(fault),"fault_mapping":row,
                    "elf_address":hex(fault-begin+offset)}
            if len(parts)>5 and Path(parts[5]).name==binary.name:
                result["emulator_executable_rip"]=True
            return result
    return {"fault_rip":hex(fault),"fault_mapping":"NOT_IN_LAST_SNAPSHOT"}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',required=True,type=Path)
    p.add_argument('--sha256',required=True)
    p.add_argument('--game',required=True)
    p.add_argument('--cwd',required=True,type=Path)
    p.add_argument('--report',required=True,type=Path)
    p.add_argument('--seconds',required=True,type=int)
    args=p.parse_args()
    if args.seconds!=118:
        p.error('Hard limit fixed at 118 seconds SIGTERM, +2 seconds SIGKILL')
    if not re.fullmatch(r'[a-f0-9]{64}',args.sha256):
        p.error('Candidate hash must be full lowercase SHA256')
    if not args.game or args.game.startswith('-') or '\0' in args.game:
        p.error('Invalid game selector')
    binary=args.binary.resolve(strict=True)
    if binary!=args.binary or args.binary.is_symlink() or digest(binary)!=args.sha256:
        p.error('Candidate binary not the immutable reviewed native executable')
    if not args.cwd.is_dir() or not (args.cwd/'user').is_dir():
        p.error('Isolated portable user profile missing')
    busy=existing_games()
    if busy:
        p.error('An emulator is already running; refuse another: '+str(busy))
    report=args.report
    report.mkdir(parents=True,exist_ok=False)
    capture_gpu_clients(report, 'before-launch')
    env=os.environ.copy()
    for key in tuple(env):
        if key.startswith(('SHADPS4_GOW_','SHADPS4_GOT_','SHADPS4_CPU_ID_')):
            del env[key]
    env.setdefault('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}')
    env.setdefault('DBUS_SESSION_BUS_ADDRESS',
                   f'unix:path={env["XDG_RUNTIME_DIR"]}/bus')
    display=env.get('DISPLAY',':0')
    env['DISPLAY']=display
    argv=[str(binary),args.game,'--fullscreen','true','--show-fps']
    launch={
        'command':argv,'sha256':args.sha256,'cpu':'emulator-native/no-DynamoRIO',
        'memory':'global emulator config','automated_game_inputs':False,
        'runtime_limit_seconds':120,'signal_at_seconds':118,
        'ordered_gds_capture_requested':env.get('SHADPS4_PR117_CAPTURE_ONCE')=='1',
        'graphics_diagnostics_requested':env.get('SHADPS4_GRAPHICS_DIAGNOSTICS')=='1',
        'vulkan_diagnostics_requested':env.get('SHADPS4_VULKAN_DIAGNOSTICS')=='1',
        'performance_diagnostics_requested':env.get('SHADPS4_PERFORMANCE_DIAGNOSTICS')=='1'
    }
    result={'binary_sha256':args.sha256,'game':args.game,
            'duration_limit':120,'time_to_sigterm':118,
            'graphics_visual_acceptance':'NOT_OBSERVED',
            'audio_audible_acceptance':'NOT_OBSERVED',
            'gameplay_acceptance':'NOT_OBSERVED','screenshots':[],
            'owned_child_terminated':False,'exit_code':None}
    live_symbols = prepare_live_symbols(binary) if launch['performance_diagnostics_requested'] else None
    console=report/'full-console.log'
    child=None
    began=time.monotonic()
    failure=None
    previous_map_capture=0.0
    try:
        with console.open('wb',buffering=0) as output:
            child=subprocess.Popen(argv,cwd=args.cwd,env=env,
                stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                start_new_session=True)
            result['owned_child_pid']=child.pid
            save(report/'launch.json',dict(launch,pid=child.pid))
            print(f'NATIVE_GENERIC_GAME_LAUNCHED=YES PID={child.pid}',flush=True)
            due=(8,25,80,110)
            taken=set()
            gpu_failure=None
            memory_failure=None
            validation_error=None
            console_offset=0
            pending_line=''
            while child.poll() is None and time.monotonic()-began<118:
                elapsed=time.monotonic()-began
                usage=capture_performance_usage(child.pid,report,elapsed)
                memory_failure=gpu_memory_headroom_failure(usage)
                if memory_failure:
                    result['gpu_memory_headroom_failure']=memory_failure
                    result['exit_reason']='GPU_MEMORY_HEADROOM_EXHAUSTED'
                    snapshot_budget = min(0.6, 118 - (time.monotonic() - began))
                    if snapshot_budget > 0.05:
                        capture_gpu_clients(report, 'headroom-failure', snapshot_budget)
                    break
                with console.open('rb') as monitor:
                    monitor.seek(console_offset)
                    recent = monitor.read(4 * 1024**2).decode('utf-8', 'replace')
                    console_offset = monitor.tell()
                complete, _, pending_line = (pending_line + recent).rpartition('\n')
                gpu_failure = next((line for line in complete.splitlines()
                                    if any(term in line for term in GPU_FAILURE_TERMS)), None)
                if gpu_failure:
                    result['gpu_failure'] = gpu_failure
                    result['exit_reason'] = 'GPU_CONTEXT_LOST'
                    break
                if launch['vulkan_diagnostics_requested']:
                    validation_error = next((message for line in complete.splitlines()
                                             if (message := validation_failure(line))), None)
                    if validation_error:
                        result['first_validation_failure'] = validation_error
                        result['exit_reason'] = 'VULKAN_VALIDATION_FAILED'
                        break
                if elapsed-previous_map_capture>=1.0:
                    capture_native_mappings(child.pid,report)
                    previous_map_capture=elapsed
                for second in due:
                    if elapsed>=second and second not in taken:
                        taken.add(second)
                        if second in (25, 80):
                            snapshot_budget = min(0.6, 118 - (time.monotonic() - began))
                            if snapshot_budget > 0.05:
                                capture_gpu_clients(report, f'{second:03d}s', snapshot_budget)
                        capture_owned_stop_state(child.pid,report,
                            filename=f'owned-process-state-{second:03d}s.json')
                        if second in (80, 110) and live_symbols is not None:
                            capture_owned_live_memory(child.pid, report, second, live_symbols)
                        if take_picture(report,second,display):
                            result['screenshots'].append(f'frame-{second:03d}s.png')
                time.sleep(min(0.4,max(0.0,118-elapsed)))
            if gpu_failure:
                result['exit_reason']='GPU_CONTEXT_LOST'
            elif memory_failure:
                result['exit_reason']='GPU_MEMORY_HEADROOM_EXHAUSTED'
            elif validation_error:
                result['exit_reason']='VULKAN_VALIDATION_FAILED'
            elif child.poll() is None:
                result['exit_reason']='TEST_118S_DURATION_LIMIT'
            else:
                result['exit_reason']='GAME_EXITED_BEFORE_LIMIT'
                result['exit_code']=child.returncode
    except Exception as error:
        failure=f'{type(error).__name__}: {error}'
        print('OBSERVER_ERROR='+failure,flush=True)
        result['exit_reason']='OBSERVER_EXCEPTION'
    finally:
        result['trigger_reason'] = result.get('exit_reason')
        if child is not None:
            if child.poll() is None:
                stop_error=term_owned(child,'TERM_THEN_OPTIONAL_KILL',began+120.0)
                if stop_error:
                    result['termination_error']=stop_error
            result['owned_child_terminated']=child.poll() is not None
            result['exit_code']=child.poll()
        result['elapsed_seconds']=round(time.monotonic()-began,3)
        capture_gpu_clients(report, 'after-stop')
        if console.is_file():
            try:
                result.update(summarize(console,report))
                result.update(summarize_validation(console,report,launch['vulkan_diagnostics_requested']))
                if result.get('gpu_failure'):
                    result['exit_reason']='GPU_CONTEXT_LOST'
                elif launch['vulkan_diagnostics_requested']:
                    if result.get('first_validation_failure'):
                        result['exit_reason']='VULKAN_VALIDATION_FAILED'
                    elif not result.get('validation_layer_active'):
                        result['exit_reason']='VULKAN_VALIDATION_NOT_OBSERVED'
                if (launch['graphics_diagnostics_requested'] and
                        not result.get('graphics_diagnostics_active')):
                    result['graphics_diagnostics_failure']='GRAPHICS_DIAGNOSTICS_NOT_OBSERVED'
                    if result['exit_reason'] in ('TEST_118S_DURATION_LIMIT','GAME_EXITED_BEFORE_LIMIT'):
                        result['exit_reason']='GRAPHICS_DIAGNOSTICS_NOT_OBSERVED'
                result["native_fault"]=classify_native_fault(console,report,binary)
                if result["native_fault"].get("fault_rip"):
                    save(report/'native-fault-mapping.json',result["native_fault"])
            except Exception as error:
                failure=f'{type(error).__name__}: {error}'
        if not result['owned_child_terminated'] and child is not None:
            capture_owned_stop_state(child.pid,report)
            result['exit_code'] = child.poll()
            result['owned_child_terminated'] = result['exit_code'] is not None
            if not result['owned_child_terminated']:
                result['exit_reason']='OWNED_PROCESS_STOP_UNCONFIRMED'
            elif result.get('termination_error'):
                result['termination_confirmed_after_timeout'] = True
        result['elapsed_seconds']=round(time.monotonic()-began,3)
        if failure:
            result['failure']=failure
        save(report/'native-result.json',result)
    print('NATIVE_GENERIC_TEST_RESULT='+result['exit_reason'],flush=True)
    print('GAME_EXIT_CODE='+str(result['exit_code']),flush=True)
    print('TEST_RUNTIME_SECONDS='+str(result['elapsed_seconds']),flush=True)
    print('GAME_PROCESS_LEFT_RUNNING='+('NO' if result['owned_child_terminated'] else 'UNKNOWN'),flush=True)
    print('NO_AUTOMATIC_INPUT=YES; FULLSCREEN_AND_FPS=YES; NO_CPU_ID_OVERRIDES=YES',flush=True)
    return 0 if not failure and not result.get('gpu_failure') and not result.get('gpu_memory_headroom_failure') and not result.get('graphics_diagnostics_failure') and not (
        launch['vulkan_diagnostics_requested'] and (
            result.get('first_validation_failure') or not result.get('validation_layer_active'))
        ) and result['owned_child_terminated'] and (
        result['exit_reason']=='TEST_118S_DURATION_LIMIT' or result['exit_code']==0) else 1


if __name__=='__main__':
    if sys.argv[1:] == ['--gpu-client-snapshot']:
        print(json.dumps(collect_gpu_clients()))
        raise SystemExit(0)
    raise SystemExit(main())
