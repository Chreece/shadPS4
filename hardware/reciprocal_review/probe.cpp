#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <chrono>
#include <vector>
#include <memory>
#include <Zydis/Zydis.h>
#include <xbyak/xbyak.h>
using u64=uint64_t; using u32=uint32_t; using u8=uint8_t;
using namespace Xbyak::util;
#define ASSERT_MSG(x,...) do {if(!(x)) abort();}while(0)
#define UNREACHABLE_MSG(...) abort()
constexpr static u64 rcp_index_table_size = 1u << 21;
static bool rcp_index_tables_initialized = false;
// VGATHER used in our RCP packing reads 32-bit elements, as there's no 8-bit read alternative
// To ensure that the page right after rcp_index_table is readable and won't fault, we add 3 bytes
// to the array size
static std::array<u8, rcp_index_table_size + 3> rcp_index_table{};
static std::array<u8, rcp_index_table_size + 3> rsqrt_index_table{};
constexpr static std::array rcp_xor_values = {
    0x00000000, 0x00001000, 0x00000800, 0x00001800, 0x00003000, 0x00007000,
    0x00003800, 0x00007800, 0x0000f800, 0x0000f000, 0x0007f800, 0x0003f800,
    0x000ff000, 0x0001f000, 0x001ff800, 0x0001f800, 0x000ff800};
constexpr static std::array rsqrt_xor_values = {
    0x00000000, 0x00000800, 0x00001800, 0x00001000, 0x00007000, 0x00003000, 0x0000f000, 0x00003800,
    0x00007800, 0x0003f800, 0x0000f800, 0x0001f800, 0x0001f000, 0x0007f000, 0x0003f000, 0x000ff000,
    0x000ff800, 0x0007f800, 0x001ff800, 0x003ff800, 0x001ff000, 0x007ff800};

static bool InitializeIndexTables() {return true;}
static Xbyak::Reg ZydisToXbyakRegister(const ZydisRegister reg) {
    if (reg >= ZYDIS_REGISTER_EAX && reg <= ZYDIS_REGISTER_R15D) {
        return Xbyak::Reg32(reg - ZYDIS_REGISTER_EAX + Xbyak::Operand::EAX);
    }
    if (reg >= ZYDIS_REGISTER_RAX && reg <= ZYDIS_REGISTER_R15) {
        return Xbyak::Reg64(reg - ZYDIS_REGISTER_RAX + Xbyak::Operand::RAX);
    }
    if (reg >= ZYDIS_REGISTER_XMM0 && reg <= ZYDIS_REGISTER_XMM31) {
        return Xbyak::Xmm(reg - ZYDIS_REGISTER_XMM0 + xmm0.getIdx());
    }
    if (reg >= ZYDIS_REGISTER_YMM0 && reg <= ZYDIS_REGISTER_YMM31) {
        return Xbyak::Ymm(reg - ZYDIS_REGISTER_YMM0 + ymm0.getIdx());
    }
    UNREACHABLE_MSG("Unsupported register: {}", static_cast<u32>(reg));
}

static Xbyak::Reg ZydisToXbyakRegisterOperand(const ZydisDecodedOperand& operand) {
    ASSERT_MSG(operand.type == ZYDIS_OPERAND_TYPE_REGISTER,
               "Expected register operand, got type: {}", static_cast<u32>(operand.type));

    return ZydisToXbyakRegister(operand.reg.value);
}

static Xbyak::Address ZydisToXbyakMemoryOperand(const ZydisDecodedOperand& operand) {
    ASSERT_MSG(operand.type == ZYDIS_OPERAND_TYPE_MEMORY, "Expected memory operand, got type: {}",
               static_cast<u32>(operand.type));

    if (operand.mem.base == ZYDIS_REGISTER_RIP) {
        return ptr[rip + operand.mem.disp.value];
    }

    Xbyak::RegExp expression{};
    if (operand.mem.base != ZYDIS_REGISTER_NONE) {
        expression = expression + ZydisToXbyakRegister(operand.mem.base);
    }
    if (operand.mem.index != ZYDIS_REGISTER_NONE) {
        if (operand.mem.scale != 0) {
            expression = expression + ZydisToXbyakRegister(operand.mem.index) * operand.mem.scale;
        } else {
            expression = expression + ZydisToXbyakRegister(operand.mem.index);
        }
    }
    if (operand.mem.disp.size != 0 && operand.mem.disp.value != 0) {
        expression = expression + operand.mem.disp.value;
    }

    return ptr[expression];
}

