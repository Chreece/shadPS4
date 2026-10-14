#!/usr/bin/env python3
"""GoT v42: identify writers of the two counters polled by the v41 guest stall.

Keep the exact v40 emulator, private RADV, driver gate, and native capture.
Add two bounded hardware WRITE watchpoints before the stall, then read the
compared values, guest caller, and job/fence context in both stall snapshots.
No counter edits, guest instruction patches, timestamp override or shader skip.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types

PREVIOUS_SHA = '7e20c82a2970254890224b73de1ccd852fbded7060cd95f9be27f0e9b47207f4'
LABEL = 'got-v42-job-writer-visual'


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError('Pinned v41 source changed at ' + repr(old[:90]))
    return text.replace(old, new, 1)


# Runs within the existing unprivileged GDB. Breakpoint callbacks only READ
# registers/memory and record data. Disabling watchpoints happens outside stop().
GDB_JOB = r'''
JOB_ADDRESSES = (0x2c5d8b0, 0x2c5d8b4)
JOB_LIMIT = 512
job_watches = []
job_events = 0
job_last = None
job_started = None
job_limit_pending = False
job_stop_detail = None
job_sites = set()

def job_values():
    inf = gdb.selected_inferior()
    return [int.from_bytes(bytes(inf.read_memory(addr, 4)), 'little')
            for addr in JOB_ADDRESSES]

def job_frame_info(frame):
    result = {'pc': hex(int(frame.pc())), 'registers': {}}
    for name in ('rip','rsp','rbp','rax','rbx','rcx','rdx','rsi','rdi',
                 'r8','r9','r10','r11','r12','r13','r14','r15'):
        try: result['registers'][name] = hex(int(frame.read_register(name)))
        except Exception: pass
    return result

class JobCounterWrite(gdb.Breakpoint):
    def __init__(self, address):
        self.address = address
        super().__init__('*(unsigned int*)'+hex(address), type=gdb.BP_WATCHPOINT,
                         wp_class=gdb.WP_WRITE, internal=True)
    def stop(self):
        global job_events, job_last, job_limit_pending, job_stop_detail
        try:
            inf = gdb.selected_inferior()
            frame = gdb.newest_frame()
            thread = gdb.selected_thread()
            values = job_values()
            job_events += 1
            row = {'event': job_events, 'seconds_since_arm': time.monotonic()-job_started,
                   'watched_address': hex(self.address), 'values_before': job_last,
                   'values_after': values, 'thread': thread.num, 'name': thread.name,
                   'ptid': list(thread.ptid), 'pc_after_write': hex(int(frame.pc())),
                   'gameplay_verified': False, 'inferior_data_written': False}
            row['registers'] = job_frame_info(frame)['registers']
            # Compact writer history. GDB stops after the writing instruction;
            # capture surrounding raw code once per distinct PC for disassembly.
            pc = int(frame.pc())
            if pc not in job_sites and len(job_sites) < 24:
                job_sites.add(pc)
                context = {'pc_after_write': hex(pc), 'module': gdb.solib_name(pc)}
                try:
                    context['raw_start'] = hex(pc-48)
                    context['raw_code_128'] = bytes(inf.read_memory(pc-48, 128)).hex()
                    context['next_instructions'] = execute('x/8i $pc')[:4000]
                except Exception as exc: context['error'] = str(exc)
                stall_write('job-writer-'+hex(pc)[2:]+'.json', context)
            with (out/'job-counter-writes.log').open('a') as stream:
                stream.write(json.dumps(row)+'\n')
                stream.flush()
            job_last = values
            if job_events >= JOB_LIMIT or time.monotonic()-job_started > 35:
                job_limit_pending = True
                job_stop_detail = 'BOUNDED_WRITER_TRACE_LIMIT'
                return True
            return False
        except Exception as exc:
            job_limit_pending = True
            job_stop_detail = 'WRITER_TRACE_ERROR: '+str(exc)
            return True

def job_finish(reason):
    global job_limit_pending
    for bp in job_watches:
        if bp.is_valid(): bp.enabled = False
    stall_write('job-watch-finished.json', {'reason': reason, 'events': job_events,
                'last_values': job_last, 'gameplay_verified': False,
                'inferior_data_written': False})
    job_limit_pending = False

def job_arm(verify_game=True):
    global job_started, job_last
    if job_started is not None:
        raise RuntimeError('Writer trace was already armed')
    inf = gdb.selected_inferior()
    # Exact RIP-relative load/compare established in the user's v41 disassembly.
    # Fail closed on another game/update. Do not write instructions or counters.
    if verify_game:
        expected = bytes.fromhex('8b055e3f15023b05543f150275a2')
        if bytes(inf.read_memory(0xb09950, len(expected))) != expected:
            raise RuntimeError('Guest counter comparison differs from v41; no watchpoints installed')
    values = job_values()
    try:
        for address in JOB_ADDRESSES:
            bp = JobCounterWrite(address)
            job_watches.append(bp)
            if bp.type != gdb.BP_HARDWARE_WATCHPOINT:
                raise RuntimeError('Hardware WRITE watchpoint unavailable; software fallback refused')
        job_last = values
        job_started = time.monotonic()
        stall_write('job-watch-armed.json', {'ok': True, 'addresses': [hex(x) for x in JOB_ADDRESSES],
                    'initial_values': values, 'hardware_only': True, 'max_events': JOB_LIMIT,
                    'guest_compare_verified': verify_game, 'gameplay_verified': False})
        print('GOT42_COUNTER_WRITERS=ARMED_HARDWARE_ONLY', flush=True)
    except Exception:
        for bp in job_watches:
            if bp.is_valid(): bp.delete()
        job_watches.clear()
        raise

def job_region(inf, address, size):
    item = {'address': hex(address), 'size': size}
    try: item['bytes'] = bytes(inf.read_memory(address, size)).hex()
    except Exception as exc: item['error'] = str(exc)
    return item

def job_snapshot(number):
    inf = gdb.selected_inferior()
    data = {'snapshot': number, 'writes_observed': job_events,
            'inferior_data_written': False, 'gameplay_verified': False}
    try:
        values = job_values()
        data.update(counter_addresses=[hex(x) for x in JOB_ADDRESSES], values=values,
                    equal=values[0] == values[1],
                    unsigned_difference=(values[0]-values[1]) & 0xffffffff)
    except Exception as exc: data['counter_read_error'] = str(exc)
    # The group address is historical context, not an assertion of ownership.
    data['regions'] = {
        'counter_neighbours': job_region(inf, 0x2c5d8a0, 64),
        'guest_delay_configuration': job_region(inf, 0x4517160, 32),
        'historical_job_group_context_unverified': job_region(inf, 0x3fb64c0, 512),
        'recent_gpu_completion_labels': job_region(inf, 0x1100000000, 384)}
    chosen = gdb.selected_thread()
    data['guest_threads'] = []
    try:
        for thread in inf.threads():
            name = thread.name or ''
            if not (name == 'Game:Main' or name.startswith(('JobWorker','ProxySetSync','GpuInterrupt'))):
                continue
            thread.switch()
            frame = gdb.newest_frame()
            # Recover guest registers below a signal trampoline as well as when
            # stopped directly in the guest. Never call an inferior function.
            for _ in range(24):
                if frame is None: break
                pc = int(frame.pc())
                if 0x10000 <= pc < 0x6000000:
                    row = {'thread': thread.num, 'name': name, **job_frame_info(frame)}
                    row['frame_chain'] = []
                    fp = int(row['registers'].get('rbp','0'),16)
                    seen = set()
                    for level in range(8):
                        if fp in seen or fp < 0x10000 or fp >= (1<<47) or fp % 8: break
                        seen.add(fp)
                        try:
                            buf = bytes(inf.read_memory(fp,16))
                            parent = int.from_bytes(buf[:8],'little')
                            ret = int.from_bytes(buf[8:],'little')
                            row['frame_chain'].append({'rbp':hex(fp),'next_rbp':hex(parent),'return':hex(ret)})
                            if 0x10000 <= ret < 0x6000000 and level < 2:
                                row.setdefault('caller_code',[]).append(job_region(inf,ret-96,192))
                            if not (fp < parent <= fp+16*1024*1024): break
                            fp = parent
                        except Exception as exc:
                            row['frame_chain_error'] = str(exc); break
                    data['guest_threads'].append(row)
                    break
                frame = frame.older()
    finally:
        if chosen and chosen.is_valid(): chosen.switch()
    if number == 1:
        # Narrow code windows only, not a game dump. Includes the verified wait,
        # its guest caller and previously investigated job enqueue/completion code.
        for tag, addr, size in (('wait',0xb09880,512), ('caller',0xcb9d20,320),
                              ('worker',0xc030a0,256), ('completion',0xc039a0,384),
                              ('enqueue',0xc03b40,320), ('interrupt',0xb032c0,256),
                              ('proxy',0xf606e0,256)):
            region = job_region(inf,addr,size)
            try: region['disassembly'] = execute('disassemble /r '+hex(addr)+','+hex(addr+size))[:20000]
            except Exception: pass
            stall_write('job-code-'+tag+'.json', region)
    stall_write('job-state-%02d.json' % number, data)
    if number == 2: job_finish('SECOND_STALL_SNAPSHOT_SAVED')
    return data
'''


def transform_previous(source: str) -> str:
    source = once(source, "LABEL = 'got-v41-stall-visual'", "LABEL = '"+LABEL+"'")
    source = once(source, "    first_at = None; first_done = None; second_at = None\n",
                  "    first_at = None; first_done = None; second_at = None\n"
                  "    arm_at = None; arm_done = smoke\n")
    marker = "                if first_at is None and not fatal_stop:\n"
    replacement = """                # Arm before the known end-of-intro stall, not after its final writer.
                if not smoke and arm_at is None and not fatal_stop and progress.last_flip is not None and progress.last_flip >= 600:
                    store(session/'stall-request.json', {'op':'arm_counter_writers'})
                    child.send_signal(signal.SIGINT)
                    arm_at = time.monotonic()
                    state['writer_arm_requested'] = True
                arm_ready = native/'job-arm-ready.json'
                if not smoke and arm_at is not None and arm_ready.is_file():
                    answer = json.loads(arm_ready.read_text())
                    arm_done = bool(answer.get('ok'))
                    if not arm_done:
                        state['writer_arm_error'] = answer; break
                if not smoke and arm_at is not None and not arm_done and time.monotonic()-arm_at > 15:
                    state['writer_arm_error'] = 'No bounded arm acknowledgement'; break
                if first_at is None and not fatal_stop and (arm_done or arm_at is None):
