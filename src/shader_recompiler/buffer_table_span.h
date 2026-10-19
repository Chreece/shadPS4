// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <algorithm>
#include <limits>
#include <span>
#include <vector>
#include "shader_recompiler/resource.h"

namespace Shader {

struct BufferTableSpan {
    SharpFetch<AmdGpu::Buffer> source{};
    u32 record_stride{};
    u32 descriptor_offset{};
    u32 max_bytes{};
    u32 metadata_offset{};

    bool operator==(const BufferTableSpan&) const = default;
};

inline constexpr u32 BufferTableSpanWords = 8;
inline constexpr u32 MaxBufferTableRecords = 32768;

struct BufferTableRange {
    u64 address{};
    u32 size{};
};

inline bool BuildBufferTableSpan(std::span<const u8> table, const BufferTableSpan& layout,
                                 AmdGpu::Buffer& result,
                                 std::vector<BufferTableRange>* ranges = nullptr) {
    result = {};
    if (layout.record_stride < sizeof(AmdGpu::Buffer) ||
        layout.descriptor_offset > layout.record_stride - sizeof(AmdGpu::Buffer) ||
        table.size() % layout.record_stride != 0 ||
        table.size() / layout.record_stride > MaxBufferTableRecords) {
        return false;
    }
    u64 first = std::numeric_limits<u64>::max();
    u64 last{};
    u32 format{};
    bool found{};
    for (size_t offset = layout.descriptor_offset; offset < table.size();
         offset += layout.record_stride) {
        AmdGpu::Buffer buffer{};
        std::memcpy(&buffer, table.data() + offset, sizeof(buffer));
        const u64 bytes =
            buffer.stride ? u64(buffer.stride) * buffer.num_records : buffer.num_records;
        if (bytes == 0 || buffer.base_address == 0) {
            continue;
        }
        if (!buffer.Valid() || buffer.swizzle_enable || buffer.add_tid_enable ||
            bytes > layout.max_bytes || buffer.base_address + bytes > (u64{1} << 40)) {
            return false;
        }
        u32 word3{};
        std::memcpy(&word3, reinterpret_cast<const u8*>(&buffer) + 12, sizeof(word3));
        if (found && format != word3) {
            return false;
        }
        if (ranges) {
            ranges->push_back({buffer.base_address, static_cast<u32>(bytes)});
        }
        found = true;
        format = word3;
        first = std::min(first, u64(buffer.base_address));
        last = std::max(last, u64(buffer.base_address) + bytes);
    }
    if (!found) {
        return true;
    }
    first &= ~u64{3};
    last = (last + 3) & ~u64{3};
    if (last - first > layout.max_bytes) {
        return false;
    }
    result.base_address = first;
    result.num_records = static_cast<u32>(last - first);
    std::memcpy(reinterpret_cast<u8*>(&result) + 12, &format, sizeof(format));
    return true;
}

} // namespace Shader
