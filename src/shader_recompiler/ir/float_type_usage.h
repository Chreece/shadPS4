// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "shader_recompiler/ir/type.h"

namespace Shader::IR {

// Shared by shader information collection and SPIR-V Phi type handling.
// These checks are exact types, not bitwise masks of unrelated IR types.
[[nodiscard]] constexpr bool IsFloat16Type(Type type) noexcept {
    switch (type) {
    case Type::F16:
    case Type::F16x2:
    case Type::F16x3:
    case Type::F16x4:
        return true;
    default:
        return false;
    }
}

[[nodiscard]] constexpr bool IsFloat64Type(Type type) noexcept {
    switch (type) {
    case Type::F64:
    case Type::F64x2:
    case Type::F64x3:
    case Type::F64x4:
        return true;
    default:
        return false;
    }
}

} // namespace Shader::IR
