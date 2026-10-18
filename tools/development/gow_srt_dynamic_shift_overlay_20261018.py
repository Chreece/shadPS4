#!/usr/bin/env python3
"""Surgical, source-safe correction of the SRT walker constant-lhs shift JIT.

Never replace the full locally corrected shader compiler. Remove precisely
one provably incorrect shl(reg, constant_lhs) from the constant-lhs case,
and fail closed for unexpected source changes.
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
from pathlib import Path

BEGIN = "static bool EmitComputeOffsetShiftLeftLogical32("
END = "static bool EmitComputeOffsetShiftRightLogical32("
ORIGINAL = ("        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));\n"
            "        c.shl(reg, inst->Arg(0).U32());\n"
            "        c.mov(ecx, reg);")
FIXED = ("        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));\n"
         "        c.mov(ecx, reg);")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def repair(source: str) -> str:
    if source.count(BEGIN) != 1 or source.count(END) != 1:
        raise ValueError("SHIFT_LEFT_AND_RIGHT_FUNCTION_BOUNDARIES_CHANGED")
    start = source.index(BEGIN)
    stop = source.index(END, start)
    method = source[start:stop]
    if method.count(ORIGINAL) != 1:
        if method.count(FIXED) == 1 and "c.shl(reg, inst->Arg(0).U32());" not in method:
            raise ValueError("SRT_SHIFT_FIX_ALREADY_APPLIED")
        raise ValueError("SRT_CONSTANT_LHS_OPCODE_PATTERN_UNRECOGNIZED")
    repaired_method = method.replace(ORIGINAL, FIXED, 1)
    if repaired_method.count("c.mov(ecx, reg);") != method.count("c.mov(ecx, reg);"):
        raise ValueError("DYNAMIC_SHIFT_COUNT_LOAD_CHANGED")
    if method.count("c.shl(reg, inst->Arg(1).U32());") != repaired_method.count(
           "c.shl(reg, inst->Arg(1).U32());"):
        raise ValueError("DYNAMIC_LHS_IMMEDIATE_SHIFT_BRANCH_CHANGED")
    new_source = source[:start] + repaired_method + source[stop:]
    if new_source.replace(FIXED, ORIGINAL, 1) != source:
        raise ValueError("MORE_THAN_TARGETED_INSTRUCTION_CHANGED")
    return new_source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest-source", type=Path)
    parser.add_argument("target", nargs="?", type=Path)
    parser.add_argument("report", nargs="?", type=Path)
    args = parser.parse_args()
    if args.selftest_source is not None:
        source = args.selftest_source.read_text(encoding="utf-8")
        patched = repair(source)
        if patched == source:
            raise SystemExit("SOURCE_SELFTEST_EMPTY_CHANGE")
        if source.count("c.shl(reg, inst->Arg(0).U32());") != (
            patched.count("c.shl(reg, inst->Arg(0).U32());") + 1):
            raise SystemExit("SRT_SELFTEST_REMOVES_MORE_THAN_ONE_INSTRUCTION")
        print("SRT_PINNED_HISTORICAL_SOURCE_SELFTEST=PASS")
        print("SRT_COUNT_SHIFT_REPAIR=EXACTLY_ONE_OPCODE")
        return
    if args.target is None or args.report is None:
        raise SystemExit("USAGE: PATCHER TARGET_CPP REPORT")
    if not args.target.is_file() or args.target.is_symlink():
        raise SystemExit("UNSAFE_SRT_SOURCE_TARGET")
    before = args.target.read_bytes()
    try:
        after = repair(before.decode("utf-8")).encode("utf-8")
    except Exception as e:
        raise SystemExit(f"SRT_DYNAMIC_SHIFT_PATCH_FAILED={e}")
    # Write diff first; unchanged original survives a report-write failure.
    lines = list(difflib.unified_diff(before.decode().splitlines(True),
                                     after.decode().splitlines(True),
                                     fromfile="existing-shader-compiler.cpp",
                                     tofile="srt-shift-corrected.cpp"))
    if sum(1 for line in lines if line.startswith("-") and not line.startswith("---")) != 1:
        raise SystemExit("SRT_UNEXPECTED_NON_SINGLE_INSTRUCTION_DIFF")
    args.report.write_text(
        f"ORIGINAL_SHA256={sha256(before)}\n"
        f"CORRECTED_SHA256={sha256(after)}\n"
        "TARGET=CONSTANT_LHS_DYNAMIC_SHIFT_COUNT\n"
        "GRAPHICS_SHADER_OTHER_CODE_UNCHANGED=YES\n\n" + "".join(lines))
    args.target.write_bytes(after)
    print("SRT_DYNAMIC_SHIFT_JIT_BUG_FIXED=ONE_INSTRUCTION")
    print(f"ORIGINAL_SHADER_COMPILER_SHA256={sha256(before)}")
    print(f"NEW_SHADER_COMPILER_SHA256={sha256(after)}")


if __name__ == "__main__":
    main()
