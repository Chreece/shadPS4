#!/usr/bin/env python3
"""Diagnose overhead without changing the rendering emulator or user config.

Use the exact same already-rendering shadPS4 executable and proven original-
configuration gameplay helper in three sequential 145-second runs:
    A: SHADPS4_CPU_TIMING_CSV absent, native FPS ON
    B: SHADPS4_CPU_TIMING_CSV enabled, native FPS ON
    A: SHADPS4_CPU_TIMING_CSV absent, native FPS ON

Every run retains the same game, driver, CPU-ID translation client, startup
arguments, logger settings, DMA, readback mode, and screenshot/Cross sequence.
The native FPS instrumentation is constant, so only nine CPU-timer scopes and
the diagnostics CSV writer differ. This proves/quantifies timer overhead,
NOT the cost of logging, the native FPS counter, or DynamoRIO itself.

No emulator recompilation, game-specific rendering workarounds, config writes,
driver changes, installed ES-DE launcher edits, or SSH session termination.
Existing original-config game runner can press Cross and create autosaves.
"""
from __future__ import annotations

import datetime as datetime
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
ROOT=HOME/"projects/shadps4/GodOfWarRagnarok"
WORK=ROOT/"tests/main-proof-20261015-151529-3107006"
BUILD=WORK/"build"
GAME=BUILD/"shadps4"
GAME_SHA="a4d93cf19f672cf8b6dc881f11564c04e8a6af3cdffeee38e8cd669286f81932"
CLIENT=BUILD/"cpu-id-runtime/libshadps4_cpu_id.so"
CLIENT_SHA="3b56a25afb0a1faf8d37704d166cc96c30fedc26332da7df6287956ee5007f50"
SOURCE=WORK/"source/src/core/cpu_id_translation/client.c"
SOURCE_BLOB="7087ff4fac43d771a88baea7beee7f08e33cf118"
CHECKPOINT=WORK/"preserved-before-cpuid-translation-selective"
PROFILE=ROOT/"scripts/gow-generic-performance-profile.py"
PROFILE_BLOB="99e9e14d1c0775a7d1c8b7c8a68e2b89d09360fd"
PROFILE_OLD_SHA="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
FPS_PROBE=WORK/"source/src/common/frame_metrics.h"
CPU_PROBE=WORK/"source/src/common/gpu_cpu_probe.h"
INSTALLED=HOME/"Applications/shadps4/shadps4"
LOGROOT=HOME/"projects/shadps4/logs"
OUT=LOGROOT/("gow-no-diagnostics-ab-"+datetime.datetime.now().strftime("%Y%m%d-%H%M%S")+
             "-"+str(os.getpid()))
SCHEDULE=[("control-1",False),("cpu-timer",True),("control-2",False)]
SECS=145

def note(s):
    print("GOW_DIAGNOSTIC_AB "+str(s),flush=True)

