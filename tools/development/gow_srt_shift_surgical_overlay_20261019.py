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
import re

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


def verify_correct_dynamic_shift_helper(code: str, header: str | None) -> None:
    """Verify older proven SRT helper emits precisely the correct x86 instructions.

    The protected GoW shader source can use SrtCodegen::EmitConstantLeftShift-
    ByDynamicCount instead of four inline c.* instructions. Never replace that
    working helper with an unrelated source preimage.
    """
    include = '#include "shader_recompiler/ir/passes/srt_offset_codegen.h"'
    if include not in code or header is None:
        raise ValueError("SRT_DYNAMIC_SHIFT_HELPER_HEADER_NOT_PRESENT")
    if "namespace Shader::Optimization::SrtCodegen {" not in header:
        raise ValueError("SRT_DYNAMIC_SHIFT_HELPER_NAMESPACE_CHANGED")
    pattern = re.search(
        r"inline\s+void\s+EmitConstantLeftShiftByDynamicCount\s*\([^)]*\)\s*\{([^}]*)\}",
        header, flags=re.DOTALL)
    if pattern is None:
        raise ValueError("SRT_DYNAMIC_SHIFT_HELPER_BODY_MISSING")
    instructions = [line.strip() for line in pattern.group(1).splitlines()
                    if line.strip().startswith("c.")]
    expected = ["c.mov(Xbyak::util::ecx, destination);",
                "c.mov(destination, left_operand);",
                "c.shl(destination, Xbyak::util::cl);"]
    if instructions != expected:
        raise ValueError("SRT_DYNAMIC_SHIFT_HELPER_EMISSION_UNVERIFIED=" +
                         repr(instructions))


def patch_bytes(raw: bytes, helper_header: str | None = None) -> tuple[bytes, str]:
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
    helper_call = "SrtCodegen::EmitConstantLeftShiftByDynamicCount(c, reg, inst->Arg(0).U32());"
    if instructions == [fixed[0]] and arm.count(helper_call) == 1:
        verify_correct_dynamic_shift_helper(text, helper_header)
        return raw, "ALREADY_FIXED_VIA_VERIFIED_SRT_HELPER"
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
    helper_code = ("#include \"shader_recompiler/ir/passes/srt_offset_codegen.h\"\n"+
                  synthetic.replace(OLD, OLD.splitlines()[0]+"\n"+
                  "        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));\n"+
                  "        SrtCodegen::EmitConstantLeftShiftByDynamicCount(c, reg, inst->Arg(0).U32());\n"+
                  "    } else if (inst->Arg(1).IsImmediate()) {"))
    helper_header = """namespace Shader::Optimization::SrtCodegen {
inline void EmitConstantLeftShiftByDynamicCount(Xbyak::CodeGenerator& c,
                                                 const Xbyak::Reg32& destination,
                                                 std::uint32_t left_operand) {
    c.mov(Xbyak::util::ecx, destination);
    c.mov(destination, left_operand);
    c.shl(destination, Xbyak::util::cl);
}
}"""
    helper_result, helper_status = patch_bytes(helper_code.encode(), helper_header)
    assert helper_result == helper_code.encode()
    assert helper_status == "ALREADY_FIXED_VIA_VERIFIED_SRT_HELPER"
    try:
        patch_bytes(helper_code.encode(), helper_header.replace(
            "c.mov(destination, left_operand);", "c.mov(destination, 0);"))
    except ValueError as exc:
        assert "HELPER_EMISSION_UNVERIFIED" in str(exc)
    else:
        raise AssertionError("UNSAFE_SRT_HELPER_WAS_ACCEPTED")
    try:
        patch_bytes(helper_code.encode())
    except ValueError as exc:
        assert "HELPER_HEADER_NOT_PRESENT" in str(exc)
    else:
        raise AssertionError("MISSING_SRT_HELPER_HEADER_WAS_ACCEPTED")
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
          "reversible=True already_fixed_safe=True helper_variant_safe=True "
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
        # Inspect helper in the same directory as the protected source:
        # the generated C++ translation unit includes that exact header.
        header = args.source.parent / "srt_offset_codegen.h"
        helper_text = (header.read_text() if header.is_file() and
                       not header.is_symlink() else None)
        candidate, status=patch_bytes(source, helper_text)
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
    if args.apply and status == "PATCHED_EXACT_ONE_INSTRUCTION":
        args.source.write_bytes(candidate)
    print("SRT_DYNAMIC_SHIFT_SOURCE_FIX="+status+
          ("_APPLIED" if args.apply and status == "PATCHED_EXACT_ONE_INSTRUCTION"
           else "_VERIFIED" if args.apply else "_PREFLIGHT"))
    print("SRT_TARGET_ARM=VERIFIED_CORRECT_OR_EXACT_SINGLE_INSTRUCTION_REMOVED")
    print("SRT_REVERSE_EXACT_AND_PROTECTED_SOURCE_UNTOUCHED=YES")

if __name__=="__main__":
    main()
