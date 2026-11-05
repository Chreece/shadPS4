// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <vector>

namespace Libraries::Ngs2::Hle {

using Handle = std::uint64_t;
enum class RackKind : std::uint32_t { Sampler = 0x1000, Submixer = 0x2000, Mastering = 0x3000 };
enum class HandleError { None, InvalidHandle, InvalidOption, ResourceLimit, OutOfMemory };
struct HandleResult {
    HandleError error{HandleError::None};
    Handle value{};
    explicit operator bool() const noexcept {
        return error == HandleError::None;
    }
};
struct SystemSpec {
    std::uint32_t max_grain_samples{512};
    std::uint32_t grain_samples{256};
    std::uint32_t sample_rate{48000};
};
struct RackSpec {
    RackKind kind{RackKind::Sampler};
    std::uint32_t max_voices{};
    std::uint32_t max_channels{8};
};
struct VoiceIdentity {
    Handle system{};
    Handle rack{};
    std::uint32_t index{};
};
struct HandleLimits {
    // Host resource budgets, NOT claims about console SDK limits.
    std::size_t systems{16};
    std::size_t racks{1024};
    std::size_t voices{65536};
};

// Own one registry for an emulator audio runtime. Tokens are never pointers and
// are not reused during that registry's lifetime. All returned data are copies.
// Guest buffer allocators and playback state are deliberately outside this layer.
class HandleRegistry {
public:
    explicit HandleRegistry(HandleLimits limits = {}) : limits{limits} {}
    [[nodiscard]] HandleResult CreateSystem(SystemSpec spec = {});
    [[nodiscard]] HandleResult CreateRack(Handle system, RackSpec spec);
    [[nodiscard]] HandleResult GetVoice(Handle rack, std::uint32_t index) const;
    [[nodiscard]] std::optional<SystemSpec> GetSystem(Handle system) const;
    [[nodiscard]] std::optional<RackSpec> GetRack(Handle rack) const;
    [[nodiscard]] std::optional<VoiceIdentity> GetVoiceIdentity(Handle voice) const;
    [[nodiscard]] HandleError SetGrainSamples(Handle system, std::uint32_t samples);
    [[nodiscard]] HandleError SetSampleRate(Handle system, std::uint32_t rate);
    [[nodiscard]] HandleError DestroyRack(Handle rack);
    [[nodiscard]] HandleError DestroySystem(Handle system);
    [[nodiscard]] std::size_t Size() const;

private:
    enum class Kind { System, Rack, Voice };
    struct Record {
        Kind kind{};
        Handle system{};
        SystemSpec system_spec{};
        RackSpec rack_spec{};
        VoiceIdentity identity{};
        std::vector<Handle> voices;
    };
    HandleLimits limits;
    mutable std::mutex mutex;
    std::map<Handle, Record> records;
    Handle next{1};
    std::size_t system_count{};
    std::size_t rack_count{};
    std::size_t voice_count{};
};

} // namespace Libraries::Ngs2::Hle
