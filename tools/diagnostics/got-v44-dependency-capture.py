#!/usr/bin/env python3
"""GoT v44: observe the blocked dependency chain, not high-volume frame counters.

Retains the pinned v43 shader-rejection callback, v40 executable and private RADV.
Uses two hardware write watchpoints on the captured dependency words instead of
v42's two hot counters. Snapshots follow the pending-list and ProxySetSync's actual
completion-slot selection. No game-data edits, shader bypass or emulator rebuild.
"""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import types

LABEL = 'got-v44-dependency-visual'
PREVIOUS_BLOB = '56694cae40af4794260187d731cca03bc168e819'

# Definitions evaluated inside the existing, unprivileged GDB process.
# Addresses/offsets are checked against the exact guest instructions before arming.
GDB_SUPPORT = r'''
DEP_ADDRESSES = (0x3fb64e4, 0x3fb652c)
DEP_MAX_EVENTS = 2048
DEP_SECONDS = 75
DEP_BYTE_LIMIT = 1024 * 1024
dep_addresses = DEP_ADDRESSES
dep_last = None
dep_first_negative = False
dep_game_mode = True

def dep_read(inf, address, size):
    if not isinstance(address, int) or not isinstance(size, int) or not 0 < size <= DEP_BYTE_LIMIT:
        raise ValueError('Invalid bounded memory read')
    if not 0x10000 <= address < (1 << 47) or address + size > (1 << 47):
        raise ValueError('Memory address outside allowed user range')
    return bytes(inf.read_memory(address, size))

def dep_u32(inf, address, signed=False):
    return int.from_bytes(dep_read(inf, address, 4), 'little', signed=signed)

def dep_u64(inf, address):
    return int.from_bytes(dep_read(inf, address, 8), 'little')

def dep_region(inf, address, size):
    result = {'address':hex(address), 'size':size}
    try: result['bytes'] = dep_read(inf, address, size).hex()
    except Exception as exc: result['error'] = str(exc)
    return result

def dep_values(inf):
    return [dep_u32(inf, a, True) for a in dep_addresses]

def dep_proxy(inf, manager):
    # f60790 reads signed slot index at +0x1f34, then a qword from
    # [*[0x1ad3208] + index*8]; compare uses only the low 32 bits.
    result = {'manager':hex(manager), 'read_only':True}
    try:
        target = dep_u32(inf, manager+0x1f30)
        index = dep_u32(inf, manager+0x1f34, True)
        table = dep_u64(inf, 0x1ad3208)
        result.update(target=target, slot_index=index, slot_table=hex(table),
                      equeue_handle=dep_u64(inf, manager+0xf420),
                      gate=hex(manager+0xf260),
                      gate_matches_captured_address=manager+0xf264 == DEP_ADDRESSES[0])
        if not 0 <= index < 4096: raise ValueError('Invalid/bounded completion-slot index')
        slot = table+index*8
        raw = dep_u64(inf, slot)
        result.update(slot_address=hex(slot), slot_qword=hex(raw), slot_low32=raw & 0xffffffff,
                      guest_unsigned_compare_satisfied=target <= (raw & 0xffffffff))
        result['manager_controls'] = dep_region(inf, manager+0x1f20, 48)
    except Exception as exc: result['error'] = str(exc)
    return result

def dep_graph(inf, guest_threads):
    result = {'gameplay_verified':False, 'inferior_data_written':False}
    try:
        result['main_counters'] = [dep_u32(inf,a) for a in (0x2c5d8b0,0x2c5d8b4)]
        result['dependency_signed_words'] = [dep_u32(inf,a,True) for a in DEP_ADDRESSES]
    except Exception as exc: result['counter_error'] = str(exc)
    result['group_context'] = dep_region(inf,0x3fb64c0,512)
    result['worker_queues'] = {
        'low':dep_region(inf,0x2c7fe08,48), 'normal':dep_region(inf,0x2c7fe38,48)}
    try:
        ptr = dep_u64(inf,0x3fb6578)
        count = dep_u32(inf,0x3fb6580)
        capacity = dep_u32(inf,0x3fb6584)
        result['pending_list'] = {'pointer':hex(ptr), 'count':count, 'capacity':capacity,
                                  'record_semantics_not_assumed':True}
        if count > capacity or capacity > 1_000_000:
            raise ValueError('Invalid pending-list count/capacity')
        rows = []
        # This array is live guest data, not a code or full-memory dump.
        for i in range(min(count,32)):
            addr = dep_u64(inf,ptr+i*8)
            rows.append({'index':i, **dep_region(inf,addr,160)})
        result['pending_list'].update(records=rows,truncated=count > 32)
    except Exception as exc: result['pending_list_error'] = str(exc)
    result['proxy_sync'] = []
    for thread in guest_threads:
        if thread.get('name') != 'ProxySetSync': continue
        try:
            manager = int(thread['registers']['rbx'],16)
            result['proxy_sync'].append(dep_proxy(inf,manager))
        except Exception as exc: result['proxy_sync'].append({'error':str(exc)})
    return result
'''

