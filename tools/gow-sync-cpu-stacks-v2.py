#!/usr/bin/env python3
"""Sample verified shadPS4 emulator CPU call stacks (Debian linux-perf).

Run manually while the verified project emulator is open, OR let this script
launch the existing verified, original-configuration gameplay profiler.
If perf requires elevation, retry ONLY perf record under cached sudo. Never
modify sysctls, guest game files directly, emulator source, installed launcher,
GPU driver, readback/DMA settings, or the caller's SSH session.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback

HOME = Path.home()
PROJECT = HOME / "projects/shadps4/GodOfWarRagnarok"
WORK = PROJECT / "tests/main-proof-20261015-151529-3107006"
BINARY = WORK / "build/shadps4"
EXPECTED_SHA = "a4d93cf19f672cf8b6dc881f11564c04e8a6af3cdffeee38e8cd669286f81932"
REFERENCE = WORK / "preserved-before-generic-hotpath/shadps4"
REFERENCE_SHA = "a22d1eab27f1b4dc1d0bcf384d00eca7118320affe7a82ec6e15b69cec047214"
PROFILE = PROJECT / "scripts/gow-generic-performance-profile.py"
PROFILE_BLOB = "99e9e14d1c0775a7d1c8b7c8a68e2b89d09360fd"
PROFILE_BASE_SHA = "26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
LOGS = HOME / "projects/shadps4/logs"
OUT = LOGS / ("gow-sync-stacks-v2-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid()))
RAW = OUT / "cpu.perf.data"

def notice(msg: str) -> None:
    print("GOW_CPU_STACKS " + str(msg), flush=True)

def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1048576), b""):
            h.update(part)
    return h.hexdigest()

def git_blob(value: bytes) -> str:
    return hashlib.sha1(b"blob "+str(len(value)).encode()+b"\0"+value).hexdigest()

def program(args, log: Path, timeout: int, *, check: bool = True):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as stream:
        p = subprocess.run(list(map(str, args)), stdin=subprocess.DEVNULL,
                           stdout=stream, stderr=subprocess.STDOUT,
                           timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f"command exit {p.returncode}: "+
                           log.read_text(errors="replace")[-1800:])
    return p.returncode

def exact_running_processes() -> list[int]:
    found = []
    for p in Path("/proc").iterdir():
        if not p.name.isdecimal():
            continue
        try:
            if p.stat().st_uid != os.getuid():
                continue
            exe = (p / "exe").resolve(strict=True)
            if exe == BINARY:
                found.append(int(p.name))
        except (OSError, ValueError):
            pass
    return found

def check_preflight():
    if not BINARY.is_file() or BINARY.is_symlink() or hash_file(BINARY) != EXPECTED_SHA:
        raise RuntimeError("verified rendered candidate binary changed: "+str(BINARY))
    if not REFERENCE.is_file() or REFERENCE.is_symlink() or hash_file(REFERENCE)!=REFERENCE_SHA:
        raise RuntimeError("earlier rendering checkpoint changed; aborting")
    if not shutil.which("perf"):
        raise RuntimeError("Debian linux-perf missing. Install with sudo apt-get install linux-perf")
    result=subprocess.run(["perf","--version"],capture_output=True,text=True,timeout=15)
    if result.returncode:
        raise RuntimeError("perf executable unusable: "+result.stderr[-900:])

def automatic_game():
    if PROFILE.is_symlink() or not PROFILE.is_file() or git_blob(PROFILE.read_bytes())!=PROFILE_BLOB:
        raise RuntimeError("original-settings gameplay profiler source not verified")
    original=PROFILE.read_text()
    if original.count(PROFILE_BASE_SHA)!=1:
        raise RuntimeError("profile has changed SHA-pinning semantics")
    path=OUT/"gameplay-profiler.py"
    path.write_text(original.replace(PROFILE_BASE_SHA,EXPECTED_SHA,1))
    program([sys.executable,"-m","py_compile",path],OUT/"pycompile.log",30)
    handle=(OUT/"gameplay-profiler.log").open("wb")
    process=subprocess.Popen([sys.executable,str(path)],cwd=PROJECT,
                             stdin=subprocess.DEVNULL,stdout=handle,
                             stderr=subprocess.STDOUT,start_new_session=True)
    notice("GAME=original-settings verified project shadPS4 auto launched")
    return process,handle

def poll_game_pid(max_seconds: int) -> int:
    deadline=time.monotonic()+max_seconds
    while time.monotonic()<deadline:
        found=exact_running_processes()
        if len(found)==1:
            return found[0]
        if len(found)>1:
            raise RuntimeError("multiple project builds of emulator running; cannot select target")
        time.sleep(.5)
    raise RuntimeError("game emulator did not appear within bounded launch window")

def sample(pid: int, result: dict):
    perf=shutil.which("perf")
    arguments=[perf,"record","--freq","59","--event","cpu-clock:u",
               "--call-graph","dwarf,4096","--pid",str(pid),
               "--output",str(RAW),"--","sleep","38"]
    notice("SAMPLE=38s CPU-clock call stacks from verified emulator PID "+str(pid))
    rc=program(arguments,OUT/"perf-record-user.log",95,check=False)
    if rc:
        raw=(OUT/"perf-record-user.log").read_text(errors="replace")
        if any(word in raw.lower() for word in (
                "permission", "operation not permitted", "access to performance",
                "perf_event_paranoid", "cap_perfmon")):
            # Elevated profiling does NOT alter perf_event_paranoid or kernel
            # settings. The operator approved sudo when invoking the command.
            notice("PERMISSIONS=retry perf sampling using cached sudo credentials")
            rc=program(["sudo","-n",*arguments],OUT/"perf-record-elevated.log",95,check=False)
            if rc:
                raise RuntimeError("perf cannot access performance counters: "+
                                   (OUT/"perf-record-elevated.log").read_text(errors="replace")[-1700:])
            rc=program(["sudo","-n","chown",
                        f"{os.getuid()}:{os.getgid()}",RAW],
                       OUT/"perf-chown.log",20,check=False)
            if rc:
                raise RuntimeError("cannot make the recorded CPU profile readable")
        else:
            raise RuntimeError("perf record failed: "+raw[-1500:])

    if not RAW.is_file() or RAW.stat().st_size < 1000:
        raise RuntimeError("perf produced no meaningful CPU stack samples")
    result["perf_data_bytes"]=RAW.stat().st_size
    report_args=[
        ("perf-self.log",[perf,"report","--stdio","--no-children","-i",RAW,
                          "--sort","comm,dso,symbol"]),
        ("perf-inclusive.log",[perf,"report","--stdio","--children","-i",RAW,
                               "--sort","comm,dso,symbol"]),
        ("perf-script.txt",[perf,"script","-i",RAW]),
    ]
    for filename,args in report_args:
        code=program(args,OUT/filename,120,check=False)
        if code:
            raise RuntimeError("profile report failed: "+filename+": "+
                               (OUT/filename).read_text(errors="replace")[-1000:])
    result["reports"]=[x[0] for x in report_args]
    result["status"]="STACK_SAMPLES_CAPTURED"

def capture_gameplay_archive():
    for row in (OUT/"gameplay-profiler.log").read_text(errors="replace").splitlines():
        if "UPLOAD=" in row:
            path=Path(row.split("UPLOAD=",1)[1].strip())
            if (path.is_file() and not path.is_symlink() and
                path.parent.resolve()==LOGS.resolve() and
                path.name.startswith("gow-generic-performance-")):
                return path
    return None

def pack(result: dict):
    (OUT/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    path=LOGS/(OUT.name+".tar.gz")
    with tarfile.open(path,"w:gz",compresslevel=3) as archive:
        for file in sorted(OUT.iterdir()):
            # perf.data usually contains raw sampled machine addresses; ship
            # the resolved callchains instead, keeping the archive phone-sized.
            if file.is_file() and not file.is_symlink() and file.name!="cpu.perf.data":
                archive.add(file,arcname=OUT.name+"/"+file.name)
    notice("UPLOAD="+str(path))

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={"status":"STARTED","phase":"PREFLIGHT","target_sha256":EXPECTED_SHA,
            "emulator_modified":False,"settings_modified":False,
            "driver_modified":False,"installed_launcher_modified":False,
            "sysctl_modified":False}
    spawned=None
    stream=None
    try:
        check_preflight()
        active=exact_running_processes()
        if len(active)>1:
            raise RuntimeError("multiple target emulator processes; refusing ambiguous profiling")
        if not active:
            spawned,stream=automatic_game()
            pid=poll_game_pid(75)
            time.sleep(18)
            if spawned.poll() is not None:
                raise RuntimeError("game capture exited before steady-state sampling")
            result["game_launched_using_original_settings"]=True
        else:
            pid=active[0]
            result["game_launched_using_original_settings"]=False
        if pid not in exact_running_processes():
            raise RuntimeError("selected emulator closed before sampling")
        result["pid"]=pid
        result["phase"]="CPU_SAMPLING"
        sample(pid,result)
        if spawned is not None:
            notice("CAPTURE=collecting the rest of the original gameplay profile")
            try:
                spawned.wait(timeout=185)
            except subprocess.TimeoutExpired:
                raise RuntimeError("original gameplay profiler exceeded its bounded capture")
            if stream:
                stream.flush()
            archive=capture_gameplay_archive()
            if archive:
                result["gameplay_profile_archive"]=str(archive)
            result["gameplay_profile_exit"]=spawned.returncode
        return 0
    except BaseException as exc:
        result["status"]="FAILED"
        result["error"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if stream is not None:
            stream.close()
        result["binary_still_unchanged"]=BINARY.is_file() and hash_file(BINARY)==EXPECTED_SHA
        pack(result)

if __name__=="__main__":
    raise SystemExit(main())
