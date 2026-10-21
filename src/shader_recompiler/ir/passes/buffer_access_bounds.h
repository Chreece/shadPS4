// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <optional>
#include <unordered_map>
#include "shader_recompiler/buffer_access_range.h"
#include "shader_recompiler/info.h"
#include "shader_recompiler/ir/microinstruction.h"
#include "shader_recompiler/ir/passes/resource_pass.h"
#include "shader_recompiler/ir/program.h"

namespace Shader::Optimization {

// This pass bounds accesses, never the descriptor's guest-visible NUM_RECORDS.
// It runs on final IR so removed descriptor-construction loads cannot widen a
// binding. Unknown instructions and cyclic values conservatively retain it.
struct BufferRangeTraits {
    static bool IsU32(IR::Value value) { return value.Type() == IR::Type::U32; }
    static bool IsF32(IR::Value value) { return value.Type() == IR::Type::F32; }
    static BufferAccess::Op Opcode(const IR::Inst& inst) {
        switch (inst.GetOpcode()) {
        case IR::Opcode::BitwiseAnd32: return BufferAccess::Op::BitwiseAnd32;
        case IR::Opcode::IAdd32: return BufferAccess::Op::IAdd32;
        case IR::Opcode::IMul32: return BufferAccess::Op::IMul32;
        case IR::Opcode::ShiftRightLogical32: return BufferAccess::Op::ShiftRightLogical32;
        case IR::Opcode::ShiftLeftLogical32: return BufferAccess::Op::ShiftLeftLogical32;
        case IR::Opcode::BitFieldUExtract: return BufferAccess::Op::BitFieldUExtract;
        case IR::Opcode::SelectU32: return BufferAccess::Op::SelectU32;
        case IR::Opcode::ConvertS32F32: return BufferAccess::Op::ConvertS32F32;
        case IR::Opcode::ConvertU32F32: return BufferAccess::Op::ConvertU32F32;
        case IR::Opcode::Phi: return BufferAccess::Op::Phi;
        case IR::Opcode::CompositeExtractF32x4: return BufferAccess::Op::CompositeExtractF32x4;
        case IR::Opcode::ImageSampleDrefExplicitLod: return BufferAccess::Op::ImageSampleDrefExplicitLod;
        case IR::Opcode::ImageSampleDrefImplicitLod: return BufferAccess::Op::ImageSampleDrefImplicitLod;
        case IR::Opcode::FPMul32: return BufferAccess::Op::FPMul32;
        case IR::Opcode::SelectF32: return BufferAccess::Op::SelectF32;
        default: return BufferAccess::Op::Unknown;
        }
    }
};
using BufferAccessBounds = BufferAccess::Analyzer<IR::Value, BufferRangeTraits>;

inline void CollectBufferAccessBounds(IR::Program& program) {
    using namespace BufferAccess;
    std::array<std::uint64_t, NUM_BUFFERS> ends{};
    BufferAccessBounds analysis;
    // Do not narrow a shader that also reads arbitrary guest addresses via
    // BDA: these accesses are outside the explicit descriptor-use graph.
    if (program.info.uses_dma) {
        for (auto& desc : program.info.buffers) desc.max_access_bytes = Unbounded;
        return;
    }
    for (auto* block : program.blocks) {
        for (const auto& inst : block->Instructions()) {
            if (!IsBufferInstruction(inst)) continue;
            const auto handle = inst.Arg(0);
            if (!handle.IsImmediate() || handle.Type() != IR::Type::U32 ||
                handle.U32() >= program.info.buffers.size()) {
                // A future indirect buffer-handle opcode must not be missed.
                ends.fill(Unbounded);
                continue;
            }
            const auto binding = handle.U32();
            const auto& desc = program.info.buffers[binding];
            if (desc.IsSpecial()) continue;
            if (desc.is_written || desc.table_span_index != ~0u) {
                ends[binding] = Unbounded;
                continue;
            }
            unsigned bytes = 4, components = 1;
            switch (inst.GetOpcode()) {
            case IR::Opcode::LoadBufferU8: bytes = 1; break;
            case IR::Opcode::LoadBufferU16: bytes = 2; break;
            case IR::Opcode::LoadBufferU64: bytes = 8; break;
            case IR::Opcode::LoadBufferU32:
            case IR::Opcode::LoadBufferF32:
            case IR::Opcode::ReadConstBuffer: break;
            case IR::Opcode::LoadBufferU32x2:
            case IR::Opcode::LoadBufferF32x2: components = 2; break;
            case IR::Opcode::LoadBufferU32x3:
            case IR::Opcode::LoadBufferF32x3: components = 3; break;
            case IR::Opcode::LoadBufferU32x4:
            case IR::Opcode::LoadBufferF32x4: components = 4; break;
            default:
                ends[binding] = Unbounded;
                continue;
            }
            auto address = inst.Arg(1);
            if (inst.Flags<IR::BufferInstInfo>().is_predicated) {
                const auto* pair = address.TryInst();
                if (!pair || pair->GetOpcode() != IR::Opcode::CompositeConstructU32x2) {
                    ends[binding] = Unbounded;
                    continue;
                }
                // Include addresses even when the predicate would reject them.
                address = pair->Arg(0);
            }
            ends[binding] = std::max(ends[binding], EndBytes(analysis.Get(address), bytes, components));
        }
    }
    for (unsigned i = 0; i < program.info.buffers.size(); ++i) {
        auto& desc = program.info.buffers[i];
        desc.max_access_bytes = ends[i] == 0 ? Unbounded : ends[i];
    }
}

} // namespace Shader::Optimization
