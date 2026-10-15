#!/usr/bin/env python3
"""Reuse a verified linked GoW104 candidate when possible; otherwise rebuild it, then auto-test."""
from __future__ import annotations
import hashlib, json, os, pathlib, re, subprocess, sys, tempfile, time
from pathlib import Path

ROOT=Path.home()/"projects/shadps4/GodOfWarRagnarok"
TESTS=ROOT/"tests"
SCRIPTS=ROOT/"scripts"
KNOWN=TESTS/"gow91-build-20261015-084213-3019525"

GOW104_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
            "e1452660f124f7613b0c92d9a179750540fbad65/tools/gow104-proven-write-extent.py")
AUTO_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
          "eb0275c13ac5ec6ccb27cdde60b956c74b067a99/tools/gow-auto-gameplay.py")

REQUIRED_CPP={
 "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
 "src/shader_recompiler/ir/passes/resource_patching_pass.cpp",
 "src/video_core/buffer_cache/buffer_cache.cpp",
 "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
 "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp",
}
PATCH_MARKERS=(
 "struct BufferWriteExtent",
 "AnalyzeInvocationAffine",
 "proven_write_size",
 "ComputeProvenWriteSize",
 "PROVEN_WRITE_EXTENT",
)

def fetch(url,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".gow104-ror-",dir=dest.parent);os.close(fd)
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

def ldd_ok(binary):
    cp=subprocess.run(["ldd",str(binary)],text=True,capture_output=True,timeout=30)
    return cp.returncode==0 and "not found" not in (cp.stdout+cp.stderr)

def verify_linked_candidate(d):
    binary=d/"shadps4"
    audit_path=d/"compile-link-audit.json"
    patch_path=d/"gow104-proven-write-extent.patch"
    if not (d.is_dir() and binary.is_file() and not binary.is_symlink() and
            os.access(binary,os.X_OK) and audit_path.is_file() and patch_path.is_file()):
        return None

    audit=json.loads(audit_path.read_text())
    comps=audit.get("compilations")
    link=audit.get("link_command")
    if not isinstance(comps,list) or len(comps)!=79 or not isinstance(link,list) or not link:
        return None

    compiled_overlay=set()
    for rec in comps:
        cmd=rec.get("command")
        if not isinstance(cmd,list):
            return None
        for arg in cmd:
            if isinstance(arg,str) and "/overlay/" in arg and arg.endswith(".cpp"):
                try:
                    rel=arg.split("/overlay/",1)[1]
                    compiled_overlay.add(rel)
                except Exception:
                    pass
    if not REQUIRED_CPP.issubset(compiled_overlay):
        return None

    out_index=[i for i,x in enumerate(link[:-1]) if x=="-o"]
    if not out_index:
        return None
    linked_out=Path(link[out_index[-1]+1])
    if linked_out.resolve()!=binary.resolve():
        return None

    patch=patch_path.read_text(errors="replace")
    if not all(m in patch for m in PATCH_MARKERS):
        return None

    logs=sorted(d.glob("compile-*.log"))
    if len(logs)!=79:
        return None
    bad=re.compile(r"(?:^|\n)(?:FAILED:|.*\berror:|.*fatal error:|.*undefined reference)",re.I)
    if any(bad.search(p.read_text(errors="replace")) for p in logs):
        return None
    if not ldd_ok(binary):
        return None
    return binary

def verify_manifest_candidate(d):
    b=d/"shadps4"; m=d/"manifest.json"
    if not (b.is_file() and not b.is_symlink() and os.access(b,os.X_OK) and m.is_file()):
        return None
    try:
        obj=json.loads(m.read_text())
    except Exception:
        return None
    p=obj.get("gow104_proven_write_extent")
    if not isinstance(p,dict):
        return None
    try:
        ok=(p["resource_h"]["game_specific"] is False and
            p["resource_h"]["arbitrary_cap"] is False and
            p["resource_patch"]["game_specific"] is False and
            p["buffer_cache"]["bounded_write_ownership"] is True and
            p["rasterizer"]["runtime_dispatch_bound"] is True and
            obj.get("game_specific") is False)
    except Exception:
        return None
    if not ok or not ldd_ok(b):
        return None
    return b

def newest_manifest_candidate(after_ns=0):
    found=[]
    for d in TESTS.glob("gow91-build-*"):
        try:
            b=verify_manifest_candidate(d)
            if b is None:
                continue
            stamp=max(b.stat().st_mtime_ns,(d/"manifest.json").stat().st_mtime_ns)
            if stamp>=after_ns:
                found.append((stamp,b))
        except OSError:
            pass
    found.sort(reverse=True)
    return found[0][1] if found else None

def main():
    SCRIPTS.mkdir(parents=True,exist_ok=True)
    auto=SCRIPTS/"gow104-auto-fixed.py"
    fetch(AUTO_URL,auto)
    subprocess.run([sys.executable,"-m","py_compile",str(auto)],check=True)
    subprocess.run([sys.executable,str(auto),"--self-test"],check=True)

    binary=verify_linked_candidate(KNOWN)
    mode="REUSED_LINKED_CANDIDATE"

    if binary is None:
        gow=SCRIPTS/"gow104-fixed.py"
        fetch(GOW104_URL,gow)
        subprocess.run([sys.executable,"-m","py_compile",str(gow)],check=True)
        subprocess.run([sys.executable,str(gow),"--self-test"],check=True)
        mark=time.time_ns()
        subprocess.run([sys.executable,str(gow),"--build-only"],check=True)
        binary=newest_manifest_candidate(mark)
        if binary is None:
            raise RuntimeError("Corrected GoW104 build finished without a verifiable candidate")
        mode="REBUILT_WITH_CORRECTED_AUDIT"

    digest=sha256(binary)
    print("GOW104_BINARY="+str(binary),flush=True)
    print("GOW104_SHA256="+digest,flush=True)
    print("GOW104_MODE="+mode,flush=True)
    print("GOW104_VALIDATION=PRECISE_READBACKS_WITH_PROVEN_WRITE_EXTENTS",flush=True)
    print("GOW104_AUTOMATION=SCREENSHOT_X_SCREENSHOT_EVERY_10S",flush=True)

    return subprocess.run([
        sys.executable,str(auto),
        "--binary",str(binary),"--sha256",digest,
        "--seconds","300","--interval","10","--after-delay","1",
        "--readbacks-mode","2",
    ]).returncode

if __name__=="__main__":
    raise SystemExit(main())
