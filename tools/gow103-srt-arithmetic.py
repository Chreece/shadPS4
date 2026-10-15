#!/usr/bin/env python3
"""GoW103: generic SRT arithmetic/codegen correctness fixes.

Retains GoW98 ReadFirstLane + GoW97 stack fix + GoW96/91 chain.
Adds only emulator-level fixes:
- correct dynamic shift count when the left operand of shl is immediate
- support ShiftRightArithmetic32
- support FindILsb32
- make BitFieldUExtract width 32 produce a full-width mask
- roll back partially emitted SRT code when an offset cannot be computed

No game/shader IDs, no fake Phi/GetAttribute/ReadLane semantics, no global readbacks.
"""
from __future__ import annotations
import argparse, difflib, hashlib, os, subprocess, sys, tempfile, types
from pathlib import Path

ROOT=Path.home()/"projects/shadps4/GodOfWarRagnarok"
GOW98_URL=("https://raw.githubusercontent.com/Chreece/shadPS4/"
           "a3bdf3fc4c761d368064efc0fb67979f31b318a0/tools/gow98-srt-readfirstlane.py")
GOW98_BLOB="50b6c3417e6cc44b46e84ad4fb67979f31b318a0"


def blob_sha(data:bytes)->str:
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()


def load_gow98():
    scripts=ROOT/"scripts";scripts.mkdir(parents=True,exist_ok=True)
    p=scripts/"gow98-pinned-for-gow103.py"
    if not p.is_file() or p.is_symlink() or blob_sha(p.read_bytes())!=GOW98_BLOB:
        fd,name=tempfile.mkstemp(prefix=".gow103-",dir=scripts);os.close(fd)
        try:
            subprocess.run(["curl","-4fsSL","--retry","2","--connect-timeout","20",
                            "--max-time","120",GOW98_URL,"-o",name],check=True,timeout=380)
            data=Path(name).read_bytes()
            if blob_sha(data)!=GOW98_BLOB:
                raise RuntimeError("Pinned GoW98 helper fingerprint mismatch")
            if p.is_symlink():
                raise RuntimeError("Refusing symlinked helper")
            os.replace(name,p)
        finally:
            Path(name).unlink(missing_ok=True)
    m=types.ModuleType("gow103_gow98");m.__file__=str(p)
    exec(compile(p.read_bytes(),str(p),"exec"),m.__dict__)
    return m


def one(text,old,new,label):
    if text.count(old)!=1:
        raise RuntimeError(label+" source shape missing/ambiguous")
    return text.replace(old,new,1)


