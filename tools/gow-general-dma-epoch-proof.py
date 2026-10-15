#!/usr/bin/env python3
"""Correct redundant full DMA CPU-memory sweeps, validate, benchmark actual FPS.

Existing stack: upstream b2d7ec7 + tested generic CPU/GPU, audio,
SRT/FP64, native FPS and CPU hotpath profiling. Never drop proven fixes.

Only change CPU page-dirty generation bookkeeping and DMA sweep reuse:
- A write/fault invalidation bumps its generation before and after mutating.
- New sparse resident ranges invalidate the saved generation.
- A sweep remembers its pre-scan generation, so concurrent writes force
  another pass rather than being falsely considered synchronized.
- DMA fault processing stays active even when a redundant sweep is skipped.
No game/shader-ID condition, fake guest values, DMA setting, readback change,
performance environment workaround, installed-launcher edit or SSH termination.
"""
from __future__ import annotations

import datetime as dt
import hashlib
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
ROOT=HOME/"projects/shadps4/GodOfWarRagnarok"
WORK=ROOT/"tests/main-proof-20261015-151529-3107006"
SRC=WORK/"source"
BUILD=WORK/"build"
GCN=WORK/"build-gcn-tests"
LOGS=HOME/"projects/shadps4/logs"
OUT=LOGS/("gow-general-dma-epoch-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
TOPIC="ccdb4d38f752538a0c73c8e2b9832ae42f058a56"
UPSTREAM="b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
STACK="966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe"
CURRENT="a4d93cf19f672cf8b6dc881f11564c04e8a6af3cdffeee38e8cd669286f81932"
PREVIOUS="a22d1eab27f1b4dc1d0bcf384d00eca7118320affe7a82ec6e15b69cec047214"
HISTORIC="7ee847328072f9d65772fc85ee19f93143cbaaafe69e381d70379479ddd81a30"
MEM="src/video_core/buffer_cache/memory_tracker.h"
CACHE_H="src/video_core/buffer_cache/buffer_cache.h"
CACHE_CPP="src/video_core/buffer_cache/buffer_cache.cpp"
EPOCH_H="src/video_core/buffer_cache/cpu_dirty_epoch.h"
TEST_CPP="tests/common/test_cpu_dirty_epoch.cpp"
PREVIOUS_BLOBS={MEM:"4e802222ba21a5f3ccd13487ce747fd549ef8766",
                CACHE_H:"1b60bd7092ce7b46ac95fb122c88a4e448328c8b"}
CURRENT_CACHE_SHA="59ad73a280f649034bb201951460838566f29d0a64745bb5d6679780ae73159b"
TOPIC_BLOBS={MEM:"277e425596f65d30addb6f14ae21dc023d073a6c",
             CACHE_H:"1a8e60dbca4b4a5eb88ba8790ff9f2c051e57d00",
             CACHE_CPP:"1b32942d19a6f91ea8c1204062cd719d3eda2ace",
             EPOCH_H:"5841dca219abde53ffc8d19ec9aa1894fd18f28a",
             TEST_CPP:"32cc8ce6afa99bf96db9bc0797c6de7ad5fe0270"}
PROFILE=ROOT/"scripts/gow-generic-performance-profile.py"
PROFILE_BLOB="99e9e14d1c0775a7d1c8b7c8a68e2b89d09360fd"
PROFILE_DEFAULT_BINARY="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
SRT_COMMIT="9165f7e9bbab0d3fb846c980f63dcdfa272d4f53"
SRT_TEST="tests/srt/test_srt_offset_codegen.cpp"
SRT_TEST_BLOB="00fded003c4f0ec076f06a8d36e6187c9d59e560"
SNAP=WORK/"preserved-before-general-dma-epoch"
MANIFEST=WORK/".general-dma-dirty-epoch.json"
INSTALLED=HOME/"Applications/shadps4/shadps4"
CSV=OUT/"gpu-cpu-hotpaths.csv"
FPS=OUT/"native-fps"
BASELINE_FPS=6.133
EXPECTED_TESTS=79

def tell(s):
    print("GOW_DMA_EPOCH "+str(s),flush=True)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def checksum(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for data in iter(lambda:f.read(1024*1024),b""):
            h.update(data)
    return h.hexdigest()

def blob(raw):
    return hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()

def run(args, *, log=None, cwd=None, timeout=180, check=True, env=None):
    argv=[str(v) for v in args]
    if argv[0]=="git":
        argv=["git","-c","http.version=HTTP/1.1",*argv[1:]]
    if log is not None:
        file=Path(log)
        file.parent.mkdir(parents=True,exist_ok=True)
        with file.open("wb") as f:
            status=subprocess.run(argv,cwd=cwd,stdin=subprocess.DEVNULL,
                stdout=f,stderr=subprocess.STDOUT,timeout=timeout,env=env).returncode
        if check and status:
            raise RuntimeError(f"rc={status} {argv[:6]}\n"+
                               file.read_text(errors="replace")[-3600:])
        return status,""
    proc=subprocess.run(argv,cwd=cwd,stdin=subprocess.DEVNULL,
                        capture_output=True,text=True,timeout=timeout,env=env)
    if check and proc.returncode:
        raise RuntimeError(f"rc={proc.returncode} {argv[:6]}\n"+
                           (proc.stdout+proc.stderr)[-3600:])
    return proc.returncode,proc.stdout

def save_json(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=".dma-epoch-")
    try:
        with os.fdopen(fd,"w") as f:
            json.dump(value,f,sort_keys=True,indent=2);f.write("\n")
            f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        Path(tmp).unlink(missing_ok=True)

def exactly(s,a,b,name):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"{name}: expected one source anchor, saw {n}")
    return s.replace(a,b,1)

def fetch(rel,expected,ref=TOPIC):
    url=f"https://raw.githubusercontent.com/Chreece/shadPS4/{ref}/{rel}"
    fd,tmp=tempfile.mkstemp(dir=OUT,prefix=".topic-dma-")
    os.close(fd)
    try:
        run(["curl","-4fsSL","--retry","3","--connect-timeout","20",
             "--max-time","120",url,"-o",tmp],timeout=360)
        raw=Path(tmp).read_bytes()
        if blob(raw)!=expected:
            raise RuntimeError("upstream-based generic DMA fix or test changed: "+rel)
        for marker in (b"CUSA34384",b"SHADPS4_GOW_",b"GOW_SRT_"):
            if marker in raw:
                raise RuntimeError("game-specific condition in general DMA source: "+rel)
        return raw
    finally:
        Path(tmp).unlink(missing_ok=True)

def installed():
    if not INSTALLED.exists():return None
    if INSTALLED.is_symlink():return {"symlink":True}
    return checksum(INSTALLED) if INSTALLED.is_file() else "non-file"

def verify_source():
    if SRC.is_symlink() or not (SRC/".git").is_dir():
        raise RuntimeError("preserved upstream+general Git checkout missing")
    lineage=run(["git","rev-list","--parents","-n","1","HEAD"],cwd=SRC)[1].split()
    if len(lineage)!=3 or lineage[1:]!=[UPSTREAM,STACK]:
        raise RuntimeError("source does not derive from prior upstream and generic fixes")
    if run(["git","ls-files","-u"],cwd=SRC)[1].strip():
        raise RuntimeError("source contains unresolved conflicts")
    current=BUILD/"shadps4"
    if not current.is_file() or current.is_symlink():
        raise RuntimeError("current tested emulator executable unavailable")
    if MANIFEST.is_symlink():
        raise RuntimeError("unsafe resume manifest")
    if not MANIFEST.exists() and checksum(current)!=CURRENT:
        raise RuntimeError("latest FPS-profiled executable has changed unexpectedly")
    old=WORK/"preserved-before-generic-hotpath/shadps4"
    good=ROOT/"tests/gow91-build-20261015-125214-1308309/shadps4"
    if not old.is_file() or checksum(old)!=PREVIOUS or not good.is_file() or checksum(good)!=HISTORIC:
        raise RuntimeError("previous rendering checkpoints not intact")
    for p in (MEM,CACHE_H,CACHE_CPP):
        target=SRC/p
        if not target.is_file() or target.is_symlink():
            raise RuntimeError("unsafe/missing emulator source: "+p)
    if not GCN.is_dir():
        raise RuntimeError("independent GCN build missing")
    if subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                      stdout=subprocess.DEVNULL).returncode==0:
        raise RuntimeError("another shadPS4 is running; close it without disrupting SSH")