GDB_WATCH = r'''
class JobCounterWrite(gdb.Breakpoint):
    def __init__(self, address):
        self.address = address
        super().__init__('*(int*)'+hex(address), type=gdb.BP_WATCHPOINT,
                         wp_class=gdb.WP_WRITE, internal=True)
    def stop(self):
        global job_events, job_last, job_limit_pending, job_stop_detail
        global dep_last, dep_first_negative
        try:
            inf = gdb.selected_inferior(); frame = gdb.newest_frame(); thread = gdb.selected_thread()
            values = dep_values(inf); job_events += 1
            row = {'event':job_events, 'seconds_since_arm':time.monotonic()-job_started,
                   'watched_address':hex(self.address), 'dependency_before':dep_last,
                   'dependency_after':values, 'values_before':dep_last, 'values_after':values,
                   'main_counters':job_values(),
                   'thread':thread.num, 'name':thread.name, 'ptid':list(thread.ptid),
                   'pc_after_write':hex(int(frame.pc())), 'registers':job_frame_info(frame)['registers'],
                   'inferior_data_written':False, 'gameplay_verified':False}
            # Save the first observed transition into a negative count, not an
            # assumption that every negative value is an emulator error.
            became_negative = dep_last is not None and any(a >= 0 > b for a,b in zip(dep_last,values))
            if became_negative and not dep_first_negative:
                dep_first_negative = True
                row['first_negative_transition'] = True
                row['backtrace'] = execute('bt 16')[:20000]
                row['group_context'] = dep_region(inf,0x3fb64c0,512)
                pc = int(frame.pc())
                row['writer_code'] = dep_region(inf,pc-32,96)
                if thread.name == 'ProxySetSync':
                    row['proxy_sync'] = dep_proxy(inf,int(frame.read_register('rbx')))
                stall_write('dependency-first-negative.json',row)
            name = 'dependency-writes.log' if dep_game_mode else 'job-counter-writes.log'
            with (out/name).open('a') as stream:
                stream.write(json.dumps(row)+'\n')
            dep_last = values; job_last = row['main_counters']
            if job_events >= DEP_MAX_EVENTS or time.monotonic()-job_started >= DEP_SECONDS:
                job_limit_pending = True
                job_stop_detail = 'DEPENDENCY_TRACE_LIMIT_REACHED_INCONCLUSIVE'
                return True
            return False
        except Exception as exc:
            job_limit_pending = True; job_stop_detail = 'DEPENDENCY_TRACE_ERROR: '+str(exc)
            stall_write('dependency-watch-error.json',{'error':str(exc),'events':job_events})
            return True
'''

