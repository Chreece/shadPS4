// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <span>

namespace Libraries::Ngs2::Hle {

enum class WaveError {
    None,
    Truncated,
    InvalidRiff,
    DuplicateChunk,
    MissingChunk,
    UnsupportedCodec,
    InvalidFormat,
    InvalidAtrac9Config,
    InvalidSampleCount,
    UnsupportedLoop,
    InvalidLoop,
    OutOfRange,
    DecoderFailure,
};
enum class Codec { Unknown, Pcm16, Atrac9 };

struct Atrac9Config {
    std::array<std::uint8_t, 4> bytes{};
    std::uint32_t channels{};
    std::uint32_t sample_rate{};
    std::uint32_t frame_bytes{};
    std::uint32_t frame_samples{};
    std::uint32_t frames_per_superframe{};
    std::uint32_t superframe_bytes{};
    std::uint32_t superframe_samples{};
};

// Positions count sample frames (one sample per channel), not interleaved samples.
// Loop positions are delay-trimmed, with an exclusive end.
struct WaveLoop {
    std::uint64_t begin{};
    std::uint64_t end{};
    std::uint32_t play_count{}; // Preserve RIFF value; zero means an unbounded loop.
};
struct Waveform {
    Codec codec{Codec::Unknown};
    std::uint32_t channels{};
    std::uint32_t sample_rate{};
    std::uint32_t channel_mask{}; // Source layout only, not the output speaker configuration.
    std::uint64_t data_offset{};
    std::uint64_t data_size{};
    std::uint64_t num_samples{};
    std::uint32_t encoder_delay{};
    Atrac9Config atrac9{};
    std::optional<WaveLoop> loop;
};

template <typename T>
struct WaveResult {
    WaveError error{WaveError::None};
    T value{};
    explicit operator bool() const noexcept {
        return error == WaveError::None;
    }
};

[[nodiscard]] std::optional<Atrac9Config> ParseAtrac9Config(std::span<const std::uint8_t, 4> bytes);

// Bounds-checked metadata parser. Does not read beyond RIFF's declared extent,
// decode audio, retain input pointers, or modify the supplied data.
[[nodiscard]] WaveResult<Waveform> ParseWaveform(std::span<const std::uint8_t> data);

struct EncodedWindow {
    std::uint64_t byte_offset{}; // Absolute offset in the RIFF input.
    std::uint64_t byte_count{};
    std::uint64_t discard_samples{};
    std::uint64_t keep_samples{};
};

// Locate enclosing storage units. ATRAC9 units are whole superframes. This is
// NOT a stateless seeking/decoder-preroll policy or the public NGS2 block ABI.
// A zero-length request returns an empty window at data_offset.
[[nodiscard]] WaveResult<EncodedWindow> LocateEncodedWindow(const Waveform& waveform,
                                                            std::uint64_t position,
                                                            std::uint64_t count);

} // namespace Libraries::Ngs2::Hle