def transform_tracker(data,topic):
    s=data.decode()
    if blob(data)!=PREVIOUS_BLOBS[MEM]:
        raise RuntimeError("memory tracker differs from proven generic source")
    if "CpuDirtyGeneration" in s:
        raise RuntimeError("DMA dirty-generation already present without manifest")
    s=exactly(s,
        '#include "video_core/buffer_cache/region_manager.h"\n',
        '#include "video_core/buffer_cache/cpu_dirty_epoch.h"\n'
        '#include "video_core/buffer_cache/region_manager.h"\n',
        "memory tracker include")
    s=exactly(s,'#include <deque>\n','#include <deque>\n#include <optional>\n',
              "memory tracker optional")
    s=exactly(s,'    ~MemoryTracker() = default;\n',
        '    ~MemoryTracker() = default;\n\n'
        '    [[nodiscard]] u64 CpuDirtyGeneration() const noexcept {\n'
        '        return cpu_dirty_epoch.Snapshot();\n'
        '    }\n',
        "memory tracker epoch accessor")
    s=exactly(s,
        '    void UnmarkRegionAsGpuModified(VAddr cpu_addr, u64 size, bool is_write) noexcept {\n'
        '        IteratePages(cpu_addr, size, [is_write](RegionManager* manager, u64 offset, u64 size) {',
        '    void UnmarkRegionAsGpuModified(VAddr cpu_addr, u64 size, bool is_write) noexcept {\n'
        '        // GPU download for a CPU write can mark guest pages as dirty.\n'
        '        std::optional<CpuDirtyEpoch::Mutation> mutation;\n'
        '        if (is_write) {\n'
        '            mutation.emplace(cpu_dirty_epoch);\n'
        '        }\n'
        '        IteratePages(cpu_addr, size, [is_write](RegionManager* manager, u64 offset, u64 size) {',
        "GPU download CPU-write generation")
    s=exactly(s,
        '    void MarkRegionAsCpuModified(VAddr cpu_addr, u64 size) noexcept {\n',
        '    void MarkRegionAsCpuModified(VAddr cpu_addr, u64 size) noexcept {\n'
        '        CpuDirtyEpoch::Mutation mutation{cpu_dirty_epoch};\n',
        "CPU dirty generation mutation")
    s=exactly(s,
        '    void InvalidateRegion(VAddr cpu_addr, u64 size, auto&& on_flush) noexcept {\n',
        '    void InvalidateRegion(VAddr cpu_addr, u64 size, auto&& on_flush) noexcept {\n'
        '        CpuDirtyEpoch::Mutation mutation{cpu_dirty_epoch};\n',
        "CPU fault invalidation generation")
    s=exactly(s,'    PageManager* tracker;\n',
        '    CpuDirtyEpoch cpu_dirty_epoch;\n    PageManager* tracker;\n',
        "CPU generation storage")
    if topic.count(b"CpuDirtyEpoch::Mutation")!=3 or s.count("CpuDirtyEpoch::Mutation")!=3:
        raise RuntimeError("unexpected dirty-epoch notification coverage")
    return s.encode()

