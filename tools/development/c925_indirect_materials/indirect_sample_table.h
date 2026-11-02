// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cstring>
#include <span>
#include "common/types.h"
#include "video_core/amdgpu/resource.h"

namespace Shader::IndirectSample {
// This is a compilation budget, never a guest allocation or fabricated bound.
inline constexpr u32 MaxEntries = 16;
inline constexpr u64 AddressMask = 0xFFFFFFFFFFFFULL;
struct Source {
    u32 root{};
    u32 descriptor_word{};
    u32 record_bytes{};
    u32 pointer_offset{};
    u32 image_word{};
    u32 sampler_word{};
    bool operator==(const Source&) const = default;
};
enum class Status : u32 { Unavailable, Empty, UnsupportedLayout, OverBudget, Unmapped, Ready };
struct Snapshot {
    std::array<u32, 4> buffer_words{};
    std::array<std::array<u32, 12>, MaxEntries> descriptors{};
    u32 count{};
    Status status{Status::Unavailable};
    bool operator==(const Snapshot&) const = default;
};
struct Dependency {
    Source source{};
    Snapshot compiled{};
};
inline bool Range(u64 base, u64 offset, u64 size) {
    return base != 0 && base <= AddressMask && size != 0 &&
           offset <= AddressMask - base && size - 1 <= AddressMask - base - offset;
}
// Reader must check mapping and use the existing coherent guest read path.
// It is never asked to read an unbounded prefix or a guest-selected host lane.
template<class Reader>
Snapshot Read(const Source& s, std::span<const u32> user_data, Reader&& reader) {
    Snapshot out;
    if (s.root >= user_data.size() || user_data.size() - s.root < 2 ||
        s.record_bytes < 8 || (s.record_bytes & 3) != 0 || (s.pointer_offset & 3) != 0 ||
        s.pointer_offset > s.record_bytes - 8) return out;
    const u64 root = (u64(user_data[s.root]) | (u64(user_data[s.root + 1]) << 32)) & AddressMask;
    const u64 source_offset = u64(s.descriptor_word) * 4;
    if (!Range(root, source_offset, 16) ||
        !reader(root + source_offset, out.buffer_words.data(), 16)) return out;
    AmdGpu::Buffer buffer{};
    std::memcpy(&buffer, out.buffer_words.data(), sizeof(buffer));
    if (!buffer.Valid() || buffer.base_address == 0) return out;
    const u64 bytes = u64(buffer.num_records) * (buffer.stride ? u64(buffer.stride) : 1u);
    if (bytes == 0) { out.status = Status::Empty; return out; }
    if (bytes % s.record_bytes != 0 || !Range(buffer.base_address, 0, bytes)) {
        out.status = Status::UnsupportedLayout; return out;
    }
    const u64 entries = bytes / s.record_bytes;
    if (entries > MaxEntries) { out.status = Status::OverBudget; return out; }
    // Fail closed before lowering any image when an actual pointed-to descriptor
    // cannot be safely read. Do not synthesize a partial table or valid descriptor.
    for (u32 i = 0; i < entries; ++i) {
        const u64 record = u64(buffer.base_address) + u64(i) * s.record_bytes + s.pointer_offset;
        std::array<u32, 2> pointer{};
        if (!reader(record, pointer.data(), 8)) { out.status = Status::Unmapped; return out; }
        const u64 base = (u64(pointer[0]) | (u64(pointer[1]) << 32)) & AddressMask;
        const u64 image = u64(s.image_word) * 4, sampler = u64(s.sampler_word) * 4;
        if (!Range(base, image, 32) || !Range(base, sampler, 16) ||
            !reader(base + image, out.descriptors[i].data(), 32) ||
            !reader(base + sampler, out.descriptors[i].data() + 8, 16)) {
            out.status = Status::Unmapped; return out;
        }
    }
    out.count = static_cast<u32>(entries);
    out.status = Status::Ready;
    return out;
}
} // namespace Shader::IndirectSample
