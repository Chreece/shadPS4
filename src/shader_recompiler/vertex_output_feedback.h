// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdlib>
#include <cstring>
#include "common/types.h"
#include "video_core/renderer_vulkan/resource_audit_hash_filter.h"

namespace Shader::Diagnostics {
inline constexpr u32 VertexOutputSamples = 64;
inline constexpr u32 VertexOutputWords = 2 + VertexOutputSamples * 6;

inline bool VertexOutputFeedbackEnabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("SHADPS4_VERTEX_OUTPUT_FEEDBACK");
        return value && std::strcmp(value, "1") == 0;
    }();
    return enabled;
}

inline bool VertexOutputFeedbackEnabled(u64 shader) {
    static const auto filter = VideoCore::Diagnostics::ParseShaderAuditHashFilter(
        std::getenv("SHADPS4_VERTEX_OUTPUT_FEEDBACK_HASHES"));
    return VertexOutputFeedbackEnabled() && filter.Matches(shader);
}
} // namespace Shader::Diagnostics
