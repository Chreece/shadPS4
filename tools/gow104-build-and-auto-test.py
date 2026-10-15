#!/usr/bin/env python3
"""Build verified GoW104 then run automatic Precise-coherence gameplay validation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"

GOW104_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
              "c70c4cade53fc82d4212f55157afd79af1830e4a/"
              "tools/gow104-proven-write-extent.py")
AUTO_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
            "eb0275c13ac5ec6ccb27cdde60b956c74b067a99/"
            "tools/gow-auto-gameplay.py")


def fetch(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".gow104-auto-", dir=dest.parent)
    os.close(fd)
    try:
        subprocess.run([
            "curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
            "--max-time", "120", url, "-o", tmp
        ], check=True, timeout=380)
        data = Path(tmp).read_bytes()
        if not data.startswith(b"#!") or b"\0" in data:
            raise RuntimeError("Downloaded helper has unexpected contents")
        os.replace(tmp, dest)
    finally:
        Path(tmp).unlink(missing_ok=True)


def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def verify_candidate(path: Path):
    binary = path / "shadps4"
    manifest = path / "manifest.json"
    if not binary.is_file() or binary.is_symlink() or not manifest.is_file() or manifest.is_symlink():
        return None
    try:
        obj = json.loads(manifest.read_text())
    except Exception:
        return None
    proof = obj.get("gow104_proven_write_extent")
    if not isinstance(proof, dict):
        return None
    required = ("resource_h", "resource_patch", "buffer_cache", "memory_tracker", "rasterizer")
    if not all(isinstance(proof.get(k), dict) for k in required):
        return None
    if proof["resource_h"].get("game_specific") is not False:
        return None
    if proof["resource_patch"].get("game_specific") is not False:
        return None
    if proof["resource_h"].get("arbitrary_cap") is not False:
        return None
    if proof["buffer_cache"].get("bounded_write_ownership") is not True:
        return None
    if proof["rasterizer"].get("runtime_dispatch_bound") is not True:
        return None
    if obj.get("game_specific") is not False:
        return None
    return obj


def new_candidate(after_ns: int) -> Path:
    matches = []
    for d in TESTS.glob("gow91-build-*"):
        try:
            proof = verify_candidate(d)
            if proof is None:
                continue
            newest = max((d / "shadps4").stat().st_mtime_ns,
                         (d / "manifest.json").stat().st_mtime_ns)
            if newest >= after_ns:
                matches.append((newest, d))
        except OSError:
            continue
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one new verified GoW104 candidate, got {len(matches)}")
    return matches[0][1]


def main():
    SCRIPTS.mkdir(parents=True, exist_ok=True)
    gow = SCRIPTS / "gow104.py"
    auto = SCRIPTS / "gow104-auto-gameplay.py"
    fetch(GOW104_URL, gow)
    fetch(AUTO_URL, auto)

    subprocess.run([sys.executable, "-m", "py_compile", str(gow), str(auto)], check=True)
    subprocess.run([sys.executable, str(gow), "--self-test"], check=True)

    mark = time.time_ns()
    subprocess.run([sys.executable, str(gow), "--build-only"], check=True)
    candidate = new_candidate(mark)
    binary = candidate / "shadps4"
    digest = sha256(binary)

    subprocess.run([sys.executable, str(auto), "--self-test"], check=True)

    print("GOW104_CANDIDATE=" + str(binary), flush=True)
    print("GOW104_SHA256=" + digest, flush=True)
    print("GOW104_VALIDATION=PRECISE_READBACKS_WITH_PROVEN_WRITE_EXTENTS", flush=True)
    print("GOW104_AUTOMATION=SCREENSHOT_X_SCREENSHOT_EVERY_10S", flush=True)

    return subprocess.run([
        sys.executable, str(auto),
        "--binary", str(binary),
        "--sha256", digest,
        "--seconds", "300",
        "--interval", "10",
        "--after-delay", "1",
        "--readbacks-mode", "2",
    ]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