def transform_cache_h(data,topic):
    s=data.decode()
    if blob(data)!=PREVIOUS_BLOBS[CACHE_H]:
        raise RuntimeError("buffer cache header differs from retained upstream general stack")
    s=exactly(s,
        '    bool fault_process_pending{};\n',
        '    bool fault_process_pending{};\n\n'
        '    u64 last_dma_synced_cpu_generation{};\n'
        '    u64 dma_scans_performed{};\n'
        '    u64 dma_scans_skipped{};\n',
        "DMA scan generation state")
    for keyword in ("last_dma_synced_cpu_generation","dma_scans_skipped"):
        if keyword not in topic.decode():
            raise RuntimeError("upstream-based general fix has no matching scan state")
    return s.encode()

def transform_cache_cpp(data,topic):
    if sha(data)!=CURRENT_CACHE_SHA:
        raise RuntimeError("instrumented current buffer-cache source differs from proven baseline")
    s=data.decode()
    s=exactly(s,
       'BufferCache::~BufferCache() = default;',
       'BufferCache::~BufferCache() {\n'
       '    LOG_INFO(Render, "DMA CPU-dirty generation: full residency sweeps={}, safely skipped={}",\n'
       '             dma_scans_performed, dma_scans_skipped);\n'
       '}',
       "DMA scan end-of-run evidence")
    s=exactly(s,
       'void BufferCache::SynchronizeDmaBuffers() {\n'
       '    fault_process_pending = true;\n'
       '    for (const auto& range : resident_ranges) {',
       'void BufferCache::SynchronizeDmaBuffers() {\n'
       '    // Preserve fault processing even when CPU backing need not be reuploaded.\n'
       '    fault_process_pending = true;\n'
       '    const u64 cpu_generation = memory_tracker->CpuDirtyGeneration();\n'
       '    if (cpu_generation == last_dma_synced_cpu_generation) {\n'
       '        ++dma_scans_skipped;\n'
       '        return;\n'
       '    }\n'
       '    ++dma_scans_performed;\n'
       '    for (const auto& range : resident_ranges) {',
       "full residency DMA scan guard")
    s=exactly(s,
       '        SynchronizeMemory(address_space[page], device_addr, size, false, false);\n'
       '    }\n}',
       '        SynchronizeMemory(address_space[page], device_addr, size, false, false);\n'
       '    }\n'
       '    // A concurrent CPU dirty notification forces the next draw to rescan.\n'
       '    last_dma_synced_cpu_generation = cpu_generation;\n'
       '}',
       "sync generation post-sweep")
    s=exactly(s,
       '    if (bind_ranges.Empty()) {\n'
       '        return;\n'
       '    }\n\n'
       '    const vk::MemoryAllocateFlagsInfo alloc_flags',
       '    if (bind_ranges.Empty()) {\n'
       '        return;\n'
       '    }\n\n'
       '    // Newly resident pages require a fresh full DMA backing sweep.\n'
       '    last_dma_synced_cpu_generation = 0;\n\n'
       '    const vk::MemoryAllocateFlagsInfo alloc_flags',
       "sparse backing allocation invalidate")
    for marker in ("last_dma_synced_cpu_generation = 0;",
                   "memory_tracker->CpuDirtyGeneration()",
                   "last_dma_synced_cpu_generation = cpu_generation;"):
        if marker not in topic.decode() or marker not in s:
            raise RuntimeError("transformed DMA logic differs from semantic fix: "+marker)
    if "Common::GpuCpuProbe::Scope" not in s:
        raise RuntimeError("previous nine-region CPU profiler was accidentally removed")
    return s.encode()

