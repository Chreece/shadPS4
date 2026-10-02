// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cstdio>
#include <cstdlib>
#include <string_view>
#include <fmt/format.h>
#include "common/assert.h"
#include "video_core/texture_cache/image_info.h"

// Only UpdateSize and the AMD tiling helpers are linked; Vulkan is not initialized.
void assert_fail_impl() {
    std::abort();
}
[[noreturn]] void unreachable_impl() {
    std::abort();
}
namespace Common::Log {
void VLog(Class, Level, const char*, int, const char*, fmt::string_view format, fmt::format_args args) {
    std::fprintf(stderr, "%s\n", fmt::vformat(format, args).c_str());
}
} // namespace Common::Log

static void Check(bool condition, const char* message) {
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", message);
        std::exit(1);
    }
}

// Permit the same fixtures to run against the pre-fix source as a negative control.
template <typename Info>
static u32 MicroMask(const Info& info) {
    if constexpr (requires { info.micro_tiled_mips; }) {
        return info.micro_tiled_mips;
    }
    return 0;
}

static VideoCore::ImageInfo Image(AmdGpu::TileMode mode, u32 width, u32 height, u32 bits,
                                 u32 levels, u32 layers = 1, bool block = false) {
    VideoCore::ImageInfo info{};
    info.tile_mode = mode;
    info.array_mode = AmdGpu::GetArrayMode(mode);
    info.size = {width, height, 1};
    info.pitch = width;
    info.num_bits = bits;
    info.resources.levels = levels;
    info.resources.layers = layers;
    info.props.is_tiled = true;
    info.props.is_block = block;
    info.UpdateSize();
    return info;
}

int main(int argc, char** argv) {
    Check(argc == 2, "one fixture name is required");
    const std::string_view test = argv[1];
    using AmdGpu::TileMode;
    if (test == "macro_mips") {
        auto info = Image(TileMode::Thin2DThin, 256, 256, 32, 9);
        const std::array<u32, 9> bytes{262144, 65536, 16384, 4096, 1024, 256, 256, 256, 256};
        u32 offset = 0;
        for (u32 mip = 0; mip < bytes.size(); ++mip) {
            Check(info.mips_layout[mip].size == bytes[mip], "thin mip byte size");
            Check(info.mips_layout[mip].offset == offset, "thin mip offset");
            offset += bytes[mip];
        }
        Check(info.guest_size == 350208, "thin chain total");
        Check(MicroMask(info) == 0x1fc, "small mips must select the micro detiler");
        info.resources.levels = 1;
        info.UpdateSize();
        Check(MicroMask(info) == 0, "recomputed layout must clear the old mip mask");
    } else if (test == "block_mips") {
        const auto info = Image(TileMode::Thin2DThin, 512, 512, 64, 10, 1, true);
        Check(info.mips_layout[0].size == 131072, "macro block base size");
        Check(info.mips_layout[1].offset == 131072, "block mip offset");
        Check(info.mips_layout[1].size == 32768, "micro block mip size");
        Check(info.mips_layout[9].size == 512, "block tail uses an 8x8 block microtile");
        Check(info.mips_layout[9].pitch == 32 && info.mips_layout[9].height == 32,
              "block layout dimensions are expressed in pixels");
        Check(MicroMask(info) == 0x3fe, "block mip detiler mask");
    } else if (test == "thick_array") {
        const auto three = Image(TileMode::Thick1DThick, 32, 32, 32, 1, 3);
        const auto five = Image(TileMode::Thick1DThick, 32, 32, 32, 1, 5);
        Check(three.guest_size == 16384, "three slices share one four-slice microtile");
        Check(five.guest_size == 32768, "five slices need two four-slice microtiles");
    } else if (test == "xthick_mip") {
        const auto info = Image(TileMode::Thick2DXThick, 64, 64, 32, 2);
        Check(info.mips_layout[0].size == 131072, "base retains eight-slice thickness");
        Check(info.mips_layout[1].size == 16384, "micro mip uses four-slice thickness");
        Check(info.guest_size == 147456, "xthick chain total");
        Check(MicroMask(info) == 2, "xthick small mip selects micro detiler");
    } else if (test == "prt_mapping") {
        Check(AmdGpu::GetArrayMode(TileMode::Thin3DThinPrt) ==
                  AmdGpu::ArrayMode::ArrayPrt3DTiledThin1,
              "3D PRT tile mode must preserve PRT addressing");
    } else {
        Check(false, "unknown fixture");
    }
    return 0;
}
