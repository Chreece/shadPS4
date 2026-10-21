// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include "shader_recompiler/buffer_access_range.h"

using namespace Shader::BufferAccess;


struct Node;
struct Value {
    const Node* node{};
    bool immediate{};
    bool floating{};
    std::uint32_t integer{};
    float real{};
    bool IsImmediate() const { return immediate; }
    const Node* TryInst() const { return node; }
    std::uint32_t U32() const { assert(!floating); return integer; }
    float F32() const { assert(floating); return real; }
    static Value Ref(const Node& n) { return {&n, false, false, 0, 0}; }
    static Value U(std::uint32_t n) { return {nullptr, true, false, n, 0}; }
    static Value F(float f) { return {nullptr, true, true, 0, f}; }
};
struct Node {
    Op op{Op::Unknown};
    std::array<Value, 3> args{};
    unsigned count = 2;
    Value Arg(unsigned index) const { return args.at(index); }
    unsigned NumArgs() const { return count; }
};
struct Traits {
    static bool IsU32(Value v) { return !v.floating; }
    static bool IsF32(Value v) { return v.floating; }
    static Op Opcode(const Node& n) { return n.op; }
};

void TestAnalyzer() {
    Node comparison{Op::ImageSampleDrefExplicitLod};
    Node red{Op::CompositeExtractF32x4, {Value::Ref(comparison), Value::U(0)}};
    Node scaled{Op::FPMul32, {Value::F(4), Value::Ref(red)}};
    Node converted{Op::ConvertS32F32, {Value::Ref(scaled)}};
    Node stride{Op::IMul32, {Value::Ref(converted), Value::U(5)}};
    Node last{Op::IAdd32, {Value::Ref(stride), Value::U(47)}};
    Analyzer<Value, Traits> analysis;
    assert((analysis.Get(Value::Ref(converted)) == Range{0, 4}));
    assert((analysis.Get(Value::Ref(last)) == Range{47, 67}));
    assert(EndBytes(analysis.Get(Value::Ref(last)), 4, 1) == 272);
    // This is not permission to cap ordinary depth values to [0,1].
    comparison.op = Op::Unknown;
    assert((Analyzer<Value, Traits>{}.Get(Value::Ref(converted)) == Range{}));
    comparison.op = Op::ImageSampleDrefImplicitLod;
    assert((Analyzer<Value, Traits>{}.Get(Value::Ref(converted)) == Range{0, 4}));
    for (float factor : {-4.0f, 0.0f, 3.0f,
                         std::numeric_limits<float>::infinity(),
                         std::numeric_limits<float>::quiet_NaN()}) {
        scaled.args[0] = Value::F(factor);
        assert((Analyzer<Value, Traits>{}.Get(Value::Ref(converted)) == Range{}));
    }
    scaled.args[0] = Value::F(4);
    red.args[1] = Value::U(1);
    assert((Analyzer<Value, Traits>{}.Get(Value::Ref(converted)) == Range{}));
    red.args[1] = Value::U(0);
    Node phi{Op::Phi};
    Node step{Op::IAdd32, {Value::Ref(phi), Value::U(1)}};
    phi.args = {Value::U(0), Value::Ref(step)};
    assert((Analyzer<Value, Traits>{}.Get(Value::Ref(phi)) == Range{}));
    Node masked{Op::BitwiseAnd32, {Value::Ref(phi), Value::U(3)}};
    assert((Analyzer<Value, Traits>{}.Get(Value::Ref(masked)) == Range{0, 3}));
    Node choose{Op::SelectU32, {Value::Ref(phi), Value::U(7), Value::U(100)}, 3};
    assert((Analyzer<Value, Traits>{}.Get(Value::Ref(choose)) == Range{7, 100}));
    Node unsupported;
    choose.args[2] = Value::Ref(unsupported);
    assert((Analyzer<Value, Traits>{}.Get(Value::Ref(choose)) == Range{}));
    // Exhaust binary fractions and endpoints of the depth comparison result.
    for (std::uint32_t n = 0; n <= 1048576; ++n) {
        const float depth_result = static_cast<float>(n) / 1048576.0f;
        const auto index = static_cast<std::int32_t>(4.0f * depth_result);
        const auto end = (std::uint64_t(index) * 5 + 47 + 1) * 4;
        assert(end <= 272);
    }
    std::cout << "BUFFER_RANGE_ANALYZER=PASS comparison_table_end=272 "
                 "ordinary_depth_unknown=YES cycles_conservative=YES "
                 "depth_fractions_checked=1048577\n";
}

