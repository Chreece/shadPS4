// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cstdint>
#include <cstdio>
#include <random>
#include "shader_recompiler/ir/passes/indexed_image_selector.h"
using Shader::Optimization::AnalyzeIndexedSharpWord;
using Shader::Optimization::AreContiguousIndexedSharpWords;
using Shader::Optimization::IndexedSharpWord;
using Shader::Optimization::IdentifyTsharpSelector;
using Shader::Optimization::GcnTextureDwordIndex;
using Shader::IR::Opcode;
struct MockInst;
struct MockValue {
    const MockInst* inst{};
    Shader::IR::Type type{Shader::IR::Type::U32};
    bool immediate{};
    std::uint32_t bits{};
    bool IsImmediate()const{return immediate;}
    Shader::IR::Type Type()const{return type;}
    const MockInst* TryInst()const{return inst;}
    std::uint32_t U32()const{return bits;}
    static MockValue Ref(const MockInst& x){return {&x,Shader::IR::Type::U32,false,0};}
    static MockValue Imm(std::uint32_t x){return {nullptr,Shader::IR::Type::U32,true,x};}
};
struct MockInst {
    Opcode opcode{};
    std::array<MockValue,3> args{};
    Opcode GetOpcode()const{return opcode;}
    MockValue Arg(std::size_t i)const{return args[i];}
};
int main(){
    MockInst selected{Opcode::SelectU32,{},};
    MockInst attr{Opcode::GetAttribute,{},};
    std::array<MockInst,8> offsets{};
    std::array<IndexedSharpWord,8> words{};
    for(unsigned i=0;i<8;++i){
        offsets[i]={Opcode::IAdd32,{MockValue::Ref(selected),MockValue::Imm(i)},};
        words[i]=AnalyzeIndexedSharpWord(MockValue::Ref(offsets[i]));
    }
    MockInst texId{Opcode::ReadConstBuffer,{},};
    MockInst mulBytes{Opcode::ShiftLeftLogical32,
                     {MockValue::Ref(texId),MockValue::Imm(5u)},};
    MockInst toDwords{Opcode::ShiftRightLogical32,
                      {MockValue::Ref(mulBytes),MockValue::Imm(2u)},};
    const auto decoding=IdentifyTsharpSelector(&toDwords);
    if(!decoding.normalized || decoding.source!=&texId ||
       decoding.descriptor_stride_words!=8u) return 8;
    MockInst invalidShift{Opcode::ShiftLeftLogical32,
                          {MockValue::Ref(texId),MockValue::Imm(4u)},};
    MockInst invalidConvert{Opcode::ShiftRightLogical32,
                            {MockValue::Ref(invalidShift),MockValue::Imm(2u)},};
    if(IdentifyTsharpSelector(&invalidConvert).normalized) return 9;
    if(!AreContiguousIndexedSharpWords(words))return 1;
    if(words[0].selector!=&selected || words[7].addend!=7)return 2;
    words[6].addend=55;
    if(AreContiguousIndexedSharpWords(words))return 3;
    words[6]=AnalyzeIndexedSharpWord(MockValue::Ref(offsets[6]));
    words[3].selector=&attr;
    if(AreContiguousIndexedSharpWords(words))return 4;
    words[3]=AnalyzeIndexedSharpWord(MockValue::Ref(offsets[3]));
    std::mt19937 gen{0xd3ac17f3u};
    for(unsigned iter=0;iter<100000;++iter){
        const auto start=gen();
        for(unsigned i=0;i<8;++i){
            offsets[i]={Opcode::IAdd32,{MockValue::Ref(selected),
                                        MockValue::Imm(start+i)},};
            words[i]=AnalyzeIndexedSharpWord(MockValue::Ref(offsets[i]));
        }
        if(!AreContiguousIndexedSharpWords(words))return 5;
        if(words[7].addend!=std::uint32_t(start+7))return 6;
        for(unsigned d=0;d<8;++d){
            if(GcnTextureDwordIndex(start,d,8u) !=
               std::uint32_t((std::uint32_t(start << 5u)>>2u)+d)) return 10;
        }
        if(GcnTextureDwordIndex(start,0,1u)!=start) return 11;
        words[gen()%8].selector=&attr;
        if(AreContiguousIndexedSharpWords(words))return 7;
    }
    std::puts("INDEXED_TSHARP_SELECTOR=PASS 100000_affine_tables "
              "real_GPU_selector=KEPT gcn_32byte_stride=EXACT "
              "mismatched_sources=REJECTED");
    return 0;
}
