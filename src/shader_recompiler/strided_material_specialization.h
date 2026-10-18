// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>
#include "shader_recompiler/guest_tsharp_array.h"

namespace Shader::Optimization {

// The material resource layout of a compute shader is determined by the
// COMPLETE *current* guest V# table. Earlier draws can supply a legal,
// zero-record V# for the same shader code; later draws can supply the actual
// strided material table. Do not reuse the zero-record SPIR-V/layout for the
// later dispatch and do not fabricate records when the table is absent.
//
// The returned count is intentionally independent of the buffer's *address*:
// two real 97-record tables share one shader permutation while their runtime
// bindings still read each dispatch's actual guest address.
[[nodiscard]] constexpr std::uint32_t StridedMaterialPermutationEntries(
    bool fetched, bool valid, std::uint64_t base_address,
    std::uint32_t vsharp_stride, std::uint32_t num_records,
    std::uint32_t record_stride_bytes, std::uint32_t descriptor_offset_bytes,
    std::uint32_t max_entries) noexcept {
    if (!fetched || !valid || base_address == 0 || num_records == 0) {
        return 0;
    }
    return CompleteGuestTsharpRecordEntries(
        vsharp_stride, num_records, record_stride_bytes,
        descriptor_offset_bytes, max_entries);
}

} // namespace Shader::Optimization
