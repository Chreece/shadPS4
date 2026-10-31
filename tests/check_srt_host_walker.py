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
assert "if (!HasUnconditionalPhiValue(inst))" in s
assert 'inst->Arg(i) != first' in s
assert "std::unordered_set<const IR::Inst*> visited;" in s
assert "!visited.insert(current).second" in s
assert "const IR::Inst* next = first.TryInst();" in s
assert "ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, common))" in s
# Never allow a non-trivial Phi, ReadLane, Ballot or arbitrary lane index
# as a pre-dispatch CPU evaluator; they are GPU-only source values.
start=s.index("static bool IsAllowedOffsetInstruction(")
end=s.index("static bool ComputeOffset(",start)
allowed=s[start:end]
for x in ("case IR::Opcode::ReadLane:", "case IR::Opcode::ReadFirstLane:",
          "case IR::Opcode::Ballot:", "case IR::Opcode::SelectU32:"):
    assert x not in allowed
assert "return HasUnconditionalPhiValue(inst)" in allowed
f=s[s.index("static bool EmitComputeOffsetShiftLeftLogical32("):
    s.index("static bool EmitComputeOffsetShiftRightLogical32(")]
assert "c.shl(reg, inst->Arg(0).U32());" not in f
assert "c.mov(ecx, reg);" in f
assert "c.mov(reg, inst->Arg(0).U32());" in f
assert "c.shl(reg, cl);" in f
assert "ASSERT_MSG(!program.info.uses_ordered_gds" in (
    root/"src/shader_recompiler/backend/spirv/emit_spirv.cpp").read_text()
print("UNIVERSAL_HOST_SRT_FINDILSB_AND_CONSTANT_SHIFT=PASS")
print("DYNAMIC_GPU_PHI_STAYS_ON_GPU=PASS")
print("ORDERED_GDS_GUEST_COUNTER_NOT_FABRICATED=PASS")
