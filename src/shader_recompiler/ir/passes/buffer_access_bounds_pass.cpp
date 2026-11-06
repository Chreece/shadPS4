// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <cmath>
#include <limits>
#include <optional>
#include <unordered_map>

#include "shader_recompiler/ir/passes/ir_passes.h"

namespace Shader::Optimization {
namespace {

class AddressBounds {
public:
    std::optional<u32> Integer(IR::Value value, u32 depth = 0) {
        if (value.Type() != IR::Type::U32 || depth >= 64) {
            return {};
        }
        if (value.IsImmediate()) {
            return value.U32();
        }
        const auto* inst = value.Inst();
        const auto [it, inserted] = integers.try_emplace(inst, std::nullopt);
        if (!inserted) {
            return it->second;
        }
        const auto bound = IntegerInstruction(*inst, depth + 1);
        integers[inst] = bound;
        return bound;
    }

private:
    std::optional<u32> IntegerInstruction(const IR::Inst& inst, u32 depth) {
        const auto arg = [&](u32 i) { return Integer(inst.Arg(i), depth); };
        switch (inst.GetOpcode()) {
        case IR::Opcode::BitwiseAnd32: {
            const auto a = arg(0);
            const auto b = arg(1);
            if (a && b) {
                return std::min(*a, *b);
            }
            return a ? a : b;
        }
        case IR::Opcode::IAdd32:
        case IR::Opcode::IMul32: {
            const auto a = arg(0);
            const auto b = arg(1);
            if (a && b) {
                const u64 result =
                    inst.GetOpcode() == IR::Opcode::IAdd32 ? u64{*a} + *b : u64{*a} * *b;
                if (result <= std::numeric_limits<u32>::max()) {
                    return u32(result);
                }
            }
            return {};
        }
        case IR::Opcode::ShiftRightLogical32:
        case IR::Opcode::ShiftLeftLogical32: {
            const auto shift = inst.Arg(1);
            const auto a = arg(0);
            if (a && shift.IsImmediate() && shift.U32() < 32) {
                const u64 result = inst.GetOpcode() == IR::Opcode::ShiftRightLogical32
                                       ? u64{*a} >> shift.U32()
                                       : u64{*a} << shift.U32();
                if (result <= std::numeric_limits<u32>::max()) {
                    return u32(result);
                }
            }
            return {};
        }
        case IR::Opcode::BitFieldUExtract: {
            const auto offset = inst.Arg(1);
            const auto count = inst.Arg(2);
            if (offset.IsImmediate() && count.IsImmediate() && offset.U32() <= 32 &&
                count.U32() <= 32 - offset.U32()) {
                if (count.U32() == 0) {
                    return 0;
                }
                const auto mask = u32((u64{1} << count.U32()) - 1);
                const auto a = arg(0);
                return a ? std::min(*a >> offset.U32(), mask) : mask;
            }
            return {};
        }
        case IR::Opcode::SelectU32: {
            const auto a = arg(1);
            const auto b = arg(2);
            return a && b ? std::optional{std::max(*a, *b)} : std::nullopt;
        }
        case IR::Opcode::ConvertS32F32:
        case IR::Opcode::ConvertU32F32: {
            const auto bound = NonnegativeFloat(inst.Arg(0), depth);
            const double limit = inst.GetOpcode() == IR::Opcode::ConvertS32F32
                                     ? double(std::numeric_limits<s32>::max())
                                     : double(std::numeric_limits<u32>::max());
            if (bound && double(*bound) <= limit) {
                return u32(*bound);
            }
            return {};
        }
        default:
            return {};
        }
    }

    std::optional<float> NonnegativeFloat(IR::Value value, u32 depth) {
        if (value.Type() != IR::Type::F32 || depth >= 64) {
            return {};
        }
        if (value.IsImmediate()) {
            const auto f = value.F32();
            return std::isfinite(f) && f >= 0 ? std::optional{f} : std::nullopt;
        }
        const auto* inst = value.Inst();
        const auto [it, inserted] = floats.try_emplace(inst, std::nullopt);
        if (!inserted) {
            return it->second;
        }
        const auto bound = FloatInstruction(*inst, depth + 1);
        floats[inst] = bound;
        return bound;
    }

