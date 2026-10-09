// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <cstring>
#include <Zydis/Zydis.h>
#include <xbyak/xbyak.h>
#include "core/cpu_id.h"
#include "core/generated_instruction.h"

namespace Core {
namespace {

constexpr u32 AbsoluteJumpSize = 14;

void JumpAbsolute(Xbyak::CodeGenerator& code, u64 address) {
    code.jmp(code.ptr[code.rip]);
    code.dq(address);
}

void PushReturnAddress(Xbyak::CodeGenerator& code, u64 address) {
    code.push(code.qword[code.rip + 2]);
    code.db(0xeb);
    code.db(sizeof(address));
    code.dq(address);
}

bool IsStraightLineInstruction(ZydisInstructionCategory category) {
    switch (category) {
    case ZYDIS_CATEGORY_AVX:
    case ZYDIS_CATEGORY_AVX2:
    case ZYDIS_CATEGORY_BINARY:
    case ZYDIS_CATEGORY_BITBYTE:
    case ZYDIS_CATEGORY_CMOV:
    case ZYDIS_CATEGORY_CONVERT:
    case ZYDIS_CATEGORY_DATAXFER:
    case ZYDIS_CATEGORY_FLAGOP:
    case ZYDIS_CATEGORY_LOGICAL:
    case ZYDIS_CATEGORY_LOGICAL_FP:
    case ZYDIS_CATEGORY_NOP:
    case ZYDIS_CATEGORY_POP:
    case ZYDIS_CATEGORY_PUSH:
    case ZYDIS_CATEGORY_ROTATE:
    case ZYDIS_CATEGORY_SETCC:
    case ZYDIS_CATEGORY_SHIFT:
    case ZYDIS_CATEGORY_SSE:
    case ZYDIS_CATEGORY_STRINGOP:
    case ZYDIS_CATEGORY_WIDENOP:
        return true;
    default:
        return false;
    }
}

bool EncodeInstruction(Xbyak::CodeGenerator& code, u64 address,
                       const ZydisDecodedInstruction& instruction,
                       const ZydisDecodedOperand* operands) {
    ZydisEncoderRequest request{};
    if (!ZYAN_SUCCESS(ZydisEncoderDecodedInstructionToEncoderRequest(
            &instruction, operands, instruction.operand_count_visible, &request))) {
        return false;
    }
    for (u32 index = 0; index < instruction.operand_count_visible; ++index) {
        const auto& operand = operands[index];
        if (operand.type == ZYDIS_OPERAND_TYPE_MEMORY &&
            (operand.mem.base == ZYDIS_REGISTER_RIP || operand.mem.base == ZYDIS_REGISTER_EIP)) {
            ZyanU64 target{};
            if (!ZYAN_SUCCESS(ZydisCalcAbsoluteAddress(&instruction, &operand, address, &target))) {
                return false;
            }
            request.operands[index].mem.displacement = static_cast<ZyanI64>(target);
        }
    }
    std::array<u8, ZYDIS_MAX_INSTRUCTION_LENGTH> encoded{};
    ZyanUSize size = encoded.size();
    if (!ZYAN_SUCCESS(ZydisEncoderEncodeInstructionAbsolute(
            &request, encoded.data(), &size, reinterpret_cast<u64>(code.getCurr())))) {
        return false;
    }
    code.db(encoded.data(), size);
    return true;
}

GeneratedInstructionStatus EmitInstruction(Xbyak::CodeGenerator& code, u64 address,
                                           std::span<const u8> bytes,
                                           const ZydisDecodedInstruction& instruction,
                                           const ZydisDecodedOperand* operands) {
    const u64 next = address + instruction.length;
    const auto category = instruction.meta.category;
    if (instruction.mnemonic == ZYDIS_MNEMONIC_CPUID) {
        GenerateCpuIdInstruction(code, CpuIdInstruction::Cpuid);
    } else if (instruction.mnemonic == ZYDIS_MNEMONIC_RDTSCP) {
        GenerateCpuIdInstruction(code, CpuIdInstruction::Rdtscp);
    } else if (instruction.mnemonic == ZYDIS_MNEMONIC_RDPID) {
        GenerateCpuIdInstruction(code, CpuIdInstruction::Rdpid,
                                 ZydisRegisterGetId(operands[0].reg.value));
    } else if (category == ZYDIS_CATEGORY_COND_BR || category == ZYDIS_CATEGORY_UNCOND_BR ||
               category == ZYDIS_CATEGORY_CALL) {
        if (instruction.meta.branch_type == ZYDIS_BRANCH_TYPE_FAR ||
            instruction.mnemonic == ZYDIS_MNEMONIC_XBEGIN) {
            return GeneratedInstructionStatus::UnsupportedInstruction;
        }
        const auto& target = operands[0];
        if (target.type == ZYDIS_OPERAND_TYPE_IMMEDIATE && target.imm.is_relative) {
            ZyanU64 destination{};
            if (!ZYAN_SUCCESS(
                    ZydisCalcAbsoluteAddress(&instruction, &target, address, &destination))) {
                return GeneratedInstructionStatus::UnencodableInstruction;
            }
            if (category == ZYDIS_CATEGORY_COND_BR) {
                const auto& immediate = instruction.raw.imm[0];
                if (!immediate.is_relative || (immediate.size != 8 && immediate.size != 32)) {
                    return GeneratedInstructionStatus::UnsupportedInstruction;
                }
                std::array<u8, ZYDIS_MAX_INSTRUCTION_LENGTH> branch{};
                std::copy_n(bytes.begin(), instruction.length, branch.begin());
                const u32 skip = AbsoluteJumpSize;
                std::memcpy(branch.data() + immediate.offset, &skip, immediate.size / 8);
                code.db(branch.data(), instruction.length);
                JumpAbsolute(code, next);
            } else if (category == ZYDIS_CATEGORY_CALL) {
                PushReturnAddress(code, next);
            }
            JumpAbsolute(code, destination);
        } else if (category == ZYDIS_CATEGORY_CALL) {
            if (target.type != ZYDIS_OPERAND_TYPE_REGISTER || target.size != 64 ||
                target.reg.value == ZYDIS_REGISTER_RSP) {
                return GeneratedInstructionStatus::UnsupportedInstruction;
            }
            PushReturnAddress(code, next);
            code.jmp(Xbyak::Reg64(ZydisRegisterGetId(target.reg.value)));
        } else if (category == ZYDIS_CATEGORY_UNCOND_BR) {
            if (!EncodeInstruction(code, address, instruction, operands)) {
                return GeneratedInstructionStatus::UnencodableInstruction;
            }
        } else {
            return GeneratedInstructionStatus::UnsupportedInstruction;
        }
        return GeneratedInstructionStatus::Translated;
    } else if (instruction.mnemonic == ZYDIS_MNEMONIC_RET &&
               instruction.meta.branch_type != ZYDIS_BRANCH_TYPE_FAR) {
        code.db(bytes.data(), instruction.length);
        return GeneratedInstructionStatus::Translated;
    } else if (instruction.mnemonic == ZYDIS_MNEMONIC_LEA || IsStraightLineInstruction(category)) {
        if (!EncodeInstruction(code, address, instruction, operands)) {
            return GeneratedInstructionStatus::UnencodableInstruction;
        }
    } else {
        return GeneratedInstructionStatus::UnsupportedInstruction;
    }
    JumpAbsolute(code, next);
    return GeneratedInstructionStatus::Translated;
}

} // namespace

GeneratedInstructionResult GenerateGuestInstruction(Xbyak::CodeGenerator& code, u64 guest_address,
                                                    std::span<const u8> bytes) {
    ZydisDecoder decoder;
    ZydisDecoderInit(&decoder, ZYDIS_MACHINE_MODE_LONG_64, ZYDIS_STACK_WIDTH_64);
    ZydisDecodedInstruction instruction;
    std::array<ZydisDecodedOperand, ZYDIS_MAX_OPERAND_COUNT> operands;
    if (!ZYAN_SUCCESS(ZydisDecoderDecodeFull(&decoder, bytes.data(), bytes.size(), &instruction,
                                             operands.data()))) {
        return {GeneratedInstructionStatus::InvalidInstruction};
    }
    const auto initial_size = code.getSize();
    GeneratedInstructionStatus status;
    try {
        status = EmitInstruction(code, guest_address, bytes, instruction, operands.data());
    } catch (const Xbyak::Error& error) {
        code.setSize(initial_size);
        if (static_cast<int>(error) == Xbyak::ERR_CODE_IS_TOO_BIG) {
            return {GeneratedInstructionStatus::BufferFull, instruction.length};
        }
        throw;
    }
    if (status != GeneratedInstructionStatus::Translated) {
        code.setSize(initial_size);
    }
    return {status, instruction.length, static_cast<u32>(code.getSize() - initial_size)};
}

} // namespace Core
