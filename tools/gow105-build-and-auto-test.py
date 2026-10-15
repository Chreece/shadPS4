#!/usr/bin/env python3
"""Build verified GoW105 and run clean automatic gameplay validation."""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, tempfile, time
from pathlib import Path

ROOT=Path.home()/"projects/shadps4/GodOfWarRagnarok"
SCRIPTS=ROOT/"scripts"
TESTS=ROOT/"tests"

GOW105_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
            "3f2662db8af9a85390a5fb2c7f4b2a4ab45cb3e6/"
            "tools/gow105-generic-srt-coherency.py")
AUTO_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
          "2c1dc100ed7b5a32ef51445778e7b15669a79508/"
          "tools/gow-auto-gameplay.py")


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
    p=obj.get("gow105_generic_srt_coherency")
    a=obj.get("gow105_acceptance")
    if not isinstance(p,dict) or not isinstance(a,dict):
        return None
    try:
        ok=(p["game_specific"] is False and
            p["flatten"]["game_specific"] is False and
            p["flatten"]["global_precise_required"] is False and
            p["flatten"]["ordering"]["behavioral_env_switches"] is False and
            p["rasterizer"]["game_specific"] is False and
            a["readbacks_mode"]==0 and
            a["SHADPS4_GOW_behavior_env"] is False and
            a["game_specific"] is False)
    except Exception:
        return None
    return b if ok else None


def newest(after_ns):
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
    print("GOW105_READBACKS_MODE=0",flush=True)
    print("GOW105_SHADPS4_GOW_ENV=NONE",flush=True)
    print("GOW105_AUTOMATION=SCREENSHOT_X_SCREENSHOT_EVERY_10S",flush=True)

    return subprocess.run([
        sys.executable,str(auto),
        "--binary",str(binary),"--sha256",digest,
        "--seconds","300","--interval","10","--after-delay","1",
        "--readbacks-mode","0",
    ]).returncode


if __name__=="__main__":
    raise SystemExit(main())