def install_fixes(gow98,gow97):
    prior=gow97.adapt_flatten

    def adapt(data:bytes):
        base,_old_patch,proof=prior(data)
        original=data.decode()
        text=base.decode()

        # 1) shl with immediate left / dynamic count:
        # reg already contains the evaluated right operand (the shift count).
        a,b=gow98.function_span(text,"EmitComputeOffsetShiftLeftLogical32")
        fn=text[a:b]
        fn=one(fn,
               "        c.shl(reg, inst->Arg(0).U32());\n"
               "        c.mov(ecx, reg);\n",
               "        c.mov(ecx, reg);\n",
               "dynamic left-shift count")
        text=text[:a]+fn+text[b:]

        # 2) arithmetic right shift: same operand evaluation as logical shift,
        # but emit SAR rather than SHR.
        if "EmitComputeOffsetShiftRightArithmetic32" not in text:
            a,b=gow98.function_span(text,"EmitComputeOffsetShiftRightLogical32")
            insert=b
            helper=r'''

static bool EmitComputeOffsetShiftRightArithmetic32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                                    PassInfo& pass_info, IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.mov(ecx, reg);
        c.mov(reg, inst->Arg(0).U32());
        c.sar(reg, cl);
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.sar(reg, inst->Arg(1).U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.pop(rcx);
        c.sar(reg, cl);
    }
    return true;
}
'''
            text=text[:insert]+helper+text[insert:]

        # 3) bfe width 32. x86/C++ 1u<<32 wraps/UB; width 32 means all
        # remaining 32 bits, i.e. no mask after the right shift.
        a,b=gow98.function_span(text,"EmitComputeOffsetBitFieldUExtract")
        fn=text[a:b]
        fn=one(fn,
               "        c.and_(reg, (1U << inst->Arg(2).U32()) - 1);\n",
               "        const u32 width = inst->Arg(2).U32();\n"
               "        if (width > 32) {\n"
               "            return false;\n"
               "        }\n"
               "        if (width < 32) {\n"
               "            c.and_(reg, (1U << width) - 1);\n"
               "        }\n",
               "BitFieldUExtract immediate width")
        fn=one(fn,
               "        c.mov(ecx, reg);\n"
               "        c.mov(edx, 1);\n"
               "        c.shl(edx, cl);\n"
               "        c.dec(edx);\n"
               "        c.and_(dword[rsp], edx);\n",
               "        c.mov(ecx, reg);\n"
               "        c.mov(edx, 0xffffffffU);\n"
               "        Xbyak::Label mask_ready;\n"
               "        c.cmp(ecx, 32);\n"
               "        c.jae(mask_ready);\n"
               "        c.mov(edx, 1);\n"
               "        c.shl(edx, cl);\n"
               "        c.dec(edx);\n"
               "        c.L(mask_ready);\n"
               "        c.and_(dword[rsp], edx);\n",
               "BitFieldUExtract dynamic width")
        text=text[:a]+fn+text[b:]

        # 4) FindILsb32: BSF matches nonzero inputs; for zero the IR/SPIR-V
        # result is 0xffffffff, so set it explicitly when ZF is set.
        if "EmitComputeOffsetFindILsb32" not in text:
            name_a,_=gow98.function_span(text,"IsAllowedOffsetInstruction")
            line=text.rfind("\n",0,name_a)+1
            helper=r'''static bool EmitComputeOffsetFindILsb32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                        PassInfo& pass_info, IR::Inst* inst) {
    const IR::Value arg = inst->Arg(0);
    if (arg.IsImmediate()) {
        c.mov(reg, arg.U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, arg));
    }
    Xbyak::Label nonzero;
    c.bsf(reg, reg);
    c.jnz(nonzero);
    c.mov(reg, 0xffffffffU);
    c.L(nonzero);
    return true;
}

'''
            text=text[:line]+helper+text[line:]

        # Whitelist the two newly evaluable opcodes.
        a,b=gow98.function_span(text,"IsAllowedOffsetInstruction")
        fn=text[a:b]
        if "case IR::Opcode::ShiftRightArithmetic32:" not in fn:
            fn=one(fn,
                   "    case IR::Opcode::ShiftRightLogical32:\n",
                   "    case IR::Opcode::ShiftRightLogical32:\n"
                   "    case IR::Opcode::ShiftRightArithmetic32:\n",
                   "allowed arithmetic shift")
        if "case IR::Opcode::FindILsb32:" not in fn:
            fn=one(fn,
                   "    case IR::Opcode::BitFieldUExtract:\n",
                   "    case IR::Opcode::BitFieldUExtract:\n"
                   "    case IR::Opcode::FindILsb32:\n",
                   "allowed FindILsb32")
        text=text[:a]+fn+text[b:]

        # Dispatch both new evaluators.
        a,b=gow98.function_span(text,"ComputeOffset")
        fn=text[a:b]
        if "case IR::Opcode::ShiftRightArithmetic32:" not in fn:
            fn=one(fn,
                   "    case IR::Opcode::ShiftRightLogical32:\n"
                   "        ABORT_ON_FAILURE(EmitComputeOffsetShiftRightLogical32(c, reg, pass_info, inst));\n"
                   "        return true;\n",
                   "    case IR::Opcode::ShiftRightLogical32:\n"
                   "        ABORT_ON_FAILURE(EmitComputeOffsetShiftRightLogical32(c, reg, pass_info, inst));\n"
                   "        return true;\n"
                   "    case IR::Opcode::ShiftRightArithmetic32:\n"
                   "        ABORT_ON_FAILURE(EmitComputeOffsetShiftRightArithmetic32(c, reg, pass_info, inst));\n"
                   "        return true;\n",
                   "ComputeOffset arithmetic shift")
        if "case IR::Opcode::FindILsb32:" not in fn:
            fn=one(fn,
                   "    case IR::Opcode::BitFieldUExtract:\n"
                   "        ABORT_ON_FAILURE(EmitComputeOffsetBitFieldUExtract(c, reg, pass_info, inst));\n"
                   "        return true;\n",
                   "    case IR::Opcode::BitFieldUExtract:\n"
                   "        ABORT_ON_FAILURE(EmitComputeOffsetBitFieldUExtract(c, reg, pass_info, inst));\n"
                   "        return true;\n"
                   "    case IR::Opcode::FindILsb32:\n"
                   "        ABORT_ON_FAILURE(EmitComputeOffsetFindILsb32(c, reg, pass_info, inst));\n"
                   "        return true;\n",
                   "ComputeOffset FindILsb32")
        text=text[:a]+fn+text[b:]

        # 5) If dynamic offset codegen fails, erase everything generated for
        # that expression. This prevents dead partial instructions from running.
        a,b=gow98.function_span(text,"PushPtr")
        fn=text[a:b]
        fn=one(fn,
               "        ABORT_ON_FAILURE(ComputeOffset(c, r10d, pass_info, off_dw));\n",
               "        const size_t code_begin = c.getSize();\n"
               "        if (!ComputeOffset(c, r10d, pass_info, off_dw)) {\n"
               "            c.setSize(code_begin);\n"
               "            return false;\n"
               "        }\n",
               "PushPtr rollback")
        text=text[:a]+fn+text[b:]

        a,b=gow98.function_span(text,"VisitPointer")
        fn=text[a:b]
        needle="            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {\n"
        if fn.count("const size_t code_begin = c.getSize();") == 0:
            fn=one(fn,needle,
                   "            const size_t code_begin = c.getSize();\n"
                   +needle+
                   "                c.setSize(code_begin);\n",
                   "VisitPointer rollback")
        text=text[:a]+fn+text[b:]

        # Structural regression checks against the actual retained source.
        a,b=gow98.function_span(text,"EmitComputeOffsetShiftLeftLogical32")
        shl=text[a:b]
        if "c.shl(reg, inst->Arg(0).U32());" in shl:
            raise RuntimeError("incorrect dynamic-left shift survived")
        a,b=gow98.function_span(text,"EmitComputeOffsetBitFieldUExtract")
        bfe=text[a:b]
        if "width > 32" not in bfe or "mask_ready" not in bfe:
            raise RuntimeError("32-bit BFE handling missing")
        a,b=gow98.function_span(text,"PushPtr")
        if "c.setSize(code_begin);" not in text[a:b]:
            raise RuntimeError("PushPtr rollback missing")
        a,b=gow98.function_span(text,"VisitPointer")
        if "c.setSize(code_begin);" not in text[a:b]:
            raise RuntimeError("VisitPointer rollback missing")
        a,b=gow98.function_span(text,"IsAllowedOffsetInstruction")
        allowed=text[a:b]
        a2,b2=gow98.function_span(text,"ComputeOffset")
        comp=text[a2:b2]
        for op in ("ReadFirstLane","ShiftRightArithmetic32","FindILsb32"):
            if ("case IR::Opcode::"+op+":") not in allowed or ("case IR::Opcode::"+op+":") not in comp:
                raise RuntimeError("missing SRT evaluator: "+op)

        patch="".join(difflib.unified_diff(
            original.splitlines(keepends=True),text.splitlines(keepends=True),
            fromfile="a/"+gow97.FLAT,tofile="b/"+gow97.FLAT,n=3)).encode()
        p=dict(proof)
        p.update(
            dynamic_left_shift_fixed=True,
            bfe_width32_fixed=True,
            shift_right_arithmetic32=True,
            findilsb32=True,
            failed_codegen_rollback=True,
            game_specific=False,
        )
        return text.encode(),patch,p

    gow97.adapt_flatten=adapt


