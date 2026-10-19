#!/usr/bin/env python3
"""Surgical GoW protected SRT source port of GoT's wave-uniform scalar guest read.

This is limited to a pinned known-working protected GoW shader preimage;
it preserves the root planner, shader arithmetic, graphics/UFFD and Audio3D.
No unknown or divergent expression is evaluated on CPU. No guessed descriptors.
"""
import argparse
import difflib
import hashlib
from pathlib import Path

PROTECTED_SHA = "14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161"
REPLACEMENTS = [
  [
    "std_includes",
    "#include <unordered_map>",
    "#include <algorithm>\n#include <limits>\n#include <unordered_map>"
  ],
  [
    "helper_includes",
    "#include \"shader_recompiler/ir/passes/srt.h\"",
    "#include \"shader_recompiler/ir/passes/srt.h\"\n#include \"shader_recompiler/ir/passes/srt_uniformity.h\"\n#include \"shader_recompiler/ir/passes/srt_inline_read_codegen.h\""
  ],
  [
    "inline_counter",
    "    u16 dst_off_dw;",
    "    u16 dst_off_dw;\n    u32 inline_native_read_count{};"
  ],
  [
    "conservative_uniform_gate",
    "static bool ComputeOffset(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,\n                          const IR::Value& off_dw);",
    "static bool CanInlineScalarRead(const IR::Inst* inst) {\n    const IR::Opcode opcode = inst->GetOpcode();\n    if (opcode != IR::Opcode::ReadConst && opcode != IR::Opcode::ReadConstBuffer) {\n        return false;\n    }\n    if (GetFlatbufOffset(inst) != 0) {\n        return false;\n    }\n    const IR::Inst* const base = inst->Arg(0).TryInst();\n    const IR::Opcode expected = opcode == IR::Opcode::ReadConst\n                                    ? IR::Opcode::CompositeConstructU32x2\n                                    : IR::Opcode::CompositeConstructU32x4;\n    return base && base->GetOpcode() == expected &&\n           IsHostUniformSrtOffset(IR::Value{const_cast<IR::Inst*>(inst)});\n}\n\nstatic bool EmitInlineReadConst(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,\n                                 PassInfo& pass_info, IR::Inst* inst);\nstatic bool ComputeOffset(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,\n                          const IR::Value& off_dw);"
  ],
  [
    "unassigned_scalar_read_fallback",
    "    case IR::Opcode::ReadConst:\n    case IR::Opcode::ReadConstBuffer:\n        if (u16 offset = GetFlatbufOffset(pass_info.DeduplicateInstruction(inst)); offset != 0) {\n            c.mov(reg, ptr[rsi + (offset << 2)]);\n            return true;\n        }\n        return false;\n",
    "    case IR::Opcode::ReadConst:\n    case IR::Opcode::ReadConstBuffer:\n        if (u16 offset = GetFlatbufOffset(pass_info.DeduplicateInstruction(inst)); offset != 0) {\n            c.mov(reg, ptr[rsi + (offset << 2)]);\n            return true;\n        }\n        if (CanInlineScalarRead(inst)) {\n            const std::size_t checkpoint = c.getSize();\n            if (EmitInlineReadConst(c, reg, pass_info, inst)) {\n                return true;\n            }\n            c.setSize(checkpoint);\n        }\n        return false;\n"
  ],
  [
    "real_guest_memory_reader",
    "static inline bool PushPtr(Xbyak::CodeGenerator& c, PassInfo& pass_info, const IR::Value& off_dw) {",
    "// Evaluate genuine wave-uniform scalar S_LOAD and S_BUFFER_LOAD from the\n// original guest pointer, using the existing fault-recoverable SRT walker.\n// Nested scalar dependencies are supported; divergent and cyclic expressions\n// remain on the GPU path without guessed values.\nstatic bool EmitInlineReadConst(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,\n                                PassInfo& pass_info, IR::Inst* inst) {\n    const bool is_sbuffer = inst->GetOpcode() == IR::Opcode::ReadConstBuffer;\n    ASSERT(inst->GetOpcode() == IR::Opcode::ReadConst || is_sbuffer);\n    IR::Inst* const base = inst->Arg(0).TryInst();\n    const IR::Opcode expected = is_sbuffer ? IR::Opcode::CompositeConstructU32x4\n                                          : IR::Opcode::CompositeConstructU32x2;\n    if (!base || base->GetOpcode() != expected ||\n        !IsHostUniformSrtOffset(IR::Value{inst})) {\n        return false;\n    }\n\n    const std::size_t checkpoint = c.getSize();\n    c.push(rdi); // original SGPR-table pointer / caller's current table\n    c.push(rax);\n    c.push(r11);\n    c.push(r9);  // scratch NUM_RECORDS for bounds-safe scalar buffer reads\n\n    if (!ComputeOffset(c, reg, pass_info, base->Arg(0))) {\n        c.setSize(checkpoint);\n        return false;\n    }\n    c.mov(eax, reg); // low 32 bits of the guest virtual address\n\n    if (!ComputeOffset(c, reg, pass_info, base->Arg(1))) {\n        c.setSize(checkpoint);\n        return false;\n    }\n    if (is_sbuffer) {\n        // GCN V# stride is bits [29:16] in dword 1 (14 bits).\n        // Keep the stride for bounds clamping before extracting only\n        // the low address eight bits from the same dword.\n        c.mov(r11d, reg);\n        c.shr(r11d, 16);\n        c.and_(r11d, 0x3fffu);\n        c.and_(reg, 0xffu);\n    }\n    c.shl(reg.cvt64(), 32);\n    c.or_(rax, reg.cvt64());\n\n    if (is_sbuffer) {\n        // Buffer SMEM uses NUM_RECORDS from descriptor dword 2 for bounds.\n        // Metadata dword 3 does not affect the memory operation.\n        if (!ComputeOffset(c, reg, pass_info, base->Arg(2))) {\n            c.setSize(checkpoint);\n            return false;\n        }\n        c.mov(r9d, reg);\n    }\n    // GCN SMRD IR index is in dwords; compute offset with 32-bit wrapping.\n    if (!ComputeOffset(c, reg, pass_info, inst->Arg(1))) {\n        c.setSize(checkpoint);\n        return false;\n    }\n    c.mov(r10d, reg);\n    if (is_sbuffer) {\n        SrtCodegen::EmitSBufferClampDwordIndex(c);\n    }\n    SrtCodegen::EmitGuestReadDword(\n        c, is_sbuffer ? SrtCodegen::SBufferGuestAddressMask\n                      : SrtCodegen::ScalarGuestAddressMask); // returns guest dword in r10d\n    c.mov(reg, r10d);\n\n    c.pop(r9);\n    c.pop(r11);\n    c.pop(rax);\n    c.pop(rdi);\n    ++pass_info.inline_native_read_count;\n    return true;\n}\n\nstatic inline bool PushPtr(Xbyak::CodeGenerator& c, PassInfo& pass_info, const IR::Value& off_dw) {"
  ],
  [
    "srt_program_signature",
    "static void GenerateSrtProgram(Info& info, PassInfo& pass_info) {",
    "static void GenerateSrtProgram(Info& info, PassInfo& pass_info,\n                               const boost::container::small_vector<IR::Inst*, 32>& all_readconsts) {"
  ],
  [
    "rootless_inline_scalar_reads",
    "    if (pass_info.srt_roots.empty()) {\n        return;\n    }",
    "    const bool has_host_uniform_scalar_reads = std::ranges::any_of(\n        all_readconsts, [](IR::Inst* read) { return CanInlineScalarRead(read); });\n    if (pass_info.srt_roots.empty() && !has_host_uniform_scalar_reads) {\n        return;\n    }"
  ],
  [
    "late_nonroot_producers",
    "    c.ret();\n    c.ready();\n\n    info.srt_info.walker_func_size",
    "    u32 late_reads{};\n    for (IR::Inst* read : all_readconsts) {\n        if (!CanInlineScalarRead(read)) {\n            continue;\n        }\n        IR::Inst* const canonical = pass_info.DeduplicateInstruction(read);\n        if (const u16 prior = GetFlatbufOffset(canonical); prior != 0) {\n            SetFlatbufOffset(read, prior);\n            continue;\n        }\n        if (pass_info.dst_off_dw == std::numeric_limits<u16>::max()) {\n            break;\n        }\n        if (!EmitInlineReadConst(c, r10d, pass_info, read)) {\n            continue;\n        }\n        const u16 flatbuf_slot = pass_info.dst_off_dw++;\n        c.mov(dword[rsi + (flatbuf_slot << 2)], r10d);\n        SetFlatbufOffset(read, flatbuf_slot);\n        if (canonical != read) {\n            SetFlatbufOffset(canonical, flatbuf_slot);\n        }\n        ++late_reads;\n    }\n    if (late_reads != 0 || pass_info.inline_native_read_count != 0) {\n        LOG_INFO(Render_Recompiler,\n                 \"SrtInlineReadConstResult: shader={:#x} late_flattened={} \"\n                 \"native_read_expressions={} final_flatbuf_words={}\",\n                 info.pgm_hash, late_reads, pass_info.inline_native_read_count,\n                 pass_info.dst_off_dw);\n    }\n\n    c.ret();\n    c.ready();\n\n    info.srt_info.walker_func_size"
  ],
  [
    "pass_producer_list",
    "    GenerateSrtProgram(program.info, pass_info);",
    "    GenerateSrtProgram(program.info, pass_info, all_readconsts);"
  ]
]