namespace baseline {
static void GenerateReciprocalInstruction(void* address, const ZydisDecodedInstruction& instruction,
                                          const ZydisDecodedOperand* operands,
                                          Xbyak::CodeGenerator& c, bool rsqrt, bool vex) {
    bool table_loaded = InitializeIndexTables();
    ASSERT_MSG(table_loaded, "Failed to load RCP index table");

    ASSERT_MSG(operands[0].type == ZYDIS_OPERAND_TYPE_REGISTER, "operand 0 must be a register");

    const auto dst = ZydisToXbyakRegisterOperand(operands[0]);

    ASSERT_MSG(dst.isXMM() || dst.isYMM(), "operand 0 must be an XMM or YMM register");
    bool is_src_mem = operands[1].type == ZYDIS_OPERAND_TYPE_MEMORY;

    std::array<bool, 16> taken_vecs = {};
    Xbyak::Xmm dst_reg;
    Xbyak::Xmm src_reg;

    if (!is_src_mem) {
        const auto src = ZydisToXbyakRegisterOperand(operands[1]);
        src_reg = Xbyak::Xmm(src.getKind(), src.getIdx());
        taken_vecs[src_reg.getIdx()] = true;
    }

    dst_reg = Xbyak::Xmm(dst.getKind(), dst.getIdx());
    taken_vecs[dst_reg.getIdx()] = true;

    const Xbyak::Reg64 scratch1 = rax;
    const Xbyak::Reg64 scratch2 = rcx;
    auto it1 = std::find(taken_vecs.begin(), taken_vecs.end(), false);
    auto it2 = std::find(std::next(it1), taken_vecs.end(), false);
    auto it3 = std::find(std::next(it2), taken_vecs.end(), false);
    auto it4 = std::find(std::next(it3), taken_vecs.end(), false);
    auto it5 = std::find(std::next(it4), taken_vecs.end(), false);
    const Xbyak::Xmm xmm_scratch1 = Xbyak::Xmm(dst.getKind(), it1 - taken_vecs.begin());
    const Xbyak::Xmm xmm_scratch2 = Xbyak::Xmm(dst.getKind(), it2 - taken_vecs.begin());
    const Xbyak::Xmm xmm_scratch3 = Xbyak::Xmm(dst.getKind(), it3 - taken_vecs.begin());
    const Xbyak::Xmm src_storage = Xbyak::Xmm(dst.getKind(), it4 - taken_vecs.begin());
    const Xbyak::Xmm nan_mask = Xbyak::Xmm(dst.getKind(), it5 - taken_vecs.begin());
    const int ymm_storage = is_src_mem ? 32 * 5 : 32 * 4;

    // Set rsp to before red zone and save scratch registers
    const int rsp_disp = 128 + ymm_storage;
    c.lea(rsp, ptr[rsp - rsp_disp]);
    c.vmovups(ptr[rsp], xmm_scratch1.cvt256());
    c.vmovups(ptr[rsp + 32], xmm_scratch2.cvt256());
    c.vmovups(ptr[rsp + 64], xmm_scratch3.cvt256());
    c.vmovups(ptr[rsp + 96], nan_mask.cvt256());
    if (is_src_mem) {
        c.vmovups(ptr[rsp + 128], src_storage.cvt256());
    }
    c.pushfq();
    c.push(scratch1);
    c.push(scratch2);

    if (is_src_mem) {
        if (operands[1].mem.base == ZYDIS_REGISTER_RIP) {
            const u64 target = (u64)address + instruction.length + operands[1].mem.disp.value;
            c.mov(rax, target);
            c.vmovups(src_storage, ptr[rax]);
        } else {
            ZydisDecodedOperand operand = operands[1];
            if (operands[1].mem.base == ZYDIS_REGISTER_RSP) { // rsp can't be index
                operand.mem.disp.size = 32;
                operand.mem.disp.value += rsp_disp + 8 * 3; // Account for what we pushed
            }
            Xbyak::Address mem = ZydisToXbyakMemoryOperand(operand);
            c.vmovups(src_storage, mem);
        }
        src_reg = src_storage;
    }
    void* index_table = rsqrt ? rsqrt_index_table.data() : rcp_index_table.data();
    const int* xor_table = rsqrt ? rsqrt_xor_values.data() : rcp_xor_values.data();
    c.mov(scratch1, reinterpret_cast<u64>(index_table));
    c.mov(scratch2, reinterpret_cast<u64>(xor_table));
    // Find NaNs in source
    // In non-VEX forms this will zero the top elements which will make the vblendvps
    // pick from dst_reg, thus preserving the top bits
    c.vcmpunordps(nan_mask, src_reg, src_reg);
    // Set mask to all ones for the elements we'll load
    c.vpcmpeqd(xmm_scratch2, xmm_scratch2, xmm_scratch2);
    // Load indices for active elements from table
    c.vpsrld(xmm_scratch3, src_reg, 11);
    c.vgatherdps(xmm_scratch1, ptr[scratch1 + xmm_scratch3], xmm_scratch2);
    c.vpslld(xmm_scratch1, xmm_scratch1, 24);
    c.vpsrld(xmm_scratch1, xmm_scratch1, 24);
    // Load XOR values using those indices
    c.vpcmpeqd(xmm_scratch2, xmm_scratch2, xmm_scratch2); // vgather sets to zero
    c.vgatherdps(xmm_scratch3, ptr[scratch2 + xmm_scratch1 * 4], xmm_scratch2);
    if (dst_reg == src_reg) {
        // The RCP would modify our source reg so we wouldn't be able to use it for NaN merging
        c.vmovaps(xmm_scratch1, src_reg);
        src_reg = xmm_scratch1;
    }
    if (vex) {
        auto func = rsqrt ? &Xbyak::CodeGenerator::vrsqrtps : &Xbyak::CodeGenerator::vrcpps;
        (c.*func)(dst_reg, src_reg);
        c.vxorps(dst_reg, dst_reg, xmm_scratch3);
    } else {
        // Preserve top bits
        auto func = rsqrt ? &Xbyak::CodeGenerator::rsqrtps : &Xbyak::CodeGenerator::rcpps;
        (c.*func)(dst_reg, src_reg);
        c.xorps(dst_reg, xmm_scratch3);
    }
    // Merge NaNs back into dst
    c.vblendvps(dst_reg.cvt256(), dst_reg.cvt256(), src_reg.cvt256(), nan_mask);

    c.pop(scratch2);
    c.pop(scratch1);
    c.popfq();
    if (is_src_mem) {
        c.vmovups(src_storage.cvt256(), ptr[rsp + 128]);
    }
    c.vmovups(nan_mask.cvt256(), ptr[rsp + 96]);
    c.vmovups(xmm_scratch3.cvt256(), ptr[rsp + 64]);
    c.vmovups(xmm_scratch2.cvt256(), ptr[rsp + 32]);
    c.vmovups(xmm_scratch1.cvt256(), ptr[rsp]);
    c.lea(rsp, ptr[rsp + rsp_disp]);
}

}
namespace packed {
static void GenerateReciprocalInstruction(void* address, const ZydisDecodedInstruction& instruction,
                                          const ZydisDecodedOperand* operands,
                                          Xbyak::CodeGenerator& c, bool rsqrt, bool vex) {
    bool table_loaded = InitializeIndexTables();
    ASSERT_MSG(table_loaded, "Failed to load RCP index table");

    ASSERT_MSG(operands[0].type == ZYDIS_OPERAND_TYPE_REGISTER, "operand 0 must be a register");

    const auto dst = ZydisToXbyakRegisterOperand(operands[0]);

    ASSERT_MSG(dst.isXMM() || dst.isYMM(), "operand 0 must be an XMM or YMM register");
    bool is_src_mem = operands[1].type == ZYDIS_OPERAND_TYPE_MEMORY;

    std::array<bool, 16> taken_vecs = {};
    Xbyak::Xmm dst_reg;
    Xbyak::Xmm src_reg;

    if (!is_src_mem) {
        const auto src = ZydisToXbyakRegisterOperand(operands[1]);
        src_reg = Xbyak::Xmm(src.getKind(), src.getIdx());
        taken_vecs[src_reg.getIdx()] = true;
    }

    dst_reg = Xbyak::Xmm(dst.getKind(), dst.getIdx());
    taken_vecs[dst_reg.getIdx()] = true;

    const Xbyak::Reg64 scratch1 = rax;
    const Xbyak::Reg64 scratch2 = rcx;
    auto it1 = std::find(taken_vecs.begin(), taken_vecs.end(), false);
    auto it2 = std::find(std::next(it1), taken_vecs.end(), false);
    auto it3 = std::find(std::next(it2), taken_vecs.end(), false);
    auto it4 = std::find(std::next(it3), taken_vecs.end(), false);
    auto it5 = std::find(std::next(it4), taken_vecs.end(), false);
    const Xbyak::Xmm xmm_scratch1 = Xbyak::Xmm(dst.getKind(), it1 - taken_vecs.begin());
    const Xbyak::Xmm xmm_scratch2 = Xbyak::Xmm(dst.getKind(), it2 - taken_vecs.begin());
    const Xbyak::Xmm xmm_scratch3 = Xbyak::Xmm(dst.getKind(), it3 - taken_vecs.begin());
    const Xbyak::Xmm src_storage = Xbyak::Xmm(dst.getKind(), it4 - taken_vecs.begin());
    const Xbyak::Xmm nan_mask = Xbyak::Xmm(dst.getKind(), it5 - taken_vecs.begin());
    const int ymm_storage = is_src_mem ? 32 * 5 : 32 * 4;

    // Set rsp to before red zone and save scratch registers
    const int rsp_disp = 128 + ymm_storage;
    c.lea(rsp, ptr[rsp - rsp_disp]);
    c.vmovups(ptr[rsp], xmm_scratch1.cvt256());
    c.vmovups(ptr[rsp + 32], xmm_scratch2.cvt256());
    c.vmovups(ptr[rsp + 64], xmm_scratch3.cvt256());
    c.vmovups(ptr[rsp + 96], nan_mask.cvt256());
    if (is_src_mem) {
        c.vmovups(ptr[rsp + 128], src_storage.cvt256());
    }
    c.pushfq();
    c.push(scratch1);
    c.push(scratch2);

    if (is_src_mem) {
        if (operands[1].mem.base == ZYDIS_REGISTER_RIP) {
            const u64 target = (u64)address + instruction.length + operands[1].mem.disp.value;
            c.mov(rax, target);
            c.vmovups(src_storage, ptr[rax]);
        } else {
            ZydisDecodedOperand operand = operands[1];
            if (operands[1].mem.base == ZYDIS_REGISTER_RSP) { // rsp can't be index
                operand.mem.disp.size = 32;
                operand.mem.disp.value += rsp_disp + 8 * 3; // Account for what we pushed
            }
            Xbyak::Address mem = ZydisToXbyakMemoryOperand(operand);
            c.vmovups(src_storage, mem);
        }
        src_reg = src_storage;
    }
    void* index_table = rsqrt ? rsqrt_index_table.data() : rcp_index_table.data();
    const int* xor_table = rsqrt ? rsqrt_xor_values.data() : rcp_xor_values.data();
    c.mov(eax, 0x7f800000);
    c.vmovd(xmm_scratch2.cvt128(), eax);
    c.vpbroadcastd(xmm_scratch2, xmm_scratch2.cvt128());
    c.vpslld(xmm_scratch3, src_reg, 1);
    c.vpsrld(xmm_scratch3, xmm_scratch3, 1);
    c.vpcmpgtd(nan_mask, xmm_scratch3, xmm_scratch2);
    c.mov(scratch1, reinterpret_cast<u64>(index_table));
    c.mov(scratch2, reinterpret_cast<u64>(xor_table));
    // Set mask to all ones for the elements we'll load
    c.vpcmpeqd(xmm_scratch2, xmm_scratch2, xmm_scratch2);
    // Load indices for active elements from table
    c.vpsrld(xmm_scratch3, src_reg, 11);
    c.vgatherdps(xmm_scratch1, ptr[scratch1 + xmm_scratch3], xmm_scratch2);
    c.vpslld(xmm_scratch1, xmm_scratch1, 24);
    c.vpsrld(xmm_scratch1, xmm_scratch1, 24);
    // Load XOR values using those indices
    c.vpcmpeqd(xmm_scratch2, xmm_scratch2, xmm_scratch2); // vgather sets to zero
    c.vgatherdps(xmm_scratch3, ptr[scratch2 + xmm_scratch1 * 4], xmm_scratch2);
    c.vpandn(xmm_scratch3, nan_mask, xmm_scratch3);
    if (vex) {
        auto func = rsqrt ? &Xbyak::CodeGenerator::vrsqrtps : &Xbyak::CodeGenerator::vrcpps;
        (c.*func)(dst_reg, src_reg);
        c.vxorps(dst_reg, dst_reg, xmm_scratch3);
    } else {
        // Preserve top bits
        auto func = rsqrt ? &Xbyak::CodeGenerator::rsqrtps : &Xbyak::CodeGenerator::rcpps;
        (c.*func)(dst_reg, src_reg);
        c.xorps(dst_reg, xmm_scratch3);
    }

    c.pop(scratch2);
    c.pop(scratch1);
    c.popfq();
    if (is_src_mem) {
        c.vmovups(src_storage.cvt256(), ptr[rsp + 128]);
    }
    c.vmovups(nan_mask.cvt256(), ptr[rsp + 96]);
    c.vmovups(xmm_scratch3.cvt256(), ptr[rsp + 64]);
    c.vmovups(xmm_scratch2.cvt256(), ptr[rsp + 32]);
    c.vmovups(xmm_scratch1.cvt256(), ptr[rsp]);
    c.lea(rsp, ptr[rsp + rsp_disp]);
}

}
namespace full {
static void GenerateReciprocalInstruction(void* address, const ZydisDecodedInstruction& instruction,
                                          const ZydisDecodedOperand* operands,
                                          Xbyak::CodeGenerator& c, bool rsqrt, bool vex,
                                          bool scalar = false) {
    bool table_loaded = InitializeIndexTables();
    ASSERT_MSG(table_loaded, "Failed to load RCP index table");

    ASSERT_MSG(operands[0].type == ZYDIS_OPERAND_TYPE_REGISTER, "operand 0 must be a register");

    const auto dst = ZydisToXbyakRegisterOperand(operands[0]);

    ASSERT_MSG(dst.isXMM() || dst.isYMM(), "operand 0 must be an XMM or YMM register");
    const auto& source = operands[scalar && vex ? 2 : 1];
    const bool is_src_mem = source.type == ZYDIS_OPERAND_TYPE_MEMORY;

    std::array<bool, 16> taken_vecs = {};
    Xbyak::Xmm dst_reg;
    Xbyak::Xmm src_reg;
    Xbyak::Xmm merge_reg;

    if (scalar && vex) {
        merge_reg = Xbyak::Xmm(ZydisToXbyakRegisterOperand(operands[1]).getIdx());
        taken_vecs[merge_reg.getIdx()] = true;
    }

    if (!is_src_mem) {
        const auto src = ZydisToXbyakRegisterOperand(source);
        src_reg = Xbyak::Xmm(src.getKind(), src.getIdx());
        taken_vecs[src_reg.getIdx()] = true;
    }

    dst_reg = Xbyak::Xmm(dst.getKind(), dst.getIdx());
    taken_vecs[dst_reg.getIdx()] = true;

    const Xbyak::Reg64 scratch1 = rax;
    const Xbyak::Reg64 scratch2 = rcx;
    auto it1 = std::find(taken_vecs.begin(), taken_vecs.end(), false);
    auto it2 = std::find(std::next(it1), taken_vecs.end(), false);
    auto it3 = std::find(std::next(it2), taken_vecs.end(), false);
    auto it4 = std::find(std::next(it3), taken_vecs.end(), false);
    auto it5 = std::find(std::next(it4), taken_vecs.end(), false);
    const Xbyak::Xmm xmm_scratch1 = Xbyak::Xmm(dst.getKind(), it1 - taken_vecs.begin());
    const Xbyak::Xmm xmm_scratch2 = Xbyak::Xmm(dst.getKind(), it2 - taken_vecs.begin());
    const Xbyak::Xmm xmm_scratch3 = Xbyak::Xmm(dst.getKind(), it3 - taken_vecs.begin());
    const Xbyak::Xmm src_storage = Xbyak::Xmm(dst.getKind(), it4 - taken_vecs.begin());
    const Xbyak::Xmm nan_mask = Xbyak::Xmm(dst.getKind(), it5 - taken_vecs.begin());
    const int ymm_storage = is_src_mem ? 32 * 5 : 32 * 4;

    // Set rsp to before red zone and save scratch registers
    const int rsp_disp = 128 + ymm_storage;
    c.lea(rsp, ptr[rsp - rsp_disp]);
    c.vmovups(ptr[rsp], xmm_scratch1.cvt256());
    c.vmovups(ptr[rsp + 32], xmm_scratch2.cvt256());
    c.vmovups(ptr[rsp + 64], xmm_scratch3.cvt256());
    c.vmovups(ptr[rsp + 96], nan_mask.cvt256());
    if (is_src_mem) {
        c.vmovups(ptr[rsp + 128], src_storage.cvt256());
    }
    c.pushfq();
    c.push(scratch1);
    c.push(scratch2);

    if (is_src_mem) {
        const auto load_source = [&](const Xbyak::Address& memory) {
            if (scalar) {
                c.vmovss(src_storage, memory);
            } else {
                c.vmovups(src_storage, memory);
            }
        };
        if (source.mem.base == ZYDIS_REGISTER_RIP) {
            const u64 target = (u64)address + instruction.length + source.mem.disp.value;
            c.mov(rax, target);
            load_source(ptr[rax]);
        } else {
            ZydisDecodedOperand operand = source;
            if (source.mem.base == ZYDIS_REGISTER_RSP) { // rsp can't be index
                operand.mem.disp.size = 32;
                operand.mem.disp.value += rsp_disp + 8 * 3; // Account for what we pushed
            }
            Xbyak::Address mem = ZydisToXbyakMemoryOperand(operand);
            load_source(mem);
        }
        src_reg = src_storage;
    }
    void* index_table = rsqrt ? rsqrt_index_table.data() : rcp_index_table.data();
    const int* xor_table = rsqrt ? rsqrt_xor_values.data() : rcp_xor_values.data();
    c.mov(eax, 0x7f800000);
    c.vmovd(xmm_scratch2.cvt128(), eax);
    c.vpbroadcastd(xmm_scratch2, xmm_scratch2.cvt128());
    c.vpslld(xmm_scratch3, src_reg, 1);
    c.vpsrld(xmm_scratch3, xmm_scratch3, 1);
    c.vpcmpgtd(nan_mask, xmm_scratch3, xmm_scratch2);
    c.mov(scratch1, reinterpret_cast<u64>(index_table));
    c.mov(scratch2, reinterpret_cast<u64>(xor_table));
    // Set mask to all ones for the elements we'll load
    c.vpcmpeqd(xmm_scratch2, xmm_scratch2, xmm_scratch2);
    // Load indices for active elements from table
    c.vpsrld(xmm_scratch3, src_reg, 11);
    c.vgatherdps(xmm_scratch1, ptr[scratch1 + xmm_scratch3], xmm_scratch2);
    c.vpslld(xmm_scratch1, xmm_scratch1, 24);
    c.vpsrld(xmm_scratch1, xmm_scratch1, 24);
    // Load XOR values using those indices
    c.vpcmpeqd(xmm_scratch2, xmm_scratch2, xmm_scratch2); // vgather sets to zero
    c.vgatherdps(xmm_scratch3, ptr[scratch2 + xmm_scratch1 * 4], xmm_scratch2);
    c.vpandn(xmm_scratch3, nan_mask, xmm_scratch3);
    if (scalar) {
        c.vpslldq(xmm_scratch3, xmm_scratch3, 12);
        c.vpsrldq(xmm_scratch3, xmm_scratch3, 12);
        if (vex) {
            if (rsqrt) {
                c.vrsqrtss(dst_reg, merge_reg, src_reg);
            } else {
                c.vrcpss(dst_reg, merge_reg, src_reg);
            }
            c.vxorps(dst_reg, dst_reg, xmm_scratch3);
        } else {
            if (rsqrt) {
                c.rsqrtss(dst_reg, src_reg);
            } else {
                c.rcpss(dst_reg, src_reg);
            }
            c.xorps(dst_reg, xmm_scratch3);
        }
    } else if (vex) {
        auto func = rsqrt ? &Xbyak::CodeGenerator::vrsqrtps : &Xbyak::CodeGenerator::vrcpps;
        (c.*func)(dst_reg, src_reg);
        c.vxorps(dst_reg, dst_reg, xmm_scratch3);
    } else {
        // Preserve top bits
        auto func = rsqrt ? &Xbyak::CodeGenerator::rsqrtps : &Xbyak::CodeGenerator::rcpps;
        (c.*func)(dst_reg, src_reg);
        c.xorps(dst_reg, xmm_scratch3);
    }

    c.pop(scratch2);
    c.pop(scratch1);
    c.popfq();
    if (is_src_mem) {
        c.vmovups(src_storage.cvt256(), ptr[rsp + 128]);
    }
    c.vmovups(nan_mask.cvt256(), ptr[rsp + 96]);
    c.vmovups(xmm_scratch3.cvt256(), ptr[rsp + 64]);
    c.vmovups(xmm_scratch2.cvt256(), ptr[rsp + 32]);
    c.vmovups(xmm_scratch1.cvt256(), ptr[rsp]);
    c.lea(rsp, ptr[rsp + rsp_disp]);
}

}
// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifdef RECIPROCAL_ORBIS
extern "C" int sceSystemServiceLoadExec(const char *, const char *const *);
#endif

