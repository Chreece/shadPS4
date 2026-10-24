#!/usr/bin/env python3
"""Normal-speed GoT GPU crash diagnosis; native only, no tracing/rebuild/ES-DE edits.

Capture process mappings, early screenshots, and game logs without ptrace.
The crash instruction RIP, when present, is resolved to a mapped ELF module.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback

ROOT=Path.home()/"projects/shadps4"
GAME=Path("/mnt/roms-all/ps4-games/CUSA11456/eboot.bin")
SESSION=ROOT/"test-profiles/got-generic-world-20261024"
REV="f5abd9511cb3b38ba6b1eea5bbc84d5476b0677c"
SHA="8b5089b0b6aab5485f55b2aa1e1da53c560e1f3a16b22c327f651131f2dfe096"
BIN=ROOT/"GhostOfTsushima/candidates"/(REV+"-"+SHA)/"shadps4"
REPORT=ROOT/"logs/got-restored-srt-20261024-150712-k7mozuu9/info/build-result.json"
NATIVE=ROOT/"scripts/build_run_got_upstream_native_20261024.py"
NATIVE_BLOB="96c6b84bde950513c237a9daec6177999fb3ef92"
FATAL=re.compile(
    r"Unhandled access violation at code address\s+(0x[0-9a-fA-F]+):\s*"
    r"(Read from|Write to|Executed from)\s+address\s+(0x[0-9a-fA-F]+)")
CONSOLE_HEAD=1024*1024
CONSOLE_TAIL=12*1024*1024

def sha256(path):
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("REGULAR_BINARY_MISSING="+str(path))
    with path.open("rb") as fp:
        return hashlib.file_digest(fp,"sha256").hexdigest()

def blob(path):
    data=path.read_bytes()
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def setup():
    if sha256(BIN)!=SHA:
        raise RuntimeError("RESTORED_GENERIC_BINARY_HASH_MISMATCH")
    if not os.access(BIN,os.X_OK):
        raise RuntimeError("RESTORED_GENERIC_BINARY_NOT_EXECUTABLE")
    if not REPORT.is_file():
        raise RuntimeError("VERIFIED_SOURCE_REPORT_MISSING")
    report=json.loads(REPORT.read_text())
    expected={
        "status":"RESTORED_PROVEN_GENERIC_SRT_BUILD_PASS",
        "revision":REV,"candidate_sha256":SHA,"dynamorio":False,
        "es_de_changed":False,"shader_fallback_restored":True,
        "uniform_lane_restored":True,
    }
    for key,value in expected.items():
        if report.get(key)!=value:
            raise RuntimeError("RESTORED_SOURCE_BUILD_REPORT_MISMATCH="+key)
    if Path(report["candidate"])!=BIN:
        raise RuntimeError("RESTORED_CANDIDATE_REPORT_PATH_MISMATCH")
    if not GAME.is_file() or not (SESSION/"user/config.json").is_file():
        raise RuntimeError("ISOLATED_GOT_TEST_PROFILE_OR_GAME_MISSING")
    if (BIN.parent/"cpu-id-runtime").exists():
        raise RuntimeError("DYNAMORIO_RUNTIME_REAPPEARED")
    if not NATIVE.is_file() or blob(NATIVE)!=NATIVE_BLOB:
        raise RuntimeError("PINNED_NATIVE_SESSION_MODULE_CHANGED")
    spec=importlib.util.spec_from_file_location("got_native_launch_helpers",NATIVE)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module

def sample_maps(pid,folder,now,previous):
    try:
        data=Path(f"/proc/{pid}/maps").read_bytes()
        comms=[]
        for entry in Path(f"/proc/{pid}/task").iterdir():
            try:comms.append(entry.joinpath("comm").read_text().strip())
            except (OSError,ValueError):pass
    except (OSError,ProcessLookupError):
        return previous
    # Save an early map and the most recent map. Sampling maps doesn't stop
    # the process or alter scheduling with ptrace.
    (folder/"last-maps.txt").write_bytes(data)
    if not (folder/"initial-maps.txt").exists():
        (folder/"initial-maps.txt").write_bytes(data)
    (folder/"threads-last.json").write_text(
        json.dumps({"seconds":round(now,1),"threads":Counter(comms)},indent=2))
    if data!=previous and previous is not None:
        with (folder/"mapping-changes.txt").open("a") as f:
            f.write(f"Changed after {now:.1f}s; bytes={len(data)}\n")
    return data

def screenshot(folder,env,index):
    dest=folder/f"screen-{index:02d}.png"
    candidates=[
        ["scrot","-o",str(dest)],
        ["import","-window","root",str(dest)],
    ]
    for command in candidates:
        if not shutil.which(command[0]):continue
        try:
            p=subprocess.run(command,env=env,stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,timeout=12)
            if p.returncode==0 and dest.is_file() and dest.stat().st_size>1024:
                return dest
        except (OSError,subprocess.TimeoutExpired):pass
    return None

def stop_owned_child(proc):
    if proc.poll() is not None:return
    for sig,wait in ((signal.SIGINT,5),(signal.SIGTERM,5),(signal.SIGKILL,3)):
        try:
            os.killpg(proc.pid,sig)
            proc.wait(timeout=wait)
            return
        except ProcessLookupError:return
        except subprocess.TimeoutExpired:pass

def read_edges(source,head,tail):
    if not source.is_file():return
    with source.open("rb") as f:
        head.write_bytes(f.read(CONSOLE_HEAD))
        f.seek(max(0,source.stat().st_size-CONSOLE_TAIL))
        tail.write_bytes(f.read(CONSOLE_TAIL))

def identify_fault(run):
    result={"crash_captured":False}
    tail=run/"console-tail.log"
    text=tail.read_text(errors="replace") if tail.is_file() else ""
    matches=list(FATAL.finditer(text))
    if not matches:
        result["explanation"]="No fatal access violation in bounded console log"
        return result
    match=matches[-1]
    rip=int(match.group(1),16)
    fault=int(match.group(3),16)
    result.update({"crash_captured":True,"rip":hex(rip),
                   "access":match.group(2),"fault_address":hex(fault),
                   "mapped_object":"UNKNOWN"})
    maps=run/"last-maps.txt"
    if not maps.is_file():return result
    for line in maps.read_text(errors="replace").splitlines():
        m=re.match(r"([0-9a-f]+)-([0-9a-f]+)\s+(\S+)\s+([0-9a-f]+)\s+\S+\s+\d+\s*(.*)",line)
        if not m:continue
        start,end,perms,offset,name=m.groups()
        start,end,offset=int(start,16),int(end,16),int(offset,16)
        if not start<=rip<end:continue
        elf_offset=rip-start+offset
        result.update({"mapping":line,"mapped_object":name,
                       "mapping_relative_rip":hex(rip-start),
                       "elf_offset":hex(elf_offset)})
        object_path=Path(name)
        if not name.startswith("/") or not object_path.is_file():break
        for exe,argv,target in (
            ("addr2line",["-f","-C","-i","-e",str(object_path),
                          hex(elf_offset)],"addr2line.txt"),
            ("objdump",["-C","-d","--start-address="+hex(max(0,elf_offset-64)),
                        "--stop-address="+hex(elf_offset+96),
                        str(object_path)],"fault-disassembly.txt"),
        ):
            if not shutil.which(exe):continue
            try:
                p=subprocess.run([exe,*argv],capture_output=True,text=True,timeout=15)
                (run/target).write_text(p.stdout+p.stderr)
            except (OSError,subprocess.TimeoutExpired) as err:
                (run/(target+".error")).write_text(str(err))
        break
    return result

def bounded_coredump_info(pid,run):
    if not shutil.which("coredumpctl"):return
    try:
        p=subprocess.run(["coredumpctl","--no-pager","info",str(pid)],
                         capture_output=True,text=True,timeout=12)
        if p.returncode==0:
            (run/"coredump-info.txt").write_text((p.stdout+p.stderr)[-100000:])
    except (OSError,subprocess.TimeoutExpired):pass

def make_archive(run):
    output=Path(str(run)+".tar.gz")
    with tarfile.open(output,"w:gz") as arc:
        for p in run.rglob("*"):
            if p.is_file() and not p.is_symlink() and p.name!="console.full.log":
                if p.stat().st_size<=14*1024*1024:
                    arc.add(p,arcname=str(p.relative_to(run)))
    return output

def main():
    args=argparse.ArgumentParser(description=__doc__)
    args.add_argument("--seconds",type=int,default=300)
    ns=args.parse_args()
    if not 60<=ns.seconds<=900:args.error("duration must be 60 to 900 seconds")
    (ROOT/"logs").mkdir(parents=True,exist_ok=True)
    run=Path(tempfile.mkdtemp(prefix="got-untraced-gpu-maps-",
                              dir=ROOT/"logs"))
    proc=None;models=[];native=None;rc=125;start=time.monotonic()
    try:
        native=setup()
        env=native.prepare_display()
        models=native.vram_prepare()
        print("RUN_MODE=UNTRACED_NATIVE_EXECUTION",flush=True)
        print("NO_REBUILD=YES DYNAMORIO=REJECTED ES_DE=UNCHANGED",flush=True)
        print("CANDIDATE_SHA256="+SHA,flush=True)
        print("DISPLAY="+env.get("DISPLAY","UNSET"),flush=True)
        print("At menu press controller X (keyboard N) to enter gameplay.",flush=True)
        command=[str(BIN),"--game",str(GAME),"--fullscreen","true","--show-fps"]
        (run/"run-input.json").write_text(json.dumps(
            {"candidate":str(BIN),"sha256":SHA,"game":str(GAME),
             "profile":str(SESSION),"duration_limit":ns.seconds,
             "ptrace":False,"dynamorio":False,"es_de_modified":False,
             "automated_controller_input":False},indent=2)+"\n")
        with (run/"console.full.log").open("wb") as log:
            proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,
                                  cwd=SESSION,env=env,start_new_session=True)
            start=time.monotonic();previous=None;next_map=0;next_status=10
            capture_times=(20,35,50,80,120,175,230,290)
            captured=0;checked=set()
            while proc.poll() is None:
                elapsed=time.monotonic()-start
                if elapsed>=ns.seconds:
                    print("NORMAL_SPEED_TIME_LIMIT=YES",flush=True)
                    break
                if elapsed>=next_map:
                    previous=sample_maps(proc.pid,run,elapsed,previous)
                    next_map+=1
                if elapsed>=next_status:
                    print(f"GAME_RUNNING_SECONDS={elapsed:.0f}",flush=True)
                    next_status+=20
                for threshold in capture_times:
                    if elapsed>=threshold and threshold not in checked:
                        checked.add(threshold)
                        shot=screenshot(run,env,captured+1)
                        if shot:
                            captured+=1
                            print("SCREENSHOT="+str(shot),flush=True)
                time.sleep(0.2)
            stop_owned_child(proc)
            rc=proc.returncode if proc.returncode is not None else 125
        print("GAME_EXIT_CODE="+str(rc),flush=True)
    except KeyboardInterrupt:
        print("USER_STOP_REQUESTED=YES",flush=True)
        if proc is not None:stop_owned_child(proc)
        rc=130
    except BaseException as err:
        traceback.print_exc()
        (run/"failure.txt").write_text(traceback.format_exc())
        if proc is not None:stop_owned_child(proc)
        rc=125
    finally:
        read_edges(run/"console.full.log",run/"console-head.log",
                   run/"console-tail.log")
        fault=identify_fault(run)
        fault.update({"exit_code":rc,
                      "duration_seconds":round(time.monotonic()-start,2),
                      "normal_speed":True,"gameplay_verified":False})
        (run/"fault-analysis.json").write_text(json.dumps(fault,indent=2)+"\n")
        if proc is not None and rc is not None and rc<0:
            bounded_coredump_info(proc.pid,run)
        if native is not None:
            try:native.vram_restore(models)
            except Exception as e:
                (run/"ollama-restore-warning.txt").write_text(str(e))
        print("GPU_FAULT_MODULE="+fault.get("mapped_object","NOT_CAPTURED"),flush=True)
        print("GPU_FAULT_RIP="+fault.get("rip","NOT_CAPTURED"),flush=True)
        print("NORMAL_SPEED_RUNTIME_RESULT="+str(rc),flush=True)
        print("UPLOAD_THIS="+str(make_archive(run)),flush=True)
    return 0 if rc==0 else 2

if __name__=="__main__":
    raise SystemExit(main())
