#!/usr/bin/env python3
"""Reliable CPU-stack capture using the existing pinned original-settings runner.

Unlike v2, launch the game capture helper DIRECTLY, read its reported game PID
and verify /proc/<pid>/exe against the exact expected binary. Do not scan for
an executable path while waiting for a nested profiler to finish setup.

No shadPS4 source/build modifications; no changes to DMA/readbacks, driver,
installed launcher, kernel sysctls or SSH. If launched automatically, the
existing game capture includes screenshots and Cross input, and may autosave.
"""
from __future__ import annotations
import datetime as dt
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
import time
import traceback

HOME=Path.home()
ROOT=HOME/"projects/shadps4/GodOfWarRagnarok"
WORK=ROOT/"tests/main-proof-20261015-151529-3107006"
BINARY=WORK/"build/shadps4"
BINARY_SHA="a4d93cf19f672cf8b6dc881f11564c04e8a6af3cdffeee38e8cd669286f81932"
OLD_BIN=WORK/"preserved-before-generic-hotpath/shadps4"
OLD_SHA="a22d1eab27f1b4dc1d0bcf384d00eca7118320affe7a82ec6e15b69cec047214"
STAGE3=ROOT/"scripts/gow-main-proof-stage3.py"
STAGE3_BLOB="f64e16d3f5fe70337b43f187f3802f9c0ae3dc81"
LOGROOT=HOME/"projects/shadps4/logs"
OUT=LOGROOT/("gow-sync-stacks-v3-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
RAW=OUT/"perf.data"
GAME_LOG=OUT/"gameplay-runner.log"
SECONDS=145

def announce(message):
    print("GOW_STACK_V3 "+str(message),flush=True)

def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(1048576),b""):
            h.update(chunk)
    return h.hexdigest()

def git_blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def run(args, log, timeout=120,check=True):
    path=Path(log)
    with path.open("wb") as f:
        p=subprocess.run([str(a) for a in args],
                         stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,
                         timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f"rc={p.returncode} {list(map(str,args))[:5]}\n"+
                           path.read_text(errors="replace")[-1800:])
    return p.returncode

def exe_matches(pid):
    try:
        proc=Path("/proc")/str(pid)
        if proc.stat().st_uid!=os.getuid():
            return False
        return file_hash(proc/"exe")==BINARY_SHA
    except (OSError,PermissionError):
        return False

def running_pids():
    matches=[]
    for directory in Path("/proc").iterdir():
        if not directory.name.isdecimal():continue
        pid=int(directory.name)
        # Cheap exact inode check first, with a SHA fallback for relocated
        # executable paths or /proc exe names containing a "(deleted)" suffix.
        try:
            proc=directory/"exe"
            if (directory.stat().st_uid==os.getuid() and
                (proc.resolve(strict=True)==BINARY or
                 proc.stat().st_ino==BINARY.stat().st_ino)):
                if exe_matches(pid):
                    matches.append(pid)
        except OSError:
            continue
    return matches

def guarded_preflight():
    if (not BINARY.is_file() or BINARY.is_symlink() or
        file_hash(BINARY)!=BINARY_SHA):
        raise RuntimeError("current shadPS4 candidate differs from last known checksum")
    if (not OLD_BIN.is_file() or OLD_BIN.is_symlink() or
        file_hash(OLD_BIN)!=OLD_SHA):
        raise RuntimeError("earlier rendering checkpoint is missing")
    perf=shutil.which("perf")
    if not perf:
        raise RuntimeError("linux-perf is still unavailable on PATH")
    if run([perf,"--version"],OUT/"perf-version.log",timeout=12,check=False):
        raise RuntimeError("linux-perf command failed: "+(OUT/"perf-version.log").read_text())
    if STAGE3.is_symlink() or not STAGE3.is_file() or git_blob(STAGE3.read_bytes())!=STAGE3_BLOB:
        raise RuntimeError("original-settings game capture helper differs from pinned source")
    all_shad=subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                           capture_output=True,text=True,timeout=10)
    tracked=running_pids()
    if all_shad.returncode==0 and not tracked:
        raise RuntimeError("Another shadPS4 is active, but it does not match the verified test binary")
    if len(tracked)>1:
        raise RuntimeError("More than one verified emulator process is running")
    return perf,tracked

