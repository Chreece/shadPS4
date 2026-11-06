// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include "common/assert.h"
#include "shader_recompiler/ir/type.h"

namespace Shader::Gcn::OrderedGds {

inline constexpr u32 InitialResumeSite = 0xfffffffeu;

inline u32 StateWords(IR::Type type) {
    switch (type) {
    case IR::Type::U1:
    case IR::Type::U8:
    case IR::Type::U16:
    case IR::Type::U32:
    case IR::Type::F16:
    case IR::Type::F32:
        return 1;
    case IR::Type::U64:
    case IR::Type::F64:
    case IR::Type::U32x2:
    case IR::Type::F32x2:
    case IR::Type::F16x2:
        return 2;
    case IR::Type::U32x3:
    case IR::Type::F32x3:
    case IR::Type::F16x3:
        return 3;
    case IR::Type::U32x4:
    case IR::Type::F32x4:
    case IR::Type::F16x4:
    case IR::Type::F64x2:
        return 4;
    case IR::Type::F64x3:
        return 6;
    case IR::Type::F64x4:
        return 8;
    default:
        UNREACHABLE_MSG("Invalid ordered GDS continuation type {}", type);
    }
}

} // namespace Shader::Gcn::OrderedGds
