#!/usr/bin/env python3
"""Verify general audio guest producer latency, retaining rendered GoW baseline.

A GENERAL emulator audio buffer/concurrency correction, not a game-specific
behavior, setting, timing hack or shader override. This runner modifies only
the four audio source/test files declared in its SHA manifest. It preserves the
previously rendering executable and installed ES-DE launcher, runs 5 real
concurrency tests and 76 existing GCN tests, then compares GPU/CPU metrics
under the SAME unchanged user settings and checked process-identity runner.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import traceback

ROOT=Path.home()/"projects/shadps4/GodOfWarRagnarok"
WORK=ROOT/"tests/main-proof-20261015-151529-3107006"
SRC=WORK/"source"
BUILD=WORK/"build"
GCN=WORK/"build-gcn-tests"
LOGS=Path.home()/"projects/shadps4/logs"
OUT=LOGS/("gow-general-audio-buffer-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
TOPIC="8674f61d61df0d2a5217b1b0d069d4fc3281b87b"
BASE="b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
STACK="966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe"
BIN_BEFORE="8fb9a3c478b9229dc478b68703cc303ac1520f7af7c3e42a1ac467910ea68ae6"
PREVIOUS_RENDERED="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
HISTORICAL_GOOD="7ee847328072f9d65772fc85ee19f93143cbaaafe69e381d70379479ddd81a30"
FILES={
 "src/core/libraries/audio/audioout.cpp":(
   "2cb12200709588bc587a22eb4a498c4e29821156",
   "cc12c074acc622b3fc28505bf9b7674626f19619"),
 "src/core/libraries/audio/audioout.h":(
   "0252daeac1374e8063753320409719d2ccd1bcbf",
   "012eda2974c9d194121148f81b4b3c22787b6f86"),
 "src/core/libraries/audio/audioout_sync.h":(
   "8efda58a938100999a248e7fcdbd7b7159ec5658",
   "abd223cb82c1c2f45fb3d25442517ecddb9c0dbc"),
 "tests/audio/test_audio_output_batch_sync.cpp":(
   "dbdd33073feb3139843a756464ef82f7a8bf9857",
   "233e4a85304a7a35b9e838be3619db58dfaadfec"),
}
TEST_FILE="tests/audio/test_audio_output_batch_sync.cpp"
PROFILE=ROOT/"scripts/gow-generic-performance-profile.py"
PROFILE_BLOB="99e9e14d1c0775a7d1c8b7c8a68e2b89d09360fd"
PROFILE_ORIGINAL_SHA="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
SNAPSHOT=WORK/"preserved-before-general-audio-buffer"
MANIFEST=WORK/".general-audio-buffer-proof.json"
INSTALLED=Path.home()/"Applications/shadps4/shadps4"
BASELINE_ARCHIVE=LOGS/"gow-generic-performance-20261015-194818-2378434.tar.gz"

def say(message):
    print("GOW_AUDIO_BUFFER "+str(message),flush=True)

def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()

def sha_file(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()

def git_blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def run(command, *, cwd=None, logfile=None, timeout=120, check=True):
    args=[str(x) for x in command]
    if args[0]=="git":
        args=["git","-c","http.version=HTTP/1.1",*args[1:]]
    if logfile:
        p=Path(logfile);p.parent.mkdir(parents=True,exist_ok=True)
        with p.open("wb") as stream:
            result=subprocess.run(args,cwd=cwd,stdin=subprocess.DEVNULL,
                                  stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(f"rc={result.returncode}: {args[:6]}\n"+
                               p.read_text(errors="replace")[-2500:])
        return result.returncode,""
    result=subprocess.run(args,cwd=cwd,stdin=subprocess.DEVNULL,text=True,
                          capture_output=True,timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"rc={result.returncode}: {args[:6]}\n"+
                           (result.stdout+result.stderr)[-2500:])
    return result.returncode,result.stdout

def write_json(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=p.parent,prefix=".audio-proof-")
    try:
        with os.fdopen(fd,"w") as stream:
            json.dump(value,stream,sort_keys=True,indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp,p)
    finally:
        Path(tmp).unlink(missing_ok=True)

def safe_atomic_write(path,raw):
    path=Path(path)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("missing/unsafe source file: "+str(path))
    mode=stat.S_IMODE(path.stat().st_mode)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=".audio-verified-")
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,"wb") as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:
        Path(tmp).unlink(missing_ok=True)

def topic_bytes(rel,after_blob):
    url=f"https://raw.githubusercontent.com/Chreece/shadPS4/{TOPIC}/{rel}"
    fd,temp=tempfile.mkstemp(dir=OUT,prefix=".pinned-")
    os.close(fd)
    try:
        run(["curl","-4fsSL","--retry","3","--connect-timeout","20",
             "--max-time","120",url,"-o",temp],timeout=360)
        value=Path(temp).read_bytes()
        if git_blob(value)!=after_blob:
            raise RuntimeError("published generic audio source/test changed: "+rel)
        if any(x in value for x in
               (b"SHADPS4_GOW_",b"GOW_SRT_",b"CUSA34384",b"0xe83356c8")):
            raise RuntimeError("game-specific code unexpectedly present: "+rel)
        return value
    finally:
        Path(temp).unlink(missing_ok=True)

def check_preflight():
    if SRC.is_symlink() or not (SRC/".git").is_dir():
        raise RuntimeError("preserved upstream+general source is absent")
    ancestry=run(["git","rev-list","--parents","-n","1","HEAD"],cwd=SRC)[1].split()
    if len(ancestry)!=3 or ancestry[1:]!=[BASE,STACK]:
        raise RuntimeError("source is not our proven upstream-main+generic stack")
    if run(["git","ls-files","-u"],cwd=SRC)[1].strip():
        raise RuntimeError("working tree has unresolved conflicts")
    binary=BUILD/"shadps4"
    if not binary.is_file() or binary.is_symlink():
        raise RuntimeError("last rendering executable is missing")
    if not MANIFEST.exists() and sha_file(binary)!=BIN_BEFORE:
        raise RuntimeError("rendering executable differs from last live benchmark")
    previous=WORK/"preserved-before-general-audio-lock"/"shadps4"
    old_good=ROOT/"tests/gow91-build-20261015-125214-1308309/shadps4"
    if (not previous.is_file() or sha_file(previous)!=PREVIOUS_RENDERED or
        not old_good.is_file() or sha_file(old_good)!=HISTORICAL_GOOD):
        raise RuntimeError("earlier successful rendering checkpoints not intact")
    for rel in FILES:
        p=SRC/rel
        if not p.is_file() or p.is_symlink():
            raise RuntimeError("missing or unsafe source: "+rel)
    if not GCN.is_dir():
        raise RuntimeError("independent GCN tests missing")
    if MANIFEST.is_symlink():
        raise RuntimeError("unsafe proof manifest")
    if subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                      stdout=subprocess.DEVNULL).returncode==0:
        raise RuntimeError("another shadPS4 session is running; not terminating it")

def patch():
    planned={rel:topic_bytes(rel,after) for rel,(_,after) in FILES.items()}
    base_record={
       "topic":TOPIC,"upstream":BASE,"general_stack":STACK,
       "baseline_binary_sha256":BIN_BEFORE,
       "files":{rel:{"before_blob":before,"after_blob":after}
                for rel,(before,after) in FILES.items()},
       "general_audio_fix":True,"emulator_game_specific_changes":False,
       "dma_and_readbacks_unchanged":True
    }
    if MANIFEST.exists():
        old=json.loads(MANIFEST.read_text())
        for k in base_record:
            if old.get(k)!=base_record[k]:
                raise RuntimeError("previous audio proof manifest mismatch: "+k)
        for rel,raw in planned.items():
            if (SRC/rel).read_bytes()!=raw:
                raise RuntimeError("applied generic audio source unexpectedly changed: "+rel)
        if sha_file(SNAPSHOT/"shadps4")!=BIN_BEFORE:
            raise RuntimeError("original executable checkpoint changed")
        say("SOURCE=retained generic audio buffer fix source verified")
        return base_record

    for rel,(before,_) in FILES.items():
        actual=git_blob((SRC/rel).read_bytes())
        if actual!=before:
            raise RuntimeError(f"previous audio lock fix unexpectedly changed {rel} ({actual})")
    SNAPSHOT.mkdir(parents=True,exist_ok=False)
    shutil.copy2(BUILD/"shadps4",SNAPSHOT/"shadps4")
    if sha_file(SNAPSHOT/"shadps4")!=BIN_BEFORE:
        raise RuntimeError("failed to preserve previous rendering binary")
    for rel in FILES:
        target=SNAPSHOT/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(SRC/rel,target)
    try:
        for rel,content in planned.items():
            safe_atomic_write(SRC/rel,content)
        run(["git","diff","--check"],cwd=SRC,timeout=90)
        write_json(MANIFEST,base_record)
        say("SOURCE=guest-facing audio mutex released before backend playback")
    except BaseException:
        for rel in FILES:
            shutil.copy2(SNAPSHOT/rel,SRC/rel)
        raise
    return base_record

def test_concurrency():
    compiler=shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        raise RuntimeError("no C++ compiler available")
    binary=OUT/"audio-concurrency-tests"
    say("TEST=real audio source 5 concurrency regressions")
    run([compiler,"-std=c++20","-O2","-pthread","-I",SRC/"src",
         SRC/TEST_FILE,"-o",binary],logfile=OUT/"audio-test-build.log",timeout=240)
    run([binary],logfile=OUT/"audio-test-run.log",timeout=35)
    if "AUDIO_MULTI_PORT_SYNC=PASS cases=5" not in (OUT/"audio-test-run.log").read_text():
        raise RuntimeError("audio concurrency suite did not pass all 5 tests")
    say("AUDIO_TEST=5/5 PASS")

def test_gcn():
    say("TEST=retain all 76 existing GCN regressions")
    run(["cmake","--build",GCN,"--target","shadps4_gcn_test","--parallel","4"],
        logfile=OUT/"gcn-build.log",timeout=7200)
    binary=GCN/"tests/shadps4_gcn_test"
    if not binary.is_file() or binary.is_symlink():
        raise RuntimeError("original GCN binary missing")
    status,_=run([binary,"--gtest_output=json:"+str(OUT/"gcn.json")],
                 logfile=OUT/"gcn-test.log",timeout=1800,check=False)
    if status or not (OUT/"gcn.json").is_file():
        raise RuntimeError("GCN suite failed")
    data=json.loads((OUT/"gcn.json").read_text())
    if int(data.get("tests",0))!=76 or int(data.get("failures",1))!=0:
        raise RuntimeError("76 GCN tests not all passing")
    say("GCN_TEST=76/76 PASS")

def get_installed():
    if not INSTALLED.is_file():
        return None
    if INSTALLED.is_symlink():
        return {"symlink":True}
    return sha_file(INSTALLED)

def read_profile_result(filename):
    p=Path(filename)
    if not p.is_file() or p.is_symlink():
        raise RuntimeError("performance archive not found: "+str(p))
    with tarfile.open(p,"r:gz") as tar:
        metas=[entry for entry in tar.getmembers()
               if entry.isfile() and entry.name.endswith("/result.json")]
        if len(metas)!=1 or metas[0].size>120000:
            raise RuntimeError("unexpected performance metadata")
        f=tar.extractfile(metas[0])
        return json.load(f)

def run_profile(new_sha):
    if not PROFILE.is_file() or PROFILE.is_symlink() or git_blob(PROFILE.read_bytes())!=PROFILE_BLOB:
        raise RuntimeError("previous original-config performance profiler source changed")
    source=PROFILE.read_text()
    if source.count(PROFILE_ORIGINAL_SHA)!=1:
        raise RuntimeError("profile must be pinned to exactly one original emulator hash")
    new_source=source.replace(PROFILE_ORIGINAL_SHA,new_sha,1)
    stage=WORK/"helpers/performance-after-audio-buffer.py"
    stage.parent.mkdir(parents=True,exist_ok=True)
    stage.write_text(new_source)
    run([sys.executable,"-m","py_compile",stage],timeout=90)
    say("PERFORMANCE=capture same unmodified game configuration")
    rc,_=run([sys.executable,stage],logfile=OUT/"profile-runner.log",timeout=430,check=False)
    archives=[]
    for line in (OUT/"profile-runner.log").read_text(errors="replace").splitlines():
        if "UPLOAD=" not in line:continue
        raw=line.split("UPLOAD=",1)[1].strip()
        candidate=Path(raw)
        if (candidate.is_file() and not candidate.is_symlink() and
            candidate.parent.resolve()==LOGS.resolve() and
            candidate.name.startswith("gow-generic-performance-")):
            archives.append(candidate)
    if not archives:
        raise RuntimeError("profile failed to produce archive rc="+str(rc)+
                           " "+(OUT/"profile-runner.log").read_text(errors="replace")[-1000:])
    archive=archives[-1]
    value=read_profile_result(archive)
    if (value.get("binary_sha256")!=new_sha or
        value.get("status")!="CPU_GPU_PROFILE_CAPTURED" or
        value.get("verified_binary_unchanged") is not True or
        value.get("original_config_hashes_unchanged") is not True):
        raise RuntimeError("new performance run did not preserve executable and settings")
    return archive,value

def emit_archive(result):
    write_json(OUT/"result.json",result)
    dest=LOGS/(OUT.name+".tar.gz")
    with tarfile.open(dest,"w:gz",compresslevel=3) as archive:
        for f in sorted(OUT.rglob("*")):
            if f.is_file() and not f.is_symlink() and f.name!="audio-concurrency-tests":
                archive.add(f,arcname=OUT.name+"/"+str(f.relative_to(OUT)))
        reference=result.get("performance_archive")
        if reference:
            f=Path(reference)
            if f.is_file() and not f.is_symlink() and f.parent.resolve()==LOGS.resolve():
                archive.add(f,arcname=OUT.name+"/performance-capture.tar.gz")
    say("UPLOAD="+str(dest))

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={
      "status":"STARTED","phase":"PREFLIGHT","general_audio_topic":TOPIC,
      "upstream_main":BASE,"stack":STACK,
      "previous_rendering_binary":BIN_BEFORE,
      "installed_emulator_unchanged":True,
      "audio_buffer_general_not_game_specific":True,
      "dma_setting_toggled":False,"readbacks_modified":False,
      "graphics_performance_improvement_unproven":True
    }
    original_install=get_installed()
    try:
        check_preflight()
        result["source_proof"]=patch()
        result["phase"]="TESTS"
        test_concurrency()
        test_gcn()
        result["audio_concurrency_tests_passed"]=5
        result["gcn_tests_passed"]=76
        result["phase"]="INCREMENTAL_BUILD"
        say("BUILD=retain old executable, relink only changed general audio code")
        run(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
            logfile=OUT/"build.log",timeout=7200)
        new_sha=sha_file(BUILD/"shadps4")
        result["new_binary_sha256"]=new_sha
        if new_sha==BIN_BEFORE or sha_file(SNAPSHOT/"shadps4")!=BIN_BEFORE:
            raise RuntimeError("new executable did not change or old baseline was lost")
        run(["ldd",BUILD/"shadps4"],logfile=OUT/"ldd.log",timeout=90)
        if "not found" in (OUT/"ldd.log").read_text(errors="replace"):
            raise RuntimeError("new executable has missing libraries")
        result["phase"]="PERFORMANCE_AB"
        archive,perf=run_profile(new_sha)
        result["performance_archive"]=str(archive)
        result["after"]={
            k:perf.get(k) for k in
            ("status","guest_exit","event_counts","resource_summary",
             "original_config_hashes_unchanged","image_count")
        }
        if BASELINE_ARCHIVE.exists() and not BASELINE_ARCHIVE.is_symlink():
            prev=read_profile_result(BASELINE_ARCHIVE)
            if prev.get("binary_sha256")==BIN_BEFORE:
                result["before"]={k:prev.get(k) for k in
                  ("status","guest_exit","event_counts","resource_summary","image_count")}
        result["status"]="TESTS_PASS_PERFORMANCE_CAPTURED_AUDIO_LISTENING_UNVERIFIED"
        return 0
    except BaseException as exc:
        result["status"]="FAILED"
        result["error"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if SNAPSHOT.joinpath("shadps4").is_file():
            result["old_binary_preserved"]=sha_file(SNAPSHOT/"shadps4")==BIN_BEFORE
        result["installed_after"]=get_installed()
        if result["installed_after"]!=original_install:
            result["status"]="INSTALLED_EMULATOR_UNEXPECTEDLY_CHANGED"
        emit_archive(result)

if __name__=="__main__":
    raise SystemExit(main())