def self_test(gow98,gow97):
    gow96=gow97.load_gow96();gow91=gow96.load_gow91()
    phase90=gow91.get_helper();phase89=phase90.get_helper();base=gow91.get_base(phase89)
    src,_,_,_,overrides=gow91.prior_state(base,phase90,phase89)

    # Verify the retained earlier deltas without running/reporting a game test.
    buf=Path(overrides[(src/gow91.CPP).resolve()]).read_bytes()
    gow91.adapt_source(buf)
    res=(src/gow96.RESOURCE).read_bytes()
    gow96.adapt_resource(res,gow96.collaborator_source())

    flat=(src/gow97.FLAT).read_bytes()
    out,_,proof=gow97.adapt_flatten(flat)
    text=out.decode()
    assert proof["game_specific"] is False
    assert proof["dynamic_left_shift_fixed"]
    assert proof["bfe_width32_fixed"]
    assert proof["shift_right_arithmetic32"]
    assert proof["findilsb32"]
    assert proof["failed_codegen_rollback"]
    assert "case IR::Opcode::ReadFirstLane:" in text
    print("GOW103_SELF_TEST=PASS",flush=True)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test",action="store_true")
    ap.add_argument("--build-only",action="store_true")
    ap.add_argument("--restore",action="store_true")
    args=ap.parse_args()

    gow98=load_gow98()
    gow97=gow98.load_gow97()
    gow98.install_readfirstlane_support(gow97)
    install_fixes(gow98,gow97)

    if args.self_test:
        self_test(gow98,gow97);return 0

    old=sys.argv[:]
    try:
        sys.argv=["gow103"] + (["--build-only"] if args.build_only else []) + (["--restore"] if args.restore else [])
        return gow97.main()
    finally:
        sys.argv=old


if __name__=="__main__":
    raise SystemExit(main())