def digest(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for data in iter(lambda:f.read(1024*1024),b""):
            h.update(data)
    return h.hexdigest()

def git_blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def execute(args,*,log=None,timeout=120,env=None,cwd=None,check=True):
    args=[str(x) for x in args]
    if log is not None:
        with Path(log).open("wb") as output:
            p=subprocess.run(args,stdout=output,stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,timeout=timeout,env=env,cwd=cwd)
        if check and p.returncode:
            raise RuntimeError("rc="+str(p.returncode)+" "+str(args[:4])+
                  "\n"+Path(log).read_text(errors="replace")[-2600:])
        return p.returncode,""
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                     text=True,stdin=subprocess.DEVNULL,env=env,cwd=cwd,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError("rc="+str(p.returncode)+" "+str(args[:4])+
                           "\n"+(p.stdout+p.stderr)[-2600:])
    return p.returncode,p.stdout

def save_json(path,data):
    Path(path).write_text(json.dumps(data,indent=2,sort_keys=True)+"\n")

def installed_state():
    if not INSTALLED.exists():return None
    if INSTALLED.is_symlink():return {"link":str(INSTALLED.resolve())}
    if INSTALLED.is_file():return {"sha256":digest(INSTALLED)}
    return {"other":True}

def config_state():
    # User settings must be byte-for-byte identical across every capture.
    files=[
        HOME/".local/share/shadPS4/user/config.toml",
        HOME/".local/share/shadPS4/config.toml",
        HOME/".local/share/shadPS4/custom_configs/CUSA34384.json",
    ]
    return {str(p):digest(p) if p.is_file() and not p.is_symlink() else None
            for p in files}

def assert_restored():
    if any(p.is_symlink() or not p.is_file() for p in (GAME,CLIENT,SOURCE,PROFILE,FPS_PROBE,CPU_PROBE)):
        raise RuntimeError("required original rendering build or diagnostic source missing")
    if digest(GAME)!=GAME_SHA:
        raise RuntimeError("working rendering executable no longer matches approved baseline")
    if digest(CLIENT)!=CLIENT_SHA or git_blob(SOURCE.read_bytes())!=SOURCE_BLOB:
        raise RuntimeError("failed CPU-ID experiment not completely rolled back: stop here")
    reference=CHECKPOINT/"libshadps4_cpu_id.so"
    if not reference.is_file() or reference.is_symlink() or digest(reference)!=CLIENT_SHA:
        raise RuntimeError("preserved original CPU-ID runtime checkpoint missing/changed")
    if git_blob(PROFILE.read_bytes())!=PROFILE_BLOB:
        raise RuntimeError("original-config gameplay profiler changed")
    if "SHADPS4_FRAME_METRICS_PREFIX" not in FPS_PROBE.read_text():
        raise RuntimeError("true guest FPS recorder is missing")
    if "SHADPS4_CPU_TIMING_CSV" not in CPU_PROBE.read_text():
        raise RuntimeError("optional CPU timer recorder is missing")
    if execute(["pgrep","-u",str(os.getuid()),"-x","shadps4"],check=False)[0]==0:
        raise RuntimeError("another shadPS4 instance is running; close the game first")
    # DynamoRIO launches a separate process, so pgrep can miss it. Check
    # for this exact mapped binary as well, without interrupting anything.
    for directory in Path("/proc").iterdir():
        if not directory.name.isdecimal():continue
        try:
            if directory.stat().st_uid!=os.getuid():continue
            if str(GAME) in (directory/"maps").read_text(errors="replace"):
                raise RuntimeError("a translated copy of the benchmark game is already running")
        except (OSError,PermissionError):
            continue

def patched_profile_script():
    raw=PROFILE.read_text()
    if raw.count(PROFILE_OLD_SHA)!=1:
        raise RuntimeError("original profile binary verification hook changed")
    script=OUT/"original-config-profiler.py"
    script.write_text(raw.replace(PROFILE_OLD_SHA,GAME_SHA,1))
    execute([sys.executable,"-m","py_compile",script],log=OUT/"profile-pycompile.log")
    return script

def percentile(values,q):
    if not values:return None
    x=sorted(values)
    p=(len(x)-1)*q
    low=int(p)
    high=min(low+1,len(x)-1)
    return x[low]+(x[high]-x[low])*(p-low)

def sample_fps(prefix):
    times={}
    for kind in ("guest-flips","swapchain-presents"):
        f=Path(str(prefix)+"."+kind+".csv")
        if not f.is_file() or f.is_symlink() or f.stat().st_size>12_000_000:
            raise RuntimeError("native FPS stream missing: "+str(f))
        lines=f.read_text().splitlines()
        if not lines or lines[0]!="monotonic_us":
            raise RuntimeError("unexpected native FPS time-series format")
        stamps=[int(x) for x in lines[1:] if x.strip()]
        if stamps!=sorted(stamps):
            raise RuntimeError("native FPS timestamps are not monotonic")
        times[kind]=stamps
    host=times["swapchain-presents"]
    if len(host)<2:raise RuntimeError("host never presented a frame")
    low=host[0]+70_000_000
    high=host[0]+130_000_000
    report={}
    for kind,stamps in times.items():
        sample=[t for t in stamps if low<=t<=high]
        gaps=[(b-a)/1000 for a,b in zip(sample,sample[1:]) if b>a]
        worst=sorted(gaps,reverse=True)
        low1=statistics.mean(worst[:max(1,math.ceil(.01*len(worst)))]) if worst else None
        report[kind]={
            "frame_count_full_run":len(stamps),
            "steady_window_seconds":60,
            "steady_frames":len(sample),
            "steady_fps":round(len(sample)/60,3),
            "median_frame_ms":round(percentile(gaps,.5),3) if gaps else None,
            "p95_frame_ms":round(percentile(gaps,.95),3) if gaps else None,
            "p99_frame_ms":round(percentile(gaps,.99),3) if gaps else None,
            "one_percent_low_fps":round(1000/low1,3) if low1 else None,
            "stalls_over_100ms":sum(dt>100 for dt in gaps),
            "stalls_over_250ms":sum(dt>250 for dt in gaps),
        }
    return report

def profile_archive_result(path):
    with tarfile.open(path,"r:gz") as archive:
        metas=[m for m in archive.getmembers()
               if m.isfile() and m.name.endswith("/result.json")]
        if len(metas)!=1 or metas[0].size>200000:
            raise RuntimeError("unexpected CPU/GPU profile manifest format")
        return json.load(archive.extractfile(metas[0]))

def cpu_timer_evidence(file):
    if not file.is_file() or file.is_symlink() or file.stat().st_size>15_000_000:
        raise RuntimeError("enabled CPU timer produced no valid CSV output")
    lines=file.read_text().splitlines()
    if not lines or lines[0]!="steady_ns,region,calls,total_ns,max_ns,slow_5ms,slow_20ms":
        raise RuntimeError("bad CPU timer stream header")
    counters={}
    for row in lines[1:]:
        fields=row.split(",")
        if len(fields)!=7:continue
        name=fields[1]
        counters[name]=dict(
            calls=int(fields[2]),
            wall_seconds_inclusive=round(int(fields[3])/1e9,3),
            max_call_ms=round(int(fields[4])/1e6,3),
        )
    expected={"BindBuffers","ClampRangeSize","ObtainBuffer","EnsureResident",
              "SynchronizeMemory","GraphicsPipeline","ComputePipeline",
              "CompileShaderModule","ClampedDescriptorLog"}
    if set(counters)!=expected or not counters["SynchronizeMemory"]["calls"]:
        raise RuntimeError("CPU timer not active or incomplete")
    return counters

def one_run(label,enable_cpu_timing,profile_script,expected_config):
    if config_state()!=expected_config:
        raise RuntimeError("game configuration changed between benchmark trials")
    assert_restored()
    fps_prefix=OUT/("frame-"+label)
    cpu_file=OUT/("cpu-"+label+".csv")
    env=os.environ.copy()
    env["SHADPS4_FRAME_METRICS_PREFIX"]=str(fps_prefix)
    env.pop("SHADPS4_CPU_TIMING_CSV",None)
    if enable_cpu_timing:
        env["SHADPS4_CPU_TIMING_CSV"]=str(cpu_file)
    env.pop("RADV_DEBUG",None) if os.getenv("RADV_DEBUG") is None else None
    note("RUN="+label+" same binary, same user settings; CPU timers="+
         ("ON" if enable_cpu_timing else "OFF"))
    runlog=OUT/(label+"-profile-runner.log")
    rc,_=execute([sys.executable,profile_script],env=env,
                 log=runlog,timeout=480,check=False)
    archives=[]
    for line in runlog.read_text(errors="replace").splitlines():
        if "UPLOAD=" not in line:continue
        f=Path(line.split("UPLOAD=",1)[1].strip())
        if (f.is_file() and not f.is_symlink() and
            f.parent.resolve()==LOGROOT.resolve() and
            f.name.startswith("gow-generic-performance-")):
            archives.append(f)
    if not archives:
        raise RuntimeError("capture "+label+" rc="+str(rc)+
                           " failed to emit profile: "+runlog.read_text(errors="replace")[-2200:])
    archive=archives[-1]
    profile=profile_archive_result(archive)
    if (rc or profile.get("status")!="CPU_GPU_PROFILE_CAPTURED" or
        profile.get("binary_sha256")!=GAME_SHA or
        profile.get("original_config_hashes_unchanged") is not True or
        profile.get("verified_binary_unchanged") is not True or
        profile.get("event_counts",{}).get("vulkan_device_lost")!=0):
        raise RuntimeError("gameplay, binary or unchanged-config proof failed for "+label+
                           " rc="+str(rc)+" status="+str(profile.get("status")))
    if config_state()!=expected_config:
        raise RuntimeError("gameplay helper unexpectedly mutated user configuration")
    if not enable_cpu_timing and cpu_file.exists():
        raise RuntimeError("CPU timers enabled despite the OFF experiment")
    result={
        "cpu_timing_enabled":enable_cpu_timing,
        "fps":sample_fps(fps_prefix),
        "resource_summary":profile.get("resource_summary"),
        "event_counts":profile.get("event_counts"),
        "gameplay_screenshot_count":profile.get("image_count"),
        "game_exit":profile.get("guest_exit"),
        "profile_archive":str(archive),
        "settings_unchanged":True,
        "game_binary_sha256":GAME_SHA,
        "cpu_id_client_sha256":CLIENT_SHA,
    }
    if enable_cpu_timing:
        result["cpu_probes"]=cpu_timer_evidence(cpu_file)
    save_json(OUT/("result-"+label+".json"),result)
    note("MEASURED="+label+": "+
         str(result["fps"]["guest-flips"]["steady_fps"])+
         " FPS; 1% low="+str(result["fps"]["guest-flips"]["one_percent_low_fps"]))
    return result

def summary(results):
    off=[results["control-1"],results["control-2"]]
    on=results["cpu-timer"]
    off_fps=[r["fps"]["guest-flips"]["steady_fps"] for r in off]
    on_fps=on["fps"]["guest-flips"]["steady_fps"]
    reference=statistics.mean(off_fps)
    variation=abs(off_fps[0]-off_fps[1])/max(reference,.001)*100
    diff=(on_fps/reference-1)*100 if reference else None
    comparison={
        "primary_question":"What is the FPS cost of optional CPU timing diagnostic scopes?",
        "design":"OFF, ON, OFF; identical current executable and client; native FPS always on",
        "changes_tested":["SHADPS4_CPU_TIMING_CSV environment variable ONLY"],
        "cannot_measure":["native FPS counter overhead","sync logging overhead",
                          "DynamoRIO inherent overhead","GPU cache configuration"],
        "baseline_control1_fps":off_fps[0],
        "baseline_control2_fps":off_fps[1],
        "baseline_mean_fps":round(reference,3),
        "cpu_timer_enabled_fps":on_fps,
        "cpu_timer_fps_difference_percent":round(diff,2) if diff is not None else None,
        "two_control_variation_percent":round(variation,2),
        "interpretation":"Compare CPU-timer effect against natural control-to-control drift; "+
                          "one A-B-A is evidence but not statistical proof.",
        "baseline_previous_proven_fps":7.183,
        "low_fps_persist_without_optional_cpu_profiling":reference<15,
    }
    save_json(OUT/"comparison.json",comparison)
    note("RESULT off="+str(comparison["baseline_mean_fps"])+
         " fps, cpu_timings_on="+str(on_fps)+" fps; difference="+
         str(comparison["cpu_timer_fps_difference_percent"])+"%, "+
         "control drift="+str(comparison["two_control_variation_percent"])+"%")
    return comparison

def package(result):
    save_json(OUT/"result.json",result)
    package_path=LOGROOT/(OUT.name+".tar.gz")
    with tarfile.open(package_path,"w:gz",compresslevel=3) as archive:
        for path in sorted(OUT.rglob("*")):
            if path.is_file() and not path.is_symlink():
                archive.add(path,arcname=OUT.name+"/"+str(path.relative_to(OUT)))
        for label,entry in result.get("runs",{}).items():
            child=Path(entry.get("profile_archive",""))
            if child.is_file() and not child.is_symlink() and child.parent.resolve()==LOGROOT.resolve():
                archive.add(child,arcname=OUT.name+"/"+label+"-gameplay-profile.tar.gz")
    note("UPLOAD="+str(package_path))

def main():
    LOGROOT.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={
        "status":"STARTED",
        "unchanged_emulator_sha256":GAME_SHA,
        "restored_CPU_ID_client_sha256":CLIENT_SHA,
        "user_game_or_GPU_settings_changed":False,
        "emulator_or_ESDE_modified":False,
        "graphics_audio_or_CPU_ID_code_modified":False,
        "SSH_session_modified":False,
        "runs":{},
    }
    install_before=installed_state()
    user_config=None
    try:
        assert_restored()
        user_config=config_state()
        result["original_config_hashes"]=user_config
        profile=patched_profile_script()
        for label,enabled in SCHEDULE:
            result["runs"][label]=one_run(label,enabled,profile,user_config)
        result["comparison"]=summary(result["runs"])
        result["status"]="DIAGNOSTIC_CPU_PROBE_ABA_COMPLETE"
        return 0
    except BaseException as exc:
        result["status"]="FAILED"
        result["failure"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        result["game_binary_unchanged"]=GAME.is_file() and digest(GAME)==GAME_SHA
        result["CPU_ID_client_unchanged"]=CLIENT.is_file() and digest(CLIENT)==CLIENT_SHA
        result["installed_ESDE_unchanged"]=installed_state()==install_before
        result["config_unchanged"]=config_state()==user_config if user_config is not None else None
        package(result)

if __name__=="__main__":
    raise SystemExit(main())
