#!/usr/bin/env python3
"""Reuse the already-linked GoW104 candidate after verifying the audit-only failure."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"
CANDIDATE = ROOT / "tests/gow91-build-20261015-084213-3019525"
SCRIPTS = ROOT / "scripts"
AUTO_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
            "eb0275c13ac5ec6ccb27cdde60b956c74b067a99/"
            "tools/gow-auto-gameplay.py")

REQUIRED_MARKERS = {
    "overlay/src/shader_recompiler/resource.h": "struct BufferWriteExtent",
    "overlay/src/shader_recompiler/ir/passes/resource_patching_pass.cpp": "AnalyzeInvocationAffine",
    "overlay/src/video_core/buffer_cache/buffer_cache.cpp": "proven_write_size",
    "overlay/src/video_core/buffer_cache/memory_tracker.h": "MarkRegionAsGpuModified",
    "overlay/src/video_core/renderer_vulkan/vk_rasterizer.cpp": "PROVEN_WRITE_EXTENT",
}

BAD_LOG = re.compile(r"(?:^|\n)(?:FAILED:|.*\berror:|.*fatal error:|.*undefined reference)", re.I)


def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def fetch(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".gow104-resume-", dir=dest.parent)
    os.close(fd)
    try:
        subprocess.run([
            "curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
            "--max-time", "120", url, "-o", tmp
        ], check=True, timeout=380)
        raw = Path(tmp).read_bytes()
        if not raw.startswith(b"#!") or b"\0" in raw:
            raise RuntimeError("Downloaded automation helper has unexpected contents")
        os.replace(tmp, dest)
    finally:
        Path(tmp).unlink(missing_ok=True)


def verify():
    if not CANDIDATE.is_dir() or CANDIDATE.is_symlink():
        raise RuntimeError("Expected GoW104 candidate directory is missing/unsafe")

    result = json.loads((CANDIDATE / "result.json").read_text())
    if result.get("status") != "FAILED":
        raise RuntimeError("Candidate result is not the known audit-only failure")
    error = str(result.get("error", ""))
    if "Earlier corrected header lost:" not in error or "buffer_cache.h" not in error:
        raise RuntimeError("Candidate failed for a reason other than the known preservation audit")

    audit = json.loads((CANDIDATE / "compile-link-audit.json").read_text())
    compilations = audit.get("compilations")
    if not isinstance(compilations, list) or len(compilations) != 79:
        raise RuntimeError(f"Unexpected compilation audit size: {len(compilations or [])}")
    if not isinstance(audit.get("link_command"), list) or not audit["link_command"]:
        raise RuntimeError("Isolated link audit is missing")

    logs = sorted(CANDIDATE.glob("compile-*.log"))
    if len(logs) != 79:
        raise RuntimeError(f"Expected 79 compile logs, found {len(logs)}")
    bad = []
    for path in logs:
        text = path.read_text(errors="replace")
        if BAD_LOG.search(text):
            bad.append(path.name)
    if bad:
        raise RuntimeError("Compile errors found: " + ", ".join(bad[:8]))

    binary = CANDIDATE / "shadps4"
    if not binary.is_file() or binary.is_symlink() or not os.access(binary, os.X_OK):
        raise RuntimeError("Linked GoW104 binary is missing/unsafe")

    for rel, marker in REQUIRED_MARKERS.items():
        p = CANDIDATE / rel
        if not p.is_file() or p.is_symlink():
            raise RuntimeError("Verified GoW104 overlay file missing: " + rel)
        if marker not in p.read_text(errors="replace"):
            raise RuntimeError("GoW104 marker missing from " + rel)

    ldd = subprocess.run(["ldd", str(binary)], text=True, capture_output=True, timeout=30)
    if ldd.returncode != 0 or "not found" in ldd.stdout + ldd.stderr:
        raise RuntimeError("Linked GoW104 binary has unavailable libraries")

    return binary, sha256(binary)


def main():
    binary, digest = verify()

    auto = SCRIPTS / "gow104-auto-gameplay-resume.py"
    fetch(AUTO_URL, auto)
    subprocess.run([sys.executable, "-m", "py_compile", str(auto)], check=True)
    subprocess.run([sys.executable, str(auto), "--self-test"], check=True)

    print("GOW104_REUSED_BINARY=" + str(binary), flush=True)
    print("GOW104_REUSED_SHA256=" + digest, flush=True)
    print("GOW104_BUILD_REUSE=VERIFIED_AUDIT_ONLY_FAILURE", flush=True)
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
