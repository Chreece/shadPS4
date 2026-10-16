#!/usr/bin/env python3
"""Test native emulator-owned CPUID/RDTSCP/RDPID short traps.

This is NOT a per-game workaround or an installed emulator release. The exact
upstream+generic working source and binary are pinned. A full backup precedes
any patch. Build the native trap handler, preserve the original CPU-ID runtime,
then run genuine native execution plus auto-mode fallback with the same game
settings, native FPS collection, audio code and graphics stack. Results include
visual screenshots and original-config CPU/GPU performance.
On any failure restore the original binary, source, and CPU-ID runtime.
A regular gameplay run may autosave. The caller's SSH session remains open.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import traceback

HOME=Path.home()
PROJECT=HOME/"projects/shadps4/GodOfWarRagnarok"
WORK=PROJECT/"tests/main-proof-20261015-151529-3107006"
SRC=WORK/"source"
BUILD=WORK/"build"
GCN=WORK/"build-gcn-tests"
BIN=BUILD/"shadps4"
BIN_SHA="a4d93cf19f672cf8b6dc881f11564c04e8a6af3cdffeee38e8cd669286f81932"
RUNTIME=BUILD/"cpu-id-runtime"
CLIENT=RUNTIME/"libshadps4_cpu_id.so"
CLIENT_SHA="3b56a25afb0a1faf8d37704d166cc96c30fedc26332da7df6287956ee5007f50"
TOPIC="d373f2a744caf66a83e4fa31aaeff261a9f76d58"
UPSTREAM="b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
GENERIC="966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe"
PROFILE=PROJECT/"scripts/gow-generic-performance-profile.py"
PROFILE_BLOB="99e9e14d1c0775a7d1c8b7c8a68e2b89d09360fd"
PROFILE_OLD_SHA="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
ROLLBACK=WORK/"preserved-before-emulator-owned-cpuid"
INSTALL=HOME/"Applications/shadps4/shadps4"
LOGS=HOME/"projects/shadps4/logs"
OUT=LOGS/("gow-native-cpuid-owned-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
EDIT={
 "src/core/cpu_id.h":("fabf283f94be8e18af100c11cd160e5dccf493b4","f0b2c7194c4aafaa9c1674591c0c86c5bf367aca"),
 "src/core/cpu_id.cpp":("fcdbac396cb109ba457122a2f5fa7b648af4392d","afeaefd67d932f361deb9183d0e422a14fa41fdc"),
 "src/core/cpu_patches.cpp":("abe343b2bba45f17e6b546ddb0028d427053ba60","86e439498ad7e5299298a145d4ff1b7a65d7ad12"),
 "src/core/signals.cpp":("c2d465edf0cb074b4af2e8b8af96cc79f3fa1457","c10db42488f23672b5868a5118014e75f02210d1"),
}
NEW={
 "src/core/cpu_id_native_traps.h":"c8abc818c662714c9582424c1d459e39881bbd95",
 "tests/cpu_id/test_native_short_traps.cpp":"3382a6dd84e96a46c7149b369c8356ac0d37fc68",
}

def say(s):
    print("OWNED_CPU_ID "+str(s),flush=True)
def sha(p):
    h=hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""):h.update(b)
    return h.hexdigest()
def blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
def run(args,*,log=None,env=None,cwd=None,timeout=100,check=True):
    args=list(map(str,args))
    if args[0]=="git":args=["git","-c","http.version=HTTP/1.1",*args[1:]]
    if log:
        with Path(log).open("wb") as dest:
            p=subprocess.run(args,stdout=dest,stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,cwd=cwd,env=env,timeout=timeout)
        if check and p.returncode:
            raise RuntimeError("rc="+str(p.returncode)+" "+str(args[:5])+
                "\n"+Path(log).read_text(errors="replace")[-3200:])
        return p.returncode,""
    p=subprocess.run(args,capture_output=True,text=True,stdin=subprocess.DEVNULL,
                     cwd=cwd,env=env,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError("rc="+str(p.returncode)+" "+str(args[:5])+
                           "\n"+(p.stdout+p.stderr)[-2500:])
    return p.returncode,p.stdout
def save(path,data):
    Path(path).write_text(json.dumps(data,sort_keys=True,indent=2)+"\n")
def download(path,pin):
    fd,tmp=tempfile.mkstemp(prefix=".native-id-",dir=OUT)
    os.close(fd)
    try:
        url="https://raw.githubusercontent.com/Chreece/shadPS4/"+TOPIC+"/"+path
        run(["curl","-4fsSL","--retry","3","--connect-timeout","20",
             "--max-time","120",url,"-o",tmp],timeout=360)
        raw=Path(tmp).read_bytes()
        if not raw or len(raw)>1050000 or blob(raw)!=pin:
            raise RuntimeError("Source pin mismatch for "+path)
        if any(word in raw for word in (b"CUSA34384",b"SHADPS4_GOW_",b"RADV_DEBUG")):
            raise RuntimeError("Game-specific code in CPU fix: "+path)
        return raw
    finally:Path(tmp).unlink(missing_ok=True)
def config():
    files=[HOME/".local/share/shadPS4/config.json",
           HOME/".local/share/shadPS4/custom_configs/CUSA34384.json"]
    return {str(p):sha(p) if p.is_file() and not p.is_symlink() else None for p in files}
def installed():
    if INSTALL.is_symlink():return {"target":str(INSTALL.resolve())}
    if INSTALL.is_file():return {"sha":sha(INSTALL)}
    return None
def active():
    if run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],check=False)[0]==0:
        return True
    for p in Path("/proc").iterdir():
        if not p.name.isdecimal():continue
        try:
            if p.stat().st_uid==os.getuid() and \
                str(BIN) in (p/"maps").read_text(errors="replace"):
                return True
        except OSError:continue
    return False
def preflight():
    if SRC.is_symlink() or not (SRC/".git").is_dir():raise RuntimeError("Source checkout missing")
    lineage=run(["git","rev-list","--parents","-n","1","HEAD"],cwd=SRC)[1].split()
    if len(lineage)!=3 or lineage[1:]!=[UPSTREAM,GENERIC]:
        raise RuntimeError("Source no longer based on verified upstream and general patches")
    if run(["git","ls-files","-u"],cwd=SRC)[1].strip():raise RuntimeError("Unresolved git merge")
    if not BIN.is_file() or BIN.is_symlink() or sha(BIN)!=BIN_SHA:
        raise RuntimeError("Preserved rendering binary changed; no build applied")
    if CLIENT.is_symlink() or not CLIENT.is_file() or sha(CLIENT)!=CLIENT_SHA:
        raise RuntimeError("Previous faster CPU-ID runtime not restored")
    if PROFILE.is_symlink() or not PROFILE.is_file() or blob(PROFILE.read_bytes())!=PROFILE_BLOB:
        raise RuntimeError("Original-config profiler missing or changed")
    if active():raise RuntimeError("Close shadPS4 before updating the project test build")
    if ROLLBACK.exists():raise RuntimeError("Existing native patch checkpoint; will not overwrite")
    if not GCN.is_dir():raise RuntimeError("79-test GCN build missing")
    for path,(before,_) in EDIT.items():
        p=SRC/path
        if p.is_symlink() or not p.is_file() or blob(p.read_bytes())!=before:
            raise RuntimeError("Prior CPU source differs, refusing overwrite: "+path)
    for path in NEW:
        if (SRC/path).exists() or (SRC/path).is_symlink():
            raise RuntimeError("New patch path already exists: "+path)
def unit_test(data):
    root=OUT/"unit-source"
    for path in ("src/core/cpu_id.h","src/core/cpu_id_native_traps.h",
                 "tests/cpu_id/test_native_short_traps.cpp"):
        dest=root/path
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(data[path])
    cxx=shutil.which("g++") or shutil.which("clang++")
    if not cxx:raise RuntimeError("No native C++ compiler")
    executable=OUT/"native-trap-unit"
    say("TEST=real SIGILL+UD2 short-trap executable")
    run([cxx,"-std=c++23","-O2","-pthread",
         "-I",root/"src","-I",SRC/"src",
         root/"tests/cpu_id/test_native_short_traps.cpp",
         "-o",executable],log=OUT/"native-test-build.log",timeout=220)
    run([executable],log=OUT/"native-test-run.log",timeout=30)
    if "NATIVE_CPU_ID_SHORT_TRAPS=PASS cases=8" not in (
            OUT/"native-test-run.log").read_text():
        raise RuntimeError("Native instruction trap tests failed")
def regressions():
    say("TEST=79 GCN + 5 audio concurrency regressions")
    run(["cmake","--build",GCN,"--target","shadps4_gcn_test","--parallel","4"],
        log=OUT/"gcn-build.log",timeout=7200)
    test=GCN/"tests/shadps4_gcn_test"
    if not test.is_file() or test.is_symlink():raise RuntimeError("Missing 79-test binary")
    rc,_=run([test,"--gtest_output=json:"+str(OUT/"gcn.json")],
             log=OUT/"gcn-run.log",timeout=1800,check=False)
    g=json.loads((OUT/"gcn.json").read_text()) if (OUT/"gcn.json").is_file() else {}
    if rc or g.get("tests")!=79 or g.get("failures")!=0:
        raise RuntimeError("79 GCN regressions did not pass")
    audio=SRC/"tests/audio/test_audio_output_batch_sync.cpp"
    if not audio.is_file() or "AUDIO_MULTI_PORT_SYNC=PASS cases=5" not in audio.read_text():
        raise RuntimeError("Existing five audio regression cases missing")
    cxx=shutil.which("g++") or shutil.which("clang++")
    bin=OUT/"audio-unit"
    run([cxx,"-std=c++20","-O2","-pthread","-I",SRC/"src",audio,"-o",bin],
        log=OUT/"audio-build.log",timeout=240)
    run([bin],log=OUT/"audio-unit.log",timeout=60)
    if "AUDIO_MULTI_PORT_SYNC=PASS cases=5" not in (OUT/"audio-unit.log").read_text():
        raise RuntimeError("Five audio regressions did not pass")
def preserve():
    ROLLBACK.mkdir(parents=True,exist_ok=False)
    shutil.copy2(BIN,ROLLBACK/"shadps4")
    if sha(ROLLBACK/"shadps4")!=BIN_SHA:raise RuntimeError("Rollback binary checksum failed")
    for path in EDIT:
        dst=ROLLBACK/path
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(SRC/path,dst)
    shutil.copytree(RUNTIME,ROLLBACK/"cpu-id-runtime")
    if sha(ROLLBACK/"cpu-id-runtime/libshadps4_cpu_id.so")!=CLIENT_SHA:
        raise RuntimeError("Rollback CPU-ID runtime checksum failed")
    save(ROLLBACK/"manifest.json",{"topic":TOPIC,"binary_sha256":BIN_SHA,
                                  "client_sha256":CLIENT_SHA,
                                  "original_source_blobs":{p:v[0] for p,v in EDIT.items()}})
    say("PRESERVED=old rendering executable, source and complete CPU-ID runtime")
def revert():
    for path in EDIT:
        (SRC/path).write_bytes((ROLLBACK/path).read_bytes())
    for path in NEW:(SRC/path).unlink(missing_ok=True)
    shutil.copy2(ROLLBACK/"shadps4",BIN)
    for p in (ROLLBACK/"cpu-id-runtime").rglob("*"):
        if p.is_file():
            dest=RUNTIME/p.relative_to(ROLLBACK/"cpu-id-runtime")
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(p,dest)
    return sha(BIN)==BIN_SHA and sha(CLIENT)==CLIENT_SHA and all(
        blob((SRC/p).read_bytes())==meta[0] for p,meta in EDIT.items())
def build(data):
    for path,raw in data.items():
        f=SRC/path
        f.parent.mkdir(parents=True,exist_ok=True)
        f.write_bytes(raw)
    run(["git","diff","--check"],cwd=SRC,timeout=60)
    say("BUILD=emulator native CPU trap handler, original generic GPU/audio unchanged")
    run(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
        log=OUT/"emulator-build.log",timeout=7200)
    candidate=sha(BIN)
    if candidate==BIN_SHA or sha(ROLLBACK/"shadps4")!=BIN_SHA:
        raise RuntimeError("Emulator was not relinked or previous executable was damaged")
    if sha(CLIENT)!=CLIENT_SHA:
        raise RuntimeError("Original DynamoRIO client changed unexpectedly")
    run(["ldd",BIN],log=OUT/"ldd.log",timeout=60)
    if "not found" in (OUT/"ldd.log").read_text(errors="replace"):
        raise RuntimeError("New emulator has missing host shared libraries")
    return candidate
def make_profile(candidate,mode):
    text=PROFILE.read_text()
    if text.count(PROFILE_OLD_SHA)!=1:
        raise RuntimeError("Original-settings binary SHA anchor changed")
    text=text.replace(PROFILE_OLD_SHA,candidate,1)
    if mode=="native":
        needle="    return script\n\ndef optional_read(path):"
        anchor='str(binary), "--cpu-id-mode", "auto", "--game", SERIAL, "--fullscreen", "true"'
        if text.count(needle)!=1:
            raise RuntimeError("Cannot safely modify original-config helper")
        replace=(
            '    raw=script.read_text()\n'
            '    anchor='+repr(anchor)+'\n'
            '    if raw.count(anchor)!=1:raise RuntimeError("Native launch anchor changed")\n'
            '    native=OUT/"native-guest-cpu-launcher.py"\n'
            '    native.write_text(raw.replace(anchor,anchor.replace(\'"auto"\',\'"native"\'),1))\n'
            '    p=subprocess.run([sys.executable,native,"--self-test"],\n'
            '                     capture_output=True,text=True,timeout=80)\n'
            '    if p.returncode:raise RuntimeError(p.stdout+p.stderr)\n'
            '    return native\n\n'
            'def optional_read(path):'
        )
        text=text.replace(needle,replace,1)
    file=OUT/("perf-"+mode+".py")
    file.write_text(text)
    run([sys.executable,"-m","py_compile",file],
        log=OUT/("perf-"+mode+"-compile.log"),timeout=50)
    return file
def perf_result(path):
    with tarfile.open(path,"r:gz") as t:
        hits=[m for m in t if m.isfile() and m.name.endswith("/result.json")
              and m.size<200000]
        if len(hits)!=1:raise RuntimeError("Missing unique CPU/GPU capture result")
        return json.load(t.extractfile(hits[0]))
def fps(prefix):
    all_values={}
    for name in ("guest-flips","swapchain-presents"):
        f=Path(str(prefix)+"."+name+".csv")
        if not f.is_file() or f.is_symlink() or f.stat().st_size>12000000:
            raise RuntimeError("True FPS stream unavailable: "+name)
        rows=f.read_text().splitlines()
        if not rows or rows[0]!="monotonic_us":
            raise RuntimeError("Unexpected FPS stream header")
        stamps=[int(x) for x in rows[1:] if x.strip()]
        if stamps!=sorted(stamps):raise RuntimeError("Unordered FPS timepoints")
        all_values[name]=stamps
    hosts=all_values["swapchain-presents"]
    if not hosts:raise RuntimeError("No frames displayed")
    start=hosts[0]
    out={}
    for name,stamps in all_values.items():
        points=[x for x in stamps if start+70_000_000<=x<=start+130_000_000]
        gaps=[(b-a)/1000 for a,b in zip(points,points[1:]) if b>a]
        lows=sorted(gaps,reverse=True)
        low1=statistics.mean(lows[:max(1,math.ceil(len(lows)*.01))]) if lows else None
        out[name]={"steady_fps":round(len(points)/60,3),
                   "one_percent_low_fps":round(1000/low1,3) if low1 else None,
                   "frames":len(points),
                   "median_frame_ms":round(statistics.median(gaps),3) if gaps else None,
                   "max_frame_ms":round(max(gaps),3) if gaps else None}
    return out
def cpu_patch_logs(path):
    with tarfile.open(path,"r:gz") as t:
        snapshots=[m for m in t if m.isfile() and
                   m.name.endswith("/gameplay-capture.tar.gz") and m.size<110000000]
        if len(snapshots)!=1:return {"console":"unavailable"}
        with tarfile.open(fileobj=io.BytesIO(t.extractfile(snapshots[0]).read())) as child:
            logs=[m for m in child if m.isfile() and
                  m.name.endswith("/console.log") and m.size<160000000]
            if len(logs)!=1:return {"console":"unavailable"}
            content=child.extractfile(logs[0]).read().decode(errors="replace")
    lines=[line for line in content.splitlines() if
           "Static CPU patching for " in line or "Failed to patch address" in line]
    (OUT/"native-cpu-static-patch-summary.log").write_text("\n".join(lines[:80])+"\n")
    m=re.findall(r"Static CPU patching for eboot\.bin:.*?\((\d+) left to the exception handler\)",
                 content)
    return {"eboot_short_unhandled":int(m[-1]) if m else None,
            "external_DynamoRIO_process_started":"Starting CPU identity translation." in content,
            "patch_failure_logs":content.count("Failed to patch address"),
            "summary":lines[:10]}
def capture(candidate,mode,config_before):
    if sha(BIN)!=candidate or sha(CLIENT)!=CLIENT_SHA or config()!=config_before:
        raise RuntimeError("Binary/CPU-ID library/game settings changed between runs")
    file=make_profile(candidate,mode)
    prefix=OUT/("frames-"+mode)
    env=os.environ.copy()
    env["SHADPS4_FRAME_METRICS_PREFIX"]=str(prefix)
    env.pop("SHADPS4_CPU_TIMING_CSV",None)
    say("CAPTURE=145-second "+mode+" unchanged-settings game")
    log=OUT/("capture-"+mode+".log")
    rc,_=run([sys.executable,file],env=env,log=log,timeout=480,check=False)
    archives=[]
    for row in log.read_text(errors="replace").splitlines():
        if "UPLOAD=" not in row:continue
        p=Path(row.split("UPLOAD=",1)[1].strip())
        if p.is_file() and not p.is_symlink() and p.parent.resolve()==LOGS.resolve() and p.name.startswith("gow-generic-performance-"):
            archives.append(p)
    if not archives:
        raise RuntimeError(mode+" capture produced no archive: "+log.read_text(errors="replace")[-2000:])
    archive=archives[-1]
    stats=perf_result(archive)
    if (rc or stats.get("status")!="CPU_GPU_PROFILE_CAPTURED" or
        stats.get("binary_sha256")!=candidate or
        stats.get("verified_binary_unchanged") is not True or
        stats.get("original_config_hashes_unchanged") is not True or
        stats.get("game_runner_failure") is not None or
        stats.get("guest_exit")!=0 or
        stats.get("event_counts",{}).get("vulkan_device_lost")!=0):
        raise RuntimeError("Game rendering/provenance failed in "+mode+": "+
                           str(stats.get("game_runner_failure")))
    patch=cpu_patch_logs(archive)
    if mode=="native" and patch.get("external_DynamoRIO_process_started"):
        raise RuntimeError("Native CPU-ID test unexpectedly used DynamoRIO")
    report={"mode":mode,"fps":fps(prefix),
            "cpu_gpu":stats.get("resource_summary"),
            "errors":stats.get("event_counts"),
            "screenshots":stats.get("image_count"),
            "cpu_id_patches":patch,
            "profile_archive":str(archive)}
    save(OUT/("report-"+mode+".json"),report)
    say("FPS_"+mode+"="+str(report["fps"]["guest-flips"]["steady_fps"]))
    return report
def archive(result):
    save(OUT/"result.json",result)
    target=LOGS/(OUT.name+".tar.gz")
    with tarfile.open(target,"w:gz",compresslevel=3) as t:
        for file in sorted(OUT.rglob("*")):
            if file.is_file() and not file.is_symlink() and file.name not in (
                "native-trap-unit","audio-unit") and OUT/"unit-source" not in file.parents:
                t.add(file,arcname=OUT.name+"/"+str(file.relative_to(OUT)))
        for name,info in result.get("runs",{}).items():
            file=Path(info.get("profile_archive",""))
            if file.is_file() and not file.is_symlink() and file.parent.resolve()==LOGS.resolve():
                t.add(file,arcname=OUT.name+"/"+name+"-screenshots-and-logs.tar.gz")
    say("UPLOAD="+str(target))
def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={"status":"STARTED","source_topic":TOPIC,"upstream":UPSTREAM,
            "generic_fixes":GENERIC,"before_binary_sha256":BIN_SHA,
            "old_native_FPS":23.083,"no_game_workarounds":True,
            "no_gpu_settings_changed":True,"installed_ESDE_unchanged":True,
            "runs":{}}
    install_before=installed()
    config_before=None
    preserved=False
    success=False
    try:
        preflight()
        config_before=config()
        data={path:download(path,sha_post) for path,(_,sha_post) in EDIT.items()}
        data.update({path:download(path,sha_post) for path,sha_post in NEW.items()})
        unit_test(data)
        regressions()
        preserve()
        preserved=True
        result["stage"]="BUILD_GENERIC_CPU_ID"
        candidate=build(data)
        result["candidate_binary_sha256"]=candidate
        result["stage"]="TEST_EMULATOR_NATIVE"
        result["runs"]["native"]=capture(candidate,"native",config_before)
        result["stage"]="TEST_TRANSLATED_FALLBACK"
        result["runs"]["auto"]=capture(candidate,"auto",config_before)
        val=result["runs"]["native"]["fps"]["guest-flips"]["steady_fps"]
        result["native_FPS_change_vs_prior_percent"]=round((val/23.083-1)*100,2)
        result["status"]="NATIVE_CPU_ID_TESTED_PENDING_CROSS_GAME_VISUAL_VERIFICATION"
        success=True
        return 0
    except BaseException as error:
        result["status"]="FAILED"
        result["error"]=repr(error)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if preserved and not success:
            try:result["rolled_back_previous_executable_and_sources"]=revert()
            except BaseException as error:result["rollback_error"]=repr(error)
        result["original_binary_backed_up"]=(
            (ROLLBACK/"shadps4").is_file() and sha(ROLLBACK/"shadps4")==BIN_SHA)
        result["installed_ESDE_unchanged"]=installed()==install_before
        if config_before is not None:result["game_settings_unchanged"]=config()==config_before
        archive(result)
if __name__=="__main__":
    raise SystemExit(main())
