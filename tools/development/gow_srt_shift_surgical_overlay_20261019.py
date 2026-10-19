#!/usr/bin/env python3
"""Minimal, reversible SRT codegen fix staged only in disposable GoW tree.

Correct x86 codegen of constant << dynamic_shift. The broken first SHL
modifies the dynamic count before it is moved to CL. Do not modify any other
shader paths, globals, guest SRT offsets, or renderer buffers.
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
from pathlib import Path

FIRST = "static bool EmitComputeOffsetShiftLeftLogical32("
LAST = "static bool EmitComputeOffsetShiftRightLogical32("
OLD = """    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.shl(reg, inst->Arg(0).U32());
        c.mov(ecx, reg);
        c.mov(reg, inst->Arg(0).U32());
        c.shl(reg, cl);
    } else if (inst->Arg(1).IsImmediate()) {"""
NEW = """    if (inst->Arg(0).IsImmediate()) {
        // x << dynamic_shift: ComputeOffset left the shift count in reg.
        // Do not shift the count by x: the old extra SHL corrupted ECX
        // and produced x << (dynamic_shift << x) instead of x << dynamic_shift.
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.mov(ecx, reg);
        c.mov(reg, inst->Arg(0).U32());
        c.shl(reg, cl);
    } else if (inst->Arg(1).IsImmediate()) {"""

def _shift_arm(text: str) -> tuple[int, int, int, int, str]:
    """Return exact first conditional arm; do not inspect unrelated SHLs."""
    if text.count(FIRST) != 1 or text.count(LAST) != 1:
        raise ValueError("SRT_FUNCTION_BOUNDARIES_NOT_UNIQUE")
    begin = text.index(FIRST)
    end = text.index(LAST, begin)
    if_begin = text.index("    if (inst->Arg(0).IsImmediate()) {", begin, end)
    if_end = text.index("    } else if (inst->Arg(1).IsImmediate()) {", if_begin, end)
    return begin, end, if_begin, if_end, text[if_begin:if_end]


def patch_bytes(raw: bytes) -> tuple[bytes, str]:
    text = raw.decode("utf8")
    _, _, start, end, arm = _shift_arm(text)
    # Compare executable code, not comments/whitespace. The local source in
    # the protected tree may already include this correction with different
    # comments than the GitHub branch. Preserve that work rather than treating
    # it as an unknown preimage or applying the fix twice.
    instructions = [
        line.strip() for line in arm.splitlines()
        if line.lstrip().startswith(("ABORT_ON_FAILURE(", "c."))
    ]
    fixed = [
        "ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));",
        "c.mov(ecx, reg);",
        "c.mov(reg, inst->Arg(0).U32());",
        "c.shl(reg, cl);",
    ]
    broken = [fixed[0], "c.shl(reg, inst->Arg(0).U32());", *fixed[1:]]
    if instructions == fixed:
        return raw, "ALREADY_FIXED_VERIFIED"
    if instructions != broken:
        raise ValueError("SRT_DYNAMIC_SHIFT_ARM_UNRECOGNIZED=" + repr(instructions))
    # Surgical deletion: remove only the redundant C++ emitter instruction.
    # All surrounding code, comments and working renderer fixes are retained.
    redundant = "        c.shl(reg, inst->Arg(0).U32());\n"
    if arm.count(redundant) != 1:
        raise ValueError("REDUNDANT_SRT_SHIFT_INSTRUCTION_NOT_UNIQUE")
    new_arm = arm.replace(redundant, "", 1)
    result = text[:start] + new_arm + text[end:]
    if result.replace(new_arm, arm, 1) != text:
        raise ValueError("SRT_SOURCE_NOT_EXACTLY_REVERSIBLE")
    _, _, _, _, result_arm = _shift_arm(result)
    result_ops = [line.strip() for line in result_arm.splitlines()
                  if line.lstrip().startswith(("ABORT_ON_FAILURE(", "c."))]
    if result_ops != fixed:
        raise ValueError("SRT_PATCH_RESULT_NOT_VERIFIED")
    return result.encode("utf8"), "PATCHED_EXACT_ONE_INSTRUCTION"

def selftest():
    synthetic="prefix\n"+FIRST+" v() {\n"+OLD+"\n}\n"+LAST+" f() {}\n"
    candidate, status = patch_bytes(synthetic.encode())
    assert status == "PATCHED_EXACT_ONE_INSTRUCTION"
    assert candidate.decode().replace("        c.mov(ecx, reg);\n",
                                     "        c.shl(reg, inst->Arg(0).U32());\n        c.mov(ecx, reg);\n",1) == synthetic
    fixed, repeated_status = patch_bytes(candidate)
    assert fixed == candidate and repeated_status == "ALREADY_FIXED_VERIFIED"
    # An independently patched user source may have different comments.
    independently_fixed = candidate.decode().replace("        c.mov(ecx, reg);",
                                                         "        // existing verified fix\n        c.mov(ecx, reg);")
    same, status = patch_bytes(independently_fixed.encode())
    assert same == independently_fixed.encode() and status == "ALREADY_FIXED_VERIFIED"
    unknown = synthetic.replace("        c.mov(ecx, reg);", "        c.mov(ebx, reg);")
    try:
        patch_bytes(unknown.encode())
    except ValueError as exc:
        assert "UNRECOGNIZED" in str(exc)
    else:
        raise AssertionError("UNKNOWN_SRT_CODE_WAS_ACCEPTED")
    def good(a,b): return ((a & 0xffffffff) << (b & 31)) & 0xffffffff
    def old(a,b): return good(a, good(b, a))
    assert good(3,2)==12 and old(3,2)==196608
    assert all(good(x,n)==((x << (n & 31)) & 0xffffffff)
               for x in (0,3,0xffffffff) for n in range(64))
    print("SRT_SHIFT_OVERLAY_SELFTEST=PASS "
          "reversible=True already_fixed_safe=True "
          "source_drift_fails_closed=True arithmetic=True")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--self-test",action="store_true")
    ap.add_argument("source",type=Path,nargs="?")
    ap.add_argument("diff",type=Path,nargs="?")
    ap.add_argument("--apply",action="store_true")
    args=ap.parse_args()
    if args.self_test:
        selftest()
        return
    if not args.source or not args.diff:
        ap.error("source and diff required")
    if not args.source.is_file() or args.source.is_symlink():
        raise SystemExit("SRT_SOURCE_UNSAFE_FAIL_CLOSED")
    source=args.source.read_bytes()
    try:
        candidate, status=patch_bytes(source)
    except (ValueError,UnicodeDecodeError) as exc:
        raise SystemExit("SRT_SHIFT_PREIMAGE_CHECK_FAILED="+str(exc))
    diff="".join(difflib.unified_diff(source.decode().splitlines(True),
                                      candidate.decode().splitlines(True),
                                      fromfile="original/SRT.cpp",tofile="candidate/SRT.cpp"))
    if status == "PATCHED_EXACT_ONE_INSTRUCTION":
        if diff.count("-        c.shl(reg, inst->Arg(0).U32());") != 1:
            raise SystemExit("SRT_EXACT_ARITHMETIC_CORRECTION_DIFF_MISSING")
    elif diff:
        raise SystemExit("SRT_ALREADY_FIXED_BUT_UNEXPECTED_DIFF")
    args.diff.parent.mkdir(parents=True,exist_ok=True)
    args.diff.write_text(
        "ORIGINAL_SHA256="+hashlib.sha256(source).hexdigest()+"\n"+
        "PATCHED_SHA256="+hashlib.sha256(candidate).hexdigest()+"\n"+diff)
    if args.apply:
        args.source.write_bytes(candidate)
    print("SRT_DYNAMIC_SHIFT_SOURCE_FIX="+("APPLIED" if args.apply else "PREFLIGHT_ONLY"))
    print("SRT_ONLY_ONE_ARITHMETIC_ARM_CHANGED=YES")
    print("SRT_REVERSE_EXACT_AND_PROTECTED_SOURCE_UNTOUCHED=YES")

if __name__=="__main__":
    main()