struct Result {
  uint32_t lanes[8];
  uint32_t mxcsr;
  uint32_t padding;
  uint64_t flags_before;
  uint64_t flags_after;
};
static_assert(offsetof(Result, flags_before) == 40);
static_assert(offsetof(Result, flags_after) == 48);

using Probe = void (*)(const uint32_t *, const uint32_t *, Result *);
static Probe generated[3][14];
static unsigned mode=1;
static void rcp_ss(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][0](a,b,c);}
static void rcp_ss_mem(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][1](a,b,c);}
static void rcp_vss(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][2](a,b,c);}
static void rcp_vss_mem(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][3](a,b,c);}
static void rcp_ps(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][4](a,b,c);}
static void rcp_vps(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][5](a,b,c);}
static void rcp_vps256(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][6](a,b,c);}
static void rsqrt_ss(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][7](a,b,c);}
static void rsqrt_ss_mem(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][8](a,b,c);}
static void rsqrt_vss(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][9](a,b,c);}
static void rsqrt_vss_mem(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][10](a,b,c);}
static void rsqrt_ps(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][11](a,b,c);}
static void rsqrt_vps(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][12](a,b,c);}
static void rsqrt_vps256(const uint32_t*a,const uint32_t*b,Result*c) {generated[mode][13](a,b,c);}


