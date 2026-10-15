#!/usr/bin/env python3
"""Build verified GoW105 and run automatic gameplay with normal readbacks and no GoW env."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path.home()/"projects/shadps4/GodOfWarRagnarok"
SCRIPTS=ROOT/"scripts"
TESTS=ROOT/"tests"

GOW105_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
            "b38afe38da4ecc4974d654467fbf8edf849c9660/"
            "tools/gow105-generic-srt-planner.py")
AUTO_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
          "dc2d639610b2dc893ea0f6301c04fc8e8cd56dd4/"
          "tools/gow-auto-gameplay.py")


def fetch(url,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".gow105-auto-",dir=dest.parent);os.close(fd)
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
    b=d/"shadps4"; m=d/"manifest.json"
    if not (b.is_file() and not b.is_symlink() and os.access(b,os.X_OK) and m.is_file()):
        return None
    try:
        obj=json.loads(m.read_text())
    except Exception:
        return None
    p=obj.get("gow105_generic_srt_planner")
    if not isinstance(p,dict):
        return None
    if p.get("default_for_all_shaders") is not True:
        return None
    if p.get("gow_behavioral_env_switches_removed") is not True:
        return None
    if p.get("game_specific") is not False:
        return None
    g104=obj.get("gow104_proven_write_extent")
    if not isinstance(g104,dict):
        return None
    try:
        if g104["buffer_cache"]["bounded_write_ownership"] is not True:
            return None
        if g104["resource_h"]["game_specific"] is not False:
            return None
    except Exception:
        return None
    return b


def newest(after):
    found=[]
    for d in TESTS.glob("gow91-build-*"):
        try:
            b=verify_candidate(d)
            if b is None:
                continue
            stamp=max(b.stat().st_mtime_ns,(d/"manifest.json").stat().st_mtime_ns)
            if stamp>=after:
                found.append((stamp,b))
        except OSError:
            pass
    if len(found)!=1:
        raise RuntimeError(f"Expected exactly one new verified GoW105 candidate, got {len(found)}")
    return found[0][1]


def main():
    SCRIPTS.mkdir(parents=True,exist_ok=True)
    gow=SCRIPTS/"gow105.py"
    auto=SCRIPTS/"gow105-auto-gameplay.py"
    fetch(GOW105_URL,gow)
    fetch(AUTO_URL,auto)

    subprocess.run([sys.executable,"-m","py_compile",str(gow),str(auto)],check=True)
    subprocess.run([sys.executable,str(gow),"--self-test"],check=True)

    mark=time.time_ns()
    subprocess.run([sys.executable,str(gow),"--build-only"],check=True)
    binary=newest(mark)
    digest=sha256(binary)

    subprocess.run([sys.executable,str(auto),"--self-test"],check=True)

    print("GOW105_CANDIDATE="+str(binary),flush=True)
    print("GOW105_SHA256="+digest,flush=True)
    print("GOW105_POLICY=NO_GOW_BEHAVIORAL_ENV; READBACKS_MODE=0",flush=True)
    print("GOW105_AUTOMATION=SCREENSHOT_X_SCREENSHOT_EVERY_10S",flush=True)

    env=os.environ.copy()
    for name in tuple(env):
        if name.startswith("SHADPS4_GOW"):
            env.pop(name,None)

    return subprocess.run([
        sys.executable,str(auto),
        "--binary",str(binary),"--sha256",digest,
        "--seconds","300","--interval","10","--after-delay","1",
        "--readbacks-mode","0",
    ],env=env).returncode


if __name__=="__main__":
    raise SystemExit(main())
