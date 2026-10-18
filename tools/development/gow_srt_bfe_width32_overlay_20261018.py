#!/usr/bin/env python3
"""Patch only a demonstrably broken x86-64 SRT BFE-width codegen path.

Inspect the actual protected source, not a guessed historical file version.
When already fixed/unknown, preserve the source, archive the real function,
and stop instead of pretending to perform a new emulator fix.
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
from pathlib import Path

BEGIN = "static bool EmitComputeOffsetBitFieldUExtract("
END = "static bool IsAllowedOffsetInstruction("
IMMEDIATE_OLD = "        c.and_(reg, (1U << inst->Arg(2).U32()) - 1);"
IMMEDIATE_NEW = """        const u32 bit_width = inst->Arg(2).U32();
        // Width >=32 keeps all 32 bits and avoids undefined 1u << 32.
        if (bit_width < 32) {
            c.and_(reg, bit_width == 0 ? 0U : ((1U << bit_width) - 1U));
        }"""
DYNAMIC_OLD = """        c.mov(ecx, reg);
        c.mov(edx, 1);
        c.shl(edx, cl);
        c.dec(edx);
        c.and_(dword[rsp], edx);"""
DYNAMIC_NEW = """        c.mov(ecx, reg);
        // Width >=32 must not wrap to zero under the x86 5-bit shift mask.
        Xbyak::Label full_width;
        c.mov(edx, 0xffffffffu);
        c.cmp(ecx, 32);
        c.jae(full_width);
        c.mov(edx, 1);
        c.shl(edx, cl);
        c.dec(edx);
        c.L(full_width);
        c.and_(dword[rsp], edx);"""


def digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def analyze(s: str):
    if s.count(BEGIN) != 1 or s.count(END) != 1:
        raise ValueError("SRT_BFE_FUNCTION_BOUNDARIES_NOT_UNIQUE")
    i, j = s.index(BEGIN), s.index(END, s.index(BEGIN))
    function = s[i:j]
    nimm, ndyn = function.count(IMMEDIATE_OLD), function.count(DYNAMIC_OLD)
    if nimm == ndyn == 1:
        return "VULNERABLE", i, j, function
    if nimm == ndyn == 0:
        # This does not establish correctness, only that the known buggy
        # pattern is absent. Do not change anything without inspecting it.
        return "NO_KNOWN_BUGGY_PATTERN", i, j, function
    return "PARTIALLY_DIFFERENT_UNSUPPORTED", i, j, function


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--selftest-source", type=Path)
    p.add_argument("source", nargs="?", type=Path)
    p.add_argument("report_dir", nargs="?", type=Path)
    a = p.parse_args()
    path = a.selftest_source or a.source
    if not path or not path.is_file() or path.is_symlink():
        raise SystemExit("UNSAFE_OR_MISSING_SHADER_SOURCE")
    before = path.read_bytes()
    src = before.decode("utf-8")
    status, i, j, method = analyze(src)
    if a.selftest_source:
        if status != "VULNERABLE":
            raise SystemExit(f"HISTORICAL_SRT_BFE_TEST_EXPECTED_VULNERABLE={status}")
        updated = method.replace(IMMEDIATE_OLD, IMMEDIATE_NEW, 1).replace(
            DYNAMIC_OLD, DYNAMIC_NEW, 1)
        assert updated.count("c.jae(full_width);") == 1
        assert updated.count("bit_width < 32") == 1
        assert updated.count("c.and_(dword[rsp], edx);") == 1
        print("SRT_BFE_ORIGINAL_BUG_AND_TARGETED_TRANSFORM=PASS")
        return
    if a.report_dir is None:
        raise SystemExit("NEED_SRT_ANALYSIS_REPORT_DIR")
    a.report_dir.mkdir(parents=True, exist_ok=True)
    (a.report_dir / "actual-srt-bfe-function.cpp").write_text(method)
    (a.report_dir / "actual-srt-bfe-status.txt").write_text(
        f"RESULT={status}\nORIGINAL_SHA256={digest(before)}\n"
        f"OLD_IMMEDIATE_HITS={method.count(IMMEDIATE_OLD)}\n"
        f"OLD_DYNAMIC_HITS={method.count(DYNAMIC_OLD)}\n"
    )
    if status != "VULNERABLE":
        print(f"SRT_BFE_ACTUAL_SOURCE_NOT_MODIFIED={status}", flush=True)
        raise SystemExit(3)
    fixed_method = method.replace(IMMEDIATE_OLD, IMMEDIATE_NEW, 1).replace(
        DYNAMIC_OLD, DYNAMIC_NEW, 1)
    after = (src[:i] + fixed_method + src[j:]).encode("utf-8")
    patches = list(difflib.unified_diff(src.splitlines(True),
                   after.decode().splitlines(True),
                   fromfile="source-original.cpp", tofile="safe-srt-bfe.cpp"))
    (a.report_dir / "srt-bfe-exact-diff.patch").write_text("".join(patches))
    reconstructed = src[:i] + fixed_method + src[j:]
    if reconstructed[:i] != src[:i] or reconstructed[i + len(fixed_method):] != src[j:]:
        raise SystemExit("SRT_BFE_UNRELATED_SHADER_CODE_CHANGED")
    path.write_bytes(after)
    (a.report_dir / "actual-srt-bfe-status.txt").write_text(
        f"RESULT=PATCHED_TWO_KNOWN_BFE_CODEGEN_BLOCKS\n"
        f"ORIGINAL_SHA256={digest(before)}\nNEW_SHA256={digest(after)}\n")
    print("SRT_BFE_32BIT_WIDTH_EMULATOR_SOURCE_FIX=STAGED", flush=True)


if __name__ == "__main__":
    main()