    std::optional<float> FloatInstruction(const IR::Inst& inst, u32 depth) {
        switch (inst.GetOpcode()) {
        case IR::Opcode::CompositeExtractF32x4: {
            const auto* source = inst.Arg(0).TryInst();
            if (source && inst.Arg(1).IsImmediate() && inst.Arg(1).U32() < 4 &&
                (source->GetOpcode() == IR::Opcode::ImageSampleDrefExplicitLod ||
                 source->GetOpcode() == IR::Opcode::ImageSampleDrefImplicitLod)) {
                return inst.Arg(1).U32() == 0 ? 1.f : 0.f;
            }
            return {};
        }
        case IR::Opcode::FPMul32: {
            const auto a = NonnegativeFloat(inst.Arg(0), depth);
            const auto b = NonnegativeFloat(inst.Arg(1), depth);
            if (a && b) {
                const float result =
                    std::nextafter(*a * *b, std::numeric_limits<float>::infinity());
                if (std::isfinite(result)) {
                    return result;
                }
            }
            return {};
        }
        default:
            return {};
        }
    }

    std::unordered_map<const IR::Inst*, std::optional<u32>> integers;
    std::unordered_map<const IR::Inst*, std::optional<float>> floats;
};

std::pair<u32, u32> AccessSize(IR::Opcode opcode) {
    switch (opcode) {
    case IR::Opcode::LoadBufferU8:
    case IR::Opcode::StoreBufferU8:
        return {1, 1};
    case IR::Opcode::LoadBufferU16:
    case IR::Opcode::StoreBufferU16:
        return {2, 2};
    case IR::Opcode::LoadBufferU64:
    case IR::Opcode::StoreBufferU64:
    case IR::Opcode::BufferAtomicIAdd64:
    case IR::Opcode::BufferAtomicSMin64:
    case IR::Opcode::BufferAtomicUMin64:
    case IR::Opcode::BufferAtomicSMax64:
    case IR::Opcode::BufferAtomicUMax64:
        return {8, 8};
    case IR::Opcode::LoadBufferU32x2:
    case IR::Opcode::LoadBufferF32x2:
    case IR::Opcode::StoreBufferU32x2:
    case IR::Opcode::StoreBufferF32x2:
    case IR::Opcode::StoreBufferU32x2Guarded:
        return {4, 8};
    case IR::Opcode::LoadBufferU32x3:
    case IR::Opcode::LoadBufferF32x3:
    case IR::Opcode::StoreBufferU32x3:
    case IR::Opcode::StoreBufferF32x3:
    case IR::Opcode::StoreBufferU32x3Guarded:
        return {4, 12};
    case IR::Opcode::LoadBufferU32x4:
    case IR::Opcode::LoadBufferF32x4:
    case IR::Opcode::StoreBufferU32x4:
    case IR::Opcode::StoreBufferF32x4:
    case IR::Opcode::StoreBufferU32x4Guarded:
        return {4, 16};
    case IR::Opcode::LoadBufferFormatF32:
    case IR::Opcode::StoreBufferFormatF32:
        return {0, 0};
    default:
        return {4, 4};
    }
}

} // namespace

void BufferAccessBoundsPass(IR::Program& program) {
    std::array<u64, NUM_BUFFERS> bounds{};
    AddressBounds addresses;
    for (const auto* block : program.post_order_blocks) {
        for (const auto& inst : block->Instructions()) {
            if (!IsBufferInstruction(inst) || !inst.Arg(0).IsImmediate()) {
                continue;
            }
            const u32 handle = inst.Arg(0).U32();
            ASSERT(handle < program.info.buffers.size());
            auto& bound = bounds[handle];
            if (bound == std::numeric_limits<u64>::max()) {
                continue;
            }
            const auto address = addresses.Integer(inst.Arg(1));
            const auto [unit, width] = AccessSize(inst.GetOpcode());
            bound = address && unit ? std::max(bound, u64{*address} * unit + width)
                                    : std::numeric_limits<u64>::max();
        }
    }
    for (u32 i = 0; i < program.info.buffers.size(); ++i) {
        auto& buffer = program.info.buffers[i];
        if (!buffer.IsSpecial() && bounds[i] != 0) {
            buffer.accessed_size = bounds[i];
        }
    }
}

} // namespace Shader::Optimization
