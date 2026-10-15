#!/usr/bin/env python3
"""Build GoW107 and auto-test selective proven-range readback with normal settings."""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, tempfile, time
from pathlib import Path

ROOT=Path.home()/"projects/shadps4/GodOfWarRagnarok"
SCRIPTS=ROOT/"scripts"
TESTS=ROOT/"tests"

GOW107_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
            "f5b6d911db65c6234ff72b5588f079f0bb97bc32/tools/gow107-selective-readback.py")
AUTO_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
          "8d5bebbb95f0f50b05a9310fdf60d6c57127e692/tools/gow-auto-gameplay.py")

def fetch(url,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".gow107-",dir=dest.parent);os.close(fd)
    try:
        subprocess.run(["curl","-4fsSL","--retry","2","--connect-timeout","20",
                        "--max-time","120",url,"-o",tmp],check=True,timeout=380)
        raw=Path(tmp).read_bytes()
        if not raw.startswith(b"#!") or b"\0" in raw:
            raise RuntimeError("Downloaded helper has unexpected contents")
        os.replace(tmp,dest)
    finally:
        Path(tmp).unlink(missing_ok=True)

def sha256(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()

def verify_candidate(d):
    b=d/"shadps4";m=d/"manifest.json"
    if not (b.is_file() and not b.is_symlink() and m.is_file() and not m.is_symlink()):
        return None
    try:
        obj=json.loads(m.read_text())
    except Exception:
        return None
    proof=obj.get("gow107_generic_srt_selective_readback")
    if not isinstance(proof,dict):
        return None
    srt=proof.get("generic_srt_order")
    region=proof.get("region_manager")
    mem=proof.get("memory_tracker")
    if not all(isinstance(x,dict) for x in (srt,region,mem)):
        return None
    if not (srt.get("generic_dependency_planner") is True and
            srt.get("game_env_controls_removed") is True and
            srt.get("game_specific") is False and
            region.get("selective_page_read_watchers") is True and
            region.get("global_precise_required") is False and
            region.get("game_specific") is False and
            mem.get("selective_cpu_read_watchers") is True and
            mem.get("global_precise_required") is False and
            obj.get("game_specific") is False):
        return None

    flat=d/"overlay/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
    rm=d/"overlay/src/video_core/buffer_cache/region_manager.h"
    mt=d/"overlay/src/video_core/buffer_cache/memory_tracker.h"
    bc=d/"overlay/src/video_core/buffer_cache/buffer_cache.cpp"
    rast=d/"overlay/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
    if not all(p.is_file() for p in (flat,rm,mt,bc,rast)):
        return None
    ft=flat.read_text(errors="replace")
    rmt=rm.read_text(errors="replace")
    mtt=mt.read_text(errors="replace")
    bct=bc.read_text(errors="replace")
    rat=rast.read_text(errors="replace")
    if "SHADPS4_GOW_SRT_AUTO_ROOTS" in ft or "SHADPS4_GOW_SRT_SGPR8_FIRST" in ft:
        return None
    if "const auto plan = MakeSrtRootDependencyPlan(pass_info);" not in ft:
        return None
    if "gpu_selective_tracked" not in rmt or "PageOp::None, PageOp::Untrack" not in rmt:
        return None
    if "MarkRegionAsGpuModifiedTracked" not in mtt or "MarkRegionAsGpuModifiedTracked" not in bct:
        return None
    if "SELECTIVE_GPU_READBACK" not in rat:
        return None
    if "SRT_COHERENCY_READBACK" in ft or "WalkerCoherentGuestRead" in ft:
        return None
    return b

def new_candidate(after_ns):
    found=[]
    for d in TESTS.glob("gow91-build-*"):
        try:
            b=verify_candidate(d)
            if b is None:
                continue
            stamp=max(b.stat().st_mtime_ns,(d/"manifest.json").stat().st_mtime_ns)
            if stamp>=after_ns:
                found.append((stamp,b))
        except OSError:
            pass
    if len(found)!=1:
        raise RuntimeError(f"Expected exactly one new verified GoW107 candidate, got {len(found)}")
    return found[0][1]

def main():
    SCRIPTS.mkdir(parents=True,exist_ok=True)
    gow=SCRIPTS/"gow107.py"
    auto=SCRIPTS/"gow107-auto.py"
    fetch(GOW107_URL,gow)
    fetch(AUTO_URL,auto)

    subprocess.run([sys.executable,"-m","py_compile",str(gow),str(auto)],check=True)
    subprocess.run([sys.executable,str(gow),"--self-test"],check=True)
    subprocess.run([sys.executable,str(auto),"--self-test"],check=True)

    mark=time.time_ns()
    subprocess.run([sys.executable,str(gow),"--build-only"],check=True)
    binary=new_candidate(mark)
    digest=sha256(binary)

    print("GOW107_CANDIDATE="+str(binary),flush=True)
    print("GOW107_SHA256="+digest,flush=True)
    print("GOW107_READBACKS=0",flush=True)
    print("GOW107_GOW_BEHAVIOR_ENV=NONE",flush=True)
    print("GOW107_SELECTIVE_READBACK=PROVEN_GPU_WRITTEN_PAGES_ONLY",flush=True)
    print("GOW107_AUTOMATION=SCREENSHOT_X_SCREENSHOT_EVERY_10S",flush=True)

    return subprocess.run([
        sys.executable,str(auto),
        "--binary",str(binary),"--sha256",digest,
        "--seconds","300","--interval","10","--after-delay","1",
        "--readbacks-mode","0",
    ]).returncode

if __name__=="__main__":
    raise SystemExit(main())
