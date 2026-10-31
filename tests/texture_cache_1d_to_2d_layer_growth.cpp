// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <cstdint>
#include <iostream>
#include "video_core/texture_cache/compatible_layer_growth.h"

enum class Type { Color1D=8, Color2D=9, Color3D=10 };
struct Extent { std::uint32_t width,height,depth; bool operator==(const Extent&) const = default; };
struct Resources { std::uint32_t levels,layers; };
struct Props { bool is_tiled, is_pow2, is_block, is_depth, is_volume, has_stencil; };
struct Info {
    Type type;
    std::uint64_t guest_address;
    std::uint32_t guest_size;
    Extent size;
    Resources resources;
    std::uint32_t pitch, num_bits, num_samples, pixel_format, tile_mode, array_mode;
    std::uint8_t bank_swizzle;
    bool alt_tile;
    Props props;
};
using VideoCore::TextureOverlap::CanExpandColor1DToColor2DLayers;
constexpr bool good(const Info& next,const Info& old) {
    return CanExpandColor1DToColor2DLayers(next,old,Type::Color1D,Type::Color2D);
}
int main() {
    const Info old{Type::Color1D,0x10330b8500ULL,0x100,{1,1,1},{1,1},
                   32,8,1,9,0xd,13,0,false,{true,false,false,false,false,false}};
    Info next=old; next.type=Type::Color2D; next.resources.layers=6;next.guest_size=0x600;
    assert(good(next,old));
    Info r=next;
    r.type=Type::Color3D; assert(!good(r,old));
    r=next;r.guest_address++;assert(!good(r,old));
    r=next;r.guest_size=0x500;assert(!good(r,old));
    r=next;r.guest_size=0x601;assert(!good(r,old));
    r=next;r.resources.layers=1;assert(!good(r,old));
    r=next;r.resources.levels=2;assert(!good(r,old));
    r=next;r.size.height=2;assert(!good(r,old));
    r=next;r.pitch=64;assert(!good(r,old));
    r=next;r.num_bits=32;assert(!good(r,old));
    r=next;r.num_samples=4;assert(!good(r,old));
    r=next;r.pixel_format=4;assert(!good(r,old));
    r=next;r.tile_mode=0xc;assert(!good(r,old));
    r=next;r.array_mode=3;assert(!good(r,old));
    r=next;r.bank_swizzle=1;assert(!good(r,old));
    r=next;r.alt_tile=true;assert(!good(r,old));
    r=next;r.props.is_depth=true;assert(!good(r,old));
    r=next;r.props.is_block=true;assert(!good(r,old));
    r=next;r.props.is_volume=true;assert(!good(r,old));
    r=next;r.props.is_pow2=true;assert(!good(r,old));
    r=next;r.props.has_stencil=true;assert(!good(r,old));
    r=next;r.props.is_tiled=false;assert(!good(r,old));
    Info wrongOld=old;wrongOld.type=Type::Color2D;assert(!good(next,wrongOld));
    wrongOld=old;wrongOld.resources.layers=2;assert(!good(next,wrongOld));
    wrongOld=old;wrongOld.guest_size=0;assert(!good(next,wrongOld));
    std::cout << "TEXTURE_1D_TO_2D_SAME_STORAGE_LAYER_GROWTH=PASS\n";
    std::cout << "TEXTURE_DIFFERENT_TILING_OR_FORMAT_REJECTED=PASS\n";
    std::cout << "TEXTURE_GPU_DATA_PRESERVATION_PRECONDITIONS=PASS\n";
}
