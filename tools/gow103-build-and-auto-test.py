#!/usr/bin/env python3
"""Build GoW103 and immediately run the bounded automatic gameplay probe."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

HOME=Path.home()
ROOT=HOME/"projects/shadps4/GodOfWarRagnarok"
SCRIPTS=ROOT/"scripts"
TESTS=ROOT/"tests"

GOW103_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
            "d79177fea1d9a3ae8a48e0b43b368c23ce16cd25/tools/gow103-srt-arithmetic.py")
AUTO_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
          "508b2c08480d30306950a087a57cb40be89d2200/tools/gow-auto-gameplay.py")


def fetch(url: str, dest: Path):
    dest.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".gow103-auto-",dir=dest.parent);os.close(fd)
    try:
        subprocess.run(["curl","-4fsSL","--retry","2","--connect-timeout","20",
                        "--max-time","120",url,"-o",tmp],check=True,timeout=380)
        data=Path(tmp).read_bytes()
        if not data.startswith(b"#!") or b"\0" in data:
            raise RuntimeError("Downloaded helper has unexpected contents")
        os.replace(tmp,dest)
    finally:
        Path(tmp).unlink(missing_ok=True)


def newest_candidate(mark_ns: int) -> Path:
    found=[]
    for d in TESTS.glob("gow91-build-*"):
        try:
            if not d.is_dir() or d.is_symlink():
                continue
            b=d/"shadps4"; m=d/"manifest.json"
            if not b.is_file() or b.is_symlink() or not m.is_file() or m.is_symlink():
                continue
            if max(b.stat().st_mtime_ns,m.stat().st_mtime_ns) < mark_ns:
                continue
            found.append(d)
        except OSError:
            continue
    if len(found)!=1:
        raise RuntimeError(f"Expected exactly one new GoW103 candidate, found {len(found)}: {found}")
    return found[0]


def verify_candidate(d: Path):
    manifest=json.loads((d/"manifest.json").read_text())
    proof=manifest.get("gow97_srt_stack_fix")
    if not isinstance(proof,dict):
        raise RuntimeError("GoW103 proof missing from manifest")
    required=("dynamic_left_shift_fixed","bfe_width32_fixed",
              "shift_right_arithmetic32","findilsb32","failed_codegen_rollback")
    missing=[k for k in required if proof.get(k) is not True]
    if missing:
        raise RuntimeError("GoW103 proof incomplete: "+", ".join(missing))
    if proof.get("game_specific") is not False:
        raise RuntimeError("GoW103 unexpectedly contains game-specific arithmetic logic")
    b=d/"shadps4"
    with b.open("rb") as f:
        digest=hashlib.file_digest(f,"sha256").hexdigest()
    return b,digest


def main():
    SCRIPTS.mkdir(parents=True,exist_ok=True)
    gow=SCRIPTS/"gow103.py"
    auto=SCRIPTS/"gow-auto-gameplay.py"
    fetch(GOW103_URL,gow)
    fetch(AUTO_URL,auto)

    subprocess.run([sys.executable,"-m","py_compile",str(gow),str(auto)],check=True)
    subprocess.run([sys.executable,str(gow),"--self-test"],check=True)

    # Nanosecond marker avoids accidentally selecting an older candidate.
    mark_ns=time.time_ns()
    subprocess.run([sys.executable,str(gow),"--build-only"],check=True)
    candidate=newest_candidate(mark_ns)
    binary,digest=verify_candidate(candidate)

    subprocess.run([sys.executable,str(auto),"--self-test"],check=True)
    print("GOW103_AUTO_CANDIDATE="+str(binary),flush=True)
    print("GOW103_AUTO_SHA256="+digest,flush=True)
    print("GOW103_AUTO_TEST=300s screenshot -> X -> screenshot every 10s",flush=True)
    return subprocess.run([
        sys.executable,str(auto),
        "--binary",str(binary),"--sha256",digest,
        "--seconds","300","--interval","10","--after-delay","1"
    ]).returncode


if __name__=="__main__":
    raise SystemExit(main())
