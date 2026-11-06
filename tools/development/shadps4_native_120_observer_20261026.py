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
                if gpu_work_bytes+size<=4*1024**2:
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
    record={'elapsed_seconds':round(elapsed,3),'gpus':[]}
    try:
        record['process_stat']=(proc/str(pid)/'stat').read_text()[:4096]
    except OSError as error:
        record['process_stat_error']=str(error)
    cards=sorted(p for p in drm.glob('card*') if re.fullmatch(r'card\d+',p.name))
    for card in cards[:8]:
        gpu={'card':card.name}
        for metric in ('gpu_busy_percent','mem_busy_percent','mem_info_vram_used'):
            try:
                value=(card/'device'/metric).read_text()[:128].strip()
                if value.isdecimal():
                    gpu[metric]=int(value)
            except OSError:
                pass
        record['gpus'].append(gpu)
    with (out/'performance-usage.jsonl').open('a') as stream:
        stream.write(json.dumps(record)+'\n')


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
            validation_error=None
            console_offset=0
            pending_line=''
            while child.poll() is None and time.monotonic()-began<118:
                elapsed=time.monotonic()-began
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
                    if launch['performance_diagnostics_requested']:
                        capture_performance_usage(child.pid,report,elapsed)
                    previous_map_capture=elapsed
                for second in due:
                    if elapsed>=second and second not in taken:
                        taken.add(second)
                        capture_owned_stop_state(child.pid,report,
                            filename=f'owned-process-state-{second:03d}s.json')
                        if take_picture(report,second,display):
                            result['screenshots'].append(f'frame-{second:03d}s.png')
                time.sleep(min(0.4,max(0.0,118-elapsed)))
            if gpu_failure:
                result['exit_reason']='GPU_CONTEXT_LOST'
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
        if child is not None:
            if child.poll() is None:
                stop_error=term_owned(child,'TERM_THEN_OPTIONAL_KILL',began+120.0)
                if stop_error:
                    result['termination_error']=stop_error
            result['owned_child_terminated']=child.poll() is not None
            result['exit_code']=child.poll()
            if not result['owned_child_terminated']:
                result['exit_reason']='OWNED_PROCESS_STOP_UNCONFIRMED'
        result['elapsed_seconds']=round(time.monotonic()-began,3)
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
            result['trigger_reason']=result['exit_reason']
            result['exit_reason']='OWNED_PROCESS_STOP_UNCONFIRMED'
            capture_owned_stop_state(child.pid,report)
        if failure:
            result['failure']=failure
        save(report/'native-result.json',result)
    print('NATIVE_GENERIC_TEST_RESULT='+result['exit_reason'],flush=True)
    print('GAME_EXIT_CODE='+str(result['exit_code']),flush=True)
    print('TEST_RUNTIME_SECONDS='+str(result['elapsed_seconds']),flush=True)
    print('GAME_PROCESS_LEFT_RUNNING='+('NO' if result['owned_child_terminated'] else 'UNKNOWN'),flush=True)
    print('NO_AUTOMATIC_INPUT=YES; FULLSCREEN_AND_FPS=YES; NO_CPU_ID_OVERRIDES=YES',flush=True)
    return 0 if not failure and not result.get('gpu_failure') and not result.get('graphics_diagnostics_failure') and not (
        launch['vulkan_diagnostics_requested'] and (
            result.get('first_validation_failure') or not result.get('validation_layer_active'))
        ) and result['owned_child_terminated'] and (
        result['exit_reason']=='TEST_118S_DURATION_LIMIT' or result['exit_code']==0) else 1


if __name__=='__main__':
    raise SystemExit(main())