"""
    source = once(source, marker, replacement)
    # Do not repeat an idle UMR scan; actual fatal/RADV callbacks remain available.
    source = once(source, '    result = drive(command,session,checkpoint,binary=binary)\n',
                          '    result = drive(command,session,None,binary=binary)\n')
    source = once(source, "'kind':'V40_LIVE_STALL_CAPTURE'", "'kind':'V40_GUEST_COUNTER_WRITER_CAPTURE'")
    source = once(source, '            smoke_gdb(p)\n',
                          '            hardware_smoke(p)\n            smoke_gdb(p)\n')
    return source


def augment_stall(text: str) -> str:
    text = once(text, "        controlled = (caught or event.get('signal') == 'SIGINT')\n",
        "        controlled = (caught or event.get('signal') == 'SIGINT')\n"
        "        trace_break = set(event.get('breakpoints', [])) & {bp.number for bp in job_watches if bp.is_valid()}\n"
        "        if job_limit_pending and trace_break:\n"
        "            job_finish(job_stop_detail)\n"
        "            command = 'continue'\n"
        "            continue\n"
        "        if controlled and request.get('op') == 'arm_counter_writers':\n"
        "            try:\n"
        "                job_arm()\n"
        "                stall_write('job-arm-ready.json', {'ok': True})\n"
        "            except Exception as exc:\n"
        "                stall_write('job-arm-ready.json', {'ok': False, 'error': str(exc)})\n"
        "                write('stop.json', {'reason':'COUNTER_TRACE_ARM_FAILED', 'error':str(exc)})\n"
        "                break\n"
        "            command = 'continue'\n"
        "            continue\n")
    text = once(text, "    meta['elapsed_capture'] = time.monotonic() - begin\n",
        "    if request.get('reason') != 'SMOKE_TEST':\n"
        "        try: meta['job_wait'] = job_snapshot(number)\n"
        "        except Exception as exc: meta['job_wait_error'] = str(exc)\n"
        "    meta['elapsed_capture'] = time.monotonic() - begin\n")
    return GDB_JOB + '\n' + text


SMOKE_C = r'''
#include <pthread.h>
volatile unsigned int watched[2];
__attribute__((noinline)) void arm_marker(void) { __asm__ __volatile__("" ::: "memory"); }
static void *writer(void *unused) {
    (void)unused;
    for (int i=0; i<6; ++i) { watched[0]++; watched[1]++; }
    return 0;
}
int main(void) {
    pthread_t thread;
    arm_marker();
    if (pthread_create(&thread, 0, writer, 0)) return 2;
    if (pthread_join(thread, 0)) return 3;
    return watched[0] == 6 && watched[1] == 6 ? 0 : 4;
}
'''


def hardware_smoke(project: Path) -> None:
    """A tiny throwaway pthread fixture, not an emulator rebuild or GPU test."""
    cc, gdb = shutil.which('cc') or shutil.which('gcc'), shutil.which('gdb')
    if not cc or not gdb:
        raise RuntimeError('GDB/C compiler missing; hardware-watchpoint preflight not run')
    tools = project/'tools'; logs = project.parent/'logs'
    if tools.is_symlink() or logs.is_symlink():
        raise RuntimeError('Unsafe smoke test directory')
    tools.mkdir(exist_ok=True); logs.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='got42-watch-smoke-',dir=tools) as temp:
        work = Path(temp)
        out = Path(tempfile.mkdtemp(prefix='got42-watch-smoke-',dir=logs))
        (work/'smoke.c').write_text(SMOKE_C)
        subprocess.run([cc,'-g','-O0','-pthread',str(work/'smoke.c'),'-o',str(work/'smoke')],
                       capture_output=True,check=True,timeout=20)
        prefix = ('import gdb,json,os,time\nfrom pathlib import Path\n'
                  'out=Path('+repr(str(out))+')\n'
                  'def stall_write(name,obj): (out/name).write_text(json.dumps(obj)+"\\n")\n'
                  'def execute(cmd): return gdb.execute(cmd,to_string=True)\n')
        tail = r'''
for cmd in ('set pagination off','set confirm off','set auto-load off',
            'set debuginfod enabled off','set may-call-functions off'):
    gdb.execute(cmd)
bp=gdb.Breakpoint('arm_marker',internal=True)
gdb.execute('run')
main_thread=gdb.selected_thread().num
bp.delete()
JOB_ADDRESSES=(int(gdb.parse_and_eval('&watched[0]')),int(gdb.parse_and_eval('&watched[1]')))
job_arm(verify_game=False)
gdb.execute('continue')
job_finish('SMOKE_DONE')
rows=[json.loads(line) for line in (out/'job-counter-writes.log').read_text().splitlines()]
assert len(rows)==12 and rows[-1]['values_after']==[6,6], rows
assert all(row['thread'] != main_thread for row in rows)
stall_write('smoke-pass.json',{'ok':True,'writes':12,'cross_thread_hardware_write_capture':True,'game_launched':False})
'''
        (work/'smoke.py').write_text(prefix+GDB_JOB+tail)
        with (out/'debugger.log').open('wb') as log:
            result=subprocess.run([gdb,'-q','-nx','--batch','-x',str(work/'smoke.py'),'--args',str(work/'smoke')],
                                  stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,timeout=25)
        if result.returncode or not (out/'smoke-pass.json').is_file():
            archive=out.with_name(out.name+'.tar.gz')
            with tarfile.open(archive,'w:gz') as tar:
                for path in out.iterdir():
                    if path.is_file(): tar.add(path,arcname=path.name,recursive=False)
            print('UPLOAD_HARDWARE_WATCH_SMOKE_FAILURE='+str(archive),flush=True)
            raise RuntimeError('Hardware writer-capture fixture failed; GoT was not started')
        print('GDB_HARDWARE_WRITER_CAPTURE=12_CROSS_THREAD_WRITES_VERIFIED; GAME_NOT_STARTED',flush=True)


def load_previous(script: Path):
    prior=script.with_name('got-v41-stall-capture.py')
    if not prior.is_file() or prior.is_symlink() or prior.stat().st_size>100_000:
        raise RuntimeError('Missing/unsafe pinned v41 helper')
    data=prior.read_bytes()
    if hashlib.sha256(data).hexdigest()!=PREVIOUS_SHA:
        raise RuntimeError('v41 helper differs from the one used in your uploaded run')
    module=types.ModuleType('got42_pinned_v41')
    module.__file__=str(script)
    module.hardware_smoke=hardware_smoke
    exec(compile(transform_previous(data.decode()),str(prior),'exec'),module.__dict__)
    module.GDB_STALL=augment_stall(module.GDB_STALL)
    return module


def main() -> int:
    os.umask(0o077)
    script=Path(__file__).resolve()
    if script.parent.name!='scripts' or script.parent.parent.name!='GhostOfTsushima':
        raise RuntimeError('Place helper in ~/projects/shadps4/GhostOfTsushima/scripts')
    return load_previous(script).main()


if __name__=='__main__':
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
    except Exception as exc:
        print('STOP: '+str(exc),file=sys.stderr); sys.exit(1)
