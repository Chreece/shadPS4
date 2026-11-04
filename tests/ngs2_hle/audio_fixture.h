// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include "fixture.h"

namespace Fixture {

inline Bytes PcmSamples(std::span<const std::int16_t> samples, std::uint16_t channels = 1,
                        std::uint32_t rate = 48000) {
    auto fmt = PcmFmt(channels);
    Put32(fmt, 4, rate);
    Put32(fmt, 8, rate * channels * 2);
    Bytes data;
    for (auto sample : samples)
        U16(data, static_cast<std::uint16_t>(sample));
    auto riff = Riff();
    Chunk(riff, "fmt ", fmt);
    Chunk(riff, "data", data);
    Finish(riff);
    return riff;
}

struct BitWriter {
    Bytes bytes;
    unsigned position{};
    void Write(unsigned value, unsigned bits) {
        for (unsigned i = bits; i > 0; --i) {
            if (position % 8 == 0)
                bytes.push_back(0);
            bytes.back() |= ((value >> (i - 1)) & 1) << (7 - position % 8);
            ++position;
        }
    }
    void Align() {
        while (position % 8)
            Write(0, 1);
    }
};

// A small, original ATRAC9 bitstream encoder for tests, derived from the pinned
// codec's unpack.c/scale_factors.c. Every standard channel has only coefficient 0
// nonzero: scale 20, precision 7, direct signed 8-bit spectra (no spectral Huffman).
// This produces nonzero windowed/overlapped audio, unlike metadata placeholder data.
inline void At9Block(BitWriter& bits, bool first, unsigned channel, unsigned count,
                     unsigned amplitude, bool lfe = false) {
    bits.Write(first ? 0 : 1, 1);
    bits.Write(0, 1); // do not reuse band parameters
    if (lfe) {
        bits.Write(20, 5);
        bits.Write(20, 5);
        for (unsigned i = 0; i < 4; ++i)
            bits.Write(i == 0 ? 4 : 0, 5);
        bits.Align();
        return;
    }
    bits.Write(0, 4); // minimum three bands -> ten quantization units, 24 coefficients
    if (count == 2)
        bits.Write(0, 4); // code both stereo channels in full
    bits.Write(0, 1);     // no band extension
    bits.Write(0, 2);     // gradient mode
    bits.Write(0, 6);
    bits.Write(0, 6); // end unit = 1
    bits.Write(13, 5);
    bits.Write(13, 5); // constant gradient: 20 - 13 = precision 7
    bits.Write(0, 4);  // boundary
    if (count == 2) {
        bits.Write(0, 1); // primary channel
        bits.Write(0, 1); // joint stereo signs
    }
    bits.Write(0, 1); // no extension data
    for (unsigned ch = 0; ch < count; ++ch) {
        bits.Write(1, 2); // CLC for first channel; delta from first for second
        if (ch == 0) {
            bits.Write(3, 2); // five-bit absolute scale factors
            for (unsigned unit = 0; unit < 10; ++unit)
                bits.Write(20, 5);
        } else {
            bits.Write(0, 2); // signed scale-factor codebook B2
            for (unsigned unit = 0; unit < 10; ++unit)
                bits.Write(0, 1); // code 0 = unchanged scale factor
        }
        for (unsigned coefficient = 0; coefficient < 24; ++coefficient)
            bits.Write(coefficient == 0 ? amplitude * (channel + ch + 1) : 0, 8);
    }
    bits.Align();
}

inline Bytes At9Audio(bool surround = false, std::uint32_t delay = 0, std::uint32_t samples = 0,
                      unsigned superframes = 3, bool inverted = false) {
    const unsigned channels = surround ? 8 : 1;
    const unsigned rate = surround ? 48000 : 24000;
    const unsigned frame_samples = surround ? 256 : 128;
    const unsigned frame_bytes = surround ? 384 : 48;
    auto fmt = At9Fmt();
    fmt[2] = channels;
    Put32(fmt, 4, rate);
    Put32(fmt, 8, frame_bytes * rate / frame_samples);
    fmt[12] = (frame_bytes * 4) & 255;
    fmt[13] = (frame_bytes * 4) >> 8;
    fmt[18] = (frame_samples * 4) & 255;
    fmt[19] = (frame_samples * 4) >> 8;
    Put32(fmt, 20, surround ? 0x63f : 4);
    fmt[45] = surround ? 0x78 : 0x40;
    fmt[46] = (frame_bytes - 1) >> 3;
    fmt[47] = (((frame_bytes - 1) & 7) << 5) | 0x10; // four frames per superframe
    Bytes data;
    for (unsigned sf = 0; sf < superframes; ++sf) {
        BitWriter bits;
        for (unsigned frame = 0; frame < 4; ++frame) {
            // Different frames expose overlap, seeking and padding mistakes.
            const unsigned positive = 2 + sf * 4 + frame;
            const unsigned amplitude = inverted ? 0u - positive : positive;
            if (surround) {
                At9Block(bits, frame == 0, 0, 2, amplitude);
                At9Block(bits, frame == 0, 2, 1, amplitude);
                At9Block(bits, frame == 0, 3, 1, amplitude, true);
                At9Block(bits, frame == 0, 4, 2, amplitude);
                At9Block(bits, frame == 0, 6, 2, amplitude);
            } else {
                At9Block(bits, frame == 0, 0, 1, amplitude);
            }
        }
        // Padding only at the end of a superframe, deliberately not zero.
        bits.bytes.resize(frame_bytes * 4, 0xa5);
        data.insert(data.end(), bits.bytes.begin(), bits.bytes.end());
    }
    auto riff = Riff();
    Chunk(riff, "fmt ", fmt);
    Chunk(riff, "fact", Fact(samples ? samples : superframes * frame_samples * 4 - delay, delay));
    Chunk(riff, "data", data);
    Finish(riff);
    return riff;
}

} // namespace Fixture
