// Standalone regression for the REAL indexed_image_selector.h source.
// The IR classes below are lightweight interface stand-ins; all matching
// operations run the actual production template in the included header.
#include <array>
#include <cstdint>
#include <deque>
#include <iostream>
#include <stdexcept>
#include "shader_recompiler/ir/passes/indexed_image_selector.h"
struct Inst;
struct Value {
    Inst* inst{};
    std::uint32_t integer{};
    bool imm{};
    bool IsImmediate() const { return imm; }
    IR::Type Type() const { return IR::Type::U32; }
    std::uint32_t U32() const { return integer; }
    Inst* TryInst() const { return inst; }
    static Value Literal(std::uint32_t x) { return {nullptr, x, true}; }
    static Value Ref(Inst* x) { return {x, 0, false}; }
};
struct Inst {
    IR::Opcode op;
    Value left, right;
    IR::Opcode GetOpcode() const { return op; }
    Value Arg(std::size_t i) const { return i == 0 ? left : right; }
};
std::deque<Inst> nodes;
Value node(IR::Opcode op, Value a={}, Value b={}) {
    nodes.push_back({op,a,b});
    return Value::Ref(&nodes.back());
}
void require(bool valid) {
    if (!valid) throw std::runtime_error("Indexed material matcher fixture failed");
}
using namespace Shader::Optimization;
int main() {
    std::uint64_t checks=0;
    constexpr std::array<std::uint32_t,10> strides{
        64,80,128,256,340,512,2048,4096,16384,65536
    };
    for (const auto stride: strides) {
        for (unsigned form=0; form<2; ++form) {
            nodes.clear();
            const auto selector=node(IR::Opcode::GetAttribute);
            const auto mul=node(IR::Opcode::IMul32,selector,Value::Literal(stride));
            const Value scaled = form==0 ?
                node(IR::Opcode::IAdd32,mul,Value::Literal(24)) : mul;
            const auto shift=node(IR::Opcode::ShiftRightLogical32,scaled,Value::Literal(2));
            std::array<StridedIndexedSharpWord,8> words{};
            for (unsigned i=0;i<8;++i) {
                const auto word=node(IR::Opcode::IAdd32,shift,
                                    Value::Literal((form ? 6u : 0u)+i));
                words[i]=AnalyzeStridedIndexedSharpWord(word);
                require(words[i].valid &&
                        words[i].gpu_record_id==selector.TryInst() &&
                        words[i].record_stride_bytes==stride &&
                        words[i].offset_dwords==i+6u);
                ++checks;
            }
            const bool fits=(6u+8u) <= stride/4u;
            require(AreContiguousStridedSharpWords(words)==fits);
            ++checks;
            words[4].offset_dwords++;
            require(!AreContiguousStridedSharpWords(words));
            ++checks;
        }
    }
    for (std::uint32_t n=0;n<10000;++n) {
        const std::uint32_t id=n*7919u;
        const std::uint32_t inside=((id*340u+24u)>>2u)+6u;
        const std::uint32_t outside=((id*340u)>>2u)+12u;
        if (std::uint64_t(id)*340u+24u < (std::uint64_t(1)<<32)) {
            require(inside==outside);
        }
        ++checks;
    }
    nodes.clear();
    const auto selector=node(IR::Opcode::GetAttribute);
    const auto mul=node(IR::Opcode::IMul32,selector,Value::Literal(340));
    const auto misaligned=node(IR::Opcode::IAdd32,mul,Value::Literal(3));
    const auto shift=node(IR::Opcode::ShiftRightLogical32,misaligned,Value::Literal(2));
    const auto word=node(IR::Opcode::IAdd32,shift,Value::Literal(3));
    require(!AnalyzeStridedIndexedSharpWord(word).valid);
    ++checks;
    std::cout<<"EXACT_INDEXED_MATCHER_TEST_PASS="<<checks<<"\n";
}
