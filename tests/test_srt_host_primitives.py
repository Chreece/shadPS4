#!/usr/bin/env python3
"""Execute production SRT offset emitters with Xbyak; no Vulkan/game required."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def body(text, signature):
    begin = text.index(signature)
    start = text.index('{', begin)
    depth = 1
    end = start + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[begin:end]


def main():
    source = (ROOT / 'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp').read_text()
    shift = body(source, 'static bool EmitComputeOffsetShiftLeftLogical32(')
    find = body(source[source.index('static bool IsAllowedOffsetInstruction('):],
                'case IR::Opcode::FindILsb32: {')
    find = find[find.index('{') + 1:-1]
    preamble = r'''
#include <bit>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <xbyak/xbyak.h>
using u32 = std::uint32_t;
using namespace Xbyak::util;
#define ASSERT(x) do { if (!(x)) std::abort(); } while (false)
#define ABORT_ON_FAILURE(x) do { if (!(x)) return false; } while (false)
#define POP_ABORT_ON_FAILURE(x) ABORT_ON_FAILURE(x)
namespace IR {
struct Value {
    bool immediate;
    u32 value;
    bool IsImmediate() const { return immediate; }
    u32 U32() const { return value; }
};
struct Inst {
    Value args[2];
    Value Arg(unsigned i) const { return args[i]; }
    bool AreAllArgsImmediates() const { return args[0].immediate && args[1].immediate; }
};
}
struct PassInfo {};
// Only the leaf input loading is substituted. The production emitter above
// generates every arithmetic instruction executed by the test.
static bool ComputeOffset(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                          PassInfo&, IR::Value input) {
    if (input.IsImmediate()) c.mov(reg, input.U32());
    else c.mov(reg, input.value == 0 ? edi : esi);
    return true;
}
'''
    program = preamble + shift + '\nstatic bool EmitFind(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info, IR::Inst* inst) {\n' + find + r'''
}
using Fn = u32 (*)(u32, u32);
struct Jit : Xbyak::CodeGenerator {
    Jit(bool ffs, IR::Inst inst) : Xbyak::CodeGenerator(4096) {
        PassInfo pass;
        ASSERT(ffs ? EmitFind(*this, eax, pass, &inst)
                   : EmitComputeOffsetShiftLeftLogical32(*this, eax, pass, &inst));
        ret(); ready();
    }
};
static unsigned checks;
static void Check(u32 actual, u32 expected) {
    ++checks;
    if (actual != expected) {
        std::fprintf(stderr, "mismatch actual=%x expected=%x\n", actual, expected);
        std::exit(1);
    }
}
int main() {
    Jit find(false, {{{true, 0x12345678u}, {false, 1}}});
    const auto shift_constant = find.getCode<Fn>();
    Jit dynamic(false, {{{false, 0}, {false, 1}}});
    const auto shift_dynamic = dynamic.getCode<Fn>();
    Jit ffs(true, {{{false, 0}, {true, 0}}});
    const auto first_bit = ffs.getCode<Fn>();
    for (u32 count = 0; count < 32; ++count) {
        Jit immediate_count(false, {{{false, 0}, {true, count}}});
        const auto shift_immediate = immediate_count.getCode<Fn>();
        Check(shift_constant(0, count), 0x12345678u << count);
        for (u32 value : {0u, 1u, 0x80000000u, 0x12345678u, 0xffffffffu}) {
            Check(shift_dynamic(value, count), value << count);
            Check(shift_immediate(value, 0), value << count);
        }
        const u32 input = 1u << count;
        Check(first_bit(input, 0), count);
        Jit immediate_find(true, {{{true, input}, {true, 0}}});
        Check(immediate_find.getCode<Fn>()(0, 0), count);
    }
    Check(first_bit(0, 0), 0xffffffffu);
    Jit zero(true, {{{true, 0}, {true, 0}}});
    Check(zero.getCode<Fn>()(0, 0), 0xffffffffu);
    u32 seed = 0x98765432u;
    for (unsigned i = 0; i < 10000; ++i) {
        seed = seed * 1664525u + 1013904223u;
        Check(first_bit(seed, 0), seed ? std::countr_zero(seed) : 0xffffffffu);
    }
    std::printf("PRODUCTION_SRT_JIT_PASS checks=%u\n", checks);
}
'''
    with tempfile.TemporaryDirectory(prefix='srt-production-') as tmp:
        path = Path(tmp)
        (path / 'test.cpp').write_text(program)
        subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++23', '-O2', '-Wall',
                        '-Wextra', '-Werror', '-I', str(ROOT / 'externals/xbyak'),
                        str(path / 'test.cpp'), '-o', str(path / 'test')], check=True)
        subprocess.run([str(path / 'test')], check=True)


if __name__ == '__main__':
    main()
