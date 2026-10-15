#!/usr/bin/env python3
"""Reuse the newest verified GoW103 candidate and run the fixed automatic gameplay harness."""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT=Path.home()/"projects/shadps4/GodOfWarRagnarok"
SCRIPTS=ROOT/"scripts"
TESTS=ROOT/"tests"
AUTO_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
          "682042a9b85fe0d018aa8824327c700fd04fca94/tools/gow-auto-gameplay.py")


def fetch(url: str, dest: Path):
    dest.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".gow103-resume-",dir=dest.parent);os.close(fd)
    try:
        subprocess.run(["curl","-4fsSL","--retry","2","--connect-timeout","20",
                        "--max-time","120",url,"-o",tmp],check=True,timeout=380)
        data=Path(tmp).read_bytes()
        if not data.startswith(b"#!") or b"\0" in data:
            raise RuntimeError("Downloaded automation helper has unexpected contents")
        os.replace(tmp,dest)
    finally:
        Path(tmp).unlink(missing_ok=True)


def candidate_proof(d: Path):
    m=d/"manifest.json"; b=d/"shadps4"
    if not m.is_file() or m.is_symlink() or not b.is_file() or b.is_symlink():
        return None
    try:
        obj=json.loads(m.read_text())
    except Exception:
        return None
    proof=obj.get("gow97_srt_stack_fix")
    if not isinstance(proof,dict):
        return None
    required=(
        "dynamic_left_shift_fixed","bfe_width32_fixed",
        "shift_right_arithmetic32","findilsb32","failed_codegen_rollback",
        "retained_gow61_fixes_reused",
    )
    if not all(proof.get(k) is True for k in required):
        return None
    if proof.get("game_specific") is not False:
        return None
    return obj


def newest_candidate():
    matches=[]
    for d in TESTS.glob("gow91-build-*"):
        try:
            proof=candidate_proof(d)
            if proof is None:
                continue
            matches.append((d.stat().st_mtime_ns,d))
        except OSError:
            continue
    if not matches:
        raise RuntimeError("No verified GoW103 candidate found")
    matches.sort(reverse=True)
    return matches[0][1]


def sha256(p: Path):
    with p.open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()


def main():
    d=newest_candidate()
    b=d/"shadps4"
    digest=sha256(b)
    auto=SCRIPTS/"gow-auto-gameplay-fixed.py"
    fetch(AUTO_URL,auto)
    subprocess.run([sys.executable,"-m","py_compile",str(auto)],check=True)
    subprocess.run([sys.executable,str(auto),"--self-test"],check=True)
    print("GOW103_REUSE_CANDIDATE="+str(b),flush=True)
    print("GOW103_REUSE_SHA256="+digest,flush=True)
    return subprocess.run([
        sys.executable,str(auto),
        "--binary",str(b),"--sha256",digest,
        "--seconds","300","--interval","10","--after-delay","1",
    ]).returncode


if __name__=="__main__":
    raise SystemExit(main())