def apply():
    topic={p:fetch(p,h) for p,h in TOPIC_BLOBS.items()}
    if MANIFEST.exists():
        old=json.loads(MANIFEST.read_text())
        if old.get("topic")!=TOPIC or old.get("before_binary")!=CURRENT:
            raise RuntimeError("previous patch manifest belongs to a different binary/topic")
        for rel,expected in old["after"].items():
            path=SRC/rel
            if not path.is_file() or path.is_symlink() or checksum(path)!=expected:
                raise RuntimeError("applied generic DMA source has changed: "+rel)
        if checksum(SNAP/"shadps4")!=CURRENT:
            raise RuntimeError("previous rendering executable checkpoint changed")
        tell("SOURCE=previous exact source correction verified; resume tests")
        return old

    for p in (EPOCH_H,TEST_CPP):
        if (SRC/p).exists() or (SRC/p).is_symlink():
            raise RuntimeError("generic DMA test/header already present outside checkpoint")
    previous={p:(SRC/p).read_bytes() for p in (MEM,CACHE_H,CACHE_CPP)}
    after={
        MEM:transform_tracker(previous[MEM],topic[MEM]),
        CACHE_H:transform_cache_h(previous[CACHE_H],topic[CACHE_H]),
        CACHE_CPP:transform_cache_cpp(previous[CACHE_CPP],topic[CACHE_CPP]),
        EPOCH_H:topic[EPOCH_H],
        TEST_CPP:topic[TEST_CPP],
    }
    SNAP.mkdir(parents=True,exist_ok=False)
    shutil.copy2(BUILD/"shadps4",SNAP/"shadps4")
    if checksum(SNAP/"shadps4")!=CURRENT:
        raise RuntimeError("previous working executable not checkpointed")
    for name,data in previous.items():
        dest=SNAP/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(data)
    try:
        for name,data in after.items():
            dest=SRC/name
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(data)
        run(["git","diff","--check"],cwd=SRC,timeout=90)
        proof={"topic":TOPIC,"upstream_main":UPSTREAM,"general_stack":STACK,
               "before_binary":CURRENT,
               "before":{p:sha(b) for p,b in previous.items()},
               "after":{p:sha(b) for p,b in after.items()},
               "only_general_DmaDirtyGeneration":True,
               "proven_FPS_telemetry_retained":True,
               "previous_CPU_hotpath_probes_retained":True,
               "game_specific_shader_conditions":False,
               "global_DMA_readback_settings_changed":False}
        save_json(MANIFEST,proof)
        save_json(OUT/"source-proof.json",proof)
        tell("SOURCE=only skip identical DMA sweeps; mark CPU writes and sparse residency")
        return proof
    except BaseException:
        for name,data in previous.items():
            (SRC/name).write_bytes(data)
        for name in (EPOCH_H,TEST_CPP):
            (SRC/name).unlink(missing_ok=True)
        raise

