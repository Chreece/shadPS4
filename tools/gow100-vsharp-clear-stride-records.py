#!/usr/bin/env python3
"""GoW100: prove upstream #5352 is already in the retained GOW84 baseline.

No #5352 source is modified here.  The retained GOW84 source descends from an
upstream main snapshot after shadps4-emu/shadPS4#5352, so the only new runtime
delta remains GoW98's general ReadFirstLane SRT support on top of GoW97/96/91.
Readbacks stay OFF and controls stay manual.
"""
from __future__ import annotations
import argparse, hashlib, os, subprocess, sys, tempfile, types
from pathlib import Path

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"
GOW98_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
             "a3bdf3fc4c761d368064efc0fb67979f31b318a0/tools/gow98-srt-readfirstlane.py")
GOW98_BLOB = "50b6c3417e6cc44b46e84ad4fb6b5b60132afa37"

DISCOVER = "src/shader_recompiler/ir/passes/resource_discover_pass.cpp"
RES_PASS = "src/shader_recompiler/ir/passes/resource_pass.h"
RES_PATCH = "src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
RES_HEADER = "src/shader_recompiler/resource.h"

def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def load_gow98():
    scripts = ROOT / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    p = scripts / "gow98-pinned-for-gow100.py"
    if not p.is_file() or p.is_symlink() or blob_sha(p.read_bytes()) != GOW98_BLOB:
        fd, name = tempfile.mkstemp(prefix=".gow100-", dir=scripts)
        os.close(fd)
        try:
            subprocess.run([
                "curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
                "--max-time", "120", GOW98_URL, "-o", name
            ], check=True, timeout=380)
            data = Path(name).read_bytes()
            if blob_sha(data) != GOW98_BLOB:
                raise RuntimeError("Pinned GoW98 helper fingerprint mismatch")
            if p.is_symlink():
                raise RuntimeError("Refusing symlinked helper")
            os.replace(name, p)
        finally:
            Path(name).unlink(missing_ok=True)
    m = types.ModuleType("gow100_gow98")
    m.__file__ = str(p)
    exec(compile(p.read_bytes(), str(p), "exec"), m.__dict__)
    return m

def retained_source(gow91):
    phase90 = gow91.get_helper()
    phase89 = phase90.get_helper()
    base = gow91.get_base(phase89)
    src = (base.BASE84 / "source").resolve()
    if not src.is_dir() or src.is_symlink():
        raise RuntimeError("Retained GOW84 source missing/unsafe")
    return src

def verify_5352(gow91):
    src = retained_source(gow91)
    data = {}
    for rel in (DISCOVER, RES_PASS, RES_PATCH, RES_HEADER):
        p = src / rel
        if not p.is_file() or p.is_symlink():
            raise RuntimeError("Missing/unsafe retained #5352 source: " + rel)
        data[rel] = p.read_text()

    checks = (
        (data[DISCOVER].count("CheckClearStrideAndPatchRecordsPattern") == 2,
         "resource discovery helper + call"),
        ("vsharp.post_op = SharpFetchPostOp::ClearStrideAndBitshiftDw2WithImm;" in data[DISCOVER],
         "resource discovery post-op"),
        ("vsharp.post_op_data.dw2_shift = shift;" in data[DISCOVER],
         "resource discovery shift capture"),
        (data[RES_PASS].count("u32 dw2_shift;") == 1,
         "PostOpData dw2_shift"),
        ("post_op_dw2_shift = resource.sharps[0].post_op_data.dw2_shift" in data[RES_PATCH],
         "PatchBufferSharp dw2 propagation"),
        (data[RES_HEADER].count("ClearStrideAndBitshiftDw2WithImm") == 2,
         "BufferResource enum + dispatch"),
        (data[RES_HEADER].count("u32 post_op_dw2_shift{};") == 1,
         "BufferResource dedicated shift field"),
        ("buffer.stride = 0;" in data[RES_HEADER],
         "BufferResource stride clear"),
        ("buffer.num_records <<= post_op_dw2_shift;" in data[RES_HEADER],
         "BufferResource record shift"),
    )
    missing = [name for ok, name in checks if not ok]
    if missing:
        raise RuntimeError("Retained GOW84 #5352 is incomplete: " + ", ".join(missing))
    return src

def self_test(gow97, gow96, gow91):
    src = verify_5352(gow91)
    # GOW98 has already monkey-patched gow97.adapt_flatten.  Calling the retained
    # GoW97 self-test therefore exercises ReadFirstLane + stack-balance + Phi +
    # Phase91 checks against the real retained source.
    gow97.self_test(gow96, gow91)
    print("GOW100_SELF_TEST=PASS #5352 already complete in retained GOW84; "
          "next runtime delta is GOW98 ReadFirstLane only; no game launched", flush=True)
    print("GOW100_BASELINE_SOURCE=" + str(src), flush=True)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    gow98 = load_gow98()
    gow97 = gow98.load_gow97()
    gow98.install_readfirstlane_support(gow97)
    gow96 = gow97.load_gow96()
    gow91 = gow96.load_gow91()

    if args.self_test:
        self_test(gow97, gow96, gow91)
        return 0

    verify_5352(gow91)
    old = sys.argv[:]
    try:
        sys.argv = ["gow98"] + (["--build-only"] if args.build_only else []) + (["--restore"] if args.restore else [])
        print("GOW100=NO_NEW_5352_PATCH; BASELINE_5352=VERIFIED; "
              "RUNNING=GOW98_READFIRSTLANE; READBACKS=OFF; MANUAL_CONTROL", flush=True)
        # Equivalent to gow98.main(), but reuse the already-loaded and patched
        # gow97 module so there is only one pinned helper chain in this process.
        return gow97.main()
    finally:
        sys.argv = old

if __name__ == "__main__":
    raise SystemExit(main())
