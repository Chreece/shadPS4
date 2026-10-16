// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Host-independent test of the exact type dispatch used for SPIR-V SSA Phi.
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <utility>

#include "shader_recompiler/backend/spirv/phi_type_id.h"
#include "shader_recompiler/ir/float_type_usage.h"

namespace {
struct TypeVector {
    std::array<std::uint32_t, 4> ids{};
    constexpr std::uint32_t operator[](std::uint32_t index) const {
        return ids[index - 1];
    }
};
struct MockContext {
    std::uint32_t U8{11};
    std::uint32_t U16{12};
    std::uint32_t U64{14};
    TypeVector U1{{21, 22, 23, 24}};
    TypeVector U32{{31, 32, 33, 34}};
    TypeVector F16{{41, 42, 43, 44}};
    TypeVector F32{{51, 52, 53, 54}};
    TypeVector F64{{61, 62, 63, 64}};
};
using Shader::IR::Type;
using Shader::Backend::SPIRV::LookupPhiTypeId;
static_assert(Shader::IR::IsFloat64Type(Type::F64));
static_assert(Shader::IR::IsFloat64Type(Type::F64x2));
static_assert(Shader::IR::IsFloat64Type(Type::F64x3));
static_assert(Shader::IR::IsFloat64Type(Type::F64x4));
static_assert(Shader::IR::IsFloat16Type(Type::F16));
static_assert(Shader::IR::IsFloat16Type(Type::F16x4));
static_assert(!Shader::IR::IsFloat64Type(Type::F32));
static_assert(!Shader::IR::IsFloat16Type(Type::U64));
constexpr MockContext context{};
static_assert(LookupPhiTypeId(context, Type::F64).value() == 61);
static_assert(LookupPhiTypeId(context, Type::F64x4).value() == 64);
static_assert(!LookupPhiTypeId(context, Type::Void).has_value());
} // namespace

int main() {
    constexpr std::array cases{
        std::pair{Type::U1, 21U}, std::pair{Type::U8, 11U},
        std::pair{Type::U16, 12U}, std::pair{Type::U32, 31U},
        std::pair{Type::U32x2, 32U}, std::pair{Type::U32x3, 33U},
        std::pair{Type::U32x4, 34U}, std::pair{Type::U64, 14U},
        std::pair{Type::F16, 41U}, std::pair{Type::F16x2, 42U},
        std::pair{Type::F16x3, 43U}, std::pair{Type::F16x4, 44U},
        std::pair{Type::F32, 51U}, std::pair{Type::F32x2, 52U},
        std::pair{Type::F32x3, 53U}, std::pair{Type::F32x4, 54U},
        std::pair{Type::F64, 61U}, std::pair{Type::F64x2, 62U},
        std::pair{Type::F64x3, 63U}, std::pair{Type::F64x4, 64U},
    };
    for (const auto& [type, expected] : cases) {
        const auto result = LookupPhiTypeId(context, type);
        if (!result.has_value() || *result != expected) {
            std::fputs("SPIRV_PHI_TYPES=FAIL\n", stderr);
            return 1;
        }
    }
    if (LookupPhiTypeId(context, Type::Void).has_value() ||
        LookupPhiTypeId(context, Type::Opaque).has_value()) {
        std::fputs("SPIRV_PHI_TYPES=FAIL unsupported accepted\n", stderr);
        return 1;
    }
    std::puts("SPIRV_PHI_TYPES=PASS mappings=20 unsupported=2 fp64=4");
    return 0;
}