struct Case {
  const char *name;
  Probe run;
  unsigned computed_lanes;
  bool vex;
};
static const Case cases[] = {
    {"rcpss", rcp_ss, 1, false},
    {"rcpss_mem", rcp_ss_mem, 1, false},
    {"vrcpss", rcp_vss, 1, true},
    {"vrcpss_mem", rcp_vss_mem, 1, true},
    {"rcpps", rcp_ps, 4, false},
    {"vrcpps", rcp_vps, 4, true},
    {"vrcpps256", rcp_vps256, 8, true},
    {"rsqrtss", rsqrt_ss, 1, false},
    {"rsqrtss_mem", rsqrt_ss_mem, 1, false},
    {"vrsqrtss", rsqrt_vss, 1, true},
    {"vrsqrtss_mem", rsqrt_vss_mem, 1, true},
    {"rsqrtps", rsqrt_ps, 4, false},
    {"vrsqrtps", rsqrt_vps, 4, true},
    {"vrsqrtps256", rsqrt_vps256, 8, true},
};

static uint32_t ReadMxcsr() {
  uint32_t value;
  asm volatile("stmxcsr %0" : "=m"(value));
  return value;
}

static void WriteMxcsr(uint32_t value) {
  asm volatile("ldmxcsr %0" : : "m"(value) : "memory");
}

