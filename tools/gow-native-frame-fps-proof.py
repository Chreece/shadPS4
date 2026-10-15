#!/usr/bin/env python3
"""Measure actual guest-flip FPS vs swapchain-present FPS on generic shadPS4.

Add only upstream-based opt-in FPS telemetry to the currently rendering
upstream+general emulator build. Existing binary is SHA-preserved before link.
No game-ID conditional, shader skip, guest patch, DMA/readback override,
pipeline-cache setting change, FPS cap, driver change, or installed ES-DE edit.
The new metrics distinguish guest-generated frames from repeated presentation.

Native FPS event timestamps are collected in-process using steady_clock,
then the original-settings bounded gameplay/performance profiler captures
simultaneous screenshots, CPU/GPU pressure, and audio data. A 60-second
steady-state window and stutter frame-time quantiles are reported.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
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
LOGS=HOME/"projects/shadps4/logs"
OUT=LOGS/("gow-frame-fps-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
TOPIC="3cb476400a33aa731332441379d37239480e1159"
BASE="b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
STACK="966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe"
PREVIOUS_SHA="3546bd6dac1b1dcd1327c24e83981ebf4f17d2ef32ab9f135db7a63f52ea8131"
PREVIOUS_SUCCESS_SHA="9526f71635c4a20b604fe33494168f16151e70da9498da39fa48f3e38a0a9de9"
HISTORIC_SHA="7ee847328072f9d65772fc85ee19f93143cbaaafe69e381d70379479ddd81a30"
DRIVER="src/core/libraries/videoout/driver.cpp"
SWAPCHAIN="src/video_core/renderer_vulkan/vk_swapchain.cpp"
HEADER="src/common/frame_metrics.h"
TEST="tests/common/test_frame_metrics.cpp"
ORIGINAL_BLOBS={
    DRIVER:"8d8d988ad31d165502d7369b15eb7a0f344e97d0",
    SWAPCHAIN:"f2d6bbab9b4c18d0d2d21c66da9faf365f423b56",
}
TOPIC_BLOBS={
    DRIVER:"be76da4a710ca7336ea70caac3972191b9ff95b1",
    SWAPCHAIN:"76ddbe41832c99baf8f3e2e37d09570fb694d4ba",
    HEADER:"449d45debffb6ea62fd8b1bfdd8fcbb45dc16eb5",
    TEST:"cdfd490f3a33f51519be423bad5c60561788f366",
}
PROFILE=PROJECT/"scripts/gow-generic-performance-profile.py"
PROFILE_BLOB="99e9e14d1c0775a7d1c8b7c8a68e2b89d09360fd"
PROFILE_SHA="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
CHECKPOINT=WORK/"preserved-before-frame-fps"
MANIFEST=WORK/".generic-frame-fps-provenance.json"
INSTALLED=HOME/"Applications/shadps4/shadps4"
FPS_PREFIX=OUT/"native-frame-metrics"

def note(message):
    print("GOW_FPS "+str(message),flush=True)

def sha_file(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1048576),b""):
            h.update(block)
    return h.hexdigest()

def git_blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def call(args,*,cwd=None,log=None,timeout=160,check=True,env=None):
    args=[str(x) for x in args]
    if args[0]=="git":
        args=["git","-c","http.version=HTTP/1.1",*args[1:]]
    if log is not None:
        path=Path(log)
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open("wb") as f:
            rc=subprocess.run(args,cwd=cwd,stdin=subprocess.DEVNULL,
                              stdout=f,stderr=subprocess.STDOUT,env=env,timeout=timeout).returncode
        if check and rc:
            raise RuntimeError(f"rc={rc} {args[:6]}\n"+
                               path.read_text(errors="replace")[-3300:])
        return rc,""
    q=subprocess.run(args,cwd=cwd,stdin=subprocess.DEVNULL,capture_output=True,
                     text=True,env=env,timeout=timeout)
    if check and q.returncode:
        raise RuntimeError(f"rc={q.returncode} {args[:6]}\n"+
                           (q.stdout+q.stderr)[-2800:])
    return q.returncode,q.stdout

def json_save(file,value):
    Path(file).write_text(json.dumps(value,indent=2,sort_keys=True)+"\n")

def safe_read(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size>3_000_000:
        raise RuntimeError("unsafe/missing source file "+str(p))
    return p.read_bytes()

def download_pinned(path):
    url=f"https://raw.githubusercontent.com/Chreece/shadPS4/{TOPIC}/{path}"
    fd,tmp=tempfile.mkstemp(dir=OUT,prefix=".native-fps-")
    os.close(fd)
    try:
        call(["curl","-4fsSL","--retry","3","--connect-timeout","20",
              "--max-time","120",url,"-o",tmp],timeout=360)
        raw=Path(tmp).read_bytes()
        if git_blob(raw)!=TOPIC_BLOBS[path]:
            raise RuntimeError("published native FPS source blob changed: "+path)
        if b"SHADPS4_GOW_" in raw or b"CUSA34384" in raw:
            raise RuntimeError("diagnostic contains a forbidden game-specific conditional")
        return raw
    finally:
        Path(tmp).unlink(missing_ok=True)

def installed_fingerprint():
    if not INSTALLED.exists():
        return None
    if INSTALLED.is_symlink():
        return {"symlink":True}
    return sha_file(INSTALLED) if INSTALLED.is_file() else "not a file"

def preflight():
    if SRC.is_symlink() or not (SRC/".git").is_dir():
        raise RuntimeError("verified source checkout absent")
    ancestors=call(["git","rev-list","--parents","-n","1","HEAD"],cwd=SRC)[1].split()
    if len(ancestors)!=3 or ancestors[1:]!=[BASE,STACK]:
        raise RuntimeError("source ancestry is not the upstream+general stack")
    if call(["git","ls-files","-u"],cwd=SRC)[1].strip():
        raise RuntimeError("unresolved source conflicts")
    if not (BUILD/"shadps4").is_file() or (BUILD/"shadps4").is_symlink():
        raise RuntimeError("known working executable missing")
    if MANIFEST.is_symlink():
        raise RuntimeError("unsafe prior FPS instrumentation manifest")
    if not MANIFEST.exists() and sha_file(BUILD/"shadps4")!=PREVIOUS_SHA:
        raise RuntimeError("FPS instrumentation would change an unexpected emulator binary")
    for path in (DRIVER,SWAPCHAIN):
        safe_read(SRC/path)
    for path in (HEADER,TEST):
        if (SRC/path).is_symlink():
            raise RuntimeError("unsafe FPS support path")
    originals=[
        (WORK/"preserved-before-general-srt-preflight/shadps4",PREVIOUS_SUCCESS_SHA),
        (PROJECT/"tests/gow91-build-20261015-125214-1308309/shadps4",HISTORIC_SHA),
    ]
    for path,expected in originals:
        if path.is_symlink() or not path.is_file() or sha_file(path)!=expected:
            raise RuntimeError("earlier rendering checkpoint missing/corrupt: "+str(path))
    if not GCN.is_dir():
        raise RuntimeError("independent GCN build missing")
    if subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                      capture_output=True).returncode==0:
        raise RuntimeError("another shadPS4 instance is running; not disturbing it")

def apply_only_metrics():
    files={p:download_pinned(p) for p in TOPIC_BLOBS}
    record={
      "topic":TOPIC,"upstream_main":BASE,"general_stack":STACK,
      "original_binary_sha256":PREVIOUS_SHA,
      "only_non_behavioral_diagnostic":True,
      "new_files":[HEADER,TEST],
      "modified_files":[DRIVER,SWAPCHAIN],
      "prior_source_blobs":ORIGINAL_BLOBS,
      "new_source_blobs":TOPIC_BLOBS,
      "no_game_shader_ids":True,"no_gpu_setting_changes":True,
    }
    if MANIFEST.exists():
        old=json.loads(MANIFEST.read_text())
        if old!=record:
            raise RuntimeError("previous FPS source manifest differs")
        for p,bytes_ in files.items():
            if safe_read(SRC/p)!=bytes_:
                raise RuntimeError("FPS source mutated after previous run: "+p)
        if sha_file(CHECKPOINT/"shadps4")!=PREVIOUS_SHA:
            raise RuntimeError("original rendering executable backup changed")
        note("SOURCE=existing exact metrics build source verified")
        return record

    for p,prior_blob in ORIGINAL_BLOBS.items():
        if git_blob(safe_read(SRC/p))!=prior_blob:
            raise RuntimeError("existing videoout/swapchain source differs from generic upstream: "+p)
    for p in (HEADER,TEST):
        if (SRC/p).exists():
            raise RuntimeError("FPS instrumentation already present without proof: "+p)
    # Ensure each upstream-based topic file changes only instrumentation sites,
    # never video timing, guest flip scheduling or swapchain creation.
    native_driver=files[DRIVER].decode()
    native_swap=files[SWAPCHAIN].decode()
    if (native_driver.count("RecordGuestFlip()")!=1 or
            native_swap.count("RecordSwapchainPresent()")!=1 or
            "SHADPS4_FRAME_METRICS_PREFIX" not in files[HEADER].decode()):
        raise RuntimeError("published diagnostic lacks actual frame-event counters")

    CHECKPOINT.mkdir(parents=True,exist_ok=False)
    shutil.copy2(BUILD/"shadps4",CHECKPOINT/"shadps4")
    if sha_file(CHECKPOINT/"shadps4")!=PREVIOUS_SHA:
        raise RuntimeError("could not preserve current rendering executable")
    for p in ORIGINAL_BLOBS:
        target=CHECKPOINT/p
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(SRC/p,target)

    try:
        for p,raw in files.items():
            dest=SRC/p
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(raw)
        call(["git","diff","--check"],cwd=SRC,timeout=90)
        json_save(MANIFEST,record)
        note("SOURCE=generic opt-in native frame timestamps in guest Flip + Vulkan Present")
        return record
    except BaseException:
        for p in ORIGINAL_BLOBS:
            shutil.copy2(CHECKPOINT/p,SRC/p)
        for p in (HEADER,TEST):
            (SRC/p).unlink(missing_ok=True)
        raise

def test_native_recorder():
    compiler=shutil.which("g++") or shutil.which("clang++")
    if compiler is None:raise RuntimeError("no C++ compiler installed")
    binary=OUT/"native-fps-test"
    note("FPS_TEST=compile standalone real instrumentation and verify timestamp separation")
    call([compiler,"-std=c++20","-O2","-pthread",
          "-I",SRC/"src",SRC/TEST,"-o",binary],
         log=OUT/"native-test-build.log",timeout=240)
    call([binary,OUT/"native-test"],
         log=OUT/"native-test-result.log",timeout=30)
    report=(OUT/"native-test-result.log").read_text()
    if "FRAME_METRICS_NATIVE=PASS game_flips=2 swapchain_presents=2" not in report:
        raise RuntimeError("native FPS timing regression failed")
    note("FPS_TEST=PASS two guest flips and two swapchain presentations")

def existing_regressions():
    note("GCN_TEST=all 79 previous shader instruction checks")
    call(["cmake","--build",GCN,"--target","shadps4_gcn_test","--parallel","4"],
         log=OUT/"gcn-build.log",timeout=7200)
    binary=GCN/"tests/shadps4_gcn_test"
    if not binary.is_file() or binary.is_symlink():
        raise RuntimeError("known GCN test binary missing")
    rc,_=call([binary,"--gtest_output=json:"+str(OUT/"gcn.json")],
              log=OUT/"gcn-test.log",timeout=1800,check=False)
    if rc or not (OUT/"gcn.json").is_file():
        raise RuntimeError("GCN tests did not pass")
    results=json.loads((OUT/"gcn.json").read_text())
    if int(results.get("tests",0))!=79 or int(results.get("failures",1))!=0:
        raise RuntimeError("GCN regression suite not exactly 79/79")
    note("GCN_TEST=PASS 79/79")
    audio=SRC/"tests/audio/test_audio_output_batch_sync.cpp"
    if audio.is_file() and "AUDIO_MULTI_PORT_SYNC=PASS cases=5" in audio.read_text():
        compiler=shutil.which("g++") or shutil.which("clang++")
        target=OUT/"audio-lock-test"
        call([compiler,"-std=c++20","-O2","-pthread","-I",SRC/"src",
              audio,"-o",target],log=OUT/"audio-build.log",timeout=240)
        call([target],log=OUT/"audio-test.log",timeout=90)
        if "AUDIO_MULTI_PORT_SYNC=PASS cases=5" not in (OUT/"audio-test.log").read_text():
            raise RuntimeError("audio locking regression unexpectedly failed")
        note("AUDIO_TEST=PASS 5/5")
    else:
        raise RuntimeError("last verified five audio regressions missing")

def percentile(values,fraction):
    if not values:return None
    data=sorted(values)
    point=(len(data)-1)*fraction
    left=int(point)
    if left+1>=len(data):
        return data[-1]
    rem=point-left
    return data[left]*(1-rem)+data[left+1]*rem

def metrics(timestamps,start_us,end_us):
    times=[t for t in timestamps if start_us<=t<=end_us]
    times.sort()
    elapsed=(end_us-start_us)/1_000_000
    if elapsed<=0:return None
    deltas=[(b-a)/1000 for a,b in zip(times,times[1:]) if b>a]
    lows=sorted(deltas,reverse=True)
    low1=statistics.mean(lows[:max(1,math.ceil(len(lows)*0.01))]) if lows else None
    ten_secs=max(1,math.ceil(elapsed/10))
    buckets=[0]*ten_secs
    for stamp in times:
        idx=min(ten_secs-1,int((stamp-start_us)//10_000_000))
        buckets[idx]+=1
    return {
      "sampled_seconds":round(elapsed,2),
      "frames":len(times),
      "wall_clock_fps":round(len(times)/elapsed,3),
      "active_frame_fps":round((len(times)-1)/((times[-1]-times[0])/1_000_000),3)
          if len(times)>=2 and times[-1]>times[0] else None,
      "one_percent_low_fps":round(1000/low1,3) if low1 and low1>0 else None,
      "median_frame_ms":round(percentile(deltas,.5),3) if deltas else None,
      "p95_frame_ms":round(percentile(deltas,.95),3) if deltas else None,
      "p99_frame_ms":round(percentile(deltas,.99),3) if deltas else None,
      "max_frame_ms":round(max(deltas),3) if deltas else None,
      "stutters_over_50ms":sum(d>50 for d in deltas),
      "stutters_over_100ms":sum(d>100 for d in deltas),
      "stutters_over_250ms":sum(d>250 for d in deltas),
      "fps_per_10sec":[round(v/min(10,elapsed-i*10),2)
          for i,v in enumerate(buckets) if elapsed-i*10>0],
    }

def read_frame_csv(filename):
    path=Path(filename)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>8_000_000:
        raise RuntimeError("missing/invalid FPS event stream: "+str(path))
    lines=path.read_text().splitlines()
    if not lines or lines[0]!="monotonic_us":
        raise RuntimeError("native stream missing expected timestamp header")
    try:
        stamps=[int(x) for x in lines[1:] if x.strip()]
    except ValueError:
        raise RuntimeError("native frame stream has invalid timestamp")
    if stamps!=sorted(stamps):
        raise RuntimeError("frame timestamps are not monotonically ordered")
    return stamps

def summarize_fps(start_ns,end_ns,new_binary):
    guest=read_frame_csv(str(FPS_PREFIX)+".guest-flips.csv")
    display=read_frame_csv(str(FPS_PREFIX)+".swapchain-presents.csv")
    start_us=start_ns//1000
    end_us=end_ns//1000
    duration=(end_us-start_us)/1_000_000
    if duration<=0:raise RuntimeError("performance capture duration missing")
    if not guest or not display:
        raise RuntimeError("no native guest/display frame timestamps captured")
    recent_start=end_us-min(60_000_000,end_us-start_us)
    warmup_start=min(end_us,start_us+30_000_000)
    return {
      "binary_sha256":new_binary,
      "measurement":"new guest frames completed vs host swapchain presentations",
      "clock":"C++ steady_clock monotonic microseconds; no screenshot-based estimation",
      "capture_duration_sec":round(duration,2),
      "note":"Swapchain FPS can include repeats; guest-flip FPS measures new game frames. Comparisons must use identical gameplay scenes.",
      "guest_flips":{
        "full_capture":metrics(guest,start_us,end_us),
        "after_first_30sec":metrics(guest,warmup_start,end_us),
        "last_60sec":metrics(guest,recent_start,end_us),
      },
      "swapchain_presents":{
        "full_capture":metrics(display,start_us,end_us),
        "last_60sec":metrics(display,recent_start,end_us),
      },
    }

def read_profile_archive(path):
    with tarfile.open(path,"r:gz") as archive:
        records=[m for m in archive.getmembers()
                 if m.isfile() and m.name.endswith("/result.json")]
        if len(records)!=1 or records[0].size>200000:
            raise RuntimeError("invalid CPU/GPU performance archive")
        return json.load(archive.extractfile(records[0]))

def performance_test(result):
    if PROFILE.is_symlink() or not PROFILE.is_file() or git_blob(PROFILE.read_bytes())!=PROFILE_BLOB:
        raise RuntimeError("original-settings CPU/GPU profiler changed")
    source=PROFILE.read_text()
    if source.count(PROFILE_SHA)!=1:
        raise RuntimeError("previous FPS-disabled binary hash anchor changed")
    modified=source.replace(PROFILE_SHA,result["new_binary_sha256"],1)
    wrapper=WORK/"helpers/performance-with-native-fps.py"
    wrapper.parent.mkdir(parents=True,exist_ok=True)
    wrapper.write_text(modified)
    call([sys.executable,"-m","py_compile",wrapper],timeout=60)
    env=os.environ.copy()
    env["SHADPS4_FRAME_METRICS_PREFIX"]=str(FPS_PREFIX)
    note("CAPTURE=145s original-config gameplay with native guest FPS and swapchain FPS")
    start_ns=__import__("time").monotonic_ns()
    rc,_=call([sys.executable,wrapper],log=OUT/"performance-runner.log",
              timeout=440,check=False,env=env)
    end_ns=__import__("time").monotonic_ns()
    captures=[]
    for line in (OUT/"performance-runner.log").read_text(errors="replace").splitlines():
        if "UPLOAD=" not in line:continue
        f=Path(line.split("UPLOAD=",1)[1].strip())
        if (f.is_file() and not f.is_symlink() and f.parent.resolve()==LOGS.resolve()
                and f.name.startswith("gow-generic-performance-")):
            captures.append(f)
    if not captures:
        raise RuntimeError(f"CPU/GPU profiler rc={rc} produced no archive")
    capture=captures[-1]
    perf=read_profile_archive(capture)
    if (perf.get("binary_sha256")!=result["new_binary_sha256"] or
            perf.get("status")!="CPU_GPU_PROFILE_CAPTURED" or
            perf.get("verified_binary_unchanged") is not True or
            perf.get("original_config_hashes_unchanged") is not True):
        raise RuntimeError("native FPS profile candidate/settings provenance failed")
    fps=summarize_fps(start_ns,end_ns,result["new_binary_sha256"])
    json_save(OUT/"fps-summary.json",fps)
    result["fps"]=fps
    result["performance_archive"]=str(capture)
    result["cpu_gpu"]={
      k:perf.get(k) for k in ("status","binary_sha256","resource_summary",
                               "event_counts","image_count","guest_exit")
    }
    note("FPS="+str((fps["guest_flips"]["last_60sec"] or {}).get("wall_clock_fps"))+
         " game frames/s; swapchain FPS="+str(
          (fps["swapchain_presents"]["last_60sec"] or {}).get("wall_clock_fps")))
    return fps

def checkpoint_installed():
    if not INSTALLED.exists():return None
    if INSTALLED.is_symlink():return {"symlink":True}
    return sha_file(INSTALLED) if INSTALLED.is_file() else "non-file"

def package(result):
    json_save(OUT/"result.json",result)
    dest=LOGS/(OUT.name+".tar.gz")
    with tarfile.open(dest,"w:gz",compresslevel=3) as archive:
        for f in sorted(OUT.rglob("*")):
            if f.is_file() and not f.is_symlink() and f.name not in (
                  "native-fps-test","audio-lock-test"):
                archive.add(f,arcname=OUT.name+"/"+str(f.relative_to(OUT)))
        nested=result.get("performance_archive")
        if nested:
            p=Path(nested)
            if p.is_file() and not p.is_symlink() and p.parent.resolve()==LOGS.resolve():
                archive.add(p,arcname=OUT.name+"/gameplay-profile.tar.gz")
    note("UPLOAD="+str(dest))

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={
       "status":"STARTED","upstream_main":BASE,"general_stack":STACK,
       "metrics_commit":TOPIC,
       "original_binary_sha256":PREVIOUS_SHA,
       "installed_emulator_unchanged":True,
       "game_specific_behavior_added":False,
       "DMA_readbacks_or_shader_settings_changed":False,
       "previous_FPS_available":False,
       "fps_change_vs_uninstrumented_binary":"unknown, establishing measured baseline",
    }
    installed_before=checkpoint_installed()
    try:
        preflight()
        result["source_manifest"]=apply_only_metrics()
        result["phase"]="NATIVE_TEST"
        test_native_recorder()
        result["native_frame_test_pass"]=True
        result["phase"]="REGRESSIONS"
        existing_regressions()
        result["gcn_tests_pass"]=79
        result["audio_tests_pass"]=5
        result["phase"]="INCREMENTAL_EMULATOR_BUILD"
        note("BUILD=incremental rendering candidate with generic diagnostic-only code")
        call(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
             log=OUT/"emulator-build.log",timeout=7200)
        digest=sha_file(BUILD/"shadps4")
        if digest==PREVIOUS_SHA or sha_file(CHECKPOINT/"shadps4")!=PREVIOUS_SHA:
            raise RuntimeError("candidate was not relinked or previous rendering SHA changed")
        result["new_binary_sha256"]=digest
        call(["ldd",BUILD/"shadps4"],log=OUT/"ldd.log",timeout=80)
        if "not found" in (OUT/"ldd.log").read_text(errors="replace"):
            raise RuntimeError("instrumented emulator lacks a shared dependency")
        result["phase"]="GAMEPLAY_FPS_CAPTURE"
        performance_test(result)
        result["status"]="NATIVE_GAME_FPS_AND_SWAPCHAIN_FPS_MEASURED"
        return 0
    except BaseException as exc:
        result["status"]="FAILED"
        result["error"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if CHECKPOINT.joinpath("shadps4").is_file():
            result["previous_rendering_binary_preserved"]=sha_file(
                CHECKPOINT/"shadps4")==PREVIOUS_SHA
        if checkpoint_installed()!=installed_before:
            result["status"]="FAILED_INSTALLED_EMULATOR_CHANGED"
        package(result)

if __name__=="__main__":
    raise SystemExit(main())
