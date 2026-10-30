// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <vector>
#include "shader_recompiler/backend/bindings.h"
#include "shader_recompiler/ir/program.h"
#include "shader_recompiler/profile.h"

namespace Shader::Backend::SPIRV {

[[nodiscard]] std::vector<u32> EmitSPIRV(const Profile& profile, const RuntimeInfo& runtime_info,
                                         const IR::Program& program, Bindings& binding);

// Compile the ORIGINAL translated GCN through the DS_ORDERED_COUNT site,
// write its real packet/register checkpoint, and terminate execution before
// consuming the ordered counter. This is an explicit first-pass helper for a
// future authenticated two-pass scheduler. It does NOT implement or enable
// final guest execution, nor alter EmitSPIRV's fail-closed opcode gate.
// No post-DS side effects or arbitrary counter fallback may execute.
[[nodiscard]] std::vector<u32> EmitSPIRVOrderedGdsCapturePass(
    const Profile& profile, const RuntimeInfo& runtime_info,
    const IR::Program& program, Bindings& binding);

} // namespace Shader::Backend::SPIRV
