#!/usr/bin/env python3
"""Build GoW106 and auto-test with normal readbacks and zero GoW behavior env."""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, tempfile, time
from pathlib import Path

ROOT=Path.home()/"projects/shadps4/GodOfWarRagnarok"
SCRIPTS=ROOT/"scripts"
TESTS=ROOT/"tests"

GOW106_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
            "a0f784632496651fecb30dbd845811048cfce517/tools/gow106-targeted-srt-coherence.py")
AUTO_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
          "8d5bebbb95f0f50b05a9310fdf60d6c57127e692/tools/gow-auto-gameplay.py")

def fetch(url,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".gow106-",dir=dest.parent);os.close(fd)
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
    proof=obj.get("gow106_generic_srt_targeted_coherence")
    if not isinstance(proof,dict):
        return None
    srt=proof.get("generic_srt_order")
    coh=proof.get("targeted_srt_coherence")
    coh_vk=proof.get("targeted_srt_rasterizer")
    if not all(isinstance(x,dict) for x in (srt,coh,coh_vk)):
        return None
    if not (srt.get("generic_dependency_planner") is True and
            srt.get("game_env_controls_removed") is True and
            srt.get("game_specific") is False and
            coh.get("global_readbacks") is False and
            coh.get("game_specific") is False and
            coh_vk.get("global_readbacks") is False and
            coh_vk.get("game_specific") is False):
        return None
    if obj.get("game_specific") is not False:
        return None

    flat=d/"overlay/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
    raster=d/"overlay/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
    header=d/"overlay/src/shader_recompiler/ir/passes/srt_coherency.h"
    if not (flat.is_file() and raster.is_file() and header.is_file()):
        return None
    ft=flat.read_text(errors="replace")
    rt=raster.read_text(errors="replace")
    if "SHADPS4_GOW_SRT_AUTO_ROOTS" in ft or "SHADPS4_GOW_SRT_SGPR8_FIRST" in ft:
        return None
    if "const auto plan = MakeSrtRootDependencyPlan(pass_info);" not in ft:
        return None
    if "SRT_COHERENCY_READBACK" not in ft or "WalkerCoherentGuestRead" not in ft:
        return None
    if "SetWalkerMemoryCallbacks" not in rt or "ReadMemory(addr, size, false, true)" not in rt:
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
        raise RuntimeError(f"Expected exactly one new verified GoW106 candidate, got {len(found)}")
    return found[0][1]

def main():
    SCRIPTS.mkdir(parents=True,exist_ok=True)
    gow=SCRIPTS/"gow106.py"
    auto=SCRIPTS/"gow106-auto.py"
    fetch(GOW106_URL,gow)
    fetch(AUTO_URL,auto)

    subprocess.run([sys.executable,"-m","py_compile",str(gow),str(auto)],check=True)
    subprocess.run([sys.executable,str(gow),"--self-test"],check=True)
    subprocess.run([sys.executable,str(auto),"--self-test"],check=True)

    mark=time.time_ns()
    subprocess.run([sys.executable,str(gow),"--build-only"],check=True)
    binary=new_candidate(mark)
    digest=sha256(binary)

    print("GOW106_CANDIDATE="+str(binary),flush=True)
    print("GOW106_SHA256="+digest,flush=True)
    print("GOW106_READBACKS=0",flush=True)
    print("GOW106_GOW_BEHAVIOR_ENV=NONE",flush=True)
    print("GOW106_COHERENCE=TARGETED_SRT_GPU_AUTHORED_PAGES_ONLY",flush=True)
    print("GOW106_AUTOMATION=SCREENSHOT_X_SCREENSHOT_EVERY_10S",flush=True)

    return subprocess.run([
        sys.executable,str(auto),
        "--binary",str(binary),"--sha256",digest,
        "--seconds","300","--interval","10","--after-delay","1",
        "--readbacks-mode","0",
    ]).returncode

if __name__=="__main__":
    raise SystemExit(main())
