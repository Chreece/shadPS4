// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Pure kernel HLE module-loader input tests, without GPU or game-specific code.
#include <cassert>
#include <cstdio>

#include "core/libraries/kernel/module_filename_validation.h"

using Libraries::Kernel::ValidateModuleFilename;
static_assert(ValidateModuleFilename(nullptr) == ORBIS_KERNEL_ERROR_EFAULT);
static_assert(ValidateModuleFilename("") == ORBIS_KERNEL_ERROR_ENOENT);
static_assert(ValidateModuleFilename("foo.prx") == ORBIS_OK);
static_assert(ValidateModuleFilename("/system/common/lib/libSceNgs2.sprx") == ORBIS_OK);

int main() {
    const char* names[] = {
        nullptr, "", "foo.prx", "sce_module/xyz.prx",
        "/app0/sce_module/foo.prx", "/system/common/lib/libSceNgs2.sprx",
    };
    constexpr int expected[] = {
        ORBIS_KERNEL_ERROR_EFAULT, ORBIS_KERNEL_ERROR_ENOENT,
        ORBIS_OK, ORBIS_OK, ORBIS_OK, ORBIS_OK,
    };
    for (unsigned i = 0; i < 6; ++i) {
        assert(ValidateModuleFilename(names[i]) == expected[i]);
    }
    std::puts("KERNEL_MODULE_FILENAME=PASS cases=6");
}
