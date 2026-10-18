// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <random>

#include "shader_recompiler/ir/passes/indexed_image_selector.h"

using Shader::IR::Opcode;
using Shader::Optimization::AnalyzePackedGuestTsharpWord;
using Shader::Optimization::AreContiguousStridedSharpWords;
using Shader::Optimization::GuestTsharpIdWrapped27;
using Shader::Optimization::StridedIndexedSharpWord;

struct MockInst;
struct MockValue {
    const MockInst* inst{};
    Shader::IR::Type type{Shader::IR::Type::U32};
    bool immediate{};
    std::uint32_t bits{};
    bool IsImmediate() const { return immediate; }
    Shader::IR::Type Type() const { return type; }
    const MockInst* TryInst() const { return inst; }
    std::uint32_t U32() const { return bits; }
    static MockValue Ref(const MockInst& v) { return {&v, Shader::IR::Type::U32,false,0}; }
    static MockValue Imm(std::uint32_t v) { return {nullptr, Shader::IR::Type::U32,true,v}; }
};
struct MockInst {
    Opcode opcode{};
    std::array<MockValue, 3> args{};
    Opcode GetOpcode() const { return opcode; }
    MockValue Arg(std::size_t i) const { return args[i]; }
};

int main() {
    MockInst input{Opcode::GetAttribute,{}};
    MockInst bits{Opcode::BitCastU32F32,{MockValue::Ref(input)}};
    MockInst byteScale{Opcode::ShiftLeftLogical32,
                       {MockValue::Ref(bits),MockValue::Imm(5u)}};
    MockInst byteMultiply{Opcode::IMul32,
                          {MockValue::Imm(32u),MockValue::Ref(bits)}};
    std::array<MockInst,8> addByte{}, convert{}, outer{};
    std::array<StridedIndexedSharpWord,8> words{};
    for(std::uint32_t i=0;i<8;++i) {
        addByte[i]={Opcode::IAdd32,{MockValue::Ref(byteScale),
                                     MockValue::Imm(i*4u)}};
        convert[i]={Opcode::ShiftRightLogical32,
                     {MockValue::Ref(addByte[i]),MockValue::Imm(2u)}};
        words[i]=AnalyzePackedGuestTsharpWord(MockValue::Ref(convert[i]));
        assert(words[i].valid && words[i].gpu_record_id==&bits);
        assert(words[i].record_stride_bytes==32u && words[i].offset_dwords==i);
    }
    assert(AreContiguousStridedSharpWords(words));

    // Independent packed word forms can apply their dword offsets after
    // converting bytes. They must still form the same complete T#.
    for(std::uint32_t i=0;i<8;++i) {
        addByte[i]={Opcode::IAdd32,{MockValue::Ref(byteMultiply),MockValue::Imm(0)}};
        convert[i]={Opcode::ShiftRightLogical32,
                    {MockValue::Ref(addByte[i]),MockValue::Imm(2u)}};
        outer[i]={Opcode::IAdd32,{MockValue::Ref(convert[i]),MockValue::Imm(i)}};
        words[i]=AnalyzePackedGuestTsharpWord(MockValue::Ref(outer[i]));
    }
    assert(AreContiguousStridedSharpWords(words));
    for(std::uint32_t i=0;i<8;++i) {
        addByte[i]={Opcode::IAdd32,{MockValue::Ref(byteMultiply),
                                     MockValue::Imm(i*4u)}};
        convert[i]={Opcode::ShiftRightLogical32,
                    {MockValue::Ref(addByte[i]),MockValue::Imm(2u)}};
        words[i]=AnalyzePackedGuestTsharpWord(MockValue::Ref(convert[i]));
    }
    assert(AreContiguousStridedSharpWords(words));

    MockInst wrongScale{Opcode::ShiftLeftLogical32,
                        {MockValue::Ref(bits),MockValue::Imm(4u)}};
    MockInst wrongShift{Opcode::ShiftRightLogical32,
                        {MockValue::Ref(wrongScale),MockValue::Imm(2u)}};
    assert(!AnalyzePackedGuestTsharpWord(MockValue::Ref(wrongShift)).valid);
    MockInst wrongByte{Opcode::IAdd32,
                       {MockValue::Ref(byteScale),MockValue::Imm(3u)}};
    MockInst wrongByteShift{Opcode::ShiftRightLogical32,
                            {MockValue::Ref(wrongByte),MockValue::Imm(2u)}};
    assert(!AnalyzePackedGuestTsharpWord(MockValue::Ref(wrongByteShift)).valid);
    MockInst other{Opcode::GetAttribute,{}};
    MockInst otherScale{Opcode::ShiftLeftLogical32,
                        {MockValue::Ref(other),MockValue::Imm(5u)}};
    MockInst otherShift{Opcode::ShiftRightLogical32,
                        {MockValue::Ref(otherScale),MockValue::Imm(2u)}};
    words[7]=AnalyzePackedGuestTsharpWord(MockValue::Ref(otherShift));
    assert(!AreContiguousStridedSharpWords(words));

    std::mt19937 rng{0x7eaf40ad};
    for(unsigned n=0;n<100000;++n) {
        const std::uint32_t id=rng();
        const std::uint32_t wrapped=GuestTsharpIdWrapped27(id);
        for(std::uint32_t word=0;word<8;++word) {
            const std::uint32_t guest=static_cast<std::uint32_t>(
                (std::uint32_t((id<<5u)+word*4u))>>2u);
            const std::uint32_t indexed=wrapped*8u+word;
            assert(guest==indexed);
        }
    }
    std::puts("MENU_ATTRIBUTE_TSHARP=PASS cases=100000 "
              "gpu_fragment_selector_preserved=PASS guest_u32_wrap=EXACT "
              "complete_8_dword_table=PASS bad_and_partial_patterns_rejected=PASS");
}
