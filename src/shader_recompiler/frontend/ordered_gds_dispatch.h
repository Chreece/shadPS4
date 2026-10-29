// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <array>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <optional>

// Compute dispatch coverage for a future two-pass GDS wave capture/replay.
// Important: the x-fastest slot number below is NOT a guest wave-creation
// sequence. Vulkan does not specify which workgroup starts first. A correct
// ordered GDS executor must obtain real guest ordering separately.
namespace Shader::Gcn::OrderedGds {

struct DispatchExtent {
    std::uint32_t x{};
    std::uint32_t y{};
    std::uint32_t z{};
};

struct LogicalWaveSlot {
    DispatchExtent group;
    std::uint32_t wave_in_group{};
};

class DispatchLayout {
public:
    // Bounds are deliberate: guest dispatch dimensions must not cause
    // overflow, unbounded buffers or unbounded host task enumeration.
    static std::optional<DispatchLayout> Create(DispatchExtent groups,
                                                DispatchExtent local_threads,
                                                std::uint32_t guest_wave_size = 64,
                                                std::uint64_t max_wave_slots = 1'000'000) {
        if (guest_wave_size == 0 || max_wave_slots == 0) {
            return std::nullopt;
        }
        std::uint64_t num_groups = 1, threads_per_group = 1;
        for (const auto extent : {groups.x, groups.y, groups.z}) {
            if (extent == 0 || !Multiply(num_groups, extent)) {
                return std::nullopt;
            }
        }
        for (const auto extent : {local_threads.x, local_threads.y, local_threads.z}) {
            if (extent == 0 || !Multiply(threads_per_group, extent)) {
                return std::nullopt;
            }
        }
        const auto waves_per_group = threads_per_group / guest_wave_size +
                                      std::uint64_t(threads_per_group % guest_wave_size != 0);
        if (waves_per_group == 0 || waves_per_group > max_wave_slots ||
            num_groups > max_wave_slots / waves_per_group ||
            waves_per_group > std::numeric_limits<std::uint32_t>::max()) {
            return std::nullopt;
        }
        return DispatchLayout(groups, local_threads, guest_wave_size,
                              static_cast<std::uint32_t>(waves_per_group),
                              num_groups * waves_per_group);
    }

    [[nodiscard]] std::optional<std::uint64_t> LogicalSlot(
        DispatchExtent group, std::uint32_t wave_in_group) const {
        if (group.x >= groups_.x || group.y >= groups_.y || group.z >= groups_.z ||
            wave_in_group >= waves_per_group_) {
            return std::nullopt;
        }
        const std::uint64_t flattened =
            (std::uint64_t(group.z) * groups_.y + group.y) * groups_.x + group.x;
        return flattened * waves_per_group_ + wave_in_group;
    }

    [[nodiscard]] std::optional<LogicalWaveSlot> Unflatten(std::uint64_t slot) const {
        if (slot >= total_wave_slots_) {
            return std::nullopt;
        }
        const auto wave_in_group = static_cast<std::uint32_t>(slot % waves_per_group_);
        auto flat_group = slot / waves_per_group_;
        const auto x = static_cast<std::uint32_t>(flat_group % groups_.x);
        flat_group /= groups_.x;
        const auto y = static_cast<std::uint32_t>(flat_group % groups_.y);
        flat_group /= groups_.y;
        return LogicalWaveSlot{
            .group = {x, y, static_cast<std::uint32_t>(flat_group)},
            .wave_in_group = wave_in_group,
        };
    }

    [[nodiscard]] std::uint64_t TotalSlots() const { return total_wave_slots_; }
    [[nodiscard]] std::uint32_t WavesPerGroup() const { return waves_per_group_; }
    [[nodiscard]] std::uint32_t GuestWaveSize() const { return guest_wave_size_; }
    [[nodiscard]] DispatchExtent Groups() const { return groups_; }
    [[nodiscard]] DispatchExtent LocalThreads() const { return local_threads_; }

private:
    static bool Multiply(std::uint64_t& value, std::uint64_t factor) {
        if (value > std::numeric_limits<std::uint64_t>::max() / factor) {
            return false;
        }
        value *= factor;
        return true;
    }

    DispatchLayout(DispatchExtent groups, DispatchExtent local_threads,
                   std::uint32_t guest_wave_size, std::uint32_t waves_per_group,
                   std::uint64_t total_wave_slots)
        : groups_{groups}, local_threads_{local_threads},
          guest_wave_size_{guest_wave_size}, waves_per_group_{waves_per_group},
          total_wave_slots_{total_wave_slots} {}

    DispatchExtent groups_;
    DispatchExtent local_threads_;
    std::uint32_t guest_wave_size_;
    std::uint32_t waves_per_group_;
    std::uint64_t total_wave_slots_;
};

} // namespace Shader::Gcn::OrderedGds