def regressions():
    cc=shutil.which("g++") or shutil.which("clang++")
    if not cc:raise RuntimeError("native C++ compiler unavailable")
    native=OUT/"test-dma-epoch"
    run([cc,"-std=c++20","-O2","-pthread","-I",SRC/"src",SRC/TEST_CPP,"-o",native],
        log=OUT/"dma-test-build.log",timeout=240)
    run([native],log=OUT/"dma-test-result.log",timeout=25)
    if "DMA_DIRTY_EPOCH_NATIVE=PASS cases=8" not in (OUT/"dma-test-result.log").read_text():
        raise RuntimeError("DMA race+generation native regressions did not pass")
    tell("DMA_RACE_TEST=PASS 8")

    run(["cmake","--build",GCN,"--target","shadps4_gcn_test","--parallel","4"],
        log=OUT/"gcn-build.log",timeout=7200)
    gcn=GCN/"tests/shadps4_gcn_test"
    if not gcn.is_file() or gcn.is_symlink():
        raise RuntimeError("GCN test binary missing")
    rc,_=run([gcn,"--gtest_output=json:"+str(OUT/"gcn.json")],
             log=OUT/"gcn-result.log",timeout=1800,check=False)
    report=json.loads((OUT/"gcn.json").read_text()) if (OUT/"gcn.json").is_file() else {}
    if rc or int(report.get("tests",0))!=EXPECTED_TESTS or int(report.get("failures",1))!=0:
        raise RuntimeError("79 GCN regression gate not passed")
    tell("GCN_TEST=PASS 79")

    audio=SRC/"tests/audio/test_audio_output_batch_sync.cpp"
    if not audio.is_file() or audio.is_symlink():
        raise RuntimeError("retained audio test source missing")
    audio_bin=OUT/"audio-test"
    run([cc,"-std=c++20","-O2","-pthread","-I",SRC/"src",audio,"-o",audio_bin],
        log=OUT/"audio-build.log",timeout=240)
    run([audio_bin],log=OUT/"audio-result.log",timeout=60)
    if "AUDIO_MULTI_PORT_SYNC=PASS cases=5" not in (OUT/"audio-result.log").read_text():
        raise RuntimeError("audio-buffer concurrency tests regressed")
    tell("AUDIO_TEST=PASS 5")

    srt=fetch(SRT_TEST,SRT_TEST_BLOB,SRT_COMMIT)
    srt_file=OUT/"previous-srt-arithmetic.cpp";srt_file.write_bytes(srt)
    executable=OUT/"srt-arithmetic-test"
    run([cc,"-std=c++20","-O2","-I",SRC/"src","-I",SRC/"externals/xbyak",
         srt_file,"-o",executable],log=OUT/"srt-build.log",timeout=240)
    run([executable],log=OUT/"srt-result.log",timeout=60)
    if "GENERAL_SRT_OFFSET_JIT=PASS cases=126" not in (OUT/"srt-result.log").read_text():
        raise RuntimeError("previous SRT arithmetic regression failed")
    tell("SRT_TEST=PASS 126")

