#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the tiling-uniform correction locally, then launch PES with image capture."""

import fcntl
import json
import os
from pathlib import Path
import re
import sys

import install_local_default as installer
import pes_current_baseline as baseline
import run_pes_image_test as images

REVISION = "1522b405a109f9f5f1db3c0852b62c1e27c2539b"
BRANCH = "fix/tiling-uniform-width"


def check_spirv(words):
    if len(words) < 5 or words[0] != 0x07230203:
        raise RuntimeError("Invalid generated SPIR-V header")
    offset = 5
    capabilities = set()
    while offset < len(words):
        size, opcode = words[offset] >> 16, words[offset] & 0xffff
        if size == 0 or offset + size > len(words):
            raise RuntimeError("Invalid generated SPIR-V instruction")
        if opcode == 17:
            if size != 2:
                raise RuntimeError("Invalid SPIR-V capability declaration")
            capabilities.add(words[offset + 1])
        offset += size
    if 1 not in capabilities or 4434 in capabilities:
        raise RuntimeError("Tiling shader still requires disabled 16-bit uniform-buffer access")


def build_checked(home, revision, branch):
    binary = installer.build(home, revision, branch)
    headers = binary.parent / "src/video_core/host_shaders/include/video_core/host_shaders"
    for mode in ("micro", "macro"):
        for bpp in (8, 16, 32, 64, 96, 128):
            path = headers / f"tiling_{mode}_{bpp}_comp.h"
            words = [int(value, 16) for value in re.findall(r"0x[0-9a-fA-F]+", path.read_text())]
            check_spirv(words)
    print("PES_TILING_SHADER_GATE=PASS; all 12 built shaders avoid the disabled capability", flush=True)
    return binary


def run(home, manifest):
    manifest = dict(manifest, candidate_fix={
        "revision": REVISION, "source_branch": BRANCH,
        "change": "Read packed tiling extents with 32-bit uniform loads",
        "pes_runtime_result": "unverified",
    })
    baseline.prepare(home, REVISION, BRANCH, manifest, build_checked)
    images.run_validated(home)


def main():
    if os.geteuid() == 0 or sys.argv[1:]:
        raise RuntimeError("Run as chreece without sudo or arguments")
    home = Path.home()
    manifest = json.loads(Path(__file__).with_name("LOCAL_TEST_BASELINE.json").read_text())
    deploy_root = home / ".local/state/shadps4-ngs2"
    deploy_root.mkdir(parents=True, exist_ok=True)
    cache = home / ".cache"
    cache.mkdir(exist_ok=True)
    with (deploy_root / "deploy.lock").open("a") as deploy_lock, \
            (cache / "shadps4-video-trace.lock").open("a") as trace_lock:
        fcntl.flock(deploy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(trace_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(home, manifest)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print("PES_TILING_TEST=FAIL: " + (str(error) or "Interrupted"), flush=True)
        sys.exit(1)
    finally:
        print("Returning to your existing SSH prompt.", flush=True)