def pinned_capture_script():
    spec=importlib.util.spec_from_file_location("staged_go_w_capture",STAGE3)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import pinned original-config capture generator")
    stage=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    baseline=stage.load_base()
    script=stage.normal_config_capture_script(baseline)
    src=script.read_text()
    if ("Leave the actual per-game configuration completely unchanged" not in src or
        "patch_config(CFG, args.readbacks_mode)" in src):
        raise RuntimeError("game capture helper tries to change the user settings")
    run([sys.executable,"-m","py_compile",script],OUT/"capture-compile.log",timeout=40)
    run([sys.executable,script,"--self-test"],OUT/"capture-self-test.log",timeout=90)
    return script

def validate_child(pid, expected_parent):
    if not exe_matches(pid):
        raise RuntimeError(f"launch PID {pid} is not the SHA-pinned shadPS4 binary")
    try:
        status=(Path("/proc")/str(pid)/"status").read_text()
        m=re.search(r"(?m)^PPid:\s*(\d+)",status)
        if not m or int(m.group(1))!=expected_parent:
            raise RuntimeError(f"launched PID {pid} is not a child of capture runner")
    except OSError as exc:
        raise RuntimeError("cannot verify game PID ancestry") from exc
    return pid

def wait_reported_pid(helper, timeout=90):
    end=time.monotonic()+timeout
    reported=None
    while time.monotonic()<end:
        # The capture runner emits GOW_AUTO_GAMEPLAY_PID immediately on
        # successful Popen. Unlike v2, trust this IDENTIFIER only after
        # independently validating exe SHA and its parent process.
        data=GAME_LOG.read_text(errors="replace") if GAME_LOG.exists() else ""
        matched=re.findall(r"GOW_AUTO_GAMEPLAY_PID=(\d+)",data)
        if matched:
            reported=int(matched[-1])
            return validate_child(reported,helper.pid)
        if helper.poll() is not None:
            raise RuntimeError("game capture failed before launch: "+data[-5000:])
        time.sleep(.25)
    raise RuntimeError("game PID not reported within bounded launch time; capture log: "+
                       (GAME_LOG.read_text(errors="replace")[-4000:] if GAME_LOG.exists() else "<missing>"))

def profile(pid,perf,result):
    logfile=OUT/"perf-record.log"
    args=[perf,"record","-F","59","-e","cpu-clock:u",
          "--call-graph","dwarf,4096","-p",str(pid),
          "-o",str(RAW),"--","sleep","38"]
    announce("SAMPLING=38s host CPU stacks for process PID "+str(pid))
    status=run(args,logfile,timeout=78,check=False)
    if status:
        original=logfile.read_text(errors="replace")
        if any(t in original.lower() for t in
               ("permission","not permitted","access to performance","perf_event_paranoid","cap_perfmon")):
            announce("PERMISSIONS=retry perf without changing kernel sysctls")
            status=run(["sudo","-n",*args],OUT/"perf-elevated.log",
                       timeout=78,check=False)
            if status:
                raise RuntimeError("perf cannot sample even with cached sudo: "+
                    (OUT/"perf-elevated.log").read_text(errors="replace")[-1800:])
            status=run(["sudo","-n","chown",f"{os.getuid()}:{os.getgid()}",RAW],
                       OUT/"perf-chown.log",timeout=18,check=False)
            if status:
                raise RuntimeError("could not make perf.data readable after privileged sample")
        else:
            raise RuntimeError("perf sampling failed: "+original[-1800:])
    if not RAW.is_file() or RAW.stat().st_size<1200:
        raise RuntimeError("perf sampling produced no frames")
    result["perf_data_bytes"]=RAW.stat().st_size
    tasks=[
        ("perf-self.log",[perf,"report","--stdio","--no-children","-i",RAW,
                          "--sort","comm,dso,symbol","--percent-limit","0.03"]),
        ("perf-inclusive.log",[perf,"report","--stdio","--children","-i",RAW,
                               "--sort","comm,dso,symbol","--percent-limit","0.03"]),
        ("perf-caller-stacks.log",[perf,"script","-i",RAW,"--max-stack","48"]),
    ]
    for filename,cmd in tasks:
        status=run(cmd,OUT/filename,timeout=150,check=False)
        if status:
            raise RuntimeError(filename+" not generated correctly: "+
                               (OUT/filename).read_text(errors="replace")[-1200:])
    result["status"]="CPU_STACKS_CAPTURED"
    result["profile_reports"]=[task[0] for task in tasks]

