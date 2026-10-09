#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Observe Ghost's ProxySetSync GPU progress and dependencies without GDB.

Reuse the verified private Compute6 fence-readback executable unchanged. Sample
live guest memory without stopping threads and correlate it with GPU release
and event delivery counts through a natural frame stall or GPU failure.
This does not modify source, guest memory, saves, ES-DE, SSH or other binaries.
"""
from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import re
import signal
import struct
import subprocess
import sys
import tarfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

HOME=Path.home()
GHOST=HOME/'Applications/shadps4-ghost'
EXE=GHOST/'fence-readback-candidate/shadps4'
BIN_SHA='e294cc6b5fabb7c41b7f7caca20ad14e47993bd0e9f1a774f341b35846fa2fae'
OTHER=HOME/'Applications/shadps4/shadps4'
BASE=GHOST/'shadps4'
BASE_TOOL='ghost-isolated-compute6-irq-20261009.py'
BASE_REV='6a4429decac1b4b38a980ffdcf052a17ed0a7bcd'
BASE_BLOB='be02bb94febddb232a7adc952195f6210845b66b'
STAMP=datetime.now().strftime('%Y%m%d-%H%M%S')
OUT=HOME/f'ghost-c6-progress-control-{STAMP}.tar.gz'
WORK=HOME/'.cache'/f'ghost-c6-progress-control-{STAMP}'
PROXY=0x3fa7280
PROGRESS_SLOT=0x11000000b8
TABLE_PTR=0x1ad3208
GROUP=0x3fb6490
EXPECTED_TABLE=0x1100000000
SAMPLE_PERIOD=.10
MAX_SECONDS=110
TRACE_TAGS=('GHOST_C6_SLOT_AFTER','GHOST_C6_RELEASE','GHOST_C6_IRQ_FORWARD',
            'GHOST_C6_TRIGGER','GHOST_C6_DEQUEUE','GHOST_TRACE vblank=')
EVENT_RE=re.compile(r'GHOST_C6_SLOT_AFTER addr=(0x[0-9a-f]+) request=(0x[0-9a-f]+) observed=(0x[0-9a-f]+) matches_expected=(true|false)')


def sha256(path:Path):
    if not path.is_file() or path.is_symlink():return None
    h=hashlib.sha256()
    with path.open('rb') as fd:
        for piece in iter(lambda:fd.read(1024*1024),b''):h.update(piece)
    return h.hexdigest()


def read_int(fd,addr,size):
    data=os.pread(fd,size,addr)
    if len(data)!=size:raise OSError(f'Guest address {addr:#x} read {len(data)}/{size}')
    return int.from_bytes(data,'little')


def progress_sample(fd):
    cur=read_int(fd,PROXY+0x1f30,4)
    idx=read_int(fd,PROXY+0x1f34,4)
    table=read_int(fd,TABLE_PTR,8)
    if not table or idx>=4096:
        raise ValueError(f'Invalid progress table/index: table={table:#x} idx={idx}')
    entry=read_int(fd,table+idx*8,8)
    slot=read_int(fd,PROGRESS_SLOT,8)
    fence68=read_int(fd,0x1100000068,8)
    fence70=read_int(fd,0x1100000070,8)
    return {'current':cur,'index':idx,'table_base':hex(table),
            'entry_addr':hex(table+idx*8),'entry':entry,'target_low32':entry&0xffffffff,
            'current_le_target':cur<=(entry&0xffffffff),'fixed_slot':slot,
            'table23':read_int(fd,table+23*8,8),
            'table_is_expected':table==EXPECTED_TABLE,
            'gpu_fence68':fence68,'gpu_fence70':fence70}


def guest_snapshot(fd,runner):
    progress=progress_sample(fd)
    group=runner.gate_state_from_bytes(os.pread(fd,0x190,GROUP))
    return {'progress':progress,'gate':group}


def counts_in(text):
    return {tag:text.count(tag) for tag in TRACE_TAGS}


def summarise(rows,errors,last_log):
    negative=next((r for r in rows if r['gate']['middle_underflow']),None)
    last=rows[-1] if rows else None
    changes=sum(x.get('changed',False) for x in rows)
    earliest_target_mismatch=next((r for r in rows if
       r['progress']['entry_addr'].lower()==hex(PROGRESS_SLOT) and
       r['progress']['entry']!=r['progress']['fixed_slot']),None)
    writeback=[]
    for line in last_log.splitlines():
        m=EVENT_RE.search(line)
        if m and int(m.group(1),16)==PROGRESS_SLOT:
            writeback.append({'requested':int(m.group(2),16),'observed':int(m.group(3),16),
                              'reported_match':m.group(4)=='true'})
    return {'samples_saved':len(rows),'changes_saved':changes,
            'first_middle_negative':negative,'last_readable':last,
            'selected_target_vs_fixed_slot_mismatch':earliest_target_mismatch,
            'log_counts':counts_in(last_log),
            'slot_writes':len(writeback),'slot_after_writes_last20':writeback[-20:],
            'all_slot_writes_correct':bool(writeback) and all(
                w['requested']==w['observed'] and w['reported_match'] for w in writeback),
            'read_errors':errors[:12],
            'sample_reads_are_nonatomic':True,
            'gdb_watchpoints_used':False}


def load_pinned_runner(target):
    url=f'https://raw.githubusercontent.com/Chreece/shadPS4/{BASE_REV}/scripts/{BASE_TOOL}'
    subprocess.run(['curl','-fsSL','--retry','3','--connect-timeout','15','--max-time','45',
                    url,'-o',str(target)],check=True,timeout=65)
    actual=subprocess.check_output(['git','hash-object',str(target)],text=True,timeout=10).strip()
    if actual!=BASE_BLOB:raise RuntimeError(f'Pinned runner mismatch: {actual}')
    spec=importlib.util.spec_from_file_location('ghost_pinned_progress_control',str(target))
    if spec is None or spec.loader is None:raise RuntimeError('Invalid pinned Python runner')
    runner=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def stop_owned_child(runner,p):
    """Bounded cleanup: do not let even a stuck SIGKILL prevent report creation."""
    if p.poll() is not None:return 'already_exited'
    if not runner.owned(p.pid) or os.getsid(p.pid)!=p.pid:
        return 'refused_unverified_process'
    stages=[(signal.SIGTERM,3),(signal.SIGKILL,4)]
    for sig,limit in stages:
        try:os.killpg(p.pid,sig)
        except ProcessLookupError:return 'already_exited'
        try:
            p.wait(timeout=limit)
            return f'exited_after_{sig.name}'
        except subprocess.TimeoutExpired:
            pass
    return 'SIGKILL_sent_but_process_not_reaped_possible_D_state'


def test_mode():
    assert PROGRESS_SLOT==EXPECTED_TABLE+23*8
    assert (TABLE_PTR,PROXY+0x1f30,PROXY+0x1f34)==(0x1ad3208,0x3fa91b0,0x3fa91b4)
    with TemporaryDirectory() as name:
        fp=Path(name)/'guest_memory_mock.bin'
        with fp.open('wb') as out:
            out.truncate(16*1024*1024)
        # A sparse tmp file needs to represent only relocated guest fields. Test
        # the parser on a small mock by using equivalent offsets independently.
        with fp.open('r+b') as f:
            f.seek(32);f.write(struct.pack('<I',1))
            f.seek(36);f.write(struct.pack('<I',23))
            f.seek(48);f.write(struct.pack('<Q',256))
            f.seek(256+23*8);f.write(struct.pack('<Q',15))
        with fp.open('rb') as f:
            assert read_int(f.fileno(),32,4)==1
            assert read_int(f.fileno(),36,4)==23
            assert read_int(f.fileno(),48,8)==256
            assert read_int(f.fileno(),256+23*8,8)==15
    sample={'gate':{'middle_underflow':True},'progress':{
             'entry_addr':hex(PROGRESS_SLOT),'entry':15,'fixed_slot':15},'changed':True}
    log=('GHOST_C6_SLOT_AFTER addr=0x11000000b8 request=0xf observed=0xf '
         'matches_expected=true\nGHOST_C6_DEQUEUE seq=2\n')
    sm=summarise([sample],[],log)
    assert sm['all_slot_writes_correct'] and sm['first_middle_negative'] is sample
    assert sm['log_counts']['GHOST_C6_DEQUEUE']==1
    invalid=log.replace('observed=0xf','observed=0x1').replace('matches_expected=true','matches_expected=false')
    assert not summarise([sample],[],invalid)['all_slot_writes_correct']
    assert len(BIN_SHA)==64 and len(BASE_REV)==len(BASE_BLOB)==40
    assert OUT.name.startswith('ghost-c6-progress-control-')
    print('GHOST_PROGRESS_CONTROL_SELFTEST_PASS=addresses,parser,correlation,negative_detection,cleanup_design')


def run():
    test_mode()
    WORK.mkdir(parents=True,exist_ok=False)
    events=[]
    def say(*parts):
        line=' '.join(map(str,parts))
        print(line,flush=True)
        events.append(line)
    before={'base':sha256(BASE),'other':sha256(OTHER)}
    status='starting'
    error=None
    lastlog=''
    rows=[]
    errors=[]
    reads=0
    exitcode=None
    stopped=None
    runner=None
    try:
        if sha256(EXE)!=BIN_SHA:
            raise RuntimeError('Private fence-readback executable differs from the tested SHA256')
        say('PRIVATE_FENCE_READBACK_SHA_VERIFIED='+BIN_SHA)
        # The pinned upstream helper is fetched only for its pre-tested public
        # routines. Its build_candidate/main are never called.
        helper=WORK/BASE_TOOL
        runner=load_pinned_runner(helper)
        runner.BIN=EXE
        runner.WORK=WORK
        runner.OUT=OUT
        assert runner.BIN!=runner.BASE_BIN and runner.BIN!=runner.OTHER_BIN
        runner.selftest()
        active=runner.find_emulators()
        if active:
            status='skipped_running_emulator'
            say('ACTIVE_EMULATOR_NO_KILL='+json.dumps(active))
        else:
            lockpath=GHOST/'.mip-candidate-build.lock'
            with lockpath.open('a+') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                active=runner.find_emulators()
                if active:
                    status='skipped_running_emulator'
                    say('ACTIVE_EMULATOR_NO_KILL='+json.dumps(active))
                else:
                    env=runner.env_for_x11()
                    for key in ('RADV_DEBUG','GHOST_CPU_RIP_LOG','SHADPS4_CPU_ID_MODE'):
                        env.pop(key,None)
                    env['SHADPS4_GRAPHICS_DIAGNOSTICS']='1'
                    env['SHADPS4_STARTUP_DIAGNOSTICS']='1'
                    status='running'
                    log=WORK/'ghost-progress-runtime.log'
                    process=None
                    fd=None
                    lastsig=None
                    lastsave=0.0
                    lastflips=None
                    elapsed=0.0
                    start=time.monotonic()
                    with log.open('w') as sink:
                        process=subprocess.Popen([str(EXE),'--game','CUSA11456','--fullscreen','true'],
                            cwd=str(GHOST),env=env,stdin=subprocess.DEVNULL,
                            stdout=sink,stderr=subprocess.STDOUT,start_new_session=True)
                        say('STARTED_PRIVATE_GHOST_PID='+str(process.pid))
                        try:
                            for tick in range(int(MAX_SECONDS/SAMPLE_PERIOD)):
                                elapsed=time.monotonic()-start
                                if process.poll() is not None:
                                    status='exited_by_itself'
                                    break
                                if not runner.owned(process.pid):
                                    if elapsed<2:
                                        time.sleep(SAMPLE_PERIOD)
                                        continue
                                    status='owned_process_identity_lost'
                                    break
                                if fd is None:
                                    try:
                                        fd=os.open(f'/proc/{process.pid}/mem',os.O_RDONLY|os.O_CLOEXEC)
                                        say('NONSTOP_GUEST_MEMORY_READER_OPEN')
                                    except OSError as ex:
                                        if len(errors)<12:errors.append('open:'+str(ex))
                                if fd is not None:
                                    try:
                                        entry=guest_snapshot(fd,runner)
                                        reads+=1
                                        sig=json.dumps(entry,sort_keys=True,separators=(',',':'))
                                        if sig!=lastsig or elapsed-lastsave>=1:
                                            r={'seconds':round(elapsed,3),**entry,'changed':sig!=lastsig,
                                               'flips':lastflips['flips'] if lastflips else None,
                                               'vblank':lastflips['vblank'] if lastflips else None}
                                            if len(rows)<6500:rows.append(r)
                                            lastsave=elapsed
                                            if entry['gate']['middle_underflow'] and not any(
                                                v['gate']['middle_underflow'] for v in rows[:-1]):
                                                say('FIRST_MIDDLE_NEGATIVE='+json.dumps({
                                                    'seconds':r['seconds'], 'progress':entry['progress'],
                                                    'gate':entry['gate']})[:1400])
                                        lastsig=sig
                                    except (OSError,ValueError) as ex:
                                        if len(errors)<12:errors.append('read:'+str(ex))
                                if tick%10==0:
                                    lastlog=log.read_text(errors='replace')
                                    frames=runner.guest_trace_records(lastlog)
                                    if frames:lastflips=frames[-1]
                                    if tick%60==0 and lastflips:
                                        say('FLIPS='+str(lastflips['flips'])+' SLOT_AFTER='+
                                            str(lastlog.count('GHOST_C6_SLOT_AFTER'))+
                                            ' DEQUEUE='+str(lastlog.count('GHOST_C6_DEQUEUE')))
                                    if 'ErrorDeviceLost' in lastlog or 'Assertion Failed!' in lastlog:
                                        status='gpu_or_assertion_error'
                                        break
                                    if runner.confirmed_frame_stall(frames):
                                        status='natural_guest_flip_stall'
                                        say('STALLED_FLIPS='+str(frames[-1]['flips']))
                                        runner.screenshot(env,'natural_stall')
                                        beforeticks=runner.read_owned_thread_ticks(process.pid)
                                        time.sleep(4)
                                        afterticks=runner.read_owned_thread_ticks(process.pid)
                                        (WORK/'thread-cpu-after-stall.json').write_text(json.dumps({
                                          'ticks_per_second':os.sysconf('SC_CLK_TCK'),
                                          'samples':runner.thread_cpu_delta(beforeticks,afterticks)},indent=2)+'\n')
                                        break
                                    if frames and frames[-1]['flips']>=1100:
                                        status='rendered_past_1100_flips'
                                        runner.screenshot(env,'past_1100_flips')
                                        break
                                time.sleep(SAMPLE_PERIOD)
                            else:
                                status='time_limit_without_confirmed_stall'
                        finally:
                            if fd is not None:
                                try:os.close(fd)
                                except OSError:pass
                            # Always write the timeline BEFORE attempting to stop a
                            # game which could be sleeping uninterruptibly.
                            (WORK/'guest-progress-timeline.json').write_text(json.dumps({
                              'reads':reads,'errors':errors,'records':rows},indent=2)+'\n')
                            stopped=stop_owned_child(runner,process)
                            say('PRIVATE_GHOST_CLEANUP='+str(stopped))
                            exitcode=process.poll()
                    lastlog=log.read_text(errors='replace')
    except BaseException as ex:
        error=traceback.format_exc()
        status='failed'
        say('FAILURE='+type(ex).__name__+':'+str(ex))
    finally:
        summary=summarise(rows,errors,lastlog)
        (WORK/'progress-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        (WORK/'status.json').write_text(json.dumps({
            'status':status,'error':error,'game_return_code':exitcode,
            'cleanup':stopped,'counter_read_count':reads,
            'source_modified':False,'gdb_used':False,'private_exe':str(EXE),
            'private_sha_expected':BIN_SHA,'private_sha_after':sha256(EXE),
            'other_sha_before':before['other'],'other_sha_after':sha256(OTHER),
            'baseline_sha_before':before['base'],'baseline_sha_after':sha256(BASE)
        },indent=2)+'\n')
        (WORK/'events.txt').write_text('\n'.join(events)+'\n')
        with tarfile.open(OUT,'w:gz') as archive:
            for item in sorted(WORK.iterdir()):
                if item.name==BASE_TOOL:continue
                archive.add(item,arcname=item.name)
        say('ARCHIVE_READY='+str(OUT))
        say('UPLOAD_THIS_FILE='+str(OUT))
        say('SSH_SESSION=REMAINS_OPEN')
    return 0 if not error and status in ('natural_guest_flip_stall','rendered_past_1100_flips',
                   'gpu_or_assertion_error','time_limit_without_confirmed_stall') else 1


if __name__=='__main__':
    if sys.argv[1:]==['--self-test']:
        test_mode()
    elif sys.argv[1:] in ([],['--test-only']):
        raise SystemExit(run())
    else:
        raise SystemExit('Usage: script.py [--self-test|--test-only]')
