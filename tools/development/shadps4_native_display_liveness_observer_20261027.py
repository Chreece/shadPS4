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

ERROR_TERMS = (
    "Unhandled access violation", "Fatal", "SIGSEGV", "SIGABRT",
    "VK_ERROR_DEVICE_LOST", "GpuComm", "sharp source was not flattened",
    "SRT", "AUDIO_", "Loading libSce",
)
GIB = 1024 ** 3


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as fd:
        for blob in iter(lambda:fd.read(1024*1024),b''):
            h.update(blob)
    return h.hexdigest()


def existing_games():
    found=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdecimal():
            continue
        try:
            if p.stat().st_uid!=os.getuid():
                continue
            state=(p/'stat').read_text().rsplit(') ',1)[1][0]
            exe=Path(os.readlink(p/'exe')).name.lower()
            if state not in ('X','Z') and exe.startswith('shadps4'):
                found.append(int(p.name))
        except (OSError,IndexError,ValueError):
            pass
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


def image_is_black(path):
    if shutil.which('identify') is None:
        return None
    try:
        probe=subprocess.run(
            ['identify','-format','%[mean]',str(path)],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            check=False,timeout=3)
        if probe.returncode!=0:
            return None
        return float(probe.stdout.decode('ascii','replace').strip())==0
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return None


def session_processes():
    found={}
    for name in ('Xorg','sunshine'):
        try:
            probe=subprocess.run(
                ['pgrep','-x',name],stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,check=False,timeout=2)
            found[name]=sorted(int(x) for x in probe.stdout.split() if x.isdigit())
        except (OSError,ValueError,subprocess.TimeoutExpired):
            found[name]=[]
    return found


def capture_process_wait_channels(pid,report):
    rows=[]
    tasks=Path('/proc')/str(pid)/'task'
    try:
        for task in sorted(tasks.iterdir(),key=lambda p:int(p.name))[:256]:
            try:
                comm=(task/'comm').read_text(errors='replace').strip()
                state=(task/'status').read_text(errors='replace')
                state=next((x.strip() for x in state.splitlines() if x.startswith('State:')),'')
                wchan=(task/'wchan').read_text(errors='replace').strip()
                rows.append(f'{task.name} {comm} {state} wchan={wchan}')
            except (OSError,UnicodeError):
                continue
    except (OSError,ValueError):
        rows.append('TASK_SNAPSHOT_UNAVAILABLE')
    (report/'last-thread-wait-channels.txt').write_text('\n'.join(rows)+'\n')


def term_owned(child,phase):
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid,signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=3)
    print('OWNED_GAME_PROCESS_TERMINATED='+phase,flush=True)


def summarize(console,out):
    total=0
    important=[]
    warnings=0
    errors=0
    with console.open('rb') as stream:
        for raw in stream:
            total+=1
            text=raw.decode('utf-8','replace').strip()
            if len(text)>1500:
                text=text[:1500]
            if ' <Error>' in text:
                errors+=1
            if ' <Warning>' in text:
                warnings+=1
            if any(term in text for term in ERROR_TERMS):
                if len(important)<420:
                    important.append(text)
    (out/'selected-console-diagnostics.txt').write_text(
        '\n'.join(important)+'\n',encoding='utf-8')
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
    return {'console_lines':total,'warning_lines':warnings,'error_lines':errors,
            'selected_diagnostic_lines':len(important),
            'full_console_bytes':size}


def capture_native_mappings(pid, out):
    # Read-only /proc evidence. Single overwritten snapshot, never a huge trace.
    try:
        content=Path(f'/proc/{pid}/maps').read_text(errors='replace')
        if len(content)<1024*1024:
            (out/'last-process-mappings.txt').write_text(content)
    except (OSError, UnicodeError):
        pass


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


