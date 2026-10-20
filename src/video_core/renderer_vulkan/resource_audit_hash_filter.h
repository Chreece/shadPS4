// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdlib>

namespace VideoCore::Diagnostics {
// Optional generic shader-resource audit filter. Empty means all shaders;
// invalid configuration fails closed instead of flooding the log.
inline constexpr std::size_t MaxShaderAuditHashes = 64;

struct ShaderAuditHashFilter {
    std::array<std::uint64_t, MaxShaderAuditHashes> hashes{};
    std::size_t count{};
    bool enabled{};
    bool valid{true};

    [[nodiscard]] constexpr bool Matches(std::uint64_t hash) const noexcept {
        if (!enabled) return true;
        if (!valid) return false;
        for (std::size_t i=0; i<count; ++i) {
            if (hashes[i] == hash) return true;
        }
        return false;
    }
};

[[nodiscard]] inline ShaderAuditHashFilter ParseShaderAuditHashFilter(const char* text) noexcept {
    ShaderAuditHashFilter filter{};
    if (text == nullptr || *text == '\0') return filter;
    filter.enabled = true;
    for (const char* start = text; *start != '\0';) {
        if (filter.count == filter.hashes.size() || *start < '0' || *start > '9') {
            filter.valid = false;
            return filter;
        }
        errno = 0;
        char* end{};
        const auto value = std::strtoull(start, &end, 0);
        if (end == start || errno == ERANGE) {
            filter.valid = false;
            return filter;
        }
        filter.hashes[filter.count++] = static_cast<std::uint64_t>(value);
        if (*end == '\0') return filter;
        if (*end != ',' || end[1] == '\0') {
            filter.valid = false;
            return filter;
        }
        start = end + 1;
    }
    filter.valid = false;
    return filter;
}
} // namespace VideoCore::Diagnostics