GDB_ARM = r'''
def job_arm(verify_game=True):
    global job_started, job_last, dep_last, dep_addresses, dep_game_mode
    if job_started is not None: raise RuntimeError('Dependency trace already armed')
    inf = gdb.selected_inferior()
    dep_game_mode = verify_game
    if verify_game:
        fingerprints = (
            (0xb09950,'8b055e3f15023b05543f150275a2'),
            (0xc03a1e,'f0ff4f04'),
            (0xf60744,'4c8db760f20000'),
            (0xf60790,'486383341f0000488b0d6a2ab700488b04c13983301f000076b6'))
        for addr,hexbytes in fingerprints:
            expected = bytes.fromhex(hexbytes)
            if dep_read(inf,addr,len(expected)) != expected:
                raise RuntimeError('Captured guest code differs at '+hex(addr))
        dep_addresses = DEP_ADDRESSES
    else:
        dep_addresses = JOB_ADDRESSES
    values = dep_values(inf)
    try:
        for address in dep_addresses:
            bp = JobCounterWrite(address); job_watches.append(bp)
            if bp.type != gdb.BP_HARDWARE_WATCHPOINT:
                raise RuntimeError('Hardware WRITE watchpoints required; no software fallback')
        dep_last = values; job_last = job_values(); job_started = time.monotonic()
        stall_write('job-watch-armed.json', {'ok':True,'kind':'DEPENDENCY_WORDS_NOT_MAIN_COUNTERS',
                    'addresses':[hex(x) for x in dep_addresses], 'initial_signed_values':values,
                    'main_counters':job_last,'hardware_only':True,'max_events':DEP_MAX_EVENTS,
                    'max_seconds':DEP_SECONDS,'gameplay_verified':False,'inferior_data_written':False})
        print('GOT44_DEPENDENCIES=ARMED; MAIN_COUNTER_WATCHPOINTS=REMOVED',flush=True)
    except Exception:
        for bp in job_watches:
            if bp.is_valid(): bp.delete()
        job_watches.clear(); raise
'''

GDB_SNAPSHOT = r'''
_original_job_snapshot = job_snapshot

def job_snapshot(number):
    data = _original_job_snapshot(number)
    detail = dep_graph(gdb.selected_inferior(),data.get('guest_threads',[]))
    detail.update(snapshot=number,dependency_events_observed=job_events)
    stall_write('dependency-state-%02d.json' % number,detail)
    data['dependency_detail'] = detail
    # Keep raw machine code around the list consumer and ProxySetSync comparison
    # for validating pointer-field interpretation against this exact executable.
    if number == 1:
        for tag,addr,size in (('list-consumer',0xb09780,352),('proxy-full',0xf60730,384),
                              ('submit-job',0xc03520,384)):
            block = dep_region(gdb.selected_inferior(),addr,size)
            block['disassembly'] = execute('disassemble /r '+hex(addr)+','+hex(addr+size))[:24000]
            stall_write('dependency-code-'+tag+'.json',block)
    stall_write('job-state-%02d.json' % number,data)
    return data
'''


def replace_definition(text: str, name: str, replacement: str) -> str:
    tree = ast.parse(text)
    found = [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name == name]
    if len(found) != 1: raise RuntimeError('Expected one debugger definition: '+name)
    lines = text.splitlines(keepends=True); node = found[0]
    return ''.join(lines[:node.lineno-1])+replacement.strip()+'\n'+''.join(lines[node.end_lineno:])


def extend_gdb(text: str) -> str:
    # The captured comparison remains unchanged. Only its two watchpoints move.
    if text.count('JOB_ADDRESSES = (0x2c5d8b0, 0x2c5d8b4)') != 1:
        raise RuntimeError('Pinned counter constants changed')
    if text.count('def stall_write(name, obj):') != 1:
        raise RuntimeError('Pinned snapshot boundary changed')
    text = replace_definition(text,'JobCounterWrite',GDB_WATCH)
    text = replace_definition(text,'job_arm',GDB_ARM)
    text = text.replace('def stall_write(name, obj):',GDB_SNAPSHOT+'\ndef stall_write(name, obj):',1)
    result = GDB_SUPPORT+'\n'+text
    compile(result,'<got44-gdb>','exec')
    return result


