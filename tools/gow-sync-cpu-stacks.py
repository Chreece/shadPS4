#!/usr/bin/env python3
"""Read-only follow-up for verified GoW GPU CPU hotspot.

Capture sampled userspace stacks for the running, SHA-pinned rendering build
and produce an inclusive/exclusive CPU hotspot report. Never patch emulator
code, memory coherency, game settings, installed launcher or SSH session.
Requires perf permission; if denied, report the permission error rather than
changing kernel sysctls or installing a workaround.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import traceback

HOME=Path.home()
ROOT=HOME/"projects/shadps4/GodOfWarRagnarok"
BUILD=ROOT/"tests/main-proof-20261015-151529-3107006/build/shadps4"
EXPECTED="a4d93cf19f672cf8b6dc881f11564c04e8a6af3cdffeee38e8cd669286f81932"
LOG=HOME/"projects/shadps4/logs"
OUT=LOG/("gow-sync-cpu-stacks-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
def sha(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for b in iter(lambda:f.read(1048576),b""):h.update(b)
    return h.hexdigest()
def run(args,log,timeout):
    with open(log,"wb") as f:
        p=subprocess.run(args,stdout=f,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,timeout=timeout)
    return p.returncode
def main():
    LOG.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={"status":"STARTED","emulator_modified":False,"settings_modified":False}
    try:
        if not BUILD.is_file() or BUILD.is_symlink() or sha(BUILD)!=EXPECTED:
            raise RuntimeError("current rendered binary differs; refusing unverified profiling")
        perf=shutil.which("perf")
        if not perf:raise RuntimeError("Linux perf is not installed; no automatic system changes")
        # A bounded read-only perf profile of this exact binary launched through
        # the established original-settings helper, not a new game-specific build.
        runner=ROOT/"scripts/gow-generic-performance-profile.py"
        if not runner.is_file() or runner.is_symlink():
            raise RuntimeError("existing unchanged-settings capture helper unavailable")
        # Only profile a game process already launched by the user. Avoid
        # automatically starting or terminating gameplay or modifying saves.
        candidates=[]
        for p in Path("/proc").iterdir():
            if not p.name.isdigit():continue
            try:
                exe=(p/"exe").resolve(strict=True)
                if exe==BUILD and p.stat().st_uid==os.getuid():candidates.append(int(p.name))
            except (OSError,ValueError):pass
        if len(candidates)!=1:
            raise RuntimeError("Start the existing project GoW build first; expected exactly one verified running emulator process")
        pid=candidates[0]
        result["pid"]=pid
        result["phase"]="PERF_SAMPLING"
        code=run([perf,"record","-F","97","-g","--call-graph","dwarf,8192",
                  "-p",str(pid),"-o",str(OUT/"perf.data"),"--","sleep","35"],
                 OUT/"perf-record.log",timeout=65)
        if code:raise RuntimeError("perf record failed; see perf-record.log (no sysctl changes made)")
        run([perf,"report","--stdio","--no-children","-i",str(OUT/"perf.data"),
             "--sort","comm,dso,symbol"],OUT/"perf-self.log",timeout=100)
        run([perf,"report","--stdio","-i",str(OUT/"perf.data"),
             "--sort","comm,dso,symbol"],OUT/"perf-inclusive.log",timeout=100)
        result["status"]="SAMPLED_35_SECONDS"
    except BaseException as e:
        result["status"]="FAILED";result["error"]=repr(e)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
    finally:
        (OUT/"result.json").write_text(json.dumps(result,indent=2)+"\n")
        dest=LOG/(OUT.name+".tar.gz")
        with tarfile.open(dest,"w:gz",compresslevel=3) as t:
            for f in OUT.iterdir():
                if f.is_file() and f.name!="perf.data":t.add(f,arcname=OUT.name+"/"+f.name)
        print("UPLOAD="+str(dest),flush=True)
    return 0 if result["status"]=="SAMPLED_35_SECONDS" else 1
if __name__=="__main__":raise SystemExit(main())
