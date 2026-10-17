// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdlib>
#include "common/types.h"
#include "video_core/renderer_vulkan/resource_audit_hash_filter.h"

namespace Shader {
// Capture GCN binary and all IR stages for only the requested hashes, never
// enabling a global shader dump when the variable is unset or invalid.
inline const VideoCore::Diagnostics::ShaderAuditHashFilter& ShaderDumpTargets() {
    static const auto filter=[] {
        const char* const value=std::getenv("SHADPS4_TARGET_SHADER_DUMP_HASHES");
        auto f=VideoCore::Diagnostics::ParseShaderAuditHashFilter(value);
        if(!f.enabled) {
            f.enabled=true;
            f.valid=false;
        }
        return f;
    }();
    return filter;
}
} // namespace Shader
