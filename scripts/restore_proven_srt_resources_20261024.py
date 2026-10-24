#!/usr/bin/env python3
"""Restore generic SRT/resource semantics proven in earlier GoT source.

This is a four-path structural backport onto the exact built native source,
not a wholesale old renderer, game override, or a new shader exception.
Fails closed if a source preimage changes. Keeps DynamoRIO rejected.
"""
from __future__ import annotations
import re

FLAT="src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
COLLECT="src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp"
SPIRV="src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp"
FILES=(FLAT,COLLECT,SPIRV)

def once(text,old,new,label):
    n=text.count(old)
    if n!=1:raise RuntimeError("PROVEN_SRT_ANCHOR_CHANGED="+label+":"+str(n))
    return text.replace(old,new,1)

def function(text,start,label):
    idx=text.find(start)
    if idx<0:raise RuntimeError("PROVEN_SRT_FUNCTION_MISSING="+label)
    brace=text.find("{",idx)
    if brace<0:raise RuntimeError("PROVEN_SRT_FUNCTION_BRACE_MISSING="+label)
    depth=0
    for j in range(brace,len(text)):
        if text[j]=="{":depth+=1
        elif text[j]=="}":depth-=1
        if depth==0:return text[idx:j+1]
    raise RuntimeError("PROVEN_SRT_FUNCTION_UNBALANCED="+label)

def transform_flat(text):
    marker='static bool IsHostUniformSrtOffset(const IR::Value& off_dw) {'
    if text.count(marker)!=1 or text.count(
            '#include "shader_recompiler/ir/passes/srt_dependency_codegen.inc"')!=1:
        raise RuntimeError("SOURCE_IS_NOT_VERIFIED_SHARED_SRT_GENERATION")

    # The October 24 reintegrated helper incorrectly accepted ReadConst
    # without checking its address/index and omitted validated ReadFirstLane.
    # Keep the shared scheduler, correct only the missing proof and lowering.
    fn=function(text,marker,"HOST_UNIFORM_PROOF")
    if 'std::unordered_set<const IR::Inst*> active;' not in fn:
        raise RuntimeError("RECONSTRUCTED_UNIFORMITY_CHECK_CHANGED")
    if 'case IR::Opcode::ReadFirstLane:' in fn:
        raise RuntimeError("UNIFORMITY_RESTORE_ALREADY_APPLIED")
    old='''        case IR::Opcode::GetUserData:
        case IR::Opcode::ReadConst:
        case IR::Opcode::ReadConstBuffer:
            // A host scalar root or a previously flattened SRT read.
            break;
        default:'''
    new='''        case IR::Opcode::GetUserData:
            // SGPR user data is scalar for every GPU lane.
            break;
        case IR::Opcode::ReadConst:
        case IR::Opcode::ReadConstBuffer:
            // A scalar load is uniform only if its pointer and index are.
            uniform = self(self, inst->Arg(0)) && self(self, inst->Arg(1));
            break;
        case IR::Opcode::ReadLane:
        case IR::Opcode::ReadFirstLane:
            // A lane read may be evaluated by the host only when its source
            // is independent of the GPU lane; the lane selector is irrelevant.
            uniform = self(self, inst->Arg(0));
            break;
        default:'''
    fn=once(fn,old,new,"HOST_UNIFORM_DEPENDENCIES")
    text=text.replace(function(text,marker,"HOST_UNIFORM_PROOF"),fn,1)

    # IsAllowedOffsetInstruction is the first check on all non-immediate
    # inputs in the helper above. Mirror the earlier validated opcode gate.
    legal=function(text,'static bool IsAllowedOffsetInstruction(',
                   "SRT_LEGAL_OFFSET_OPCODE")
    legal=once(legal,'    case IR::Opcode::ReadConstBuffer:\n',
               '    case IR::Opcode::ReadConstBuffer:\n'
               '    case IR::Opcode::ReadLane:\n'
               '    case IR::Opcode::ReadFirstLane:\n',
               "LANE_OPCODE_ALLOWED")
    text=text.replace(function(text,'static bool IsAllowedOffsetInstruction(',
                               'SRT_LEGAL_OFFSET_OPCODE'),legal,1)

    forward='''static bool ComputeOffset(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,
                          const IR::Value& off_dw);'''
    text=once(text,forward,
              'static bool IsHostUniformSrtOffset(const IR::Value& off_dw);\n'
              +forward,"SRT_PROOF_FORWARD_DECL")

    old_header='''    ASSERT(reg != ecx && reg != edx);
    auto inst = off_dw.Inst();
    switch (inst->GetOpcode()) {'''
    new_header='''    ASSERT(reg != ecx && reg != edx);
    if (off_dw.IsImmediate()) {
        if (off_dw.Type() != IR::Type::U32) {
            return false;
        }
        c.mov(reg, off_dw.U32());
        return true;
    }
    auto inst = off_dw.Inst();
    switch (inst->GetOpcode()) {'''
    offset=function(text,'''static bool ComputeOffset(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,
                          const IR::Value& off_dw) {''',"SRT_OFFSET_GENERATOR")
    offset=once(offset,old_header,new_header,"SRT_IMMEDIATE_OFFSET")
    lane='''    case IR::Opcode::ReadLane:
    case IR::Opcode::ReadFirstLane:
        // Equivalent to a GPU lane read only when the source is wave-uniform.
        // Divergent sources stay shader-side; no lane-zero substitution.
        if (!IsHostUniformSrtOffset(inst->Arg(0))) {
            return false;
        }
        return ComputeOffset(c, reg, pass_info, inst->Arg(0));
'''
    offset=once(offset,'    case IR::Opcode::IAdd32:\n',
                lane+'    case IR::Opcode::IAdd32:\n',"HOST_LOWER_UNIFORM_LANE")
    text=text.replace(function(text,'''static bool ComputeOffset(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,
                          const IR::Value& off_dw) {''',"SRT_OFFSET_GENERATOR"),offset,1)

    for marker in ("case IR::Opcode::ReadFirstLane:", "case IR::Opcode::ReadLane:",
                   "IsHostUniformSrtOffset(inst->Arg(0))",
                   "uniform = self(self, inst->Arg(0)) && self(self, inst->Arg(1))",
                   '#include "shader_recompiler/ir/passes/srt_dependency_codegen.inc"'):
        if marker not in text:
            raise RuntimeError("PORT_LOST_PROVEN_SRT_FEATURE="+marker)
    return text

