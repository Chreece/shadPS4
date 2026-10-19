#!/usr/bin/env python3
"""Offline verification of SRT codegen for constant << dynamic shift.

No game launch, no emulator build, no renderer/audio edits. The observed
source emitted two SHLs on the dynamic shift count; x86 only needs one
SHL after loading the constant into the destination register.

This verifies exact emitter instruction order and integer semantics.
It does not establish that GoW's currently failing shader uses this case.
"""
import argparse
import pathlib
import random


def x86_shl32(a: int, n: int) -> int:
    return ((a & 0xffffffff) << (n & 31)) & 0xffffffff


def expected(lhs: int, rhs: int) -> int:
    return x86_shl32(lhs, rhs)


def previously_emitted(lhs: int, rhs: int) -> int:
    incorrect_shift_count = x86_shl32(rhs, lhs)
    return x86_shl32(lhs, incorrect_shift_count)


def verify_source(path):
    src=path.read_text()
    start=src.index("static bool EmitComputeOffsetShiftLeftLogical32(")
    end=src.index("static bool EmitComputeOffsetShiftRightLogical32(",start)
    sub=src[start:end]
    a=sub.index("if (inst->Arg(0).IsImmediate()) {")
    b=sub.index("} else if (inst->Arg(1).IsImmediate()) {",a)
    arm=sub[a:b]
    code=("\n".join(line.strip() for line in arm.splitlines()
            if line.lstrip().startswith(("ABORT_ON_FAILURE","c."))))
    expected_code="\n".join((
        "ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));",
        "c.mov(ecx, reg);",
        "c.mov(reg, inst->Arg(0).U32());",
        "c.shl(reg, cl);",
    ))
    if code!=expected_code:
        raise AssertionError("EMITTED_CONSTANT_LEFT_VARIABLE_RIGHT_SHL_SEQUENCE_MISMATCH:\n"+code)
    return True


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=pathlib.Path)
    args=p.parse_args()
    if args.source:
        verify_source(args.source)
        print("EXACT_PATCHED_SRT_EMITTER_SEQUENCE=PASS")
    rng=random.Random(0xAB1500)
    counter=0
    broken=0
    for _ in range(100000):
        x=rng.getrandbits(32)
        y=rng.getrandbits(32)
        result=expected(x,y)
        # Correct emitted sequence is mov ecx,y; mov reg,x; shl reg,cl.
        emitted=x86_shl32(x,y)
        assert result==emitted
        if previously_emitted(x,y)!=result:
            broken+=1
        counter+=1
    assert previously_emitted(3,2)==196608
    assert expected(3,2)==12
    assert broken>10000
    assert x86_shl32(0xffffffff, 32)==0xffffffff
    assert x86_shl32(0xffffffff, 33)==0xfffffffe
    print(f"SRT_CONST_DYNAMIC_SHIFT_REGRESSION=PASS random_cases={counter} "
          f"old_wrong_cases={broken}")
    print("GAME_CORRELATION=UNPROVEN; NO_BUILD_NO_GAME_NO_INPUT_NO_SCREENSHOTS=YES")


if __name__=="__main__":
    main()
