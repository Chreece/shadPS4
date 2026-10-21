// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cstdint>
#include <cmath>
#include <optional>
#include <unordered_map>
#include <limits>

namespace Shader::BufferAccess {

inline constexpr std::uint64_t Unbounded = std::numeric_limits<std::uint64_t>::max();
inline constexpr std::uint32_t MaxU32 = std::numeric_limits<std::uint32_t>::max();

// Inclusive unsigned interval. Unknown expressions retain the entire domain.
// In particular, a wrapping add/multiply must never produce a small upper bound.
struct Range {
    std::uint32_t lo = 0;
    std::uint32_t hi = MaxU32;
    constexpr bool operator==(const Range&) const = default;
};

constexpr Range Constant(std::uint32_t value) { return {value, value}; }
constexpr Range Join(Range a, Range b) {
    return {std::min(a.lo, b.lo), std::max(a.hi, b.hi)};
}
constexpr Range And(Range a, Range b) {
    if (a.lo == a.hi && b.lo == b.hi) return Constant(a.lo & b.lo);
    return {0, std::min(a.hi, b.hi)};
}
constexpr Range Add(Range a, Range b) {
    if (a.lo == a.hi && b.lo == b.hi) return Constant(a.lo + b.lo);
    if (std::uint64_t(a.hi) + b.hi > MaxU32) return {};
    return {a.lo + b.lo, a.hi + b.hi};
}
constexpr Range Multiply(Range a, Range b) {
    if (a.lo == a.hi && b.lo == b.hi) return Constant(a.lo * b.lo);
    if (std::uint64_t(a.hi) * b.hi > MaxU32) return {};
    return {a.lo * b.lo, a.hi * b.hi};
}
constexpr Range ShiftRight(Range a, std::uint32_t bits) {
    if (bits >= 32) return {};
    return {a.lo >> bits, a.hi >> bits};
}
constexpr Range ShiftLeft(Range a, std::uint32_t bits) {
    if (bits >= 32) return {};
    if (a.lo == a.hi) return Constant(a.lo << bits);
    if (a.hi > (MaxU32 >> bits)) return {};
    return {a.lo << bits, a.hi << bits};
}
constexpr Range Extract(Range a, std::uint32_t offset, std::uint32_t bits) {
    if (offset >= 32 || bits > 32 - offset) return {};
    if (bits == 0) return Constant(0);
    const auto mask = bits == 32 ? MaxU32 : (std::uint32_t{1} << bits) - 1;
    if (a.lo == a.hi) return Constant((a.lo >> offset) & mask);
    return {0, std::min(a.hi >> offset, mask)};
}

constexpr std::uint64_t DescriptorBytes(std::uint32_t stride, std::uint32_t records) {
    return stride == 0 ? records : std::uint64_t(stride) * records;
}

// Index is the final element index, after lowering formatted loads to raw loads.
// Do not narrow unknown addresses, unused descriptors, atomics, or writers.
constexpr std::uint64_t EndBytes(Range index, std::uint32_t element_bytes,
                                  std::uint32_t components) {
    if (index.hi == MaxU32 || element_bytes == 0 || components == 0 ||
        element_bytes > 8 || components > 4) return Unbounded;
    return std::uint64_t(index.hi) * element_bytes +
           std::uint64_t(element_bytes) * components;
}

// (uint32(index * stride + offset) >> shift) must still wrap when its
// divisible operands are shifted before multiplication/addition.
constexpr std::uint32_t ScaledAddressMask(std::uint32_t shift) {
    return shift < 32 ? MaxU32 >> shift : 0;
}

enum class Op { Unknown, BitwiseAnd32, IAdd32, IMul32, ShiftRightLogical32, ShiftLeftLogical32, BitFieldUExtract, SelectU32, ConvertS32F32, ConvertU32F32, Phi, CompositeExtractF32x4, ImageSampleDrefExplicitLod, ImageSampleDrefImplicitLod, FPMul32, SelectF32 };

template <class Value, class Traits>
class Analyzer {
public:
    BufferAccess::Range Get(Value value, unsigned depth = 0) {
        using namespace BufferAccess;
        if (value.IsImmediate()) {
            return Traits::IsU32(value) ? Constant(value.U32()) : Range{};
        }
        const auto* inst = value.TryInst();
        if (!inst || depth >= 64) return {};
        if (const auto it = cache.find(inst); it != cache.end()) return it->second;
        cache.emplace(inst, Range{}); // An in-progress cycle is unknown, not zero.
        const auto arg = [&](unsigned i) { return Get(inst->Arg(i), depth + 1); };
        const auto literal = [&](unsigned i) {
            const auto v = inst->Arg(i);
            return v.IsImmediate() && Traits::IsU32(v);
        };
        Range result;
        switch (Traits::Opcode(*inst)) {
        case Op::BitwiseAnd32: result = And(arg(0), arg(1)); break;
        case Op::IAdd32: result = Add(arg(0), arg(1)); break;
        case Op::IMul32: result = Multiply(arg(0), arg(1)); break;
        case Op::ShiftRightLogical32:
            if (literal(1)) result = ShiftRight(arg(0), inst->Arg(1).U32());
            break;
        case Op::ShiftLeftLogical32:
            if (literal(1)) result = ShiftLeft(arg(0), inst->Arg(1).U32());
            break;
        case Op::BitFieldUExtract:
            if (literal(1) && literal(2)) {
                result = Extract(arg(0), inst->Arg(1).U32(), inst->Arg(2).U32());
            }
            break;
        case Op::SelectU32: result = Join(arg(1), arg(2)); break;
        case Op::ConvertS32F32:
        case Op::ConvertU32F32:
            if (const auto upper = NonnegativeFloatUpper(inst->Arg(0), depth + 1)) {
                result = {0, *upper};
            }
            break;
        case Op::Phi:
            if (inst->NumArgs() > 0) {
                result = arg(0);
                for (unsigned i = 1; i < inst->NumArgs(); ++i) result = Join(result, arg(i));
            }
            break;
        default: break;
        }
        cache[inst] = result;
        return result;
    }

private:
    // Comparison sampling is in [0,1], including linear comparison filtering
    // (Vulkan textures.html, Depth Compare Operation). Do not apply this to
    // an ordinary depth read: a floating-point depth image need not be [0,1].
    // Only exactly representable nonnegative power-of-two scaling is handled.
    // NaNs, negative values, arbitrary arithmetic and cycles remain unknown.
    std::optional<std::uint32_t> NonnegativeFloatUpper(Value value, unsigned depth) {
        if (depth >= 64) return {};
        if (value.IsImmediate()) {
            if (!Traits::IsF32(value)) return {};
            const float f = value.F32();
            if (!std::isfinite(f) || f < 0 || f > 16777216.0f) return {};
            return static_cast<std::uint32_t>(std::ceil(f));
        }
        const auto* inst = value.TryInst();
        if (!inst) return {};
        if (Traits::Opcode(*inst) == Op::CompositeExtractF32x4 &&
            inst->Arg(1).IsImmediate() && Traits::IsU32(inst->Arg(1)) &&
            inst->Arg(1).U32() == 0) {
            const auto* sample = inst->Arg(0).TryInst();
            if (sample && (Traits::Opcode(*sample) == Op::ImageSampleDrefExplicitLod ||
                           Traits::Opcode(*sample) == Op::ImageSampleDrefImplicitLod)) {
                return 1;
            }
        }
        if (Traits::Opcode(*inst) == Op::FPMul32) {
            for (unsigned side = 0; side < 2; ++side) {
                const auto literal = inst->Arg(side);
                if (!literal.IsImmediate() || !Traits::IsF32(literal)) continue;
                const float factor = literal.F32();
                int exponent{};
                if (!std::isfinite(factor) || factor < 1 || factor > 16777216.0f ||
                    std::frexp(factor, &exponent) != 0.5f) continue;
                const auto bound = NonnegativeFloatUpper(inst->Arg(1 - side), depth + 1);
                if (!bound) continue;
                const auto product = std::uint64_t(*bound) * static_cast<std::uint32_t>(factor);
                if (product <= 16777216) return static_cast<std::uint32_t>(product);
            }
        }
        if (Traits::Opcode(*inst) == Op::SelectF32) {
            const auto a = NonnegativeFloatUpper(inst->Arg(1), depth + 1);
            const auto b = NonnegativeFloatUpper(inst->Arg(2), depth + 1);
            if (a && b) return std::max(*a, *b);
        }
        return {};
    }

    std::unordered_map<const void*, BufferAccess::Range> cache;
};


} // namespace Shader::BufferAccess