def read_csv():
    if not CSV.is_file() or CSV.is_symlink():
        raise RuntimeError("CPU timing CSV not captured")
    lines=CSV.read_text().splitlines()
    if not lines or lines[0]!="steady_ns,region,calls,total_ns,max_ns,slow_5ms,slow_20ms":
        raise RuntimeError("unexpected CPU timing CSV header")
    rows={}
    for l in lines[1:]:
        v=l.split(",")
        if len(v)!=7:continue
        t,name,*nums=v
        try:nums=[int(x) for x in nums]
        except ValueError:continue
        rows[name]={"calls":nums[0],"inclusive_seconds":round(nums[1]/1e9,4),
                    "max_ms":round(nums[2]/1e6,3),"calls_over_20ms":nums[-1]}
    for required in ("SynchronizeMemory","ObtainBuffer","GraphicsPipeline","BindBuffers"):
        if required not in rows:
            raise RuntimeError("missing hotpath benchmark region: "+required)
    return rows

def read_fps():
    streams={}
    for name,suffix in (("guest",".guest-flips.csv"),("display",".swapchain-presents.csv")):
        f=Path(str(FPS)+suffix)
        if not f.is_file() or f.is_symlink() or f.stat().st_size>6000000:
            raise RuntimeError("missing native frame stream: "+str(f))
        lines=f.read_text().splitlines()
        if not lines or lines[0]!="monotonic_us":
            raise RuntimeError("frame stream missing canonical monotonic header")
        stamps=[int(x) for x in lines[1:] if x.strip()]
        if not stamps or stamps!=sorted(stamps):
            raise RuntimeError("no valid native FPS readings in "+name)
        streams[name]=stamps
    origin=streams["display"][0]
    result={}
    for name,timestamps in streams.items():
        period=[t for t in timestamps if origin+70_000_000<=t<=origin+130_000_000]
        diffs=[(b-a)/1000 for a,b in zip(period,period[1:]) if b>a]
        if not diffs:
            raise RuntimeError("not enough gameplay frames for comparable 70–130 s window")
        worst=sorted(diffs,reverse=True)
        one_percent=statistics.mean(worst[:max(1,math.ceil(len(worst)*.01))])
        result[name]={
            "actual_fps_70_130":round(len(period)/60,3),
            "frames_70_130":len(period),
            "one_percent_low_fps":round(1000/one_percent,3) if one_percent>0 else None,
            "median_ms":round(statistics.median(diffs),3),
            "stalls_over_100ms":sum(v>100 for v in diffs),
            "all_events":len(timestamps),
        }
    result["baseline_instrumented_guest_fps"]=BASELINE_FPS
    result["fps_change_vs_instrumented_baseline"]=round(result["guest"]["actual_fps_70_130"]-BASELINE_FPS,3)
    return result