def capture_gpu_kernel_evidence(out, since):
    """Read-only kernel messages; missing permission is not 'zero GPU faults'."""
    path=out/'amdgpu-kernel-evidence.txt'
    status={"source":"journalctl -k", "permission":"UNVERIFIED",
            "matched_lines":0}
    if shutil.which('journalctl') is None:
        status["permission"]="JOURNALCTL_NOT_INSTALLED"
        path.write_text("journalctl unavailable; do not infer absence of GPU errors\n")
        return status
    try:
        proc=subprocess.run(
            ['journalctl', '-k', '--since', since, '--no-pager', '--quiet',
             '--output=short-iso'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=5)
        if proc.returncode != 0:
            status['permission']='JOURNAL_ACCESS_DENIED_OR_FAILED'
            path.write_text(
                "JOURNAL_STATUS="+str(proc.returncode)+"\n"+
                proc.stderr.decode('utf-8','replace')[:1000]+"\n")
            return status
        status['permission']='READABLE'
        lines=proc.stdout.decode('utf-8','replace').splitlines()
        terms=('amdgpu', 'drm:', 'gfx ', 'gpu fault', 'gpu reset',
               'ring timeout', 'gpu recover', 'vm fault', 'radv', 'navi31')
        selected=[x[:1300] for x in lines if any(term in x.lower() for term in terms)]
        status['matched_lines']=len(selected)
        path.write_text(
            "GPU_KERNEL_MESSAGES_WITHIN_TEST_WINDOW="+str(len(selected))+"\n"+
            "\n".join(selected[-220:])+"\n")
    except (subprocess.TimeoutExpired,OSError,UnicodeError) as error:
        status['permission']='JOURNAL_QUERY_ERROR'
        path.write_text(type(error).__name__+": "+str(error)[:700]+"\n")
    return status


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
    save(report/'launch.json',{
        'command':argv,'sha256':args.sha256,'cpu':'emulator-native/no-DynamoRIO',
        'memory':'global emulator config','automated_game_inputs':False,
        'runtime_limit_seconds':120,'signal_at_seconds':118
    })
    result={'binary_sha256':args.sha256,'game':args.game,
            'duration_limit':120,'time_to_sigterm':118,
            'graphics_visual_acceptance':'NOT_OBSERVED',
            'audio_audible_acceptance':'NOT_OBSERVED',
            'gameplay_acceptance':'NOT_OBSERVED','screenshots':[],
            'screenshot_checks':[],'fps_overlay_status':'NOT_VERIFIED',
            'owned_child_terminated':False,'exit_code':None}
    console=report/'full-console.log'
    child=None
    began=time.monotonic()
    kernel_since=dt.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')
    failure=None
    previous_map_capture=0.0
    try:
        with console.open('wb',buffering=0) as output:
            child=subprocess.Popen(argv,cwd=args.cwd,env=env,
                stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                start_new_session=True)
            print(f'NATIVE_GENERIC_GAME_LAUNCHED=YES PID={child.pid}',flush=True)
            initial_session=session_processes()
            result['session_initial']=initial_session
            last_checked=0.0
            due=(11,21,26,58,104)
            taken=set()
            while child.poll() is None and time.monotonic()-began<118:
                elapsed=time.monotonic()-began
                if elapsed-previous_map_capture>=1.0:
                    capture_native_mappings(child.pid,report)
                    previous_map_capture=elapsed
                if elapsed-last_checked>=2.0:
                    current_session=session_processes()
                    last_checked=elapsed
                    changed=[name for name in initial_session if
                             initial_session[name] and
                             initial_session[name]!=current_session[name]]
                    if changed:
                        result['session_changed']=changed
                        result['session_after']=current_session
                        result['session_change_second']=round(elapsed,2)
                        result['exit_reason']=(
                            'DISPLAY_SERVER_RESTARTED' if 'Xorg' in changed
                            else 'SUNSHINE_SESSION_RESTARTED')
                        capture_process_wait_channels(child.pid,report)
                        print('DISPLAY_SESSION_FAILURE='+result['exit_reason'],flush=True)
                        break
                for second in due:
                    if elapsed>=second and second not in taken:
                        taken.add(second)
                        image=report/f'frame-{second:03d}s.png'
                        if take_picture(report,second,display):
                            result['screenshots'].append(image.name)
                            black=image_is_black(image)
                            label=('BLACK_NO_FPS' if black is True else
                                   'NOT_ALL_BLACK_FPS_NOT_VERIFIED' if black is False else
                                   'IMAGE_CHECK_UNAVAILABLE')
                            if black is True:
                                result['fps_overlay_status']='ABSENT_ON_BLACK_SCREEN'
                        else:
                            label='SCREEN_CAPTURE_FAILED'
                        result['screenshot_checks'].append(
                            {'second':second,'status':label})
                        print(f'FRAME_OBSERVATION second={second} status={label}',flush=True)
                startup_checks=[x for x in result['screenshot_checks']
                                if x['second'] in (11,21,26)]
                if elapsed>=27 and len(startup_checks)==3 and all(
                    x['status'] in ('BLACK_NO_FPS','SCREEN_CAPTURE_FAILED')
                    for x in startup_checks):
                    result['exit_reason']='NO_VISIBLE_WINDOW_OR_FPS_27S'
                    result['fps_overlay_status']='ABSENT_OR_UNAVAILABLE'
                    capture_process_wait_channels(child.pid,report)
                    print('DISPLAY_SESSION_FAILURE=NO_VISIBLE_WINDOW_OR_FPS_27S',flush=True)
                    break
                time.sleep(min(0.4,max(0.0,118-elapsed)))
            if 'exit_reason' not in result:
                if child.poll() is None:
                    result['exit_reason']='TEST_118S_DURATION_LIMIT'
                    capture_process_wait_channels(child.pid,report)
                else:
                    result['exit_reason']='GAME_EXITED_BEFORE_LIMIT'
                    result['exit_code']=child.returncode
    except Exception as error:
        failure=f'{type(error).__name__}: {error}'
        print('OBSERVER_ERROR='+failure,flush=True)
        result['exit_reason']='OBSERVER_EXCEPTION'
    finally:
        if child is not None:
            if child.poll() is None:
                term_owned(child,'TERM_THEN_OPTIONAL_KILL')
            result['owned_child_terminated']=child.poll() is not None
            result['exit_code']=child.poll()
        result['elapsed_seconds']=round(time.monotonic()-began,3)
        result['gpu_kernel_events']=capture_gpu_kernel_evidence(report,kernel_since)
        if console.is_file():
            result.update(summarize(console,report))
            result["native_fault"]=classify_native_fault(console,report,binary)
            if result["native_fault"].get("fault_rip"):
                save(report/'native-fault-mapping.json',result["native_fault"])
        if failure:
            result['failure']=failure
        save(report/'native-result.json',result)
    print('NATIVE_GENERIC_TEST_RESULT='+result['exit_reason'],flush=True)
    print('GAME_EXIT_CODE='+str(result['exit_code']),flush=True)
    print('TEST_RUNTIME_SECONDS='+str(result['elapsed_seconds']),flush=True)
    print('GAME_PROCESS_LEFT_RUNNING='+('NO' if result['owned_child_terminated'] else 'UNKNOWN'),flush=True)
    print('NO_AUTOMATIC_INPUT=YES; FULLSCREEN_AND_FPS=YES; NO_CPU_ID_OVERRIDES=YES',flush=True)
    return 0 if result['owned_child_terminated'] and (
        (result['exit_reason']=='TEST_118S_DURATION_LIMIT'
         and result['fps_overlay_status']!='ABSENT_ON_BLACK_SCREEN')
        or (result['exit_reason']=='GAME_EXITED_BEFORE_LIMIT' and
            result['exit_code']==0)) else 1


if __name__=='__main__':
    raise SystemExit(main())
