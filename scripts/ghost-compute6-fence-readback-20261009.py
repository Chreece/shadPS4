#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rebuild only private Ghost with observed Compute6 fence readback; stop on first underflow.

Pins and reuses the proven seven-file isolated Ghost build/rollback orchestrator;
adds one independently tested, logging-only liverpool.cpp probe at the existing
SignalFence call site. No other source tree, emulator, SSH session or save changed.
"""
from __future__ import annotations
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_NAME='ghost-isolated-compute6-irq-20261009.py'
BASE_REV='6a4429decac1b4b38a980ffdcf052a17ed0a7bcd'
BASE_BLOB='be02bb94febddb232a7adc952195f6210845b66b'
ADD_NAME='ghost-c6-fence-readback-patch-20261009.py'
ADD_REV='6bb8ffd7a9bf55977caf57ba9c9d15bdeae1e2a9'
ADD_BLOB='ef6c31ab477df74b79d19b6feadf376e3e7d9aef'
SLOT_ADDR=0x11000000b8
MARKERS=(b'GHOST_C6_SLOT_BEFORE',b'GHOST_C6_SLOT_AFTER')
RELEASE_RE=re.compile(r'GHOST_C6_RELEASE seq=(\d+) .*?addr_lo=(0x[0-9a-f]+) addr_hi=(0x[0-9a-f]+) data_lo=(0x[0-9a-f]+) data_hi=(0x[0-9a-f]+)')
READ_RE=re.compile(r'GHOST_C6_SLOT_(BEFORE|AFTER) addr=(0x[0-9a-f]+) request=(0x[0-9a-f]+) observed=(0x[0-9a-f]+)(?: .*?matches_expected=(true|false))?')


def download_fixed(path,name,revision,expected):
    url='https://raw.githubusercontent.com/Chreece/shadPS4/'+revision+'/scripts/'+name
    subprocess.run(['curl','-fsSL','--retry','3','--connect-timeout','15','--max-time','45',url,'-o',str(path)],check=True,timeout=65)
    actual=subprocess.check_output(['git','hash-object',str(path)],text=True,timeout=15).strip()
    if actual!=expected:
        raise RuntimeError(f'Pinned {name} Git blob mismatch: {actual} != {expected}')
    compile(path.read_bytes(),str(path),'exec')
    return path


def load_runner(path):
    spec=importlib.util.spec_from_file_location('ghost_pinned_c6_runner',str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot import pinned Ghost runner')
    runner=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def validate_fence_events(text):
    releases=[]
    reads={'BEFORE':[],'AFTER':[]}
    for raw in text.splitlines():
        m=RELEASE_RE.search(raw)
        if m:
            seq,lo,hi,low,high=m.groups()
            addr=(int(hi,16)<<32)|int(lo,16)
            if addr==SLOT_ADDR:
                releases.append({'seq':int(seq),'addr':hex(addr),
                                 'requested':(int(high,16)<<32)|int(low,16)})
        m=READ_RE.search(raw)
        if m:
            kind,addr,req,obs,matched=m.groups()
            if int(addr,16)==SLOT_ADDR:
                reads[kind].append({'requested':int(req,16),'observed':int(obs,16),
                                    'reported_match':None if matched is None else matched=='true'})
    pairs=[]
    for i,(before,after) in enumerate(zip(reads['BEFORE'],reads['AFTER'])):
        pairs.append({'ordinal':i+1,'before':before['observed'],
                      'after':after['observed'],'requested':after['requested'],
                      'write_matches_expected':after['observed']==after['requested'],
                      'matches_reported':after['reported_match']})
    return {'pm4_commands_for_exact_slot':len(releases),
            'readback_before_count':len(reads['BEFORE']),
            'readback_after_count':len(reads['AFTER']),
            'first_commands':releases[:12],'last_commands':releases[-5:],
            'all_readback_pairs':pairs[:40],
            'mismatched_after_writes':[p for p in pairs if not p['write_matches_expected']],
            'exact_pair_count':len(pairs),
            'unexpected_pair_count':abs(len(reads['BEFORE'])-len(reads['AFTER'])),
            'no_fence_or_event_semantics_changed':True}


def test_guest_until_underflow(runner):
    if not runner.BIN.is_file() or not runner.GHOST.joinpath('user/config.json').is_file():
        raise RuntimeError('Private candidate or config missing')
    active=runner.find_emulators()
    if active:
        runner.say('GAME_SKIPPED_OTHER_EMULATOR_ACTIVE='+json.dumps(active))
        runner.GAME_STATUS='skipped_other_emulator_active'
        return
    env=runner.env_for_x11()
    for name in ('RADV_DEBUG','GHOST_CPU_RIP_LOG','SHADPS4_CPU_ID_MODE'):
        env.pop(name,None)
    env['SHADPS4_GRAPHICS_DIAGNOSTICS']='1'
    env['SHADPS4_STARTUP_DIAGNOSTICS']='1'
    log=runner.WORK/'ghost-candidate-runtime.log'
    records=[]
    errors=[]
    samples=0
    last_sig=None
    mem_fd=None
    last_frames=None
    detected='not_started'
    first_underflow=None
    elapsed=0.0
    start=time.monotonic()
    runner.say('STARTING_PRIVATE_FENCE_READBACK='+str(runner.BIN))
    with log.open('w') as sink:
        proc=subprocess.Popen([str(runner.BIN),'--game','CUSA11456','--fullscreen','true'],
                              cwd=str(runner.GHOST),env=env,stdin=subprocess.DEVNULL,
                              stdout=sink,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            detected='sampling_unmodified_guest_dependencies'
            for tick in range(1300):
                elapsed=time.monotonic()-start
                if proc.poll() is not None:
                    detected='candidate_exited'
                    runner.say('PRIVATE_CANDIDATE_EXITED='+str(proc.returncode))
                    break
                if not runner.owned(proc.pid):
                    detected='candidate_identity_lost'
                    break
                if mem_fd is None:
                    try:
                        mem_fd=os.open(f'/proc/{proc.pid}/mem',os.O_RDONLY|os.O_CLOEXEC)
                        runner.say('READONLY_DEPENDENCY_MONITOR=OPEN')
                    except OSError as ex:
                        if len(errors)<6:errors.append('open:'+str(ex))
                if mem_fd is not None:
                    try:
                        gate=runner.gate_state_from_bytes(os.pread(mem_fd,0x190,0x3fb6490))
                        slot_bytes=os.pread(mem_fd,8,SLOT_ADDR)
                        slot=int.from_bytes(slot_bytes,'little') if len(slot_bytes)==8 else None
                        samples+=1
                        sig=tuple((key,x['state'],x['count_raw']) for key,x in gate['gates'].items())
                        sig+=(slot,)
                        if sig!=last_sig or not records or elapsed-records[-1]['elapsed']>=2:
                            row={'elapsed':round(elapsed,3),'guest_flips':last_frames['flips'] if last_frames else None,
                                 'slot':slot,'gate':gate}
                            if len(records)<3500:records.append(row)
                            last_sig=sig
                        if gate['middle_underflow']:
                            first_underflow=records[-1] if records else None
                            runner.say('GHOST_FIRST_MIDDLE_UNDERFLOW='+json.dumps(first_underflow))
                            detected='first_middle_underflow_with_fence_readbacks'
                            break
                    except (OSError,ValueError) as ex:
                        if len(errors)<6:errors.append('read:'+str(ex))
                if tick%10==0:
                    frames=runner.guest_trace_records(log.read_text(errors='replace'))
                    if frames:
                        last_frames=frames[-1]
                    if runner.confirmed_frame_stall(frames):
                        detected='early_guest_flip_stall_before_underflow'
                        runner.say('EARLY_FLIP_STALL='+json.dumps(frames[-5:]))
                        break
                    if frames and frames[-1]['flips']>=1000:
                        detected='rendered_past_1000_guest_flips'
                        break
                time.sleep(.1)
            else:
                detected='timeout_without_underflow'
        finally:
            if mem_fd is not None:
                try:os.close(mem_fd)
                except OSError:pass
            (runner.WORK/'guest-dependency-timeline.json').write_text(
                json.dumps({'samples':samples,'errors':errors,'records':records,
                            'first_underflow':first_underflow},indent=2)+'\n')
            if proc.poll() is None and runner.owned(proc.pid) and os.getsid(proc.pid)==proc.pid:
                runner.say('STOP_ONLY_OWN_FENCE_READBACK_PID='+str(proc.pid))
                os.killpg(proc.pid,runner.signal.SIGTERM)
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if runner.owned(proc.pid):os.killpg(proc.pid,runner.signal.SIGKILL)
                    proc.wait(timeout=5)
            elif proc.poll() is None:
                runner.say('UNKNOWN_EMULATOR_IDENTITY_REFUSE_SIGNAL='+str(proc.pid))
    body=log.read_text(errors='replace')
    report=runner.classify(body,proc.returncode)
    readbacks=validate_fence_events(body)
    report.update({'test_stop_reason':detected,'fence_slot_readbacks':readbacks,
                   'first_middle_underflow':first_underflow,'samples':samples,
                   'sample_errors':errors,'underflow_elapsed':first_underflow['elapsed'] if first_underflow else None})
    (runner.WORK/'runtime-analysis.json').write_text(json.dumps(report,indent=2)+'\n')
    runner.GAME_STATUS=detected
    runner.say('C6_FENCE_READBACK_SUMMARY='+json.dumps({
        'status':detected,'pm4_commands':readbacks['pm4_commands_for_exact_slot'],
        'before_count':readbacks['readback_before_count'],
        'after_count':readbacks['readback_after_count'],
        'mismatches':len(readbacks['mismatched_after_writes']),
        'sampled_underflow':first_underflow is not None}))


def wire_pinned_runner(runner):
    original_bin=runner.BIN
    runner.BIN=runner.GHOST/'fence-readback-candidate'/'shadps4'
    runner.WORK=runner.HOME/'.cache'/('ghost-c6-fence-readback-'+runner.STAMP)
    runner.OUT=runner.HOME/('ghost-c6-fence-readback-'+runner.STAMP+'.tar.gz')
    assert str(original_bin)!=str(runner.BIN)
    assert str(runner.BIN)!=str(runner.BASE_BIN)!=str(runner.OTHER_BIN)
    download=runner.download_patcher
    def download_plus(name,rev,blob):
        helper=download(name,rev,blob)
        if name=='ghost-c6-irq-instrument-20261009.py':
            addon=download(ADD_NAME,ADD_REV,ADD_BLOB)
            transform=helper.transform
            def wrapped_transform(files):
                output=transform(files)
                output[helper.LIVERPOOL]=addon.transform(output[helper.LIVERPOOL])
                return output
            helper.transform=wrapped_transform
            runner.say('PINNED_C6_FENCE_READBACK_PATCH_COMPOSED='+ADD_BLOB)
        return helper
    runner.download_patcher=download_plus
    binary_markers=runner.binary_markers_present
    def binary_markers_extended(path,markers):
        return binary_markers(path,list(markers)+list(MARKERS))
    runner.binary_markers_present=binary_markers_extended
    runner.test_candidate=lambda:test_guest_until_underflow(runner)
    return runner


def selftest():
    fake='GHOST_C6_RELEASE seq=23 pipe=6 int_sel=3 data_sel=2 dw1=0x0 dw2=0x0 addr_lo=0xb8 addr_hi=0x11 data_lo=0xa data_hi=0x0\n'
    fake+='GHOST_C6_SLOT_BEFORE addr=0x11000000b8 request=0xa observed=0x9 data_sel=2 int_sel=3\n'
    fake+='GHOST_C6_SLOT_AFTER addr=0x11000000b8 request=0xa observed=0xa matches_expected=true\n'
    data=validate_fence_events(fake)
    assert data['pm4_commands_for_exact_slot']==1
    assert data['exact_pair_count']==1
    assert data['all_readback_pairs'][0]=={'ordinal':1,'before':9,'after':10,'requested':10,
                                           'write_matches_expected':True,'matches_reported':True}
    assert not data['mismatched_after_writes']
    invalid=validate_fence_events(fake.replace('observed=0xa matches_expected=true','observed=0x1 matches_expected=false'))
    assert len(invalid['mismatched_after_writes'])==1
    assert invalid['all_readback_pairs'][0]['after']==1
    assert len(BASE_REV)==len(BASE_BLOB)==len(ADD_REV)==len(ADD_BLOB)==40
    assert len(MARKERS)==2 and all(len(v)>15 for v in MARKERS)
    print('FENCE_READBACK_RUNNER_SELFTEST_PASS=regex,matching,unexpected_write,exact_isolation')


def main():
    if sys.argv[1:]==['--self-test']:
        selftest()
        return 0
    if sys.argv[1:] not in ([],):
        raise SystemExit('Usage: ghost-compute6-fence-readback-20261009.py [--self-test]')
    selftest()
    temp=Path('/tmp')/('ghost-c6-fence-runner-'+str(os.getpid())+'-'+datetime.now().strftime('%Y%m%d%H%M%S')+'.py')
    try:
        download_fixed(temp,BASE_NAME,BASE_REV,BASE_BLOB)
        runner=load_runner(temp)
        runner.selftest() # Full preexisting isolated build/rollback script self-test.
        wire_pinned_runner(runner)
        result=runner.main()
        if runner.ERROR is None and runner.GAME_STATUS=='first_middle_underflow_with_fence_readbacks':
            return 0
        return result
    finally:
        temp.unlink(missing_ok=True)


if __name__=='__main__':
    raise SystemExit(main())
