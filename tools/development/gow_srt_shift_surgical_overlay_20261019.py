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

def patch_bytes(raw: bytes) -> bytes:
    text = raw.decode("utf8")
    if text.count(FIRST) != 1 or text.count(LAST) != 1:
        raise ValueError("SRT_FUNCTION_BOUNDARIES_NOT_UNIQUE")
    start=text.index(FIRST)
    stop=text.index(LAST,start)
    body=text[start:stop]
    if body.count(OLD)!=1 or body.count(NEW)!=0:
        raise ValueError("SRT_SHIFT_SOURCE_CHANGED_OR_ALREADY_PATCHED")
    candidate=text[:start]+body.replace(OLD,NEW,1)+text[stop:]
    revert=candidate[:start]+candidate[start:stop+len(NEW)-len(OLD)].replace(NEW,OLD,1)+candidate[stop+len(NEW)-len(OLD):]
    if revert != text:
        raise ValueError("SRT_PATCH_NOT_EXACTLY_REVERSIBLE")
    if candidate.count(NEW)!=1:
        raise ValueError("SRT_EXPECTED_FIXED_EMITTER_MISSING")
    return candidate.encode("utf8")

def selftest():
    synthetic="prefix\n"+FIRST+" v() {\n"+OLD+"\n}\n"+LAST+" f() {}\n"
    candidate=patch_bytes(synthetic.encode()).decode()
    assert candidate.count(NEW)==1
    assert candidate.replace(NEW,OLD,1)==synthetic
    try:
        patch_bytes(candidate.encode())
    except ValueError:
        pass
    else:
        raise AssertionError("ALREADY_FIXED_SOURCE_NOT_REJECTED")
    try:
        patch_bytes(synthetic.replace("c.shl(reg, inst->Arg(0).U32());","").encode())
    except ValueError:
        pass
    else:
        raise AssertionError("UNKNOWN_PREIMAGE_NOT_REJECTED")
    def good(a,b): return ((a & 0xffffffff) << (b & 31)) & 0xffffffff
    def old(a,b): return good(a,good(b,a))
    assert good(3,2)==12 and old(3,2)==196608
    assert all(good(x,n)==((x << (n & 31)) & 0xffffffff) for x in (0,3,0xffffffff) for n in range(64))
    print("SRT_SHIFT_OVERLAY_SELFTEST=PASS reversible=True source_drift_fails_closed=True arithmetic=True")

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
        candidate=patch_bytes(source)
    except (ValueError,UnicodeDecodeError) as exc:
        raise SystemExit("SRT_SHIFT_PREIMAGE_CHECK_FAILED="+str(exc))
    diff="".join(difflib.unified_diff(source.decode().splitlines(True),
                                      candidate.decode().splitlines(True),
                                      fromfile="original/SRT.cpp",tofile="candidate/SRT.cpp"))
    if diff.count("c.shl(reg, inst->Arg(0).U32());") != 1:
        raise SystemExit("SRT_EXACT_ARITHMETIC_CORRECTION_DIFF_MISSING")
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
