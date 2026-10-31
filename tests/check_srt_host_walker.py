#!/usr/bin/env python3
"""Static safety contract for universal host-SRT address evaluation.

A host walker may evaluate only constants and host-visible resource data.
It cannot select a GPU control-flow Phi predecessor or guess waterfall lanes.
Pair this with the executable Xbyak JIT and native shadPS4 compilation.
"""
from pathlib import Path
import re

root=Path(__file__).resolve().parents[1]
s=(root/"src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp").read_text()
gvn=(root/"src/shader_recompiler/ir/srt_gvn_table.h").read_text()
assert "case IR::Opcode::FindILsb32:" in s
assert "c.bsf(reg, reg);" in s
assert "c.cmovz(reg, ecx);" in s
assert "c.mov(ecx, ~u32{0});" in s
assert "value ? std::countr_zero(value) : ~u32{0}" in s
assert "case IR::Opcode::FindILsb32:" in gvn
assert "HasUnconditionalPhiValue" in s
assert "if (!HasUnconditionalPhiValue(inst) && !HasHostEquivalentPhiInputs(inst))" in s
assert 'for (std::size_t i = 1; i < current->NumArgs(); ++i)' in s
assert 'if (current->Arg(i) != first)' in s
assert "std::unordered_set<const IR::Inst*> visited;" in s
assert "!visited.insert(current).second" in s
assert "const IR::Inst* next = first.TryInst();" in s
assert "ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, common))" in s
# Never allow a non-trivial Phi, ReadLane, Ballot or arbitrary lane index
# as a pre-dispatch CPU evaluator; they are GPU-only source values.
start=s.index("static bool IsAllowedOffsetInstruction(")
end=s.index("static bool ComputeOffset(",start)
allowed=s[start:end]
for x in ("case IR::Opcode::ReadLane:", "case IR::Opcode::Ballot:",
          "case IR::Opcode::SelectU32:"):
    assert x not in allowed
assert "return HasUnconditionalPhiValue(inst) || HasHostEquivalentPhiInputs(inst)" in allowed
# ReadFirstLane is safe on the host only when every active lane necessarily
# holds the same scalar GetUserData-derived value. GPU-dependent index paths
# stay GPU-only; no selected waterfall predecessor may be guessed.
proof=s[s.index("static std::optional<std::string> HostUniformSrtExpression("):start]
assert "case IR::Opcode::GetUserData:" in proof
assert "case IR::Opcode::ReadFirstLane:" in proof
assert "case IR::Opcode::Phi:" in proof
assert "HasHostEquivalentPhiInputs(" in proof
assert "!visiting.insert(inst).second" in proof
assert "depth >= 64" in proof
assert "std::optional<std::string>" in proof
assert "key += std::to_string(part->size())" in proof
assert "if (!candidate || *candidate != *first)" in proof
assert "visiting.erase(inst)" in proof
assert "inst->Flags<u32>()" in proof
assert 'return "u" + std::to_string' in proof
assert "key.size() + part->size() >= 4096" in proof
for disallowed in ("case IR::Opcode::ReadConst:", "case IR::Opcode::ReadConstBuffer:",
                   "case IR::Opcode::ReadLane:", "case IR::Opcode::Ballot:",
                   "case IR::Opcode::GetVectorRegister:", "case IR::Opcode::GetExec:"):
    assert disallowed not in proof
assert "case IR::Opcode::ReadFirstLane:\n        return IsHostUniformLaneValue(inst->Arg(0));" in allowed
compute=s[s.index("static bool ComputeOffset(", end):s.index("static inline bool PushPtr(")]
assert "if (!IsHostUniformLaneValue(input))" in compute
assert "if (!HasUnconditionalPhiValue(inst) && !HasHostEquivalentPhiInputs(inst))" in compute
assert "phi_inputs" in compute
assert "phi_first_source" in compute
assert "GPU_DYNAMIC_SRT_REQUIRES_SHADER_RESOURCE_LOOKUP" in compute
assert "ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, input))" in compute
assert "pass_info.shader_hash = program.info.pgm_hash;" in s
# Rejected guest wave-dependent offsets must remain rejected, but log the
# real shader ID and producer opcode for every host walker failure.
visit = s[s.index("static void VisitPointer("):s.index("static void GenerateSrtProgram(")]
assert visit.count("HOST_SRT_OFFSET_UNRESOLVED shader={:#x}") == 2
assert "origin=pointer " in visit and "origin=resource " in visit
assert visit.count("pass_info.shader_hash, magic_enum::enum_name(subtree->GetOpcode())") == 2
assert "c.setSize(checkpoint);" in visit
assert "Failed to compute offset for SRT walker" not in visit
print("HOST_SRT_REJECTION_HAS_SHADER_OPCODE_PROVENANCE=PASS")
f=s[s.index("static bool EmitComputeOffsetShiftLeftLogical32("):
    s.index("static bool EmitComputeOffsetShiftRightLogical32(")]
assert "c.shl(reg, inst->Arg(0).U32());" not in f
assert "c.mov(ecx, reg);" in f
assert "c.mov(reg, inst->Arg(0).U32());" in f
assert "c.shl(reg, cl);" in f
assert "ASSERT_MSG(!program.info.uses_ordered_gds" in (
    root/"src/shader_recompiler/backend/spirv/emit_spirv.cpp").read_text()
print("UNIVERSAL_HOST_SRT_FINDILSB_AND_CONSTANT_SHIFT=PASS")
print("HOST_LANE_UNIFORM_ONLY_GPU_DEPENDENT_SRT_STAYS_GPU=PASS")
print("HOST_SCALAR_EQUIVALENT_PHI_NO_GPU_BRANCH_GUESS=PASS")
print("DYNAMIC_GPU_PHI_STAYS_ON_GPU=PASS")
print("ORDERED_GDS_GUEST_COUNTER_NOT_FABRICATED=PASS")
