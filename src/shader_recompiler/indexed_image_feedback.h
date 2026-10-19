// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <cstdlib>
#include "common/types.h"
#include "video_core/renderer_vulkan/resource_audit_hash_filter.h"

namespace Shader::Diagnostics {

inline const auto& IndexedImageFeedbackFilter() {
    static const auto filter = VideoCore::Diagnostics::ParseShaderAuditHashFilter(
        std::getenv("SHADPS4_INDEXED_IMAGE_FEEDBACK_HASHES"));
    return filter;
}

inline bool IndexedImageFeedbackEnabled() {
    const auto& filter = IndexedImageFeedbackFilter();
    return filter.enabled && filter.valid;
}

inline bool IndexedImageFeedbackEnabled(u64 hash) {
    return IndexedImageFeedbackEnabled() && IndexedImageFeedbackFilter().Matches(hash);
}

inline constexpr u32 IndexedImageFeedbackOverflow = 32768;
inline constexpr u32 IndexedImageFeedbackHeaderWords = 4;
inline constexpr u32 IndexedImageFeedbackWordsPerImage =
    IndexedImageFeedbackHeaderWords + IndexedImageFeedbackOverflow / 32 + 1;

inline bool CaptureIndexedImageFeedback(u32 bindings) {
    return bindings == 1 || bindings == 32 || bindings == 256;
}

} // namespace Shader::Diagnostics
