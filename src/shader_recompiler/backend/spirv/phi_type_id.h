// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <optional>
#include <type_traits>

#include "shader_recompiler/ir/type.h"

namespace Shader::Backend::SPIRV {

// Map SSA Phi's actual IR result type to the corresponding SPIR-V result type.
// Typed Phi nodes must never be coerced to U64/F32 as a crash avoidance hack.
// A missing case is reported to the existing diagnostic path by the caller.
template <typename Context>
[[nodiscard]] constexpr auto LookupPhiTypeId(const Context& ctx, IR::Type type)
    -> std::optional<std::remove_cvref_t<decltype(ctx.U32[1])>> {
    switch (type) {
    case IR::Type::U1:
        return ctx.U1[1];
    case IR::Type::U8:
        return ctx.U8;
    case IR::Type::U16:
        return ctx.U16;
    case IR::Type::U32:
        return ctx.U32[1];
    case IR::Type::U32x2:
        return ctx.U32[2];
    case IR::Type::U32x3:
        return ctx.U32[3];
    case IR::Type::U32x4:
        return ctx.U32[4];
    case IR::Type::U64:
        return ctx.U64;
    case IR::Type::F16:
        return ctx.F16[1];
    case IR::Type::F16x2:
        return ctx.F16[2];
    case IR::Type::F16x3:
        return ctx.F16[3];
    case IR::Type::F16x4:
        return ctx.F16[4];
    case IR::Type::F32:
        return ctx.F32[1];
    case IR::Type::F32x2:
        return ctx.F32[2];
    case IR::Type::F32x3:
        return ctx.F32[3];
    case IR::Type::F32x4:
        return ctx.F32[4];
    case IR::Type::F64:
        return ctx.F64[1];
    case IR::Type::F64x2:
        return ctx.F64[2];
    case IR::Type::F64x3:
        return ctx.F64[3];
    case IR::Type::F64x4:
        return ctx.F64[4];
    default:
        return std::nullopt;
    }
}

} // namespace Shader::Backend::SPIRV
