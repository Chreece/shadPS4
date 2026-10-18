// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <bit>
#include <cstdint>
#include <cstring>
#include <span>

namespace Vulkan::Diagnostics {

// Pure bounded decoder. These are guest/Vulkan argument fields, not the CPU
// register num_indices (which merely describes the original command).
struct GotIndexedIndirectCommand {
    std::uint32_t index_count{};
    std::uint32_t instance_count{};
    std::uint32_t first_index{};
    std::int32_t vertex_offset{};
    std::uint32_t first_instance{};
};
static_assert(sizeof(GotIndexedIndirectCommand)==20);
static_assert(std::endian::native==std::endian::little);

inline constexpr std::uint32_t MaxGotIndirectProofCommands=64;

enum class GotIndirectInterpretation {
    IncompleteSnapshot,
    NoCommandsSelected,
    AllSelectedCommandsZero,
    HasNonzeroDrawCommand,
};

[[nodiscard]] inline GotIndexedIndirectCommand DecodeGotIndexedIndirect(
    std::span<const std::uint8_t> bytes, std::size_t offset) {
    GotIndexedIndirectCommand value{};
    if(offset <= bytes.size() && bytes.size()-offset>=sizeof(value)) {
        std::memcpy(&value,bytes.data()+offset,sizeof(value));
    }
    return value;
}

[[nodiscard]] inline GotIndirectInterpretation ClassifyGotGpuIndirectCommands(
    std::span<const std::uint8_t> bytes, std::uint32_t stride,
    std::uint32_t max_count, bool has_count_buffer, std::uint32_t actual_count) {
    if(stride!=sizeof(GotIndexedIndirectCommand)) {
        return GotIndirectInterpretation::IncompleteSnapshot;
    }
    const auto selected=has_count_buffer
        ? (actual_count<max_count?actual_count:max_count) : max_count;
    if(selected==0) return GotIndirectInterpretation::NoCommandsSelected;
    if(selected>MaxGotIndirectProofCommands ||
       bytes.size()<std::uint64_t(selected)*stride) {
        return GotIndirectInterpretation::IncompleteSnapshot;
    }
    for(std::uint32_t i=0;i<selected;++i) {
        const auto cmd=DecodeGotIndexedIndirect(bytes,std::size_t(i)*stride);
        if(cmd.index_count!=0 && cmd.instance_count!=0) {
            return GotIndirectInterpretation::HasNonzeroDrawCommand;
        }
    }
    return GotIndirectInterpretation::AllSelectedCommandsZero;
}

} // namespace Vulkan::Diagnostics
