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
using Shader::Optimization::AnalyzeStridedIndexedSharpWord;
using Shader::Optimization::AreContiguousStridedSharpWords;
using Shader::Optimization::StridedIndexedSharpWord;
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
    // GoT compute-material descriptor IR, independently observed in both
    // cs_0x55d17c16 and cs_0x5c4c2b6f:
    // 4x ((id * 536 + 128) >> 2) + 4..7 dwords
    // 4x ((id * 536 + 160) >> 2) + 0..3 dwords.
    MockInst recordId{Opcode::ReadConstBuffer,{},};
    MockInst mulRecord{Opcode::IMul32,
                       {MockValue::Ref(recordId),MockValue::Imm(536u)},};
    MockInst prefixLo{Opcode::IAdd32,
                      {MockValue::Ref(mulRecord),MockValue::Imm(128u)},};
    MockInst prefixHi{Opcode::IAdd32,
                      {MockValue::Ref(mulRecord),MockValue::Imm(160u)},};
    MockInst dwordsLo{Opcode::ShiftRightLogical32,
                      {MockValue::Ref(prefixLo),MockValue::Imm(2u)},};
    MockInst dwordsHi{Opcode::ShiftRightLogical32,
                      {MockValue::Ref(prefixHi),MockValue::Imm(2u)},};
    std::array<MockInst,8> materialWords{};
    std::array<StridedIndexedSharpWord,8> material{};
    for(unsigned i=0;i<8;++i) {
        const bool high=i>=4u;
        materialWords[i]={Opcode::IAdd32,
            {MockValue::Ref(high?dwordsHi:dwordsLo),
             MockValue::Imm(high?i-4u:i+4u)},};
        material[i]=AnalyzeStridedIndexedSharpWord(MockValue::Ref(materialWords[i]));
    }
    if(!AreContiguousStridedSharpWords(material) ||
       material[0].gpu_record_id!=&recordId ||
       material[0].record_stride_bytes!=536u ||
       material[0].offset_dwords!=36u ||
       material[7].offset_dwords!=43u) return 17;
    for(std::uint32_t id=0;id<100000u;++id) {
        for(std::uint32_t word=0;word<8u;++word) {
            const std::uint32_t low=((id*536u+128u)>>2u);
            const std::uint32_t high=((id*536u+160u)>>2u);
            const std::uint32_t actual =
                word<4u ? low+word+4u : high+word-4u;
            const std::uint32_t predicted =
                ((id*536u)>>2u)+36u+word;
            // 32-bit wrap is part of guest GCN arithmetic; only indices
            // inside the mapped host table are eligible for Vulkan binding.
            if(id<=10000u && actual!=predicted) return 18;
        }
    }
    // Sampler S# shares the SAME original GPU record selector as the T#.
    // The four 32-bit S# words begin at byte offset 128, immediately
    // before the guest texture at offset 144.
    MockInst samplerOffset{Opcode::IAdd32,
                          {MockValue::Ref(mulRecord),MockValue::Imm(128u)},};
    MockInst samplerDwords{Opcode::ShiftRightLogical32,
                          {MockValue::Ref(samplerOffset),MockValue::Imm(2u)},};
    std::array<MockInst,4> samplerWords{};
    std::array<StridedIndexedSharpWord,4> samplerRefs{};
    for (unsigned i=0;i<4u;++i) {
        samplerWords[i]={Opcode::IAdd32,
                         {MockValue::Ref(samplerDwords),MockValue::Imm(i)},};
        samplerRefs[i]=AnalyzeStridedIndexedSharpWord(MockValue::Ref(samplerWords[i]));
    }
    if(!AreContiguousStridedSharpWords(samplerRefs) ||
       samplerRefs[0].gpu_record_id!=material[0].gpu_record_id ||
       samplerRefs[0].record_stride_bytes!=536u ||
       samplerRefs[0].offset_dwords!=32u ||
       samplerRefs[3].offset_dwords!=35u) return 21;
    samplerRefs[2].gpu_record_id=&attr;
    if(AreContiguousStridedSharpWords(samplerRefs)) return 22;
    samplerRefs[2]=AnalyzeStridedIndexedSharpWord(MockValue::Ref(samplerWords[2]));
    samplerRefs[3].offset_dwords=36u;
    if(AreContiguousStridedSharpWords(samplerRefs)) return 23;

    material[7].offset_dwords=44u;
    if(AreContiguousStridedSharpWords(material)) return 19;
    material[7]=AnalyzeStridedIndexedSharpWord(MockValue::Ref(materialWords[7]));
    MockInst badStride{Opcode::IMul32,
                       {MockValue::Ref(recordId),MockValue::Imm(538u)},};
    MockInst badPrefix{Opcode::IAdd32,
                       {MockValue::Ref(badStride),MockValue::Imm(128u)},};
    MockInst badShift{Opcode::ShiftRightLogical32,
                      {MockValue::Ref(badPrefix),MockValue::Imm(2u)},};
    if(AnalyzeStridedIndexedSharpWord(MockValue::Ref(badShift)).valid) return 20;
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
              "mismatched_sources=REJECTED strided_536_Tsharp=144 matched_Ssharp=128");
    return 0;
}
