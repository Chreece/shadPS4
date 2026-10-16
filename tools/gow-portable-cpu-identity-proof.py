#!/usr/bin/env python3
"""Integrate shadPS4's portable guest-CPU identity policy into a preserved build.

This is a general x86-64 emulator refactor, NOT game-specific native-mode
bypass. A separate reusable guest CPUID implementation now owns feature masks,
topology and APIC identities; the existing Linux handler supplies host inputs.
All eight guest CPU identities and 22 CPUID leaves are regression-tested across
GCC/Clang with 45,056 deterministic input tuples.

No game IDs or shader hashes are present in the patch; no GPU, audio, DMA,
readbacks, driver, CPU-ID mode, installed ES-DE binary, or SSH changes.
Preserves original running binary and source before touching anything. Runs
one original-settings gameplay capture to catch functional regression. A
normal game launch may create autosaves.
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
GCN=WORK/"build-gcn-tests"
GAME=BUILD/"shadps4"
OLD_GAME_SHA="a4d93cf19f672cf8b6dc881f11564c04e8a6af3cdffeee38e8cd669286f81932"
RUNTIME_CLIENT=BUILD/"cpu-id-runtime/libshadps4_cpu_id.so"
CPU_CLIENT_SHA="3b56a25afb0a1faf8d37704d166cc96c30fedc26332da7df6287956ee5007f50"
PROFILER=ROOT/"scripts/gow-generic-performance-profile.py"
PROFILER_BLOB="99e9e14d1c0775a7d1c8b7c8a68e2b89d09360fd"
PROFILER_OLD_SHA="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
REPO_COMMIT="f1291aa69c1ae8e13717d8a9449ecd6c99e94fba"
SOURCE="src/core/cpu_id.cpp"
HEADER="src/core/cpu_id_guest.h"
TEST="tests/cpu_id/test_guest_cpuid.cpp"
OLD_SOURCE_BLOB="fcdbac396cb109ba457122a2f5fa7b648af4392d"
TOPIC_BLOBS={
    SOURCE:"d126e8b34cd31cbc5fa244dc6ed880886e8b8c3c",
    HEADER:"0e711f113dbc7c8edad4434968e11180dc88859f",
    TEST:"876de33e20bbdb9c6f901abc2dee3166f81c95f0",
}
PRESERVED=WORK/"preserved-before-portable-guest-cpuid"
LOG_ROOT=HOME/"projects/shadps4/logs"
OUT=LOG_ROOT/("gow-portable-cpuid-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+
              "-"+str(os.getpid()))
INSTALLED=HOME/"Applications/shadps4/shadps4"
CONFIG_FILES=[
    HOME/".local/share/shadPS4/config.json",
    HOME/".local/share/shadPS4/custom_configs/CUSA34384.json",
]

def note(msg):
    print("PORTABLE_CPU "+str(msg),flush=True)

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""):
            h.update(b)
    return h.hexdigest()

def blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def call(cmd,*,cwd=None,timeout=180,log=None,check=True,env=None):
    args=[str(x) for x in cmd]
    if args[0]=="git":
        args=["git","-c","http.version=HTTP/1.1",*args[1:]]
    if log is None:
        p=subprocess.run(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,
                         capture_output=True,text=True,timeout=timeout)
        if check and p.returncode:
            raise RuntimeError("rc="+str(p.returncode)+" "+
                (p.stdout+p.stderr)[-2600:])
        return p.returncode,p.stdout
    with Path(log).open("wb") as f:
        p=subprocess.run(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,
                         stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError("command="+str(args[:5])+" rc="+str(p.returncode)+" "+
                           Path(log).read_text(errors="replace")[-3600:])
    return p.returncode,""

def write_json(path,value):
    Path(path).write_text(json.dumps(value,indent=2,sort_keys=True)+"\n")

def installed_identity():
    if not INSTALLED.exists():return None
    if INSTALLED.is_symlink():return {"symlink":str(INSTALLED.resolve())}
    if INSTALLED.is_file():return {"sha256":sha(INSTALLED)}
    return {"nonfile":True}

def config_hashes():
    data={}
    for path in CONFIG_FILES:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError("unreadable user config: "+str(path))
        data[str(path)]=sha(path) if path.is_file() else None
    return data

def check_no_active_emulator():
    if call(["pgrep","-u",str(os.getuid()),"-x","shadps4"],check=False)[0]==0:
        raise RuntimeError("shadPS4 is running: close game before rebuilding")
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():continue
        try:
            if proc.stat().st_uid==os.getuid() and str(GAME) in (
                    proc/"maps").read_text(errors="replace"):
                raise RuntimeError("DynamoRIO-translated shadPS4 is running")
        except OSError:
            pass

def preflight():
    if SRC.is_symlink() or not (SRC/".git").is_dir():
        raise RuntimeError("local original upstream/general source not present")
    if GAME.is_symlink() or not GAME.is_file() or sha(GAME)!=OLD_GAME_SHA:
        raise RuntimeError("latest rendering executable is not SHA-pinned")
    if RUNTIME_CLIENT.is_symlink() or not RUNTIME_CLIENT.is_file() or sha(RUNTIME_CLIENT)!=CPU_CLIENT_SHA:
        raise RuntimeError("restored CPU-ID client is missing or not the safe baseline")
    if not PROFILER.is_file() or PROFILER.is_symlink() or blob(PROFILER.read_bytes())!=PROFILER_BLOB:
        raise RuntimeError("previous original-configuration profiler missing")
    if PRESERVED.exists():
        raise RuntimeError("a portable-CPUID checkpoint already exists; refuse to overwrite")
    for rel in TOPIC_BLOBS:
        path=SRC/rel
        if path.is_symlink():
            raise RuntimeError("unsafe symlink in source "+rel)
    if not (SRC/SOURCE).is_file() or blob((SRC/SOURCE).read_bytes())!=OLD_SOURCE_BLOB:
        raise RuntimeError("guest CPUID handler does not match audited source")
    for rel in (HEADER,TEST):
        if (SRC/rel).exists():
            raise RuntimeError("new CPU-ID portable file already exists without a checkpoint: "+rel)
    check_no_active_emulator()

def download(rel):
    uri=f"https://raw.githubusercontent.com/Chreece/shadPS4/{REPO_COMMIT}/{rel}"
    fd,tmp=tempfile.mkstemp(prefix=".cpu-policy-",dir=OUT)
    os.close(fd)
    try:
        call(["curl","-4fsSL","--retry","3","--connect-timeout","25",
              "--max-time","120",uri,"-o",tmp],timeout=360)
        content=Path(tmp).read_bytes()
        if blob(content)!=TOPIC_BLOBS[rel]:
            raise RuntimeError("published source blob mismatch: "+rel)
        if b"CUSA34384" in content or b"SHADPS4_GOW_" in content:
            raise RuntimeError("unexpected game-specific branch in general CPUID source")
        return content
    finally:
        Path(tmp).unlink(missing_ok=True)

def snapshot():
    PRESERVED.mkdir(parents=True,exist_ok=False)
    shutil.copy2(GAME,PRESERVED/"shadps4")
    shutil.copy2(RUNTIME_CLIENT,PRESERVED/"libshadps4_cpu_id.so")
    shutil.copy2(SRC/SOURCE,PRESERVED/"cpu_id.cpp")
    if (sha(PRESERVED/"shadps4")!=OLD_GAME_SHA or
        sha(PRESERVED/"libshadps4_cpu_id.so")!=CPU_CLIENT_SHA):
        raise RuntimeError("rollback snapshot did not match SHA-pinned baseline")
    note("CHECKPOINT=original executable, CPU-ID implementation, runtime client preserved")

def revert():
    # Only undo the exact three source paths this proof may modify.
    if not (PRESERVED/"cpu_id.cpp").is_file():
        return False
    shutil.copy2(PRESERVED/"cpu_id.cpp",SRC/SOURCE)
    for rel in (HEADER,TEST):
        path=SRC/rel
        if path.is_file() and not path.is_symlink() and blob(path.read_bytes())==TOPIC_BLOBS[rel]:
            path.unlink()
    if (PRESERVED/"shadps4").is_file():
        shutil.copy2(PRESERVED/"shadps4",GAME)
    if (PRESERVED/"libshadps4_cpu_id.so").is_file():
        shutil.copy2(PRESERVED/"libshadps4_cpu_id.so",RUNTIME_CLIENT)
    return (blob((SRC/SOURCE).read_bytes())==OLD_SOURCE_BLOB and
            sha(GAME)==OLD_GAME_SHA and sha(RUNTIME_CLIENT)==CPU_CLIENT_SHA)

def apply(raw):
    snapshot()
    try:
        for rel,data in raw.items():
            target=SRC/rel
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
        call(["git","diff","--check","--",SOURCE],cwd=SRC,timeout=80)
    except BaseException:
        revert()
        raise
    note("SOURCE=portable CPU feature/topology logic applied to source checkout")

def portable_tests():
    available=[c for c in ("g++","clang++") if shutil.which(c)]
    if not available:
        raise RuntimeError("neither GCC nor Clang available for portable guest CPU tests")
    for cc in available:
        exe=OUT/("test-"+cc)
        log=OUT/("compile-"+cc+".log")
        call([cc,"-std=c++23","-O2","-Wall","-Wextra","-Werror",
              "-pedantic","-I",SRC/"src",SRC/TEST,"-o",exe],
             log=log,timeout=240)
        resultlog=OUT/("result-"+cc+".log")
        call([exe],log=resultlog,timeout=60)
        if "PORTABLE_GUEST_CPUID=PASS cases=45056" not in resultlog.read_text():
            raise RuntimeError("guest CPU identity mismatch under "+cc)
        note("GUEST_CPUID_"+cc+"=PASS 45056 original-vs-portable cases")

def unchanged_regressions():
    if not GCN.is_dir():
        raise RuntimeError("original 79-case GCN test build unavailable")
    call(["cmake","--build",GCN,"--target","shadps4_gcn_test","--parallel","4"],
         log=OUT/"gcn-build.log",timeout=7200)
    gcn=GCN/"tests/shadps4_gcn_test"
    if not gcn.is_file() or gcn.is_symlink():
        raise RuntimeError("previous GCN test binary missing")
    code,_=call([gcn,"--gtest_output=json:"+str(OUT/"gcn-results.json")],
                log=OUT/"gcn-run.log",timeout=1800,check=False)
    results=json.loads((OUT/"gcn-results.json").read_text()) if (
        OUT/"gcn-results.json").is_file() else {}
    if code or results.get("tests")!=79 or results.get("failures")!=0:
        raise RuntimeError("previous 79-case GCN regression suite failed")
    note("GCN=PASS 79/79")
    src=SRC/"tests/audio/test_audio_output_batch_sync.cpp"
    if not src.is_file() or "AUDIO_MULTI_PORT_SYNC=PASS cases=5" not in src.read_text():
        raise RuntimeError("previous audio synchronization regression source unavailable")
    cc=shutil.which("g++") or shutil.which("clang++")
    exe=OUT/"audio-regression"
    call([cc,"-std=c++20","-O2","-pthread","-I",SRC/"src",src,"-o",exe],
         log=OUT/"audio-build.log",timeout=200)
    call([exe],log=OUT/"audio-run.log",timeout=60)
    if "AUDIO_MULTI_PORT_SYNC=PASS cases=5" not in (
        OUT/"audio-run.log").read_text():
        raise RuntimeError("audio 5-case regression failed")
    note("AUDIO=PASS 5/5")

def recompile():
    note("BUILD=incremental emulator compilation (same CPU-ID mode and GPU settings)")
    call(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
         log=OUT/"emulator-build.log",timeout=7200)
    if not GAME.is_file() or GAME.is_symlink():
        raise RuntimeError("incremental build did not produce a regular emulator executable")
    if sha(RUNTIME_CLIENT)!=CPU_CLIENT_SHA:
        raise RuntimeError("bundled CPU identity runtime unexpectedly changed")
    if blob((SRC/SOURCE).read_bytes())!=TOPIC_BLOBS[SOURCE]:
        raise RuntimeError("portable guest identity source mutated during build")
    new=sha(GAME)
    note("BUILT_BINARY_SHA="+new)
    return new

def gameplay(candidate):
    text=PROFILER.read_text()
    if text.count(PROFILER_OLD_SHA)!=1:
        raise RuntimeError("original profiler changed SHA validation anchor")
    profile=OUT/"profile-with-portable-cpuid.py"
    profile.write_text(text.replace(PROFILER_OLD_SHA,candidate,1))
    call([sys.executable,"-m","py_compile",profile],
         log=OUT/"profiler-syntax.log",timeout=45)
    original_config=config_hashes()
    environment=os.environ.copy()
    environment["SHADPS4_FRAME_METRICS_PREFIX"]=str(OUT/"fps")
    environment.pop("SHADPS4_CPU_TIMING_CSV",None)
    note("GAMEPLAY=unchanged-config capture with native FPS measurement and preserved graphics")
    rc,_=call([sys.executable,profile],env=environment,
              log=OUT/"gameplay-runner.log",timeout=470,check=False)
    options=[]
    for row in (OUT/"gameplay-runner.log").read_text(errors="replace").splitlines():
        if "UPLOAD=" not in row:continue
        f=Path(row.split("UPLOAD=",1)[1].strip())
        if f.is_file() and not f.is_symlink() and f.parent.resolve()==LOG_ROOT.resolve() and f.name.startswith("gow-generic-performance-"):
            options.append(f)
    if not options:
        raise RuntimeError("no gameplay performance archive: "+(
            OUT/"gameplay-runner.log").read_text(errors="replace")[-1700:])
    archive=options[-1]
    with tarfile.open(archive,"r:gz") as t:
        meta=[m for m in t if m.isfile() and m.name.endswith("/result.json")]
        if len(meta)!=1 or meta[0].size>200000:
            raise RuntimeError("invalid gameplay result archive")
        result=json.load(t.extractfile(meta[0]))
    if (rc or result.get("status")!="CPU_GPU_PROFILE_CAPTURED" or
        result.get("verified_binary_unchanged") is not True or
        result.get("original_config_hashes_unchanged") is not True or
        result.get("binary_sha256")!=candidate or
        result.get("event_counts",{}).get("vulkan_device_lost")!=0 or
        result.get("image_count",0)<6):
        raise RuntimeError("graphics/gameplay regression in new portable CPU identity build")
    if config_hashes()!=original_config:
        raise RuntimeError("user config changed during gameplay test")
    guest=Path(str(OUT/"fps")+".guest-flips.csv")
    host=Path(str(OUT/"fps")+".swapchain-presents.csv")
    if not guest.is_file() or not host.is_file():
        raise RuntimeError("FPS telemetry missing; cannot validate comparable rendering")
    host_times=[int(x) for x in host.read_text().splitlines()[1:] if x.strip()]
    if not host_times:
        raise RuntimeError("game did not present any graphics")
    start,end=host_times[0]+70_000_000,host_times[0]+130_000_000
    game_times=[int(x) for x in guest.read_text().splitlines()[1:] if x.strip()]
    fps=len([x for x in game_times if start<=x<=end])/60
    if fps<4:
        raise RuntimeError("candidate dropped below 4 FPS; preserving prior rendered baseline")
    return {
        "candidate_guest_fps_steady_60s":round(fps,3),
        "previous_measured_FPS_about":7.0,
        "guest_frame_count":len(game_times),
        "screenshot_count":result.get("image_count"),
        "cpu_gpu":result.get("resource_summary"),
        "performance_archive":str(archive),
        "game_exit":result.get("guest_exit"),
        "device_lost":0,
    }

def package(result):
    write_json(OUT/"result.json",result)
    archive=LOG_ROOT/(OUT.name+".tar.gz")
    with tarfile.open(archive,"w:gz",compresslevel=3) as t:
        for f in sorted(OUT.iterdir()):
            if f.is_file() and not f.is_symlink() and f.name not in (
                    "test-g++","test-clang++","audio-regression"):
                t.add(f,arcname=OUT.name+"/"+f.name)
        gameplay_file=result.get("gameplay",{}).get("performance_archive")
        if gameplay_file:
            p=Path(gameplay_file)
            if p.is_file() and not p.is_symlink() and p.parent.resolve()==LOG_ROOT.resolve():
                t.add(p,arcname=OUT.name+"/rendering-capture.tar.gz")
    note("UPLOAD="+str(archive))

def main():
    LOG_ROOT.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={"status":"STARTED","topic_sha":REPO_COMMIT,
            "scope":"OS-independent reusable guest CPU policy",
            "game_or_GPU_specific_workarounds":False,
            "installed_emulator_modified":False,
            "CPU_ID_execution_mode_changed":False,
            "emulator_or_game_settings_changed":False,
            "SSH_session_modified":False,
            "old_emulator_sha256":OLD_GAME_SHA}
    before=installed_identity()
    touched=False
    successful=False
    try:
        preflight()
        raw={rel:download(rel) for rel in TOPIC_BLOBS}
        touched=True
        apply(raw)
        portable_tests()
        unchanged_regressions()
        candidate=recompile()
        result["candidate_sha256"]=candidate
        result["gameplay"]=gameplay(candidate)
        result["status"]="PORTABLE_CPUID_PARITY_AND_RENDERING_CAPTURED"
        result["performance_gain_claimed"]=False
        successful=True
        return 0
    except BaseException as error:
        result["status"]="FAILED"
        result["error"]=repr(error)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if touched and not successful:
            try:
                result["rolled_back_to_original_rendering_binary"]=revert()
            except BaseException as err:
                result["rollback_failed"]=repr(err)
        result["installed_emulator_unchanged"]=installed_identity()==before
        package(result)

if __name__=="__main__":
    raise SystemExit(main())