def run_profile(new_hash):
    if not PROFILE.is_file() or PROFILE.is_symlink() or blob(PROFILE.read_bytes())!=PROFILE_BLOB:
        raise RuntimeError("previous unchanged-settings performance harness changed")
    source=PROFILE.read_text()
    if source.count(PROFILE_DEFAULT_BINARY)!=1:
        raise RuntimeError("profile runner SHA anchor ambiguous")
    runner=WORK/"helpers/performance-after-generic-dma-epoch.py"
    runner.parent.mkdir(parents=True,exist_ok=True)
    runner.write_text(source.replace(PROFILE_DEFAULT_BINARY,new_hash,1))
    run([sys.executable,"-m","py_compile",runner],timeout=60)
    env=os.environ.copy()
    env["SHADPS4_CPU_TIMING_CSV"]=str(CSV)
    env["SHADPS4_FRAME_METRICS_PREFIX"]=str(FPS)
    tell("GAMEPLAY=unchanged settings; exact previous FPS and CPU profiler")
    rc,_=run([sys.executable,runner],env=env,log=OUT/"performance-runner.log",
             timeout=460,check=False)
    captures=[]
    for line in (OUT/"performance-runner.log").read_text(errors="replace").splitlines():
        if "UPLOAD=" not in line:continue
        file=Path(line.split("UPLOAD=",1)[1].strip())
        if (file.is_file() and not file.is_symlink() and
            file.parent.resolve()==LOGS.resolve() and
            file.name.startswith("gow-generic-performance-")):
            captures.append(file)
    if not captures:raise RuntimeError(f"GPU/FPS game runner produced no archive rc={rc}")
    file=captures[-1]
    with tarfile.open(file,"r:gz") as archive:
        matches=[m for m in archive if m.isfile() and m.name.endswith("/result.json")]
        if len(matches)!=1 or matches[0].size>160000:
            raise RuntimeError("invalid existing performance archive")
        perf=json.load(archive.extractfile(matches[0]))
    if (perf.get("status")!="CPU_GPU_PROFILE_CAPTURED" or
        perf.get("binary_sha256")!=new_hash or
        perf.get("original_config_hashes_unchanged") is not True or
        perf.get("verified_binary_unchanged") is not True):
        raise RuntimeError("gameplay profile binary/settings parity failed")
    return file,perf

def read_dma_stats(perf_archive):
    with tarfile.open(perf_archive,"r:gz") as outer:
        matches=[m for m in outer if m.isfile() and m.name.endswith("/gameplay-capture.tar.gz")]
        if len(matches)!=1 or matches[0].size>100000000:
            return None
        binary=outer.extractfile(matches[0]).read()
    import io
    with tarfile.open(fileobj=io.BytesIO(binary),mode="r:gz") as inner:
        logs=[m for m in inner if m.isfile() and m.name.endswith("/console.log")]
        if len(logs)!=1 or logs[0].size>160000000:
            return None
        text=inner.extractfile(logs[0]).read().decode(errors="replace")
        matches=list(re.finditer(
          r"DMA CPU-dirty generation: full residency sweeps=(\d+), safely skipped=(\d+)",text))
    if not matches:return None
    scan,skip=map(int,matches[-1].groups())
    return {"full_scans":scan,"redundant_scans_skipped":skip,
            "fraction_skipped":round(skip/(scan+skip),5) if scan+skip else None}

