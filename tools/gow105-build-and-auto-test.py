#!/usr/bin/env python3
"""Build GoW105 and automatically validate it with normal readbacks and no GoW knobs."""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, tempfile, time
from pathlib import Path

ROOT=Path.home()/"projects/shadps4/GodOfWarRagnarok"
SCRIPTS=ROOT/"scripts"
TESTS=ROOT/"tests"

GOW105_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
            "25ae58836e9a408d312921c5c722aac0be764750/"
            "tools/gow105-generic-srt-coherence.py")
AUTO_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
          "bf2b206c9d8bbe47eb62817d3b4ec113d4d2d18c/"
          "tools/gow105-auto-gameplay.py")


def fetch(url,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".gow105-",dir=dest.parent);os.close(fd)
    try:
        subprocess.run(["curl","-4fsSL","--retry","2","--connect-timeout","20",
                        "--max-time","120",url,"-o",tmp],check=True,timeout=380)
        raw=Path(tmp).read_bytes()
        if not raw.startswith(b"#!") or b"\0" in raw:
            raise RuntimeError("Downloaded helper has unexpected contents")
        os.replace(tmp,dest)
    finally:
        Path(tmp).unlink(missing_ok=True)


def sha256(p):
    with Path(p).open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()


def verify_candidate(d):
    b=d/"shadps4";m=d/"manifest.json"
    if not (b.is_file() and not b.is_symlink() and os.access(b,os.X_OK) and m.is_file()):
        return None
    try:
        obj=json.loads(m.read_text())
    except Exception:
        return None
    p=obj.get("gow105_generic_srt_coherence")
    if not isinstance(p,dict):
        return None
    gs=p.get("generic_srt",{})
    rw=p.get("srt_coherence_wiring",{})
    if not (
        gs.get("generic_dependency_ordering") is True and
        gs.get("behavioral_gow_srt_switches_removed") is True and
        gs.get("targeted_srt_guest_coherence") is True and
        gs.get("global_precise_readbacks_required") is False and
        gs.get("game_specific") is False and
        rw.get("global_precise_readbacks_required") is False and
        rw.get("game_specific") is False and
        obj.get("behavioral_gow_env_required") is False and
        obj.get("game_specific") is False
    ):
        return None
    return b


def new_candidate(mark):
    found=[]
    for d in TESTS.glob("gow91-build-*"):
        try:
            b=verify_candidate(d)
            if b is None:
                continue
            stamp=max(b.stat().st_mtime_ns,(d/"manifest.json").stat().st_mtime_ns)
            if stamp>=mark:
                found.append((stamp,b))
        except OSError:
            pass
    if len(found)!=1:
        raise RuntimeError(f"Expected exactly one verified GoW105 candidate, got {len(found)}")
    return found[0][1]


def main():
    SCRIPTS.mkdir(parents=True,exist_ok=True)
    gow=SCRIPTS/"gow105.py"
    auto=SCRIPTS/"gow105-auto-gameplay.py"
    fetch(GOW105_URL,gow)
    fetch(AUTO_URL,auto)

    subprocess.run([sys.executable,"-m","py_compile",str(gow),str(auto)],check=True)
    subprocess.run([sys.executable,str(gow),"--self-test"],check=True)
    subprocess.run([sys.executable,str(auto),"--self-test"],check=True)

    mark=time.time_ns()
    subprocess.run([sys.executable,str(gow),"--build-only"],check=True)
    binary=new_candidate(mark)
    digest=sha256(binary)

    print("GOW105_BINARY="+str(binary),flush=True)
    print("GOW105_SHA256="+digest,flush=True)
    print("GOW105_FIX=GENERIC_SRT_ORDER+TARGETED_SRT_COHERENCE",flush=True)
    print("GOW105_READBACKS_MODE=0",flush=True)
    print("GOW105_GOW_BEHAVIOR_ENV=NONE",flush=True)
    print("GOW105_AUTOMATION=SCREENSHOT_X_SCREENSHOT_EVERY_10S",flush=True)

    env=os.environ.copy()
    for key in tuple(env):
        if key.startswith("SHADPS4_GOW_"):
            env.pop(key,None)

    return subprocess.run([
        sys.executable,str(auto),
        "--binary",str(binary),"--sha256",digest,
        "--seconds","300","--interval","10","--after-delay","1",
        "--readbacks-mode","0",
    ],env=env).returncode


if __name__=="__main__":
    raise SystemExit(main())