def find_capture():
    if not GAME_LOG.is_file():return None
    for row in GAME_LOG.read_text(errors="replace").splitlines():
        if row.startswith("UPLOAD="):
            f=Path(row[7:].strip())
            if (f.is_file() and not f.is_symlink() and
                f.parent.resolve()==LOGROOT.resolve() and
                f.name.startswith("gow-auto-gameplay-")):
                return f
    return None

def package(result):
    (OUT/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    out=LOGROOT/(OUT.name+".tar.gz")
    with tarfile.open(out,"w:gz",compresslevel=3) as archive:
        for path in sorted(OUT.iterdir()):
            if path.is_file() and not path.is_symlink() and path.name!="perf.data":
                archive.add(path,arcname=OUT.name+"/"+path.name)
    announce("UPLOAD="+str(out))

def main():
    LOGROOT.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={"status":"STARTED","binary_sha256":BINARY_SHA,"executable_modified":False,
            "user_settings_modified":False,"installed_launcher_modified":False,
            "kernel_sysctls_modified":False,"perf_sampling_seconds":38}
    helper=None
    stream=None
    launched=False
    exit_code=1
    try:
        perf,already=guarded_preflight()
        if already:
            pid=already[0]
            announce("ATTACH=existing verified game PID "+str(pid))
            result["game_launched_automatically"]=False
        else:
            # Pre-verify the launch script before starting any child. Avoid
            # nesting it inside the previous 145-second performance profiler.
            script=pinned_capture_script()
            stream=GAME_LOG.open("wb")
            helper=subprocess.Popen([
                sys.executable,str(script),"--binary",str(BINARY),
                "--sha256",BINARY_SHA,"--seconds",str(SECONDS),
                "--interval","8","--after-delay","0.5"],
                stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,
                start_new_session=True)
            launched=True
            pid=wait_reported_pid(helper,timeout=90)
            announce("LAUNCHED=verified game PID "+str(pid))
            result["game_launched_automatically"]=True
            # Avoid shader load spikes when sampling the steady-state scene.
            time.sleep(15)
            if not exe_matches(pid):
                raise RuntimeError("game stopped before CPU profiling began")
        result["pid"]=pid
        profile(pid,perf,result)
        if helper is not None:
            try:
                helper.wait(timeout=190)
            except subprocess.TimeoutExpired:
                raise RuntimeError("bounded original-config capture did not exit normally")
            result["game_runner_exit"]=helper.returncode
            stream.flush()
            snapshot=find_capture()
            if snapshot:
                result["gameplay_archive"]=str(snapshot)
        exit_code=0
    except BaseException as exc:
        result["status"]="FAILED"
        result["error"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
    finally:
        # Only stop a game that THIS script launched. Do not terminate an
        # independently running game, the user's shell, or other session.
        if helper is not None and helper.poll() is None:
            try:
                os.killpg(helper.pid,signal.SIGINT)
                helper.wait(timeout=15)
            except (OSError,subprocess.TimeoutExpired):
                result["cleanup_warning"]="owned helper did not exit after SIGINT"
        if stream:
            stream.close()
        result["binary_unchanged"]=BINARY.is_file() and file_hash(BINARY)==BINARY_SHA
        package(result)
    return exit_code

if __name__=="__main__":
    raise SystemExit(main())
