#!/usr/bin/env python3
"""Pinpoint emulator slow graphics and stuttering audio with passive host metrics.

Runs the exact already-rendering emulator under the same verified, unmodified
configuration. Samples GPU busy, memory, CPU utilization and host scheduling
of individual renderer/audio/guest threads. Reads no guest RAM; has no emulator
patches, DMA/readback toggles, GPU driver flags, source writes, or installations.
Uses the established bounded screenshot/Cross helper, with its usual normal
gameplay/save implications. SSH session and all unrelated processes untouched.
"""
from __future__ import annotations
import collections
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tarfile
import time
import traceback

HOME=Path.home()
ROOT=HOME/"projects/shadps4/GodOfWarRagnarok"
WORK=ROOT/"tests/main-proof-20261015-151529-3107006"
LOGROOT=HOME/"projects/shadps4/logs"
OUT=LOGROOT/("gow-generic-performance-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
BINARY=WORK/"build/shadps4"
BINARY_SHA="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
STAGE3=ROOT/"scripts/gow-main-proof-stage3.py"
STAGE3_BLOB="f64e16d3f5fe70337b43f187f3802f9c0ae3dc81"
GOOD=ROOT/"tests/gow91-build-20261015-125214-1308309/shadps4"
GOOD_SHA="7ee847328072f9d65772fc85ee19f93143cbaaafe69e381d70379479ddd81a30"
PROFILE=HOME/".local/share/shadPS4"
GAME="CUSA34384"
HZ=os.sysconf("SC_CLK_TCK")
PAGESIZE=os.sysconf("SC_PAGE_SIZE")
SAMPLE_INTERVAL=1.0
SECONDS=145

def announce(s):
    print("GOW_PERFORMANCE "+str(s),flush=True)

def digest(file):
    h=hashlib.sha256()
    with Path(file).open("rb") as f:
        for part in iter(lambda:f.read(1048576),b""):
            h.update(part)
    return h.hexdigest()

def git_blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def config():
    result={}
    for f in [PROFILE/"config.json",PROFILE/"custom_configs"/f"{GAME}.json"]:
        if f.is_symlink():
            raise RuntimeError("refusing symlinked configuration")
        if f.exists() and (not f.is_file() or f.stat().st_size>5000000):
            raise RuntimeError("unsafe original configuration")
        result[str(f)]=digest(f) if f.exists() else None
    return result

def verified_script():
    if (not STAGE3.is_file() or STAGE3.is_symlink()
            or git_blob(STAGE3.read_bytes())!=STAGE3_BLOB):
        raise RuntimeError("previous original-configuration helper changed")
    spec=importlib.util.spec_from_file_location("performance_staged3",STAGE3)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load pinned prior capture")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous=module.load_base()
    script=module.normal_config_capture_script(previous)
    content=script.read_text()
    if "patch_config(CFG, args.readbacks_mode)" in content or (
            "Leave the actual per-game configuration completely unchanged" not in content):
        raise RuntimeError("performance capture must not change user settings")
    p=subprocess.run([sys.executable,script,"--self-test"],capture_output=True,
                     text=True,timeout=80)
    if p.returncode:
        raise RuntimeError("capture helper self-test failed: "+p.stdout[-1500:]+p.stderr[-1500:])
    return script

def optional_read(path):
    try:
        return Path(path).read_text().strip()
    except (OSError,UnicodeError):
        return None

def optional_int(path):
    try:
        data=Path(path).read_text().strip()
        return int(data,0) if data.lower().startswith("0x") else int(data)
    except (OSError,ValueError):
        return None

def find_amd_gpu():
    matches=[]
    for device in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        if not device.is_dir() or optional_read(device/"device/vendor")!="0x1002":
            continue
        root=device/"device"
        total=optional_int(root/"mem_info_vram_total")
        matches.append({"root":root,"card":device.name,"vram_total":total or 0,
                        "pci_device":optional_read(root/"device")})
    matches.sort(key=lambda x:x["vram_total"],reverse=True)
    return matches[0] if matches else None

def gpu_sample(gpu):
    if gpu is None:return {}
    root=gpu["root"]
    return {key:optional_int(root/key) for key in (
        "gpu_busy_percent","mem_busy_percent","mem_info_vram_used",
        "mem_info_vram_total","gpu_clock","mem_clock")}

def parse_stat(path):
    try:
        value=Path(path).read_text()
        end=value.rfind(")")
        if end<0:return None
        comm=value[value.find("(")+1:end]
        p=value[end+2:].split()
        if len(p)<22:return None
        return {"comm":comm,"state":p[0],"ppid":int(p[1]),"utime":int(p[11]),
                "stime":int(p[12]),"rss_bytes":int(p[21])*PAGESIZE,
                "num_threads":int(p[17]),"minor_faults":int(p[7]),
                "major_faults":int(p[9])}
    except (OSError,ValueError,IndexError):
        return None

def process_info(pid):
    p=Path("/proc")/str(pid)
    meta=parse_stat(p/"stat")
    if not meta:return None
    data={"pid":pid,**meta}
    raw=optional_read(p/"schedstat")
    if raw:
        parts=raw.split()
        if len(parts)>=2:
            try:data["sched_run_ns"]=int(parts[0]);data["sched_wait_ns"]=int(parts[1])
            except ValueError:pass
    io=optional_read(p/"io")
    if io:
        for line in io.splitlines():
            if ":" in line:
                key,val=line.split(":",1)
                if key in ("rchar","wchar","syscr","syscw","read_bytes","write_bytes"):
                    try:data["io_"+key]=int(val.strip())
                    except ValueError:pass
    return data

def thread_info(pid):
    result={}
    for p in (Path("/proc")/str(pid)/"task").glob("[0-9]*"):
        tid=int(p.name)
        info=parse_stat(p/"stat")
        if not info:continue
        result[tid]={"comm":info["comm"],"utime":info["utime"],
                     "stime":info["stime"],"state":info["state"],
                     "schedstat":optional_read(p/"schedstat")}
    return result

def pressure():
    result={}
    for category in ("cpu","memory","io"):
        data=optional_read(Path("/proc/pressure")/category)
        if not data:continue
        avg=re.search(r"some avg10=([0-9.]+)",data)
        result[category]=float(avg.group(1)) if avg else None
    return result

def child_pid(helper_pid):
    root=Path(f"/proc/{helper_pid}/task/{helper_pid}/children")
    data=optional_read(root)
    candidates=[]
    for word in (data or "").split():
        try:pid=int(word)
        except ValueError:continue
        if (Path("/proc")/str(pid)/"maps").is_file():
            try:
                with (Path("/proc")/str(pid)/"maps").open(errors="replace") as f:
                    # The translated program must be backed by our exact binary.
                    if any(str(BINARY) in line for line in f):
                        candidates.append(pid)
            except OSError:pass
    return candidates[0] if len(candidates)==1 else None

def observed_game_pid(log,helper_pid):
    raw=optional_read(log)
    match=re.search(r"GOW_AUTO_GAMEPLAY_PID=(\d+)",raw or "")
    if match:
        pid=int(match.group(1))
        info=process_info(pid)
        if info and info["ppid"]==helper_pid:
            return pid
    return child_pid(helper_pid)

def aggregate(samples):
    all_cpu=collections.defaultdict(float)
    all_wait=collections.defaultdict(float)
    prev=None
    for item in samples:
        threads=item.get("threads",{})
        if prev is not None and item.get("pid")==prev.get("pid"):
            dt_sec=max(.001,item["elapsed"]-prev["elapsed"])
            before=prev.get("threads",{})
            total=0
            for tid,thread in threads.items():
                old=before.get(tid)
                if old is None:continue
                delta=max(0,thread["utime"]+thread["stime"]-old["utime"]-old["stime"])
                cpu_pct=100*delta/HZ/dt_sec
                if cpu_pct>0:
                    key=thread["comm"]
                    all_cpu[key]+=cpu_pct*dt_sec
                    total+=cpu_pct
                sched=thread.get("schedstat")
                old_sched=old.get("schedstat")
                try:
                    cur=int(sched.split()[1]); prior=int(old_sched.split()[1])
                    if cur>=prior:
                        all_wait[thread["comm"]]+=(cur-prior)/1e9
                except (ValueError,IndexError,AttributeError):
                    pass
            item["cpu_percent_all_threads"]=round(total,2)
        prev=item
    with_cpu=[s["cpu_percent_all_threads"] for s in samples if "cpu_percent_all_threads" in s]
    busy=[s.get("gpu",{}).get("gpu_busy_percent") for s in samples]
    busy=[x for x in busy if x is not None]
    total_seconds=max(.001,samples[-1]["elapsed"]-samples[0]["elapsed"]) if len(samples)>1 else 1
    return {
        "sample_count":len(samples),
        "elapsed_seconds":round(total_seconds,3),
        "gpu_busy_average_percent":round(statistics.mean(busy),2) if busy else None,
        "gpu_busy_peak_percent":max(busy) if busy else None,
        "emulator_cpu_average_percent_all_cores":round(statistics.mean(with_cpu),2) if with_cpu else None,
        "emulator_cpu_peak_percent_all_cores":round(max(with_cpu),2) if with_cpu else None,
        "threads_by_cpu_seconds":[{"name":k,"cpu_seconds":round(v/100,3),
                                   "host_sched_wait_seconds":round(all_wait[k],3)}
                                  for k,v in sorted(all_cpu.items(),key=lambda x:-x[1])[:24]],
    }

def package(outcome):
    result=OUT/"result.json"
    result.write_text(json.dumps(outcome,indent=2,sort_keys=True)+"\n")
    archive=LOGROOT/(OUT.name+".tar.gz")
    with tarfile.open(archive,"w:gz",compresslevel=3) as t:
        for f in sorted(OUT.rglob("*")):
            if f.is_file() and not f.is_symlink():
                t.add(f,arcname=OUT.name+"/"+str(f.relative_to(OUT)))
        nested=outcome.get("gameplay_archive")
        if nested:
            p=Path(nested)
            if p.is_file() and not p.is_symlink() and p.parent.resolve()==LOGROOT.resolve():
                t.add(p,arcname=OUT.name+"/gameplay-capture.tar.gz")
    announce("UPLOAD="+str(archive))

def main():
    LOGROOT.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={"status":"STARTED","binary_sha256":BINARY_SHA,
            "method":"original-config CPU-thread/GPU passive performance profile",
            "emulator_source_modified":False,"emulator_binary_modified":False,
            "user_dma_or_readback_setting_modified":False,
            "installed_emulator_modified":False,"rendering_baseline_preserved":True}
    before=None
    helper=None
    try:
        if (not BINARY.is_file() or BINARY.is_symlink() or digest(BINARY)!=BINARY_SHA):
            raise RuntimeError("rendering binary differs from verified result")
        if not GOOD.is_file() or GOOD.is_symlink() or digest(GOOD)!=GOOD_SHA:
            raise RuntimeError("historical GoW108 checkpoint changed")
        if subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                          capture_output=True).returncode==0:
            raise RuntimeError("shadps4 is already active; close it yourself before profiling")
        before=config()
        script=verified_script()
        gpu=find_amd_gpu()
        result["amd_gpu"]={k:str(v) for k,v in gpu.items()} if gpu else None
        logfile=OUT/"capture-runner.log"
        samples=[]
        t0=time.monotonic()
        announce("CAPTURE=original-config game runner, simultaneous passive CPU/GPU sampling")
        with logfile.open("wb") as output:
            helper=subprocess.Popen(
                [sys.executable,str(script),
                 "--binary",str(BINARY),"--sha256",BINARY_SHA,
                 "--seconds",str(SECONDS),"--interval","8","--after-delay","0.5"],
                stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                start_new_session=True)
            next_sample=t0
            while helper.poll() is None and time.monotonic()-t0<SECONDS+65:
                now=time.monotonic()
                if now<next_sample:
                    time.sleep(min(.25,next_sample-now))
                    continue
                next_sample=now+SAMPLE_INTERVAL
                pid=observed_game_pid(logfile,helper.pid)
                item={"elapsed":round(now-t0,3),"pid":pid,"gpu":gpu_sample(gpu),
                      "pressure":pressure(),"host_loadavg":os.getloadavg()}
                if pid is not None:
                    item["process"]=process_info(pid)
                    item["threads"]=thread_info(pid)
                samples.append(item)
            if helper.poll() is None:
                announce("TIMEOUT=stopping only the dedicated capture helper process group")
                os.killpg(helper.pid,signal.SIGINT)
                try:helper.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    raise RuntimeError("capture helper did not finish cleanup after SIGINT")
            result["capture_runner_exit"]=helper.returncode

        (OUT/"cpu-gpu-samples.jsonl").write_text(
            "".join(json.dumps(x,sort_keys=True)+"\n" for x in samples))
        result["resource_summary"]=aggregate(samples)
        (OUT/"thread-profile.json").write_text(json.dumps(
            result["resource_summary"],indent=2,sort_keys=True)+"\n")
        capture=None
        for line in logfile.read_text(errors="replace").splitlines():
            if line.startswith("UPLOAD="):
                candidate=Path(line[7:].strip())
                if (candidate.is_file() and not candidate.is_symlink()
                        and candidate.parent.resolve()==LOGROOT.resolve()
                        and candidate.name.startswith("gow-auto-gameplay-")):
                    capture=candidate
        if not capture:
            raise RuntimeError("no trusted gameplay archive generated")
        result["gameplay_archive"]=str(capture)
        with tarfile.open(capture,"r:gz") as t:
            members=t.getmembers()
            metadata=[m for m in members if m.isfile() and m.name.endswith("/result.json")]
            consoles=[m for m in members if m.isfile() and m.name.endswith("/console.log")]
            if len(metadata)!=1 or len(consoles)!=1 or consoles[0].size>200000000:
                raise RuntimeError("invalid archived capture")
            status=json.load(t.extractfile(metadata[0]))
            logs=t.extractfile(consoles[0]).read().decode(errors="replace")
            result["guest_exit"]=status.get("returncode")
            result["game_runner_failure"]=status.get("failure")
            result["image_count"]=sum(m.isfile() and
                m.name.lower().endswith((".jpg",".jpeg",".png")) for m in members)
            result["event_counts"]={
                "graphics_pipeline_compilations":logs.count("Compiling graphics pipeline"),
                "shader_module_compilations":logs.count("CompileModule: Compiling"),
                "large_virtual_buffer_clamps":logs.count("Clamped size from"),
                "srt_unsupported_phi":logs.count("Unexpected instruction for offset computation, Phi"),
                "srt_offset_failures":logs.count("Failed to compute offset for SRT walker"),
                "vulkan_device_lost":logs.count("ErrorDeviceLost"),
                "audio_output_errors":logs.count("[Lib.AudioOut] <Error>"),
                "audio_queue_clear_diagnostics":logs.count("Clearing backed up audio queue"),
            }
        result["status"]="CPU_GPU_PROFILE_CAPTURED"
        return 0
    except BaseException as exc:
        result["status"]="FAILED"
        result["error"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if before is not None:
            result["original_config_hashes_unchanged"]=before==config()
            if result["original_config_hashes_unchanged"] is False:
                result["status"]="FAILED_CONFIGURATION_CHANGED"
        result["verified_binary_unchanged"]=BINARY.is_file() and digest(BINARY)==BINARY_SHA
        package(result)

if __name__=="__main__":
    raise SystemExit(main())
