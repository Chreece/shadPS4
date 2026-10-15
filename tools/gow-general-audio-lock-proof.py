#!/usr/bin/env python3
"""General shadPS4 multi-port audio correctness: pinned incremental A/B test.

Apply only the upstream-based multi-port audio synchronization fix to the
existing clean upstream-main+general-fixes source tree. Save a byte-identical
copy of the currently rendering executable before relinking. Compile and run
the standalone real-helper concurrency regression, run existing GCN suite,
then profile the rebuilt game with exactly the same unmodified user settings
and profiler as the previous performance capture. Never touch the installed
emulator, saves directly, GPU drivers, DMA, readbacks, or SSH session.
A normal game launch can make ordinary game saves.
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
import traceback

HOME=Path.home()
ROOT=HOME/"projects/shadps4/GodOfWarRagnarok"
WORK=ROOT/"tests/main-proof-20261015-151529-3107006"
SRC=WORK/"source"
BUILD=WORK/"build"
TEST_BUILD=WORK/"build-gcn-tests"
LOGS=HOME/"projects/shadps4/logs"
OUT=LOGS/("gow-general-audio-lock-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
TOPIC="9c4a99ed399f1ee9b2fe5c54a9bfe75ad12fb27e"
UPSTREAM="b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
GENERAL_STACK="966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe"
BIN_OLD="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
GOOD_SHA="7ee847328072f9d65772fc85ee19f93143cbaaafe69e381d70379479ddd81a30"
PREVIOUS_PROFILE=ROOT/"scripts/gow-generic-performance-profile.py"
PROFILE_BLOB="99e9e14d1c0775a7d1c8b7c8a68e2b89d09360fd"
CPP="src/core/libraries/audio/audioout.cpp"
HEADER="src/core/libraries/audio/audioout_sync.h"
TEST="tests/audio/test_audio_output_batch_sync.cpp"
FILES={
    CPP:"2cb12200709588bc587a22eb4a498c4e29821156",
    HEADER:"8efda58a938100999a248e7fcdbd7b7159ec5658",
    TEST:"dbdd33073feb3139843a756464ef82f7a8bf9857",
}
CPP_OLD_BLOB="92fcdbf56bc40e91d588030404316eb0713a05e5"
SNAP=WORK/"preserved-before-general-audio-lock"
MANIFEST=WORK/".general-audio-multiport-lock.json"
LAST_REPORT=LOGS/"gow-generic-performance-20261015-192250-2083055/result.json"

def say(v):
    print("GENERAL_AUDIO_SYNC "+str(v),flush=True)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def sha_file(p):
    h=hashlib.sha256()
    with Path(p).open("rb") as f:
        for d in iter(lambda:f.read(1024*1024),b""):h.update(d)
    return h.hexdigest()

def blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def run(args,*,cwd=None,log=None,timeout=800,check=True):
    cmd=[str(x) for x in args]
    if cmd[0]=="git":
        cmd=["git","-c","http.version=HTTP/1.1",*cmd[1:]]
    if log:
        p=Path(log);p.parent.mkdir(parents=True,exist_ok=True)
        with p.open("wb") as f:
            p0=subprocess.run(cmd,cwd=cwd,stdin=subprocess.DEVNULL,
                              stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
        if check and p0.returncode:
            raise RuntimeError(f"rc={p0.returncode} {cmd[:5]}\n"+
                               p.read_text(errors="replace")[-2600:])
        return p0.returncode,""
    p0=subprocess.run(cmd,cwd=cwd,stdin=subprocess.DEVNULL,capture_output=True,
                      text=True,timeout=timeout)
    if check and p0.returncode:
        raise RuntimeError(f"rc={p0.returncode} {cmd[:5]}\n"+
                           (p0.stderr+p0.stdout)[-3000:])
    return p0.returncode,p0.stdout

def json_dump(path,model):
    path=Path(path)
    path.write_text(json.dumps(model,indent=2,sort_keys=True)+"\n")

def fetch(path):
    url=f"https://raw.githubusercontent.com/Chreece/shadPS4/{TOPIC}/{path}"
    fd,temp=tempfile.mkstemp(prefix=".audio-topic-",dir=OUT)
    os.close(fd)
    try:
        run(["curl","-4fsSL","--retry","3","--connect-timeout","20",
             "--max-time","120",url,"-o",temp],timeout=360)
        raw=Path(temp).read_bytes()
        if not raw or len(raw)>2_000_000:
            raise RuntimeError("invalid published source "+path)
        return raw
    finally:
        Path(temp).unlink(missing_ok=True)

def check_candidate():
    if SRC.is_symlink() or not (SRC/".git").is_dir():
        raise RuntimeError("original upstream source missing")
    parts=run(["git","rev-list","--parents","-n","1","HEAD"],cwd=SRC)[1].split()
    if len(parts)!=3 or parts[1:]!=[UPSTREAM,GENERAL_STACK]:
        raise RuntimeError("source is not previous upstream main + proven general stack")
    if run(["git","ls-files","-u"],cwd=SRC)[1].strip():
        raise RuntimeError("unresolved source merge conflict")
    game_binary=BUILD/"shadps4"
    previous_good=ROOT/"tests/gow91-build-20261015-125214-1308309/shadps4"
    if not previous_good.is_file() or previous_good.is_symlink() or sha_file(previous_good)!=GOOD_SHA:
        raise RuntimeError("historic rendered executable changed")
    if not game_binary.is_file() or game_binary.is_symlink():
        raise RuntimeError("current rendering executable missing")
    if not MANIFEST.exists() and sha_file(game_binary)!=BIN_OLD:
        raise RuntimeError("latest rendering candidate changed; refusing another regression")
    if not TEST_BUILD.is_dir():
        raise RuntimeError("independent GCN build unavailable")
    if MANIFEST.is_symlink():
        raise RuntimeError("unsafe recovery manifest")
    if subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                      stdout=subprocess.DEVNULL).returncode==0:
        raise RuntimeError("another emulator is running; not interrupting it")

def commit_audio_fix():
    body=fetch(CPP)
    header=fetch(HEADER)
    test=fetch(TEST)
    for path,raw in ((CPP,body),(HEADER,header),(TEST,test)):
        if blob(raw)!=FILES[path]:
            raise RuntimeError("published general audio fix/test has an unexpected blob: "+path)
    if b"WaitForAudioPortsReady" not in body or b"std::ranges::find_if" not in header or b"AUDIO_MULTI_PORT_SYNC=PASS cases=3" not in test:
        raise RuntimeError("audio code or test coverage incomplete")
    for raw in (body,header,test):
        if any(x in raw for x in (b"CUSA34384",b"SHADPS4_GOW_",b"GOW_SRT_")):
            raise RuntimeError("game-specific logic found in generic audio source")
    manifest={
        "topic":TOPIC,
        "base_upstream":UPSTREAM,
        "general_stack":GENERAL_STACK,
        "before_binary":BIN_OLD,
        "changed_files":[CPP,HEADER,TEST],
        "published_blob_shas":FILES,
        "game_specific_emulator_changes":False,
    }
    if MANIFEST.exists():
        current=json.loads(MANIFEST.read_text())
        for key in ("topic","base_upstream","general_stack","before_binary",
                    "changed_files","published_blob_shas"):
            if current.get(key)!=manifest[key]:
                raise RuntimeError("previous general audio source manifest mismatch: "+key)
        for p,raw in ((CPP,body),(HEADER,header),(TEST,test)):
            if not (SRC/p).is_file() or (SRC/p).is_symlink() or (SRC/p).read_bytes()!=raw:
                raise RuntimeError("previous applied audio fix changed unexpectedly: "+p)
        if sha_file(SNAP/"shadps4")!=BIN_OLD:
            raise RuntimeError("original emulator executable checkpoint corrupted")
        say("SOURCE=verified previously applied generic audio changes")
        return current

    cpp=SRC/CPP
    if not cpp.is_file() or cpp.is_symlink() or blob(cpp.read_bytes())!=CPP_OLD_BLOB:
        raise RuntimeError("audio implementation diverged from pinned upstream")
    for p in (HEADER,TEST):
        if (SRC/p).exists() or (SRC/p).is_symlink():
            raise RuntimeError("new generic source already present without provenance: "+p)
    SNAP.mkdir(parents=True,exist_ok=False)
    shutil.copy2(BUILD/"shadps4",SNAP/"shadps4")
    shutil.copy2(cpp,SNAP/"audioout.cpp")
    if sha_file(SNAP/"shadps4")!=BIN_OLD:
        raise RuntimeError("previous rendering executable not preserved byte for byte")
    try:
        cpp.write_bytes(body)
        for p,raw in ((HEADER,header),(TEST,test)):
            dst=SRC/p
            dst.parent.mkdir(parents=True,exist_ok=True)
            dst.write_bytes(raw)
        run(["git","diff","--check"],cwd=SRC,timeout=60)
        json_dump(MANIFEST,manifest)
        say("SOURCE=correct multi-port audio mutex ownership; no game-specific changes")
        return manifest
    except BaseException:
        shutil.copy2(SNAP/"audioout.cpp",cpp)
        for p in (HEADER,TEST): (SRC/p).unlink(missing_ok=True)
        raise

def compile_audio_concurrency():
    compiler=shutil.which("g++") or shutil.which("clang++")
    if not compiler:raise RuntimeError("C++ compiler missing")
    binary=OUT/"test-audio-multiport"
    say("AUDIO_TEST=compile and execute real sync helper regression")
    run([compiler,"-std=c++20","-O2","-pthread","-I",SRC/"src",
         SRC/TEST,"-o",binary],log=OUT/"audio-test-build.log",timeout=240)
    run([binary],log=OUT/"audio-test-result.log",timeout=15)
    text=(OUT/"audio-test-result.log").read_text()
    if "AUDIO_MULTI_PORT_SYNC=PASS cases=3" not in text:
        raise RuntimeError("audio concurrency tests did not pass")
    say("AUDIO_TEST=PASS three independent multi-port concurrency cases")

def check_gcn():
    say("GCN=verify last 76 general tests retained")
    run(["cmake","--build",TEST_BUILD,"--target","shadps4_gcn_test","--parallel","4"],
        log=OUT/"gcn-build.log",timeout=7200)
    g=TEST_BUILD/"tests/shadps4_gcn_test"
    if not g.is_file() or g.is_symlink():
        raise RuntimeError("known GCN test executable missing")
    rc,_=run([g,"--gtest_output=json:"+str(OUT/"gcn-results.json")],
             log=OUT/"gcn-test.log",timeout=1400,check=False)
    if rc!=0 or not (OUT/"gcn-results.json").is_file():
        raise RuntimeError("independent GCN tests did not pass")
    report=json.loads((OUT/"gcn-results.json").read_text())
    if int(report.get("tests",0))!=76 or int(report.get("failures",1))!=0:
        raise RuntimeError("expected exact 76 GCN regressions missing/failed")
    say("GCN=PASS 76/76")

def check_installed():
    installed=HOME/"Applications/shadps4/shadps4"
    if not installed.exists():
        return {"path":str(installed),"exists":False,"changed":False}
    return {"path":str(installed),"exists":True,"is_symlink":installed.is_symlink(),
            "sha256":sha_file(installed) if installed.is_file() else None,
            "changed":False}

def execute_perf(binary_sha):
    script=PREVIOUS_PROFILE
    if not script.is_file() or script.is_symlink() or blob(script.read_bytes())!=PROFILE_BLOB:
        raise RuntimeError("pinned previous unchanged-settings performance profiler not found")
    original=script.read_text()
    if original.count(BIN_OLD)!=1:
        raise RuntimeError("pinned profiler differs from the original runtime contract")
    new=original.replace(BIN_OLD,binary_sha,1)
    dest=WORK/"helpers/performance-after-audio-lock.py"
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(new)
    run([sys.executable,"-m","py_compile",dest],timeout=60)
    say("PERF=identical original-settings profiler on new generic audio build")
    p=OUT/"performance-runner.log"
    rc,_=run([sys.executable,dest],log=p,timeout=420,check=False)
    candidates=[]
    for line in p.read_text(errors="replace").splitlines():
        if line.startswith("UPLOAD="):
            f=Path(line[7:].strip())
            if (f.is_file() and not f.is_symlink() and
                    f.parent.resolve()==LOGS.resolve() and
                    f.name.startswith("gow-generic-performance-")):
                candidates.append(f)
    if not candidates:
        raise RuntimeError(f"performance capture rc={rc}: "+p.read_text(errors="replace")[-1300:])
    return candidates[-1],rc

def read_measurements(profile):
    with tarfile.open(profile,"r:gz") as archive:
        members=[m for m in archive if m.isfile() and m.name.endswith("/result.json")]
        if len(members)!=1 or members[0].size>120000:
            raise RuntimeError("profile missing unique compact result")
        stream=archive.extractfile(members[0])
        if stream is None:raise RuntimeError("profile metadata inaccessible")
        return json.load(stream)

def package(result):
    json_dump(OUT/"result.json",result)
    dest=LOGS/(OUT.name+".tar.gz")
    with tarfile.open(dest,"w:gz",compresslevel=3) as t:
        for f in sorted(OUT.rglob("*")):
            if f.is_file() and not f.is_symlink() and f.name!="test-audio-multiport":
                t.add(f,arcname=OUT.name+"/"+str(f.relative_to(OUT)))
        capture=result.get("performance_archive")
        if capture:
            p=Path(capture)
            if p.is_file() and not p.is_symlink() and p.parent.resolve()==LOGS.resolve():
                t.add(p,arcname=OUT.name+"/performance-capture.tar.gz")
    say("UPLOAD="+str(dest))

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={
        "status":"STARTED","phase":"SOURCE_CHECK","upstream_main":UPSTREAM,
        "general_stack":GENERAL_STACK,"general_audio_topic":TOPIC,
        "proven_rendered_baseline_sha256":BIN_OLD,
        "installed_emulator_unchanged":True,"game_specific_source_fix":False,
        "dma_or_readbacks_toggled":False,"audio_fix_runtime_verified":False,
        "graphics_performance_fix_claimed":False,
    }
    try:
        check_candidate()
        result["installed_before"]=check_installed()
        result["source"]=commit_audio_fix()
        result["phase"]="REGRESSIONS"
        compile_audio_concurrency()
        check_gcn()
        result["phase"]="INCREMENTAL_BUILD"
        say("BUILD=incrementally rebuild current upstream+generic emulator; save old binary")
        run(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
            log=OUT/"build.log",timeout=7200)
        result["new_binary_sha256"]=sha_file(BUILD/"shadps4")
        result["previous_binary_checkpoint_sha256"]=sha_file(SNAP/"shadps4")
        if result["previous_binary_checkpoint_sha256"]!=BIN_OLD or (
                result["new_binary_sha256"]==BIN_OLD):
            raise RuntimeError("rebuilt executable not new or previous binary was changed")
        run(["ldd",BUILD/"shadps4"],log=OUT/"ldd.log",timeout=60)
        if "not found" in (OUT/"ldd.log").read_text(errors="replace"):
            raise RuntimeError("new executable missing shared libraries")
        result["phase"]="PERFORMANCE_AB"
        capture,rc=execute_perf(result["new_binary_sha256"])
        result["performance_archive"]=str(capture)
        result["performance_runner_exit"]=rc
        after=read_measurements(capture)
        result["new_performance"]={
            "status":after.get("status"),
            "binary_sha256":after.get("binary_sha256"),
            "events":after.get("event_counts"),
            "resource_summary":after.get("resource_summary"),
            "config_unchanged":after.get("original_config_hashes_unchanged"),
            "binary_unchanged":after.get("verified_binary_unchanged"),
        }
        if LAST_REPORT.is_file() and not LAST_REPORT.is_symlink():
            prev=json.loads(LAST_REPORT.read_text())
            if prev.get("binary_sha256")==BIN_OLD:
                result["previous_performance"]={
                    "status":prev.get("status"),
                    "events":prev.get("event_counts"),
                    "resource_summary":prev.get("resource_summary"),
                }
        result["audio_fix_runtime_verified"]=(
            after.get("binary_sha256")==result["new_binary_sha256"] and
            after.get("verified_binary_unchanged") is True and
            after.get("original_config_hashes_unchanged") is True and
            after.get("status")=="CPU_GPU_PROFILE_CAPTURED")
        result["status"]=("COMPLETE_PERFORMANCE_COMPARISON_NEEDS_AUDIO_LISTENING"
                          if result["audio_fix_runtime_verified"] else
                          "PERFORMANCE_VERIFICATION_INCOMPLETE")
        result["installed_after"]=check_installed()
        if result["installed_after"]!=result["installed_before"]:
            result["status"]="FAILED_INSTALLED_EMULATOR_CHANGED"
        return 0 if result["audio_fix_runtime_verified"] else 1
    except BaseException as error:
        result["status"]="FAILED"
        result["error"]=repr(error)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if MANIFEST.exists() and SNAP.joinpath("shadps4").is_file():
            result["old_binary_preserved"]=sha_file(SNAP/"shadps4")==BIN_OLD
        package(result)

if __name__=="__main__":
    raise SystemExit(main())