def pack(result):
    save_json(OUT/"result.json",result)
    tarpath=LOGS/(OUT.name+".tar.gz")
    with tarfile.open(tarpath,"w:gz",compresslevel=3) as output:
        for p in sorted(OUT.rglob("*")):
            if p.is_file() and not p.is_symlink() and p.name not in (
                "test-dma-epoch","audio-test","srt-arithmetic-test"):
                output.add(p,arcname=OUT.name+"/"+str(p.relative_to(OUT)))
        capture=result.get("performance_archive")
        if capture:
            p=Path(capture)
            if p.is_file() and not p.is_symlink() and p.parent.resolve()==LOGS.resolve():
                output.add(p,arcname=OUT.name+"/gameplay-performance.tar.gz")
    tell("UPLOAD="+str(tarpath))

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={"status":"STARTED","phase":"PREFLIGHT",
            "upstream_main":UPSTREAM,"general_stack":STACK,"fix_topic":TOPIC,
            "original_rendered_executable":CURRENT,"old_bin_preserved":False,
            "installed_emulator_modified":False,
            "DMA_settings_modified":False,"readbacks_modified":False,
            "game_specific_conditions":False,"FPS_benefit_unproven":True,
            "initial_SynchronizeMemory_calls":146412065}
    install_before=installed()
    try:
        verify_source()
        result["source_manifest"]=apply()
        result["phase"]="TESTS"
        regressions()
        result["native_dirty_epoch_pass"]=8
        result["gcn_pass"]=79
        result["audio_pass"]=5
        result["srt_pass"]=126
        result["phase"]="BUILD"
        tell("BUILD=incremental generic emulator with existing FPS instrumentation")
        run(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
            log=OUT/"build.log",timeout=7200)
        newhash=checksum(BUILD/"shadps4")
        if newhash==CURRENT or checksum(SNAP/"shadps4")!=CURRENT:
            raise RuntimeError("new executable not relinked or original checkpoint changed")
        result["new_executable_sha256"]=newhash
        run(["ldd",BUILD/"shadps4"],log=OUT/"ldd.log",timeout=90)
        if "not found" in (OUT/"ldd.log").read_text(errors="replace"):
            raise RuntimeError("new emulator has missing linked dependencies")
        result["phase"]="GAMEPLAY_FPS"
        perf_archive,perf=run_profile(newhash)
        result["performance_archive"]=str(perf_archive)
        result["performance"]={
            key:perf.get(key) for key in
            ("status","event_counts","resource_summary","guest_exit","image_count",
             "original_config_hashes_unchanged")}
        result["hotpaths"]=read_csv()
        result["fps"]=read_fps()
        result["dma_generation_stats"]=read_dma_stats(perf_archive)
        save_json(OUT/"benchmark-comparison.json",{
            "previous_instrumented_fps":BASELINE_FPS,
            "new_fps":result["fps"],
            "previous_sync_calls":146412065,
            "new_sync_calls":result["hotpaths"]["SynchronizeMemory"]["calls"],
            "new_dma_scan_summary":result["dma_generation_stats"],
            "cpu_average":(perf.get("resource_summary") or {}).get(
                "emulator_cpu_average_percent_all_cores"),
            "gpu_busy_average":(perf.get("resource_summary") or {}).get(
                "gpu_busy_average_percent"),
        })
        result["status"]="DMA_SYNC_AVOIDANCE_TESTED_GRAPHICS_PENDING_VISUAL_REVIEW"
        tell("FPS_COMPARE baseline=6.133 new="+str(result["fps"]["guest"]["actual_fps_70_130"]))
        tell("SYNC_CALLS baseline=146412065 new="+str(result["hotpaths"]["SynchronizeMemory"]["calls"]))
        return 0
    except BaseException as e:
        result["status"]="FAILED"
        result["error"]=repr(e)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if (SNAP/"shadps4").is_file():
            result["old_bin_preserved"]=checksum(SNAP/"shadps4")==CURRENT
        if installed()!=install_before:
            result["status"]="FAILED_INSTALLED_EMULATOR_CHANGED"
        pack(result)

if __name__=="__main__":
    raise SystemExit(main())
