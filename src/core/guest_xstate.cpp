// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <cstring>
#include <limits>
#include <Zydis/Zydis.h>
#include "core/guest_xstate.h"

namespace Core {
namespace {

template <typename T>
T Load(const u8* bytes) {
    T value;
    std::memcpy(&value, bytes, sizeof(value));
    return value;
}

template <typename T>
void Store(u8* bytes, T value) {
    std::memcpy(bytes, &value, sizeof(value));
}

bool IsCanonical(u64 address) {
    return address < 0x800000000000 || address >= 0xffff800000000000;
}

XstateResult CheckRange(u64 address, size_t offset, size_t size) {
    if (address > std::numeric_limits<u64>::max() - offset ||
        address + offset > std::numeric_limits<u64>::max() - (size - 1) ||
        !IsCanonical(address + offset) || !IsCanonical(address + offset + size - 1)) {
        return {XstateFault::GeneralProtection};
    }
    return {};
}

XstateResult Read(const XstateMemory& memory, u64 address, size_t offset, std::span<u8> bytes) {
    const auto checked = CheckRange(address, offset, bytes.size());
    return checked ? memory.read(memory.context, address + offset, bytes) : checked;
}

XstateResult Write(const XstateMemory& memory, u64 address, size_t offset,
                   std::span<const u8> bytes) {
    const auto checked = CheckRange(address, offset, bytes.size());
    return checked ? memory.write(memory.context, address + offset, bytes) : checked;
}

} // namespace

XstateResult ReadGuestXcr(u32 index, u64& rax, u64& rdx) {
    if (index != 0) {
        return {XstateFault::GeneralProtection};
    }
    rax = GuestXcr0;
    rdx = 0;
    return {};
}

XstateResult SaveGuestXstate(const GuestXstate& state, u64 mask, u64 address,
                             const XstateMemory& memory, bool format64) {
    if ((address & 63) != 0 || !IsCanonical(address)) {
        return {XstateFault::GeneralProtection};
    }
    mask &= GuestXcr0;
    std::array<u8, 8> bitmap;
    if (const auto result = Read(memory, address, 512, bitmap); !result) {
        return result;
    }
    const auto bytes = std::span{state.bytes};
    if (mask & 1) {
        if (const auto result = Write(memory, address, 0, bytes.first(6)); !result) {
            return result;
        }
        if (Load<u16>(&state.bytes[2]) & 0x80) {
            std::array<u8, 18> pointers;
            std::copy_n(state.bytes.begin() + 6, pointers.size(), pointers.begin());
            if (!format64) {
                Store<u32>(&pointers[2], static_cast<u32>(Load<u64>(&state.bytes[8])));
                Store<u16>(&pointers[6], state.x87_cs);
                Store<u32>(&pointers[10], static_cast<u32>(Load<u64>(&state.bytes[16])));
                Store<u16>(&pointers[14], state.x87_ds);
                if (const auto result = Write(memory, address, 6, std::span{pointers}.first(8));
                    !result) {
                    return result;
                }
                if (const auto result =
                        Write(memory, address, 16, std::span{pointers}.subspan(10, 6));
                    !result) {
                    return result;
                }
            } else if (const auto result = Write(memory, address, 6, pointers); !result) {
                return result;
            }
        }
        if (const auto result = Write(memory, address, 32, bytes.subspan(32, 128)); !result) {
            return result;
        }
    }
    if (mask & 6) {
        std::array<u8, 8> mxcsr;
        Store<u32>(mxcsr.data(), Load<u32>(&state.bytes[24]));
        Store<u32>(&mxcsr[4], GuestMxcsrMask);
        if (const auto result = Write(memory, address, 24, mxcsr); !result) {
            return result;
        }
    }
    if (mask & 2) {
        if (const auto result = Write(memory, address, 160, bytes.subspan(160, 256)); !result) {
            return result;
        }
    }
    if (mask & 4) {
        if (const auto result = Write(memory, address, 576, bytes.subspan(576, 256)); !result) {
            return result;
        }
    }
    Store<u64>(bitmap.data(), (Load<u64>(bitmap.data()) & ~mask) | (state.in_use & mask));
    return Write(memory, address, 512, bitmap);
}

XstateResult RestoreGuestXstate(GuestXstate& state, u64 mask, u64 address,
                                const XstateMemory& memory, bool format64) {
    if ((address & 63) != 0 || !IsCanonical(address)) {
        return {XstateFault::GeneralProtection};
    }
    mask &= GuestXcr0;
    std::array<u8, 64> header;
    if (const auto result = Read(memory, address, 512, header); !result) {
        return result;
    }
    const auto saved = Load<u64>(header.data());
    if ((saved & ~GuestXcr0) != 0 ||
        std::any_of(header.begin() + 8, header.end(), [](u8 byte) { return byte != 0; })) {
        return {XstateFault::GeneralProtection};
    }
    auto next = state;
    auto bytes = std::span{next.bytes};
    if (mask & 6) {
        if (const auto result = Read(memory, address, 24, bytes.subspan(24, 4)); !result) {
            return result;
        }
        if (Load<u32>(&next.bytes[24]) & ~GuestMxcsrMask) {
            return {XstateFault::GeneralProtection};
        }
    }
    if (mask & 1) {
        if (saved & 1) {
            std::array<u8, 24> legacy;
            if (const auto result = Read(memory, address, 0, legacy); !result) {
                return result;
            }
            std::copy_n(legacy.begin(), 6, next.bytes.begin());
            if (Load<u16>(&legacy[2]) & 0x80) {
                std::copy(legacy.begin() + 6, legacy.end(), next.bytes.begin() + 6);
                if (!format64) {
                    Store<u64>(&next.bytes[8], Load<u32>(&legacy[8]));
                    Store<u64>(&next.bytes[16], Load<u32>(&legacy[16]));
                    next.x87_cs = Load<u16>(&legacy[12]);
                    next.x87_ds = Load<u16>(&legacy[20]);
                }
            }
            if (const auto result = Read(memory, address, 32, bytes.subspan(32, 128)); !result) {
                return result;
            }
        } else {
            std::fill_n(next.bytes.begin(), 24, 0);
            Store<u16>(next.bytes.data(), 0x37f);
            next.x87_cs = 0;
            next.x87_ds = 0;
        }
    }
    if (mask & 2) {
        if (saved & 2) {
            if (const auto result = Read(memory, address, 160, bytes.subspan(160, 256)); !result) {
                return result;
            }
        } else {
            std::fill_n(next.bytes.begin() + 160, 256, 0);
        }
    }
    if (mask & 4) {
        if (saved & 4) {
            if (const auto result = Read(memory, address, 576, bytes.subspan(576, 256)); !result) {
                return result;
            }
        } else {
            std::fill_n(next.bytes.begin() + 576, 256, 0);
        }
    }
    next.in_use = (next.in_use & ~mask) | (saved & mask);
    state = next;
    return {};
}

XstateInstructionResult ExecuteGuestXstateInstruction(std::span<const u8> bytes,
                                                      XstateContext& context,
                                                      const XstateMemory& memory) {
    ZydisDecoder decoder;
    ZydisDecoderInit(&decoder, ZYDIS_MACHINE_MODE_LONG_64, ZYDIS_STACK_WIDTH_64);
    ZydisDecodedInstruction instruction;
    std::array<ZydisDecodedOperand, ZYDIS_MAX_OPERAND_COUNT> operands;
    if (!ZYAN_SUCCESS(ZydisDecoderDecodeFull(&decoder, bytes.data(), bytes.size(), &instruction,
                                             operands.data()))) {
        return {};
    }
    XstateResult result;
    if (instruction.mnemonic == ZYDIS_MNEMONIC_XGETBV) {
        result = ReadGuestXcr(static_cast<u32>(context.registers[1]), context.registers[0],
                              context.registers[2]);
    } else {
        const bool restore = instruction.mnemonic == ZYDIS_MNEMONIC_XRSTOR ||
                             instruction.mnemonic == ZYDIS_MNEMONIC_XRSTOR64;
        const bool save = instruction.mnemonic == ZYDIS_MNEMONIC_XSAVE ||
                          instruction.mnemonic == ZYDIS_MNEMONIC_XSAVE64 ||
                          instruction.mnemonic == ZYDIS_MNEMONIC_XSAVEOPT ||
                          instruction.mnemonic == ZYDIS_MNEMONIC_XSAVEOPT64;
        if ((!save && !restore) || operands[0].type != ZYDIS_OPERAND_TYPE_MEMORY) {
            return {};
        }
        const auto register_value = [&context, &instruction](ZydisRegister reg) -> u64 {
            if (reg == ZYDIS_REGISTER_NONE) {
                return 0;
            }
            if (reg == ZYDIS_REGISTER_RIP || reg == ZYDIS_REGISTER_EIP) {
                return context.rip + instruction.length;
            }
            return context.registers[ZydisRegisterGetId(
                ZydisRegisterGetLargestEnclosing(ZYDIS_MACHINE_MODE_LONG_64, reg))];
        };
        const auto& operand = operands[0].mem;
        u64 address = register_value(operand.base) + register_value(operand.index) * operand.scale +
                      operand.disp.value;
        if (instruction.address_width == 32) {
            address = static_cast<u32>(address);
        }
        if (operand.segment == ZYDIS_REGISTER_FS) {
            address += context.fs_base;
        } else if (operand.segment == ZYDIS_REGISTER_GS) {
            address += context.gs_base;
        }
        const auto mask = static_cast<u32>(context.registers[0]) |
                          (static_cast<u64>(static_cast<u32>(context.registers[2])) << 32);
        const bool format64 = instruction.mnemonic == ZYDIS_MNEMONIC_XSAVE64 ||
                              instruction.mnemonic == ZYDIS_MNEMONIC_XSAVEOPT64 ||
                              instruction.mnemonic == ZYDIS_MNEMONIC_XRSTOR64;
        result = restore ? RestoreGuestXstate(context.state, mask, address, memory, format64)
                         : SaveGuestXstate(context.state, mask, address, memory, format64);
    }
    if (result) {
        context.rip += instruction.length;
    }
    return {true, result};
}

} // namespace Core
