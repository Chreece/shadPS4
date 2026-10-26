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
            'owned_child_terminated':False,'exit_code':None}
    console=report/'full-console.log'
    child=None
    began=time.monotonic()
    failure=None
    try:
        with console.open('wb',buffering=0) as output:
            child=subprocess.Popen(argv,cwd=args.cwd,env=env,
                stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                start_new_session=True)
            print(f'NATIVE_GENERIC_GAME_LAUNCHED=YES PID={child.pid}',flush=True)
            due=(40,80,110)
            taken=set()
            while child.poll() is None and time.monotonic()-began<118:
                elapsed=time.monotonic()-began
                for second in due:
                    if elapsed>=second and second not in taken:
                        taken.add(second)
                        if take_picture(report,second,display):
                            result['screenshots'].append(f'frame-{second:03d}s.png')
                time.sleep(min(0.4,max(0.0,118-elapsed)))
            if child.poll() is None:
                result['exit_reason']='TEST_118S_DURATION_LIMIT'
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
        if console.is_file():
            result.update(summarize(console,report))
        if failure:
            result['failure']=failure
        save(report/'native-result.json',result)
    print('NATIVE_GENERIC_TEST_RESULT='+result['exit_reason'],flush=True)
    print('GAME_EXIT_CODE='+str(result['exit_code']),flush=True)
    print('TEST_RUNTIME_SECONDS='+str(result['elapsed_seconds']),flush=True)
    print('GAME_PROCESS_LEFT_RUNNING='+('NO' if result['owned_child_terminated'] else 'UNKNOWN'),flush=True)
    print('NO_AUTOMATIC_INPUT=YES; FULLSCREEN_AND_FPS=YES; NO_CPU_ID_OVERRIDES=YES',flush=True)
    return 0 if result['owned_child_terminated'] and (
        result['exit_reason']=='TEST_118S_DURATION_LIMIT' or result['exit_code']==0) else 1


if __name__=='__main__':
    raise SystemExit(main())
