#!/usr/bin/env python3
"""Reliable CPU sampling of shadPS4's translated Linux process.

Uses the ALREADY-PROVEN process validation from the original-config gameplay
capture helper. This handles DynamoRIO bin64/drrun and libdynamorio.so as
/proc/PID/exe, requiring the SHA-pinned game executable in the process maps.
No build/config/driver/kernel/SSH changes; normal auto game play may autosave.
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
OUT=LOGROOT/("gow-sync-stacks-v4-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
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

def owned_process_snapshot(guard, pid, expected_parent=None):
    """Use the EXACT identity and mapped-binary test from our gameplay runner.

    CPU-ID mode auto execs drrun/libdynamorio.so; hashing /proc/PID/exe
    is incorrect even though the pinned emulator is mapped and executing.
    """
    proc=Path("/proc")/str(pid)
    stat=guard.parse_stat((proc/"stat").read_text())
    parent=stat["parent_pid"]
    if expected_parent is not None and parent!=expected_parent:
        raise RuntimeError("reported game PID is not a child of its capture runner")
    if expected_parent is not None and (stat["process_group"]!=pid or
                                        stat["session_id"]!=pid):
        raise RuntimeError("reported game PID did not retain its dedicated session")
    launch={
        "pid":pid,
        "owner_uid":os.getuid(),
        "parent_pid":parent,
        "process_group":stat["process_group"],
        "session_id":stat["session_id"],
        "start_ticks":stat["start_ticks"],
        "binary_identity":guard.identity(BINARY),
        "runtime_executables":guard.runtime_candidates(BINARY),
    }
    mode=guard.validate_process(pid,BINARY,launch)
    return {"pid":pid,"mode":mode,"launch":launch}


def running_pids(guard):
    # A translated game uses drrun or libdynamorio.so as /proc/PID/exe.
    # Search the same inode identities as the established safe game-input
    # guard, then verify that the exact emulator binary is mapped.
    allowed={guard.identity(BINARY)}
    allowed.update(candidate["identity"] for candidate in guard.runtime_candidates(BINARY))
    matches=[]
    for directory in Path("/proc").iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            if directory.stat().st_uid!=os.getuid():
                continue
            if guard.identity(directory/"exe") not in allowed:
                continue
            match=owned_process_snapshot(guard,int(directory.name))
            matches.append(match)
        except (OSError,RuntimeError,ValueError):
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
    return perf

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

    spec=importlib.util.spec_from_file_location("verified_original_gameplay_guard",script)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import the verified original gameplay guard")
    guard=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    for name in ("identity","parse_stat","runtime_candidates","validate_process"):
        if not callable(getattr(guard,name,None)):
            raise RuntimeError("original gameplay identity guard incomplete: "+name)
    return script,guard

def wait_reported_pid(helper, guard, timeout=90):
    deadline=time.monotonic()+timeout
    failure=None
    pid=None
    while time.monotonic()<deadline:
        data=GAME_LOG.read_text(errors="replace") if GAME_LOG.exists() else ""
        matches=re.findall(r"GOW_AUTO_GAMEPLAY_PID=(\d+)",data)
        if matches:
            pid=int(matches[-1])
            try:
                return owned_process_snapshot(guard,pid,helper.pid)
            except (OSError,RuntimeError,ValueError) as error:
                # The launcher can still be transitioning from the emulator
                # image to DynamoRIO. Retry until the exact mapped game
                # binary and process identity pass the established guard.
                failure=repr(error)
        if helper.poll() is not None:
            raise RuntimeError("capture runner exited before the verified game was ready; "+
                "reported_pid="+str(pid)+" last_validation="+str(failure)+
                " runner_log="+data[-2600:])
        time.sleep(.25)
    raise RuntimeError("game PID could not be verified through CPU-ID runtime: "+
        "reported_pid="+str(pid)+" last_error="+str(failure)+
        " runner_log="+(GAME_LOG.read_text(errors="replace")[-1700:]
                       if GAME_LOG.exists() else "<missing>"))

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
        perf=guarded_preflight()
        script,guard=pinned_capture_script()
        already=running_pids(guard)
        if len(already)>1:
            raise RuntimeError("multiple verified emulator processes found; won't guess a PID")
        if already:
            game=already[0]
            pid=game["pid"]
            announce("ATTACH=existing verified game PID "+str(pid)+
                     " executable_mode="+game["mode"])
            result["game_launched_automatically"]=False
        else:
            # Don't disturb another unrelated user game/process.
            pgrep=subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                                 capture_output=True,timeout=10)
            if pgrep.returncode==0:
                raise RuntimeError("another shadPS4 session appears active; close it before profiling")
            stream=GAME_LOG.open("wb")
            helper=subprocess.Popen([
                sys.executable,str(script),"--binary",str(BINARY),
                "--sha256",BINARY_SHA,"--seconds",str(SECONDS),
                "--interval","8","--after-delay","0.5"],
                stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,
                start_new_session=True)
            launched=True
            game=wait_reported_pid(helper,guard,timeout=90)
            pid=game["pid"]
            announce("LAUNCHED=verified game PID "+str(pid)+
                     " executable_mode="+game["mode"])
            result["game_launched_automatically"]=True
            time.sleep(15)
            # Defend against PID reuse before touching perf.
            guard.validate_process(pid,BINARY,game["launch"])
        result["pid"]=pid
        result["cpu_id_mode"]=game["mode"]
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
