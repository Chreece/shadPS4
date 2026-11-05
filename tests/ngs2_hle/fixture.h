// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cstdint>
#include <span>
#include <vector>

namespace Fixture {
using Bytes = std::vector<std::uint8_t>;
inline void U16(Bytes &out, std::uint16_t x) {
    out.push_back(x & 255);
    out.push_back(x >> 8);
}
inline void U32(Bytes &out, std::uint32_t x) {
    for (int i = 0; i < 4; ++i)
        out.push_back((x >> (i * 8)) & 255);
}
inline void Put32(Bytes &out, std::size_t at, std::uint32_t x) {
    for (int i = 0; i < 4; ++i)
        out.at(at + i) = (x >> (i * 8)) & 255;
}
inline void Id(Bytes &out, const char *id) {
    for (int i = 0; i < 4; ++i)
        out.push_back(id[i]);
}
inline void Chunk(Bytes &out, const char *id, const Bytes &body) {
    Id(out, id);
    U32(out, static_cast<std::uint32_t>(body.size()));
    out.insert(out.end(), body.begin(), body.end());
    if (body.size() & 1)
        out.push_back(0);
}
inline Bytes Riff() {
    Bytes b;
    Id(b, "RIFF");
    U32(b, 0);
    Id(b, "WAVE");
    return b;
}
inline void Finish(Bytes &b) {
    Put32(b, 4, static_cast<std::uint32_t>(b.size() - 8));
}
inline Bytes PcmFmt(std::uint16_t channels) {
    Bytes b;
    U16(b, 1);
    U16(b, channels);
    U32(b, 48000);
    U32(b, 48000 * channels * 2);
    U16(b, channels * 2);
    U16(b, 16);
    return b;
}
inline Bytes At9Fmt() {
    // Public WAVE/ATRAC9 metadata matching the observed 24-kHz mono input.
    // No game audio bytes are included in any fixture.
    Bytes b;
    U16(b, 0xfffe);
    U16(b, 1);
    U32(b, 24000);
    U32(b, 9000);
    U16(b, 192);
    U16(b, 0);
    U16(b, 34);
    U16(b, 512);
    U32(b, 4);
    const std::array<std::uint8_t, 16> guid{0xd2, 0x42, 0xe1, 0x47, 0xba, 0x36, 0x8d, 0x4d,
                                            0x88, 0xfc, 0x61, 0x65, 0x4f, 0x8c, 0x83, 0x6c};
    b.insert(b.end(), guid.begin(), guid.end());
    U32(b, 1);
    b.insert(b.end(), {0xfe, 0x40, 0x05, 0xf0});
    U32(b, 0);
    return b;
}
inline Bytes Fact(std::uint32_t samples = 34534, std::uint32_t delay = 128) {
    Bytes b;
    U32(b, samples);
    U32(b, delay);
    U32(b, delay);
    return b;
}
inline Bytes Smpl(std::uint32_t first = 128, std::uint32_t last = 34661) {
    Bytes b(36);
    Put32(b, 28, 1);
    Put32(b, 32, 24);
    U32(b, 0);
    U32(b, 0);
    U32(b, first);
    U32(b, last);
    U32(b, 0);
    U32(b, 0);
    return b;
}
inline Bytes At9(bool loop = true) {
    auto b = Riff();
    Chunk(b, "fmt ", At9Fmt());
    Chunk(b, "fact", Fact());
    if (loop)
        Chunk(b, "smpl", Smpl());
    Chunk(b, "data", Bytes(13056, 0));
    Finish(b);
    return b;
}
inline Bytes Pcm(std::uint16_t channels = 8, std::uint32_t frames = 256) {
    auto b = Riff();
    Chunk(b, "fmt ", PcmFmt(channels));
    Chunk(b, "data", Bytes(frames * channels * 2));
    Finish(b);
    return b;
}
} // namespace Fixture