static bool DescribeCpu(FILE *output) {
  uint32_t a, b, c, d;
  asm volatile("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(0), "c"(0));
  char vendor[13]{};
  memcpy(vendor, &b, 4);
  memcpy(vendor + 4, &d, 4);
  memcpy(vendor + 8, &c, 4);
  asm volatile("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(1), "c"(0));
  fprintf(output, "CPU vendor=%s signature=%08x ecx=%08x edx=%08x\n", vendor, a,
          c, d);
  if ((c & 0x1c000000) != 0x1c000000) {
    return false;
  }
  asm volatile("xgetbv" : "=a"(a), "=d"(d) : "c"(0));
  fprintf(output, "XCR0=%08x%08x\n", d, a);
  return (a & 6) == 6;
}

int probe_main(int argc, char **argv) {
#ifdef RECIPROCAL_ORBIS
  const char *path = "/data/reciprocal-hardware.txt";
  (void)argc;
  (void)argv;
#else
  const char *path = argc > 1 ? argv[1] : "reciprocal-hardware.txt";
#endif
  FILE *output = fopen(path, "w");
  if (!output) {
    perror("reciprocal output");
    return 1;
  }
  puts("RECIPROCAL_BEGIN format=1");
  fprintf(output, "RECIPROCAL_BEGIN format=1\n");
  if (!DescribeCpu(output)) {
    fprintf(output, "RECIPROCAL_ERROR AVX unavailable\n");
    fclose(output);
    return 1;
  }
  const uint32_t saved_mxcsr = ReadMxcsr();
  const uint32_t merge[8] = {0x41200000, 0x42200001, 0x43200002, 0x44200003,
                             0x45200004, 0x46200005, 0x47200006, 0x48200007};
  const uint32_t edges[] = {
      0,          0x80000000, 1,          0x80000001, 0x007fffff, 0x807fffff,
      0x00800000, 0x80800000, 0x00800001, 0x80800001, 0x3f7fffff, 0x3f800000,
      0x3f800001, 0xbf800000, 0x3fffffff, 0x40000000, 0x40000001, 0x40400000,
      0x40800000, 0x7f7fffff, 0xff7fffff, 0x7f800000, 0xff800000, 0x7f800001,
      0xff800001, 0x7fbfffff, 0xffbfffff, 0x7fc00000, 0xffc00000, 0x7fc12345,
      0xffc12345, 0x7fffffff,
  };
  uint32_t values[sizeof(edges) / sizeof(edges[0]) + 512];
  memcpy(values, edges, sizeof(edges));
  unsigned count = sizeof(edges) / sizeof(edges[0]);
  uint32_t random = 0x194abc23;
  for (unsigned exponent = 0; exponent < 256; ++exponent) {
    random = random * 1664525 + 1013904223;
    const uint32_t positive = (exponent << 23) | (random & 0x7fffff);
    values[count++] = positive;
    values[count++] = positive | 0x80000000;
  }
  unsigned rows = 0, failures = 0, scalar_packed_differences = 0;
  for (unsigned setting = 0; setting < 16; ++setting) {
    const uint32_t mxcsr = 0x1f80 | ((setting & 3) << 13) |
                           ((setting & 4) << 4) | ((setting & 8) << 12);
    for (unsigned index = 0; index < count; ++index) {
      uint32_t input[8];
      for (auto &lane : input) {
        lane = values[index];
      }
      uint32_t scalar[2]{};
      for (unsigned operation = 0; operation < sizeof(cases) / sizeof(cases[0]);
           ++operation) {
        const auto &test = cases[operation];
        Result result{};
        WriteMxcsr(mxcsr);
        test.run(input, merge, &result);
        WriteMxcsr(saved_mxcsr);
        unsigned errors = result.flags_before != result.flags_after ? 1 : 0;
        errors += result.mxcsr != mxcsr;
        for (unsigned lane = test.computed_lanes; lane < 8; ++lane) {
          const uint32_t expected = test.vex && lane >= 4 ? 0 : merge[lane];
          errors += result.lanes[lane] != expected;
        }
        failures += errors;
        if (operation % 7 == 0) {
          scalar[operation / 7] = result.lanes[0];
        } else if (operation % 7 == 4) {
          scalar_packed_differences += result.lanes[0] != scalar[operation / 7];
        }
        fprintf(output, "RAW op=%s in=%08x mxcsr_in=%08x mxcsr_out=%08x out=",
                test.name, input[0], mxcsr, result.mxcsr);
        for (unsigned lane = 0; lane < 8; ++lane) {
          fprintf(output, "%s%08x", lane ? "," : "", result.lanes[lane]);
        }
        fprintf(output, " flags=%llx/%llx errors=%u\n",
                static_cast<unsigned long long>(result.flags_before),
                static_cast<unsigned long long>(result.flags_after), errors);
        ++rows;
      }
    }
    fflush(output);
    printf("RECIPROCAL_PROGRESS settings=%u/16\n", setting + 1);
  }
  WriteMxcsr(saved_mxcsr);
  fprintf(
      output,
      "RECIPROCAL_END rows=%u state_errors=%u scalar_packed_differences=%u\n",
      rows, failures, scalar_packed_differences);
  const bool write_failed = ferror(output) != 0;
  const int close_result = fclose(output);
  printf("RECIPROCAL_END rows=%u state_errors=%u scalar_packed_differences=%u "
         "file_ok=%u\n",
         rows, failures, scalar_packed_differences,
         !write_failed && close_result == 0);
#ifdef RECIPROCAL_ORBIS
  sceSystemServiceLoadExec("EXIT", nullptr);
#endif
  return failures == 0 && !write_failed && close_result == 0 ? 0 : 1;
}

int main(int argc,char**argv) {
 for(auto name : {"rcp","rsqrt"}) {
  std::ifstream f(std::string("./")+name+".bin",std::ios::binary);
  auto& table=std::string(name)=="rcp"?rcp_index_table:rsqrt_index_table;
  f.read(reinterpret_cast<char*>(table.data()),table.size()); if(!f)abort();
 }
 std::vector<std::unique_ptr<Xbyak::CodeGenerator>> codes;
 for(unsigned m=0;m<3;++m)for(unsigned i=0;i<14;++i) {
  bool rsqrt=i>=7,scalar=i%7<4,vex=i%7==2||i%7==3||i%7>=5,wide=i%7==6,mem=i%7==1||i%7==3;
  auto c=std::make_unique<Xbyak::CodeGenerator>(4096);
  c->vmovdqu(ymm0,ptr[rsi]); c->vmovdqu(ymm1,ptr[rdi]);
  c->pushfq();c->pop(r8);c->mov(ptr[rdx+40],r8);
  ZydisDecodedInstruction ins{};ZydisDecodedOperand op[3]{};
  op[0].type=ZYDIS_OPERAND_TYPE_REGISTER;op[0].reg.value=wide?ZYDIS_REGISTER_YMM0:ZYDIS_REGISTER_XMM0;
  unsigned src=scalar&&vex?2:1;
  op[1].type=ZYDIS_OPERAND_TYPE_REGISTER;op[1].reg.value=ZYDIS_REGISTER_XMM0;
  op[src].type=mem?ZYDIS_OPERAND_TYPE_MEMORY:ZYDIS_OPERAND_TYPE_REGISTER;
  op[src].reg.value=wide?ZYDIS_REGISTER_YMM1:ZYDIS_REGISTER_XMM1;
  if(mem){op[src].mem.base=ZYDIS_REGISTER_RDI;op[src].mem.index=ZYDIS_REGISTER_NONE;}
  if(m==2) full::GenerateReciprocalInstruction(nullptr,ins,op,*c,rsqrt,vex,scalar);
  else if(!scalar) {
   if(m==0)baseline::GenerateReciprocalInstruction(nullptr,ins,op,*c,rsqrt,vex);
   else packed::GenerateReciprocalInstruction(nullptr,ins,op,*c,rsqrt,vex);
  } else {
   if(vex) {if(rsqrt){if(mem)c->vrsqrtss(xmm0,xmm0,ptr[rdi]);else c->vrsqrtss(xmm0,xmm0,xmm1);}else{if(mem)c->vrcpss(xmm0,xmm0,ptr[rdi]);else c->vrcpss(xmm0,xmm0,xmm1);}}
   else{if(rsqrt){if(mem)c->rsqrtss(xmm0,ptr[rdi]);else c->rsqrtss(xmm0,xmm1);}else{if(mem)c->rcpss(xmm0,ptr[rdi]);else c->rcpss(xmm0,xmm1);}}
  }
  c->pushfq();c->pop(r8);c->mov(ptr[rdx+48],r8);
  c->vmovdqu(ptr[rdx],ymm0);c->stmxcsr(ptr[rdx+32]);c->vzeroupper();c->ret();c->ready();
  generated[m][i]=c->getCode<Probe>(); codes.push_back(std::move(c));
 }
 if(argc>1 && std::string(argv[1])=="bench") {
  alignas(32) uint32_t input[8]={0x3fc00000,0x40000000,0x40400000,0x40800000,0x3f800000,0x41000000,0x42000000,0x43000000};
  uint32_t merge[8]={};Result result{};
  for(unsigned i=0;i<14;++i) {
   std::vector<double> samples[3];
   for(unsigned rep=0;rep<21;++rep)for(unsigned slot=0;slot<3;++slot){
    unsigned m=(slot+rep)%3;
    for(unsigned k=0;k<1000;++k)generated[m][i](input,merge,&result);
    auto start=std::chrono::steady_clock::now();
    for(unsigned k=0;k<100000;++k)generated[m][i](input,merge,&result);
    auto end=std::chrono::steady_clock::now();
    samples[m].push_back(std::chrono::duration<double,std::nano>(end-start).count()/100000);
   }
   for(unsigned m=0;m<3;++m){
    auto& values=samples[m];std::sort(values.begin(),values.end());
    printf("BENCH op=%s mode=%u median_ns=%.3f min_ns=%.3f max_ns=%.3f bytes=%zu\n",cases[i].name,m,values[10],values[0],values[20],codes[m*14+i]->getSize());
   }
  }
  return 0;
 }
 if(argc>2)mode=std::atoi(argv[2]);
 return probe_main(argc,argv);
}
