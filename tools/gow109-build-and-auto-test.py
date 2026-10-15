#!/usr/bin/env python3
"""Build clean generic-only GoW109, verify no historical GoW runtime markers, then auto-test."""
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

GOW109_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
              "eca40fe1122eff2acdd1e44b2233228f6668f773/tools/gow109-clean-general.py")
AUTO_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
            "343ae209e22391aff8ca513b536a858bb5ee4bfd/tools/gow-auto-gameplay.py")

BINARY_FORBIDDEN = (
    b"SHADPS4_GOW_", b"GOW43_", b"GOW57_", b"GOW58_", b"GOW_SRT_",
    b"CUSA34384",
)


def fetch(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".gow109-", dir=dest.parent)
    os.close(fd)
    try:
        subprocess.run([
            "curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
            "--max-time", "120", url, "-o", tmp
        ], check=True, timeout=380)
        raw = Path(tmp).read_bytes()
        if not raw.startswith(b"#!") or b"\0" in raw:
            raise RuntimeError("Downloaded helper has unexpected contents")
        os.replace(tmp, dest)
    finally:
        Path(tmp).unlink(missing_ok=True)


def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def binary_marker_audit(binary: Path):
    cp = subprocess.run(["strings", "-a", str(binary)], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=60, check=True)
    hits = [x.decode() for x in BINARY_FORBIDDEN if x in cp.stdout]
    if hits:
        raise RuntimeError("Historical game-specific runtime marker survived clean link: " +
                           ", ".join(hits))


def verify_candidate(directory: Path):
    binary = directory / "shadps4"
    manifest = directory / "manifest.json"
    if not (binary.is_file() and not binary.is_symlink() and
            manifest.is_file() and not manifest.is_symlink()):
        return None
    try:
        obj = json.loads(manifest.read_text())
    except Exception:
        return None

    proof = obj.get("gow109_clean_generic_provenance")
    replaced = obj.get("historical_game_cpp_replaced")
    if not isinstance(proof, dict) or not isinstance(replaced, dict):
        return None
    if obj.get("game_specific") is not False:
        return None
    if obj.get("clean_base_commit") != "c19f883ba41aa48fa39f3db4e1916d4bca8b384e":
        return None
    if obj.get("historical_game_cpp_count") != 11 or len(replaced) != 11:
        return None
    if obj.get("forbidden_game_source_markers_absent") is not True:
        return None
    if obj.get("phase87_generic_metadata_reapplied") is not True:
        return None

    clean = proof.get("clean_historical_source_replacement")
    order = proof.get("generic_srt_order")
    region = proof.get("region_manager")
    memory = proof.get("memory_tracker")
    if not isinstance(clean, dict) or len(clean) != 14:
        return None
    if not all(isinstance(x, dict) for x in (order, region, memory)):
        return None
    if not (order.get("constructed_from_clean_source") is True and
            order.get("game_specific") is False and
            region.get("global_precise_required") is False and
            memory.get("selective_cpu_write_fault_flush") is True):
        return None

    forbidden = (
        b"SHADPS4_GOW_", b"CUSA34384", b"God of War", b"Ragnar",
        b"GOW43_", b"GOW57_", b"GOW58_", b"GOW_SRT_",
        b"0xe83356c8", b"0xad0b8f1f", b"0x04691bd5",
    )
    overlay = directory / "overlay"
    if not overlay.is_dir():
        return None
    for rel in clean:
        p = overlay / rel
        if not p.is_file() or p.is_symlink():
            return None
        data = p.read_bytes()
        if any(marker.lower() in data.lower() for marker in forbidden):
            return None

    for rel, object_path in replaced.items():
        p = Path(object_path)
        if not p.is_file() or not p.is_relative_to(directory):
            return None

    cp = subprocess.run(["ldd", str(binary)], text=True, capture_output=True, timeout=30)
    if cp.returncode != 0 or "not found" in cp.stdout + cp.stderr:
        return None
    binary_marker_audit(binary)
    return binary


def new_candidate(after_ns: int):
    found = []
    for d in TESTS.glob("gow91-build-*"):
        try:
            binary = verify_candidate(d)
            if binary is None:
                continue
            stamp = max(binary.stat().st_mtime_ns, (d / "manifest.json").stat().st_mtime_ns)
            if stamp >= after_ns:
                found.append((stamp, binary))
        except OSError:
            continue
    if len(found) != 1:
        raise RuntimeError(f"Expected exactly one new verified GoW109 candidate, got {len(found)}")
    return found[0][1]


def main():
    SCRIPTS.mkdir(parents=True, exist_ok=True)
    gow = SCRIPTS / "gow109.py"
    auto = SCRIPTS / "gow109-auto.py"
    fetch(GOW109_URL, gow)
    fetch(AUTO_URL, auto)

    subprocess.run([sys.executable, "-m", "py_compile", str(gow), str(auto)], check=True)
    subprocess.run([sys.executable, str(gow), "--self-test"], check=True)
    subprocess.run([sys.executable, str(auto), "--self-test"], check=True)

    mark = time.time_ns()
    subprocess.run([sys.executable, str(gow), "--build-only"], check=True)
    binary = new_candidate(mark)
    digest = sha256(binary)

    print("GOW109_CANDIDATE=" + str(binary), flush=True)
    print("GOW109_SHA256=" + digest, flush=True)
    print("GOW109_SOURCE=GENERIC_CLEAN_C19F_PLUS_PROVEN_GENERIC_FIXES", flush=True)
    print("GOW109_HISTORICAL_GAME_CPP_REPLACED=11", flush=True)
    print("GOW109_BINARY_GAME_MARKERS=NONE", flush=True)
    print("GOW109_READBACKS=0", flush=True)
    print("GOW109_GOW_BEHAVIOR_ENV=NONE", flush=True)
    print("GOW109_AUTOMATION=SCREENSHOT_X_SCREENSHOT_EVERY_10S", flush=True)

    return subprocess.run([
        sys.executable, str(auto),
        "--binary", str(binary),
        "--sha256", digest,
        "--seconds", "300",
        "--interval", "10",
        "--after-delay", "1",
        "--readbacks-mode", "0",
    ]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
