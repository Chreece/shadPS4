#!/usr/bin/env python3
"""Pinpoint slow graphics on the already-rendering upstream+generic shadPS4.

Records inclusive CPU time in nine engine operations and real guest-flip FPS,
while preserving every working CPU/GPU/audio fix and the original executable.
The source patch is opt-in instrumentation only: no shader hash, game ID,
hardcoded buffer cap, log suppression, DMA/readback toggle, or driver changes.
Does not change ES-DE installation or deliberately save/close your SSH.
A normal game run may create in-game autosaves.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import traceback

HOME = Path.home()
PROJECT = HOME / "projects/shadps4/GodOfWarRagnarok"
WORK = PROJECT / "tests/main-proof-20261015-151529-3107006"
SRC = WORK / "source"
BUILD = WORK / "build"
GCN = WORK / "build-gcn-tests"
LOGROOT = HOME / "projects/shadps4/logs"
OUT = LOGROOT / ("gow-generic-hotpath-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid()))
TOPIC = "9af33cc55404eb6505de96ac0130d499fececa92"
UPSTREAM = "b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
GENERAL = "966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe"
BINARY_OLD = "a22d1eab27f1b4dc1d0bcf384d00eca7118320affe7a82ec6e15b69cec047214"
OLD_RENDERED = "3546bd6dac1b1dcd1327c24e83981ebf4f17d2ef32ab9f135db7a63f52ea8131"
HEADER = "src/common/gpu_cpu_probe.h"
NATIVE_TEST = "tests/common/test_gpu_cpu_probe.cpp"
TOPIC_BLOBS = {
    HEADER: "c9acb140b66f9d735f547c7c1c0aeeb52f47fb08",
    NATIVE_TEST: "78ffb7bdda0fb72718cf52c17ea8eb53edecc298",
}
RASTER = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
MEMORY = "src/core/memory.cpp"
BUFFER = "src/video_core/buffer_cache/buffer_cache.cpp"
PIPE = "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp"
MODIFIED = (RASTER, MEMORY, BUFFER, PIPE)
CHECKPOINT = WORK / "preserved-before-generic-hotpath"
MANIFEST = WORK / ".generic-hotpath-probe.json"
PERF_SCRIPT = PROJECT / "scripts/gow-generic-performance-profile.py"
PERF_SCRIPT_BLOB = "99e9e14d1c0775a7d1c8b7c8a68e2b89d09360fd"
PERF_SCRIPT_OLD_SHA = "26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
CSV = OUT / "gpu-cpu-hotpaths.csv"
FPS_PREFIX = OUT / "native-fps"
INSTALLED = HOME / "Applications/shadps4/shadps4"

def note(v):
    print("GOW_HOTPATH " + str(v), flush=True)

def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()

def checksum(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1048576), b""):
            h.update(part)
    return h.hexdigest()

def blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def invoke(argv, *, log=None, cwd=None, timeout=180, check=True, env=None):
    args = [str(x) for x in argv]
    if args[0] == "git":
        args = ["git", "-c", "http.version=HTTP/1.1", *args[1:]]
    if log:
        path=Path(log)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            p=subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL,
                             stdout=f, stderr=subprocess.STDOUT, timeout=timeout, env=env)
        if check and p.returncode:
            raise RuntimeError(f"command rc={p.returncode}: {args[:6]}\n"+
                               path.read_text(errors="replace")[-3600:])
        return p.returncode, ""
    p=subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL, text=True,
                     capture_output=True, timeout=timeout, env=env)
    if check and p.returncode:
        raise RuntimeError(f"command rc={p.returncode}: {args[:6]}\n"+
                           (p.stderr+p.stdout)[-3600:])
    return p.returncode, p.stdout

def json_save(path,value):
    p=Path(path)
    p.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".hotpath-proof-",dir=p.parent)
    try:
        with os.fdopen(fd,"w") as f:
            json.dump(value,f,sort_keys=True,indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,p)
    finally:
        Path(tmp).unlink(missing_ok=True)

def download(rel):
    url = f"https://raw.githubusercontent.com/Chreece/shadPS4/{TOPIC}/{rel}"
    fd,temp=tempfile.mkstemp(prefix=".probe-topic-",dir=OUT)
    os.close(fd)
    try:
        invoke(["curl","-4fsSL","--retry","3","--connect-timeout","20",
                "--max-time","120",url,"-o",temp],timeout=360)
        raw=Path(temp).read_bytes()
        if blob(raw)!=TOPIC_BLOBS[rel]:
            raise RuntimeError("pinned diagnostic source blob changed: "+rel)
        return raw
    finally:
        Path(temp).unlink(missing_ok=True)

def one(s,a,b,reason):
    if s.count(a)!=1:
        raise RuntimeError("source anchor unavailable/ambiguous in "+reason+": "+str(s.count(a)))
    return s.replace(a,b,1)

def function_scope(s,qualified_name,slot):
    pattern = re.compile(r"(?m)"+re.escape(qualified_name)+r"\([^;]*?\)\s*\{")
    found=list(pattern.finditer(s))
    if len(found)!=1:
        raise RuntimeError("expected one function definition: "+qualified_name)
    m=found[0]
    return s[:m.end()]+(
        "\n    Common::GpuCpuProbe::Scope gpu_cpu_probe"
        "{Common::GpuCpuProbe::Slot::"+slot+"};")+s[m.end():]

def patch_source(path,data):
    s=data.decode()
    if "GpuCpuProbe::" in s:
        raise RuntimeError("unexpected existing probe in "+path)
    marker=re.search(r'#include "common/[^"\n]+"\n',s)
    if not marker:
        raise RuntimeError("expected common include in "+path)
    s=s[:marker.end()]+'#include "common/gpu_cpu_probe.h"\n'+s[marker.end():]
    if path==RASTER:
        s=function_scope(s,"Rasterizer::BindBuffers","BindBuffers")
        s=one(s,
           '                if (size != vsharp.GetSize()) {\n                    LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x}",',
           '                if (size != vsharp.GetSize()) {\n'
           '                    Common::GpuCpuProbe::Scope log_probe{Common::GpuCpuProbe::Slot::ClampedDescriptorLog};\n'
           '                    LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x}",',
           "rasterizer clamp diagnostic")
    elif path==MEMORY:
        s=function_scope(s,"MemoryManager::ClampRangeSize","ClampRangeSize")
    elif path==BUFFER:
        for function,slot in (
            ("BufferCache::ObtainBuffer","ObtainBuffer"),
            ("BufferCache::EnsureResident","EnsureResident"),
            ("BufferCache::SynchronizeMemory","SynchronizeMemory")
        ):
            s=function_scope(s,function,slot)
    elif path==PIPE:
        for function,slot in (
            ("PipelineCache::GetGraphicsPipeline","GraphicsPipeline"),
            ("PipelineCache::GetComputePipeline","ComputePipeline"),
            ("PipelineCache::CompileModule","CompileShaderModule")
        ):
            s=function_scope(s,function,slot)
    else:
        raise RuntimeError("unexpected source in diagnostic patch")
    return s.encode()

def verify_tree():
    if SRC.is_symlink() or not (SRC/".git").is_dir():
        raise RuntimeError("previous upstream+general source checkout missing")
    lineage=invoke(["git","rev-list","--parents","-n","1","HEAD"],cwd=SRC)[1].split()
    if len(lineage)!=3 or lineage[1:]!=[UPSTREAM,GENERAL]:
        raise RuntimeError("source no longer matches pinned upstream plus general fixes")
    if invoke(["git","ls-files","-u"],cwd=SRC)[1].strip():
        raise RuntimeError("unresolved merge conflicts")
    binary=BUILD/"shadps4"
    if binary.is_symlink() or not binary.is_file():
        raise RuntimeError("rendered proof executable unavailable")
    if MANIFEST.is_symlink():
        raise RuntimeError("unsafe existing proof manifest")
    if not MANIFEST.exists() and checksum(binary)!=BINARY_OLD:
        raise RuntimeError("rendering executable no longer matches the proven FPS baseline")
    previous=WORK/"preserved-before-frame-fps/shadps4"
    if not previous.is_file() or previous.is_symlink() or checksum(previous)!=OLD_RENDERED:
        raise RuntimeError("previous rendered checkpoint not available")
    if not GCN.is_dir():
        raise RuntimeError("independent GCN suite missing")
    for path in MODIFIED:
        p=SRC/path
        if p.is_symlink() or not p.is_file() or p.stat().st_size>3_000_000:
            raise RuntimeError("unexpected renderer source: "+path)
    for path in TOPIC_BLOBS:
        if (SRC/path).is_symlink():
            raise RuntimeError("unsafe new diagnostic source path")
    if subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                      stdout=subprocess.DEVNULL).returncode==0:
        raise RuntimeError("shadPS4 already running; close the game without killing SSH")

def apply():
    header=download(HEADER)
    test=download(NATIVE_TEST)
    if MANIFEST.exists():
        proof=json.loads(MANIFEST.read_text())
        if proof.get("topic")!=TOPIC or proof.get("original_binary")!=BINARY_OLD:
            raise RuntimeError("existing CPU timing source manifest differs")
        for path,expected in proof["after"].items():
            file=SRC/path
            if not file.is_file() or file.is_symlink() or checksum(file)!=expected:
                raise RuntimeError("previously applied timing source has changed: "+path)
        if checksum(CHECKPOINT/"shadps4")!=BINARY_OLD:
            raise RuntimeError("previous rendering binary checkpoint missing")
        note("SOURCE=already instrumented, resume compilation/capture")
        return proof

    for path in TOPIC_BLOBS:
        if (SRC/path).exists():
            raise RuntimeError("diagnostic header/test already exist without manifest")
    originals={path:(SRC/path).read_bytes() for path in MODIFIED}
    patched={path:patch_source(path,data) for path,data in originals.items()}
    patched.update({HEADER:header,NATIVE_TEST:test})
    for path,raw in patched.items():
        if b"SHADPS4_GOW_" in raw or b"CUSA34384" in raw:
            raise RuntimeError("forbidden game-specific instrumentation")
    CHECKPOINT.mkdir(parents=True,exist_ok=False)
    shutil.copy2(BUILD/"shadps4",CHECKPOINT/"shadps4")
    if checksum(CHECKPOINT/"shadps4")!=BINARY_OLD:
        raise RuntimeError("failed to preserve rendering executable")
    for path in MODIFIED:
        p=CHECKPOINT/path
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(originals[path])
    try:
        for path,raw in patched.items():
            dest=SRC/path
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(raw)
        invoke(["git","diff","--check"],cwd=SRC,timeout=90)
        proof={
          "topic":TOPIC,"upstream":UPSTREAM,"general_stack":GENERAL,
          "original_binary":BINARY_OLD,
          "before":{path:sha_bytes(raw) for path,raw in originals.items()},
          "after":{path:sha_bytes(raw) for path,raw in patched.items()},
          "changes_only_opt_in_instrumentation":True,
          "no_GPU_configuration_changes":True,
          "non_game_specific":True,
        }
        json_save(MANIFEST,proof)
        json_save(OUT/"source-proof.json",proof)
        note("SOURCE=applied nine opt-in CPU timing regions")
        return proof
    except BaseException:
        for path in MODIFIED:
            shutil.copy2(CHECKPOINT/path,SRC/path)
        for path in TOPIC_BLOBS:
            (SRC/path).unlink(missing_ok=True)
        raise

def regressions():
    compiler=shutil.which("g++") or shutil.which("clang++")
    if not compiler:
        raise RuntimeError("native compiler not found")
    exe=OUT/"test-gpu-cpu-probe"
    invoke([compiler,"-std=c++20","-O2","-pthread","-I",SRC/"src",
            SRC/NATIVE_TEST,"-o",exe],
           log=OUT/"probe-native-build.log",timeout=240)
    invoke([exe,OUT/"probe-native-test.csv"],log=OUT/"probe-native-test.log",timeout=60)
    if "GPU_CPU_TIMING_NATIVE=PASS paths=2" not in (OUT/"probe-native-test.log").read_text():
        raise RuntimeError("CPU timing implementation failed its native regression")
    note("NATIVE_PROBE=PASS")
    invoke(["cmake","--build",GCN,"--target","shadps4_gcn_test","--parallel","4"],
           log=OUT/"gcn-build.log",timeout=7200)
    test=GCN/"tests/shadps4_gcn_test"
    if not test.is_file() or test.is_symlink():
        raise RuntimeError("GCN test binary missing")
    rc,_=invoke([test,"--gtest_output=json:"+str(OUT/"gcn.json")],
                log=OUT/"gcn-run.log",timeout=1800,check=False)
    report=json.loads((OUT/"gcn.json").read_text()) if (OUT/"gcn.json").is_file() else {}
    if rc or report.get("tests")!=79 or report.get("failures")!=0:
        raise RuntimeError("previously proven 79 GCN tests did not pass")
    audio=SRC/"tests/audio/test_audio_output_batch_sync.cpp"
    if not audio.is_file() or "AUDIO_MULTI_PORT_SYNC=PASS cases=5" not in audio.read_text():
        raise RuntimeError("previously proven audio regression source missing")
    audio_bin=OUT/"audio-regressions"
    invoke([compiler,"-std=c++20","-O2","-pthread","-I",SRC/"src",audio,
            "-o",audio_bin],log=OUT/"audio-build.log",timeout=240)
    invoke([audio_bin],log=OUT/"audio-run.log",timeout=50)
    if "AUDIO_MULTI_PORT_SYNC=PASS cases=5" not in (OUT/"audio-run.log").read_text():
        raise RuntimeError("previously proven audio concurrency tests failed")
    note("REGRESSIONS=79 GCN and 5 audio passed")

def read_csv():
    path=CSV
    if not path.is_file() or path.is_symlink() or path.stat().st_size>15000000:
        raise RuntimeError("CPU timing CSV missing/unsafe (instrumentation not active)")
    lines=path.read_text().splitlines()
    if not lines or lines[0]!="steady_ns,region,calls,total_ns,max_ns,slow_5ms,slow_20ms":
        raise RuntimeError("timing stream missing expected header")
    by_region={}
    for line in lines[1:]:
        parts=line.split(",")
        if len(parts)!=7:
            continue
        _,region,*data=parts
        if region not in (
            "BindBuffers","ClampRangeSize","ObtainBuffer","EnsureResident",
            "SynchronizeMemory","GraphicsPipeline","ComputePipeline",
            "CompileShaderModule","ClampedDescriptorLog"):
            continue
        try:
            values=[int(x) for x in data]
        except ValueError:
            continue
        by_region[region]={
            "calls":values[0],
            "inclusive_cpu_seconds":round(values[1]/1e9,4),
            "max_call_ms":round(values[2]/1e6,3),
            "calls_over_5ms":values[3],
            "calls_over_20ms":values[4],
            "average_call_us":round((values[1]/1000/values[0]),3)
               if values[0] else None
        }
    if len(by_region)!=9:
        raise RuntimeError("CPU timing recorder did not capture all nine regions")
    return dict(sorted(by_region.items(),
                       key=lambda row:-row[1]["inclusive_cpu_seconds"]))

def fps_summary():
    paths=[Path(str(FPS_PREFIX)+x) for x in (".guest-flips.csv",".swapchain-presents.csv")]
    results={}
    for kind,path in zip(("guest_fps","swapchain_fps"),paths):
        if not path.is_file() or path.is_symlink() or path.stat().st_size>5000000:
            raise RuntimeError("FPS stream unavailable: "+str(path))
        lines=path.read_text().splitlines()
        if not lines or lines[0]!="monotonic_us":
            raise RuntimeError("invalid native FPS stream")
        stamps=[int(t) for t in lines[1:] if t.strip()]
        if len(stamps)<2:
            raise RuntimeError("not enough native FPS events")
        # Comparable steady scene: fixed 70-130 seconds after the first
        # actual host presentation, as in the original FPS baseline report.
        results[kind]={"total_events":len(stamps),"first_us":stamps[0],"last_us":stamps[-1]}
        if kind=="swapchain_fps":
            window_origin=stamps[0]
    origin=results["swapchain_fps"]["first_us"]
    for kind,path in zip(("guest_fps","swapchain_fps"),paths):
        stamps=[int(t) for t in path.read_text().splitlines()[1:] if t.strip()]
        low=origin+70_000_000
        high=origin+130_000_000
        subset=[t for t in stamps if low<=t<=high]
        diffs=[(b-a)/1000 for a,b in zip(subset,subset[1:]) if b>a]
        results[kind]["steady_70_130_fps"]=round(len(subset)/60,3)
        results[kind]["steady_70_130_frames"]=len(subset)
        results[kind]["median_frame_ms"]=round(sorted(diffs)[len(diffs)//2],3) if diffs else None
        results[kind]["stalls_over_100ms"]=sum(t>100 for t in diffs)
    return results

def perf():
    p=PERF_SCRIPT
    if p.is_symlink() or not p.is_file() or blob(p.read_bytes())!=PERF_SCRIPT_BLOB:
        raise RuntimeError("last known unbiased CPU/GPU profiler unavailable")
    contents=p.read_text()
    if contents.count(PERF_SCRIPT_OLD_SHA)!=1:
        raise RuntimeError("profile binary SHA anchor changed")
    new_hash=checksum(BUILD/"shadps4")
    copied=contents.replace(PERF_SCRIPT_OLD_SHA,new_hash,1)
    runfile=WORK/"helpers/performance-with-hotpath-probes.py"
    runfile.parent.mkdir(parents=True,exist_ok=True)
    runfile.write_text(copied)
    invoke([sys.executable,"-m","py_compile",runfile],timeout=60)
    env=os.environ.copy()
    env["SHADPS4_FRAME_METRICS_PREFIX"]=str(FPS_PREFIX)
    env["SHADPS4_CPU_TIMING_CSV"]=str(CSV)
    note("PERFORMANCE=original-settings game/FPS/CPU timing capture")
    code,_=invoke([sys.executable,runfile],env=env,
                   log=OUT/"performance-runner.log",timeout=460,check=False)
    found=[]
    for line in (OUT/"performance-runner.log").read_text(errors="replace").splitlines():
        if "UPLOAD=" not in line:continue
        path=Path(line.split("UPLOAD=",1)[1].strip())
        if (path.is_file() and not path.is_symlink() and
            path.parent.resolve()==LOGROOT.resolve() and
            path.name.startswith("gow-generic-performance-")):
            found.append(path)
    if not found:
        raise RuntimeError("performance archive not emitted, rc="+str(code))
    archive=found[-1]
    with tarfile.open(archive,"r:gz") as ar:
        meta=[m for m in ar if m.isfile() and m.name.endswith("/result.json")]
        if len(meta)!=1 or meta[0].size>100000:
            raise RuntimeError("unexpected performance result format")
        result=json.load(ar.extractfile(meta[0]))
    if (result.get("status")!="CPU_GPU_PROFILE_CAPTURED" or
        result.get("binary_sha256")!=new_hash or
        result.get("original_config_hashes_unchanged") is not True or
        result.get("verified_binary_unchanged") is not True):
        raise RuntimeError("emulator/settings not verified in new CPU performance capture")
    return archive,result

def installed():
    if not INSTALLED.exists():return None
    if INSTALLED.is_symlink():return {"symlink":True}
    return checksum(INSTALLED) if INSTALLED.is_file() else "not-file"

def package(result):
    json_save(OUT/"result.json",result)
    path=LOGROOT/(OUT.name+".tar.gz")
    with tarfile.open(path,"w:gz",compresslevel=3) as archive:
        for f in sorted(OUT.rglob("*")):
            if f.is_file() and not f.is_symlink() and f.name not in (
                "test-gpu-cpu-probe","audio-regressions"):
                archive.add(f,arcname=OUT.name+"/"+str(f.relative_to(OUT)))
        nested=result.get("performance_archive")
        if nested:
            file=Path(nested)
            if file.is_file() and not file.is_symlink() and file.parent.resolve()==LOGROOT.resolve():
                archive.add(file,arcname=OUT.name+"/gameplay-performance.tar.gz")
    note("UPLOAD="+str(path))

def main():
    LOGROOT.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={"status":"STARTED","phase":"PREFLIGHT",
            "upstream":UPSTREAM,"general_stack":GENERAL,"topic":TOPIC,
            "original_rendering_sha256":BINARY_OLD,
            "installed_emulator_unchanged":True,"settings_overridden":False,
            "game_specific_workaround":False}
    install_before=installed()
    try:
        verify_tree()
        result["source_proof"]=apply()
        result["phase"]="REGRESSION_GATES"
        regressions()
        result["phase"]="BUILD"
        note("BUILD=only touched generic GPU CPU timing sites")
        invoke(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
               log=OUT/"build.log",timeout=7200)
        new_hash=checksum(BUILD/"shadps4")
        if new_hash==BINARY_OLD or checksum(CHECKPOINT/"shadps4")!=BINARY_OLD:
            raise RuntimeError("new emulator was not relinked or rollback checkpoint changed")
        result["new_binary_sha256"]=new_hash
        invoke(["ldd",BUILD/"shadps4"],log=OUT/"ldd.log",timeout=90)
        if "not found" in (OUT/"ldd.log").read_text(errors="replace"):
            raise RuntimeError("emulator linked to missing host libraries")
        result["phase"]="GAMEPLAY_CPU_FPS"
        archive,perf_result=perf()
        result["performance_archive"]=str(archive)
        result["cpu_gpu"]={k:perf_result.get(k) for k in
                          ("resource_summary","event_counts","image_count","guest_exit")}
        result["timings_by_total_cpu_seconds"]=read_csv()
        result["fps"]=fps_summary()
        json_save(OUT/"cpu-hotpath-summary.json",result["timings_by_total_cpu_seconds"])
        json_save(OUT/"fps-summary.json",result["fps"])
        result["status"]="NINE_CPU_HOTPATHS_AND_TRUE_FPS_CAPTURED"
        note("HOTTEST="+next(iter(result["timings_by_total_cpu_seconds"])))
        return 0
    except BaseException as error:
        result["status"]="FAILED"
        result["error"]=repr(error)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if (CHECKPOINT/"shadps4").is_file():
            result["rendered_rollback_intact"]=checksum(CHECKPOINT/"shadps4")==BINARY_OLD
        if installed()!=install_before:
            result["status"]="FAILED_INSTALLED_EMULATOR_CHANGED"
        package(result)

if __name__=="__main__":
    raise SystemExit(main())