def transform_spirv(text):
    # Official upstream may read flattened slot 0 when DMA is disabled.
    # The earlier GoT proof requires GPU guest address translation for an
    # unflattened S_LOAD irrespective of the optional speed setting.
    marker='Id EmitReadConst(EmitContext& ctx, IR::Inst* inst, Id addr, Id offset) {'
    old=function(text,marker,"READCONST_SPIRV")
    before='''    if (!EmulatorSettings.IsDirectMemoryAccessEnabled()) {
        return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));
    }
    if (flatbuf_off_dw == 0) {
        return ctx.OpFunctionCall(ctx.U32[1], ctx.read_const_dynamic, addr, offset);
    } else {
        return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));
    }'''
    after='''    if (flatbuf_off_dw == 0) {
        // Zero is a sentinel for a read not flattened by the SRT walker.
        // A flat-buffer slot-zero fetch is never equivalent to this read.
        ASSERT_MSG(ctx.info.uses_dma && Sirit::ValidId(ctx.read_const_dynamic),
                   "Unflattened ReadConst needs GPU guest-address translation");
        return ctx.OpFunctionCall(ctx.U32[1], ctx.read_const_dynamic, addr, offset);
    }
    return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));'''
    replacement=once(old,before,after,"GUEST_READCONST_NOT_FLATTENED")
    return once(text,old,replacement,"READCONST_FUNCTION_RESTORE")

def transform_collect(text):
    old='''    if (!EmulatorSettings.IsDirectMemoryAccessEnabled()) {
        info.uses_dma = false;
        info.readconst_types = Info::ReadConstType::None;
    }

'''
    if 'info.uses_dma = true;' not in text:
        raise RuntimeError("UNFLATTENED_READS_NOT_RECORDED")
    text=once(text,old,'', "RETAIN_MANDATORY_GUEST_READS")
    if 'if (info.uses_dma) {' not in text:
        raise RuntimeError("DMA_PAGE_TABLE_RESOURCE_REGISTRATION_MISSING")
    return text

def transform(path,text):
    if path==FLAT:return transform_flat(text)
    if path==COLLECT:return transform_collect(text)
    if path==SPIRV:return transform_spirv(text)
    raise RuntimeError("UNREVIEWED_SRT_BACKPORT_FILE="+path)

def verify(index_read):
    flat=index_read(FLAT)
    spirv=index_read(SPIRV)
    collector=index_read(COLLECT)
    expected={
        FLAT:("static bool IsHostUniformSrtOffset(", "ReadFirstLane",
              "IsHostUniformSrtOffset(inst->Arg(0))",
              "srt_dependency_codegen.inc"),
        SPIRV:("Unflattened ReadConst needs GPU guest-address translation",
               "flatbuf_off_dw == 0","ctx.read_const_dynamic"),
        COLLECT:("info.uses_dma = true;","if (info.uses_dma) {"),
    }
    for p,tokens in expected.items():
        content={FLAT:flat,SPIRV:spirv,COLLECT:collector}[p]
        for token in tokens:
            if token not in content:
                raise RuntimeError("VERIFIED_GENERIC_SRT_RESTORATION_MISSING="+p+":"+token)
    if 'if (!EmulatorSettings.IsDirectMemoryAccessEnabled())' in spirv[
            spirv.index('Id EmitReadConst('):spirv.index('Id EmitReadConstBuffer(')]:
        raise RuntimeError("GUEST_READCONST_OLD_OPTIONAL_PATH_REMAINS")
    if 'info.uses_dma = false;' in collector:
        raise RuntimeError("MANDATORY_GUEST_DMA_SUPPRESSED")
    return True

if __name__=='__main__':
    import pathlib,sys
    if sys.argv[1:]==['--self-test']:
        print('PROVEN_SRT_PORT_MODULE=READY')
    else:raise SystemExit('Usage: --self-test')
