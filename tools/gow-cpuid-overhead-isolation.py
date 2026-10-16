#!/usr/bin/env python3
"""Determine whether DynamoRIO CPU-ID translation is the severe FPS limiter.

A (auto) -> B (native diagnostic ONLY) -> A (auto), using the same SHA-pinned
rendered emulator, launcher inputs, logged game scene and user configuration.
Native mode is NOT a proposed fix: it may execute unsupported guest CPUID/
RDPID/RDTSCP instructions incorrectly or fail. In that case label B INVALID
and keep the previously working auto mode. Nothing is installed or changed in
user settings, GPU driver, emulator source, executable or CPU-ID runtime.
Game launches may autosave. SSH is never stopped by this runner.

The only experimental difference is the --cpu-id-mode argument in a temporary,
verified copy of the existing original-configuration game capture helper.
Native game FPS is comparable ONLY after visual/screenshot parity checks.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tarfile
import traceback

HOME=Path.home()
ROOT=HOME/"projects/shadps4/GodOfWarRagnarok"
WORK=ROOT/"tests/main-proof-20261015-151529-3107006"
GAME=WORK/"build/shadps4"
GAME_SHA="a4d93cf19f672cf8b6dc881f11564c04e8a6af3cdffeee38e8cd669286f81932"
CPU_LIB=WORK/"build/cpu-id-runtime/libshadps4_cpu_id.so"
CPU_SHA="3b56a25afb0a1faf8d37704d166cc96c30fedc26332da7df6287956ee5007f50"
CPU_SRC=WORK/"source/src/core/cpu_id_translation/client.c"
CPU_SRC_GIT_BLOB="7087ff4fac43d771a88baea7beee7f08e33cf118"
CHECKPOINT=WORK/"preserved-before-cpuid-translation-selective/libshadps4_cpu_id.so"
PROFILER=ROOT/"scripts/gow-generic-performance-profile.py"
PROFILE_GIT_BLOB="99e9e14d1c0775a7d1c8b7c8a68e2b89d09360fd"
ORIGINAL_PIN="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
STAGE3=ROOT/"scripts/gow-main-proof-stage3.py"
STAGE3_GIT_BLOB="f64e16d3f5fe70337b43f187f3802f9c0ae3dc81"
CONFIG_ROOT=HOME/".local/share/shadPS4"
INSTALLED=HOME/"Applications/shadps4/shadps4"
LOG_ROOT=HOME/"projects/shadps4/logs"
OUT=LOG_ROOT/("gow-cpuid-overhead-isolation-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
RUNS=(("auto-first","auto"),("native-diagnostic","native"),("auto-last","auto"))
NATIVE_MARKER="    return script\n\ndef optional_read(path):"
NATIVE_LAUNCH='str(binary), "--cpu-id-mode", "auto", "--game", SERIAL, "--fullscreen", "true"'

def out(message):
    print("GOW_CAUSE "+str(message),flush=True)

def sha(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()

def git_blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def run(args, *, log=None, env=None, timeout=100, check=True):
    argv=[str(a) for a in args]
    if log:
        with Path(log).open("wb") as dest:
            p=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=dest,
                             stderr=subprocess.STDOUT,env=env,timeout=timeout)
        if check and p.returncode:
            raise RuntimeError(f"command exit={p.returncode}: {argv[:5]}; "+
                Path(log).read_text(errors="replace")[-2500:])
        return p.returncode,""
    p=subprocess.run(argv,stdin=subprocess.DEVNULL,text=True,
                     capture_output=True,env=env,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f"command exit={p.returncode}: {argv[:5]}; "+
                           (p.stdout+p.stderr)[-2500:])
    return p.returncode,p.stdout

def config_hashes():
    settings=[CONFIG_ROOT/"config.json", CONFIG_ROOT/"custom_configs/CUSA34384.json"]
    result={}
    for p in settings:
        if p.is_symlink() or (p.exists() and not p.is_file()):
            raise RuntimeError("unexpected nonregular game configuration: "+str(p))
        result[str(p)]=sha(p) if p.is_file() else None
    return result

def installed_hash():
    if INSTALLED.is_symlink():
        return "symlink:"+str(INSTALLED.resolve())
    if INSTALLED.is_file():
        return sha(INSTALLED)
    return None

def guard():
    for p in (GAME,CPU_LIB,CPU_SRC,CHECKPOINT,PROFILER,STAGE3):
        if p.is_symlink() or not p.is_file():
            raise RuntimeError("missing verified baseline path: "+str(p))
    if sha(GAME)!=GAME_SHA:
        raise RuntimeError("working emulator executable changed")
    if sha(CPU_LIB)!=CPU_SHA or sha(CHECKPOINT)!=CPU_SHA:
        raise RuntimeError("faster, restored CPU-ID client is missing; do not run A/B")
    if git_blob(CPU_SRC.read_bytes())!=CPU_SRC_GIT_BLOB:
        raise RuntimeError("CPU-ID source was not rolled back")
    if git_blob(PROFILER.read_bytes())!=PROFILE_GIT_BLOB:
        raise RuntimeError("benchmark profiler has changed")
    if git_blob(STAGE3.read_bytes())!=STAGE3_GIT_BLOB:
        raise RuntimeError("original-config capture generator has changed")
    if run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],check=False)[0]==0:
        raise RuntimeError("a shadPS4 session is already running")
    # A translated process may be named libdynamorio.so rather than shadps4.
    for folder in Path("/proc").iterdir():
        if not folder.name.isdecimal():continue
        try:
            if folder.stat().st_uid!=os.getuid():continue
            if str(GAME) in (folder/"maps").read_text(errors="replace"):
                raise RuntimeError("a translated shadPS4 session is already running")
        except OSError:
            continue
    return config_hashes()

def profile_script(mode):
    source=PROFILER.read_text()
    if source.count(ORIGINAL_PIN)!=1:
        raise RuntimeError("the SHA-pinned profiler source has changed format")
    source=source.replace(ORIGINAL_PIN,GAME_SHA,1)
    if mode=="native":
        if source.count(NATIVE_MARKER)!=1:
            raise RuntimeError("cannot safely target the original-config helper")
        patch=(
            '    text=script.read_text()\n'
            '    anchor='+repr(NATIVE_LAUNCH)+'\n'
            '    if text.count(anchor)!=1:\n'
            '        raise RuntimeError("native diagnostic launcher anchor differs")\n'
            '    native=OUT/"temporary-native-diagnostic-launcher.py"\n'
            '    native.write_text(text.replace(anchor, anchor.replace(\'"auto"\', \'"native"\'), 1))\n'
            '    p=subprocess.run([sys.executable,native,"--self-test"],\n'
            '                     capture_output=True,text=True,timeout=80)\n'
            '    if p.returncode:\n'
            '        raise RuntimeError("native helper self-test failed: "+p.stdout+p.stderr)\n'
            '    return native\n\n'
            'def optional_read(path):'
        )
        source=source.replace(NATIVE_MARKER,patch,1)
    elif mode!="auto":
        raise RuntimeError("unexpected CPU-ID mode")
    dest=OUT/("profile-"+mode+".py")
    dest.write_text(source)
    run([sys.executable,"-m","py_compile",dest],
        log=OUT/("compile-"+mode+".log"),timeout=60)
    return dest

def percentile(values,fraction):
    if not values:return None
    arr=sorted(values)
    p=(len(arr)-1)*fraction
    n=int(p)
    return arr[n]+(arr[min(n+1,len(arr)-1)]-arr[n])*(p-n)

def fps_summary(prefix):
    streams={}
    for name in ("guest-flips","swapchain-presents"):
        file=Path(str(prefix)+"."+name+".csv")
        if not file.is_file() or file.is_symlink() or file.stat().st_size>15000000:
            raise RuntimeError("FPS recorder did not produce a stream: "+str(file))
        lines=file.read_text().splitlines()
        if not lines or lines[0]!="monotonic_us":
            raise RuntimeError("unexpected native FPS header")
        stamps=[int(x) for x in lines[1:] if x.strip()]
        if stamps!=sorted(stamps):
            raise RuntimeError("unordered frame timestamps")
        streams[name]=stamps
    host=streams["swapchain-presents"]
    if not host:raise RuntimeError("no host frames were presented")
    start=host[0]
    window_start=start+70_000_000
    window_end=start+130_000_000
    result={}
    for name,stamps in streams.items():
        active=[x for x in stamps if window_start<=x<=window_end]
        gaps=[(b-a)/1000 for a,b in zip(active,active[1:]) if b>a]
        slow=sorted(gaps,reverse=True)
        worst=statistics.mean(slow[:max(1,math.ceil(len(slow)*.01))]) if slow else None
        result[name]={
            "total_frames":len(stamps),
            "steady_window_sec":60,
            "steady_frames":len(active),
            "steady_fps":round(len(active)/60,3),
            "one_percent_low_fps":round(1000/worst,3) if worst else None,
            "median_ms":round(percentile(gaps,.5),3) if gaps else None,
            "p95_ms":round(percentile(gaps,.95),3) if gaps else None,
            "p99_ms":round(percentile(gaps,.99),3) if gaps else None,
            "stalls_over_100ms":sum(d>100 for d in gaps),
            "stalls_over_250ms":sum(d>250 for d in gaps),
        }
    return result

def get_archived_result(file):
    with tarfile.open(file,"r:gz") as archive:
        entries=[m for m in archive.getmembers() if m.isfile() and
                 m.name.endswith("/result.json") and m.size<=200000]
        if len(entries)!=1:raise RuntimeError("profile archive has no unique result")
        return json.load(archive.extractfile(entries[0]))

def capture(label,mode,config_reference):
    if config_hashes()!=config_reference:
        raise RuntimeError("game configuration changed between A/B/A runs")
    if sha(GAME)!=GAME_SHA or sha(CPU_LIB)!=CPU_SHA:
        raise RuntimeError("emulator or CPU-ID client unexpectedly mutated during A/B/A")
    env=os.environ.copy()
    env["SHADPS4_FRAME_METRICS_PREFIX"]=str(OUT/("frames-"+label))
    env.pop("SHADPS4_CPU_TIMING_CSV",None)
    # All unrelated environment variables and application settings remain
    # unchanged in both diagnostic modes. The mode differs ONLY on the CLI.
    runner=profile_script(mode)
    log=OUT/("profile-"+label+".log")
    out("CAPTURE="+label+" mode="+mode+" with unchanged GPU and user settings")
    rc,_=run([sys.executable,runner],log=log,env=env,timeout=480,check=False)
    candidates=[]
    for row in log.read_text(errors="replace").splitlines():
        if "UPLOAD=" not in row:continue
        file=Path(row.split("UPLOAD=",1)[1].strip())
        if (file.is_file() and not file.is_symlink() and
            file.parent.resolve()==LOG_ROOT.resolve() and
            file.name.startswith("gow-generic-performance-")):
            candidates.append(file)
    if not candidates:
        return {"mode":mode,"status":"NO_PROFILE_ARCHIVE","runner_exit":rc,
                "runner_tail":log.read_text(errors="replace")[-2500:]}
    file=candidates[-1]
    result=get_archived_result(file)
    capture_ok=(rc==0 and result.get("status")=="CPU_GPU_PROFILE_CAPTURED" and
                result.get("binary_sha256")==GAME_SHA and
                result.get("verified_binary_unchanged") is True and
                result.get("original_config_hashes_unchanged") is True and
                result.get("game_runner_failure") is None and
                result.get("guest_exit")==0 and
                result.get("event_counts",{}).get("vulkan_device_lost")==0)
    entry={
        "mode":mode,
        "status":"COMPLETE" if capture_ok else "GUEST_OR_PROFILE_FAILURE",
        "runner_exit":rc,
        "profile_status":result.get("status"),
        "game_runner_failure":result.get("game_runner_failure"),
        "guest_exit":result.get("guest_exit"),
        "GPU_and_CPU":result.get("resource_summary"),
        "events":result.get("event_counts"),
        "screenshots":result.get("image_count"),
        "profile_archive":str(file),
    }
    if capture_ok:
        try:
            entry["fps"]=fps_summary(OUT/("frames-"+label))
        except Exception as exc:
            entry["status"]="FPS_STREAM_UNAVAILABLE"
            entry["fps_error"]=repr(exc)
    if config_hashes()!=config_reference:
        entry["status"]="FAILED_CONFIGURATION_CHANGED"
    (OUT/("summary-"+label+".json")).write_text(json.dumps(entry,indent=2)+"\n")
    if entry.get("fps"):
        out("FPS_"+label+"="+str(entry["fps"]["guest-flips"]["steady_fps"])+
            " (1% low "+str(entry["fps"]["guest-flips"]["one_percent_low_fps"])+")")
    else:
        out("FPS_"+label+"=UNAVAILABLE mode_failed="+entry["status"])
    return entry

def compare(results):
    first=results["auto-first"]
    last=results["auto-last"]
    native=results["native-diagnostic"]
    if first.get("status")!="COMPLETE" or last.get("status")!="COMPLETE":
        raise RuntimeError("the original auto mode failed during controls")
    a=[first["fps"]["guest-flips"]["steady_fps"],
       last["fps"]["guest-flips"]["steady_fps"]]
    avg=statistics.mean(a)
    entry={
        "question":"Does running through DynamoRIO CPU-ID translation limit true game FPS?",
        "method":"auto-native-auto in separate original-config game processes",
        "same_executable_and_CPU_ID_library":True,
        "same_logging_GPU_pipeline_cache_and_DMA_settings":True,
        "same_native_FPS_recording_in_all_runs":True,
        "same_optional_CPU_timer_off_in_all_runs":True,
        "native_is_diagnostic_only_not_a_workaround":True,
        "auto_control_fps":a,
        "auto_mean_fps":round(avg,3),
        "control_drift_percent":round(abs(a[0]-a[1])/avg*100,2),
        "native_status":native.get("status"),
        "native_fps":(native.get("fps") or {}).get("guest-flips",{}).get("steady_fps"),
        "visual_parity_verified":False,
        "performance_conclusion":"Pending independent screenshot verification",
    }
    if native.get("status")!="COMPLETE":
        entry["performance_conclusion"]="INCONCLUSIVE: native mode cannot substitute correct translated guest execution"
    elif native.get("fps"):
        v=native["fps"]["guest-flips"]["steady_fps"]
        entry["native_vs_auto_difference_percent"]=round((v/avg-1)*100,2) if avg else None
        entry["performance_conclusion"]="Native path completed but only visual parity can validate comparability"
    (OUT/"comparison.json").write_text(json.dumps(entry,indent=2)+"\n")
    return entry

def package(report):
    (OUT/"result.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    dest=LOG_ROOT/(OUT.name+".tar.gz")
    with tarfile.open(dest,"w:gz",compresslevel=3) as t:
        for file in sorted(OUT.iterdir()):
            if file.is_file() and not file.is_symlink():
                t.add(file,arcname=OUT.name+"/"+file.name)
        for label,entry in report.get("runs",{}).items():
            file=Path(entry.get("profile_archive",""))
            if file.is_file() and not file.is_symlink() and file.parent.resolve()==LOG_ROOT.resolve():
                t.add(file,arcname=OUT.name+"/"+label+"-gameplay.tar.gz")
    out("UPLOAD="+str(dest))

def main():
    LOG_ROOT.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    status={"status":"STARTED","runs":{},"verified_emulator_sha256":GAME_SHA,
            "verified_original_cpu_id_client":CPU_SHA,
            "GPU_or_user_config_changed":False,
            "emulator_source_or_binary_changed":False,
            "CPU_ID_library_replaced":False,
            "native_mode_installed_for_normal_launch":False,
            "SSH_session_modified":False}
    original_installed=installed_hash()
    original_config=None
    try:
        original_config=guard()
        profile_script("auto")
        profile_script("native")
        for label,mode in RUNS:
            entry=capture(label,mode,original_config)
            status["runs"][label]=entry
            if label!="native-diagnostic" and entry["status"]!="COMPLETE":
                raise RuntimeError("auto control failed; not a comparable experiment")
        status["comparison"]=compare(status["runs"])
        status["status"]="CAUSE_ISOLATION_CAPTURED"
        return 0
    except BaseException as exc:
        status["status"]="FAILED"
        status["error"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        status["emulator_binary_unchanged"]=GAME.is_file() and sha(GAME)==GAME_SHA
        status["CPU_ID_client_unchanged"]=CPU_LIB.is_file() and sha(CPU_LIB)==CPU_SHA
        status["installed_emulator_unchanged"]=installed_hash()==original_installed
        status["game_config_unchanged"]=(config_hashes()==original_config) if original_config else None
        package(status)

if __name__=="__main__":
    raise SystemExit(main())
