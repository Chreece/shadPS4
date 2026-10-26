// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

namespace Shader::Optimization {

// A ReadConst not flattened by the host SRT walker still needs the
// original guest address on the GPU. Ordinary flattened reads stay on the
// fast host SRT path; DMA is provisioned only for unresolved dynamic reads.
struct SrtDmaPolicy {
    bool has_unflattened_readconst;
    bool supports_bda;

    constexpr bool NeedsGpuRead() const noexcept {
        return has_unflattened_readconst && supports_bda;
    }

    constexpr bool IsUnsupported() const noexcept {
        return has_unflattened_readconst && !supports_bda;
    }
};

} // namespace Shader::Optimization