int main() {
    TestAnalyzer();
    static_assert(DescriptorBytes(20, 0xffffffffu) == 85899345900ull);
    static_assert(DescriptorBytes(16, 0x10000000u) == 4294967296ull);
    static_assert(DescriptorBytes(0, 0xffffffffu) == 4294967295ull);
    static_assert(DescriptorBytes(16383, 0xffffffffu) == 70364449193985ull);
    static_assert(DescriptorBytes(16, 0) == 0);
    static_assert(EndBytes(ShiftRight(Multiply(Extract(And({}, Constant(3)), 0, 24),
                                                     Constant(8)), 2), 4, 2) == 32);
    static_assert(EndBytes({}, 4, 1) == Unbounded);
    static_assert(EndBytes(Constant(7), 8, 1) == 64);
    static_assert(Add({0, 0xffffffffu}, Constant(1)) == Range{});
    static_assert(Multiply({1, 0x80000000u}, Constant(2)) == Range{});
    static_assert(Add(Constant(0xffffffffu), Constant(1)) == Constant(0));
    static_assert(ShiftLeft({0, 0x80000000u}, 1) == Range{});
    static_assert(Extract({}, 0, 32) == Range{});
    static_assert(Extract({}, 32, 1) == Range{});
    static_assert(Extract({}, 0, 0) == Constant(0));
    static_assert(EndBytes(Join(Constant(2), Constant(8)), 4, 4) == 48);
    std::mt19937 rng(0x42554646u);
    std::uint64_t old_mismatches = 0, checked = 0;
    const auto contained = [](Range r, std::uint32_t v) { return r.lo <= v && v <= r.hi; };
    for (unsigned i = 0; i < 200000; ++i) {
        const std::uint32_t a = rng(), b = rng();
        const Range ar{std::min(a, b), std::max(a, b)};
        const std::uint32_t c = rng(), d = rng();
        const Range br{std::min(c, d), std::max(c, d)};
        const std::uint32_t x = ar.lo + std::uint32_t(rng() % (std::uint64_t(ar.hi) - ar.lo + 1));
        const std::uint32_t y = br.lo + std::uint32_t(rng() % (std::uint64_t(br.hi) - br.lo + 1));
        assert(contained(Add(ar, br), x + y));
        assert(contained(Multiply(ar, br), x * y));
        assert(contained(And(ar, br), x & y));
        const unsigned bits = rng() % 32;
        assert(contained(ShiftRight(ar, bits), x >> bits));
        assert(contained(ShiftLeft(ar, bits), x << bits));
        const unsigned width = rng() % (33 - bits);
        const auto mask = width == 32 ? MaxU32 : (std::uint32_t{1} << width) - 1;
        assert(contained(Extract(ar, bits, width), (x >> bits) & mask));
        // Exercise the actual old shifted-address optimization independently
        // against uint32 byte arithmetic. The corrected result must match.
        const unsigned shift = rng() % 4;
        const std::uint32_t stride = (rng() & 0x3fffu) & ~((1u << shift) - 1);
        const std::uint32_t offset = rng() & ~((1u << shift) - 1);
        const std::uint32_t expected = std::uint32_t(a * stride + offset) >> shift;
        const std::uint32_t old = a * (stride >> shift) + (offset >> shift);
        old_mismatches += old != expected;
        assert((old & ScaledAddressMask(shift)) == expected);
        ++checked;
    }
    assert(old_mismatches > 0);
    std::cout << "BUFFER_ACCESS_RANGE=PASS random_cases=" << checked
              << " old_scaled_address_failures=" << old_mismatches << '\n';
    std::cout << "BOUNDED_MASK_ADDRESS_PROOF=PASS maximum_read_end=32 "
                 "exact_descriptor_bytes=85899345900\n";
    return 0;
}