def load_previous(script: Path):
    path = script.with_name('got-v43-spirv-rejection-capture.py')
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 100_000:
        raise RuntimeError('The existing v43 helper is missing or unsafe')
    raw = path.read_bytes()
    blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    if blob != PREVIOUS_BLOB: raise RuntimeError('v43 helper differs; no unknown dependency executed')
    prior = types.ModuleType('got44_verified_v43'); prior.__file__ = str(script)
    exec(compile(raw,str(path),'exec'),prior.__dict__)
    original_load = prior.load_previous
    def load_previous(inner_script):
        module = original_load(inner_script)
        module.LABEL = LABEL
        module.GDB_STALL = extend_gdb(module.GDB_STALL)
        # Exercise this revised callback in the existing real threaded preflight.
        smoke_globals = module.hardware_smoke.__globals__
        smoke_source = smoke_globals['GDB_JOB']
        smoke_source = replace_definition(smoke_source,'JobCounterWrite',GDB_WATCH)
        smoke_source = replace_definition(smoke_source,'job_arm',GDB_ARM)
        smoke_globals['GDB_JOB'] = GDB_SUPPORT+'\n'+smoke_source
        previous_install = module.install
        def install(retained,project):
            retained = previous_install(retained,project)
            previous_stack = retained.load_stack
            def load_stack(p):
                dual,base = previous_stack(p)
                old_transform,old_gather = base.transform,base.gather
                def transform(source,helper,session):
                    return old_transform(source,helper,session).replace(
                        'got-v43-portable-ready-','got-v44-portable-ready-')
                def gather(session,report):
                    rc = old_gather(session,report)
                    summary = {'kind':'DEPENDENCY_CHAIN_AND_COMPLETION_SLOT',
                               'gameplay_verified':False,'emulator_modified':False,'driver_modified':False,
                               'main_counter_watchpoints':False,'snapshots':[]}
                    try:
                        for name in ('dependency-state-01.json','dependency-state-02.json',
                                     'dependency-first-negative.json','dependency-watch-error.json'):
                            src = session/'native'/name
                            if base.regular(src) and src.stat().st_size <= 1_000_000:
                                obj = json.loads(src.read_text())
                                base.store(report/'logs'/('native-'+name),obj)
                                if name.startswith('dependency-state-'):
                                    summary['snapshots'].append({'snapshot':obj['snapshot'],
                                      'main_counters':obj.get('main_counters'),
                                      'dependency_signed_words':obj.get('dependency_signed_words'),
                                      'pending_list':obj.get('pending_list'), 'proxy_sync':obj.get('proxy_sync')})
                        summary['snapshot_count'] = len(summary['snapshots'])
                        summary['result'] = 'CAPTURED_NOT_A_FIX' if summary['snapshots'] else 'NO_DEPENDENCY_SNAPSHOT'
                    except Exception as exc: summary['collection_error'] = str(exc)
                    base.store(report/'info/dependency-capture.json',summary)
                    return rc
                base.transform,base.gather = transform,gather
                return dual,base
            retained.load_stack = load_stack
            return retained
        module.install = install
        return module
    prior.load_previous = load_previous
    return prior


def main() -> int:
    os.umask(0o077)
    script = Path(__file__).resolve()
    if script.parent.name != 'scripts' or script.parent.parent.name != 'GhostOfTsushima':
        raise RuntimeError('Place helper in ~/projects/shadps4/GhostOfTsushima/scripts')
    return load_previous(script).main()


if __name__ == '__main__':
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
    except Exception as exc:
        print('STOP: '+str(exc),file=sys.stderr); sys.exit(1)