def patched_source(raw):
    actual = hashlib.sha256(raw).hexdigest()
    if actual != PROTECTED_SHA:
        raise ValueError("PROTECTED_GOW_SRT_SHA_MISMATCH="+actual)
    original = raw.decode("utf8")
    new = original
    for label, old, replacement in REPLACEMENTS:
        count = new.count(old)
        if count != 1:
            raise ValueError("PROTECTED_SOURCE_ANCHOR_%s_MATCHES_%d"%(label,count))
        new = new.replace(old, replacement, 1)
    reversed_source = new
    for label, old, replacement in reversed(REPLACEMENTS):
        if reversed_source.count(replacement) != 1:
            raise ValueError("SRT_REVERSE_ANCHOR_CHANGED_"+label)
        reversed_source = reversed_source.replace(replacement,old,1)
    if reversed_source != original:
        raise ValueError("SRT_PATCH_NONREVERSIBLE")
    for expected in ["MakeSrtRootDependencyPlan(pass_info)",
                     "SrtCodegen::EmitConstantLeftShiftByDynamicCount",
                     "IsHostUniformSrtOffset",
                     "SrtInlineReadConstResult:"]:
        if expected not in new:
            raise ValueError("PRESERVATION_OR_GUARD_MISSING_"+expected)
    return new.encode("utf8")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("source",type=Path)
    p.add_argument("diff",type=Path)
    p.add_argument("--apply",action="store_true")
    args=p.parse_args()
    if args.source.is_symlink() or not args.source.is_file():
        raise SystemExit("SRT_SOURCE_NOT_REGULAR_FILE")
    src=args.source.read_bytes()
    try: new=patched_source(src)
    except (ValueError,UnicodeDecodeError) as err:
        raise SystemExit("SRT_INLINE_INTEGRATION_REFUSED="+str(err))
    args.diff.parent.mkdir(parents=True,exist_ok=True)
    args.diff.write_text(
        "ORIGINAL_SHA256="+hashlib.sha256(src).hexdigest()+"\n"+
        "TEST_CANDIDATE_SHA256="+hashlib.sha256(new).hexdigest()+"\n"+
        "".join(difflib.unified_diff(src.decode().splitlines(True),
                                    new.decode().splitlines(True),
              fromfile="protected/GoW-SRT.cpp",tofile="candidate/GoW-SRT.cpp")))
    if args.apply: args.source.write_bytes(new)
    print("GOW_SRT_WAVE_UNIFORM_PRODUCER_PORT="+("STAGED_ONLY_IN_DISPOSABLE_WORKTREE" if args.apply else "SOURCE_PREFLIGHT_PASS"))
    print("ORIGINAL_ROOT_DEPENDENCY_PLANNER=RETAINED")
    print("INVALID_NONUNIFORM_EXPRESSIONS=REMAIN_GUEST_SIDE")
    print("EXACT_REVERSE_SOURCE_CHECK=PASS")

if __name__=="__main__":main()
