// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <span>
#include "common/types.h"

namespace Xbyak {
class CodeGenerator;
}

namespace Core {

enum class GeneratedInstructionStatus {
    Translated,
    InvalidInstruction,
    UnsupportedInstruction,
    UnencodableInstruction,
    BufferFull,
};

struct GeneratedInstructionResult {
    GeneratedInstructionStatus status;
    u32 guest_size{};
    u32 native_size{};
};

// The caller supplies a stable instruction snapshot at an actual execution boundary. Generation
// runs outside signal handlers; source memory and execution permissions are not modified here.
GeneratedInstructionResult GenerateGuestInstruction(Xbyak::CodeGenerator& code, u64 guest_address,
                                                    std::span<const u8> bytes);

} // namespace Core
