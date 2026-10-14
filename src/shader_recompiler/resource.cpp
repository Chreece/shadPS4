// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cstring>
#include <limits>

#include "core/memory.h"
#include "shader_recompiler/info.h"
#include "shader_recompiler/resource.h"

namespace Shader {
namespace {

AmdGpu::Image NormalizeImage(const ImageResource& desc, AmdGpu::Image image) {
    if (desc.post_op == SharpFetchPostOp::ConvertCubeTo2DArray) {
        image.type = u64(AmdGpu::ImageType::Color2DArray);
        image.depth = (image.depth + 1) * 6 - 1;
    }
    if (!image.Valid()) {
        return AmdGpu::Image::Null(desc.is_depth);
    }
    if (desc.is_depth) {
        const auto data_fmt = image.GetDataFmt();
        if (data_fmt != AmdGpu::DataFormat::Format16 &&
            data_fmt != AmdGpu::DataFormat::Format32) {
            return AmdGpu::Image::Null(true);
        }
    }
    return image;
}

} // Anonymous namespace

AmdGpu::Buffer ImageResource::GetDynamicTable(const Info& info) const noexcept {
    if (!dynamic_table.enabled) {
        return AmdGpu::Buffer::Null();
    }

    AmdGpu::Buffer table{};
    if (!dynamic_table.table_fetch.Fetch(info.flattened_ud_buf.data(), &table) || !table.Valid()) {
        return AmdGpu::Buffer::Null();
    }

    const u32 image_bytes = is_r128 ? 4 * sizeof(u32) : sizeof(AmdGpu::Image);
    if (table.GetStride() != dynamic_table.record_stride_bytes ||
        dynamic_table.record_stride_bytes == 0 ||
        dynamic_table.field_offset_bytes > dynamic_table.record_stride_bytes ||
        image_bytes > dynamic_table.record_stride_bytes - dynamic_table.field_offset_bytes) {
        return AmdGpu::Buffer::Null();
    }

    return table;
}

bool ImageResource::FetchDynamicSharp(const Info& info, u32 record,
                                      AmdGpu::Image* out) const noexcept {
    const AmdGpu::Buffer table = GetDynamicTable(info);
    if (!table || record >= table.num_records) {
        return false;
    }

    const u64 row_offset = u64(record) * dynamic_table.record_stride_bytes;
    const u64 byte_offset = row_offset + dynamic_table.field_offset_bytes;
    const u32 image_bytes = is_r128 ? 4 * sizeof(u32) : sizeof(AmdGpu::Image);
    if (byte_offset > std::numeric_limits<VAddr>::max() - table.base_address ||
        table.base_address + byte_offset >
            std::numeric_limits<VAddr>::max() - image_bytes) {
        return false;
    }

    const VAddr address = table.base_address + byte_offset;
    auto* memory = Core::Memory::Instance();
    if (!memory->IsAccessibleRange(address, image_bytes, Core::MemoryProt::CpuRead)) {
        return false;
    }

    AmdGpu::Image image{};
    std::memcpy(&image, reinterpret_cast<const void*>(address), image_bytes);
    if (is_r128) {
        image.pitch = image.width;
    }
    *out = NormalizeImage(*this, image);
    return true;
}

AmdGpu::Image ImageResource::GetSharp(const Info& info) const noexcept {
    if (!dynamic_table.enabled) {
        AmdGpu::Image image{};
        if (!Fetch(info.flattened_ud_buf.data(), &image)) {
            return AmdGpu::Image::Null(is_depth);
        }
        return NormalizeImage(*this, image);
    }

    const AmdGpu::Buffer table = GetDynamicTable(info);
    if (!table) {
        return AmdGpu::Image::Null(is_depth);
    }

    for (u32 record = 0; record < table.num_records; ++record) {
        AmdGpu::Image image{};
        if (FetchDynamicSharp(info, record, &image) && image) {
            return image;
        }
    }
    return AmdGpu::Image::Null(is_depth);
}

u32 ImageResource::NumBindings(const Info& info) const noexcept {
    if (dynamic_table.enabled) {
        const AmdGpu::Buffer table = GetDynamicTable(info);
        return table ? table.num_records : 0;
    }

    const AmdGpu::Image tsharp = GetSharp(info);
    return (mip_fallback_mode == MipStorageFallbackMode::DynamicIndex)
               ? (tsharp.last_level - tsharp.base_level + 1)
               : 1;
}

} // namespace Shader
