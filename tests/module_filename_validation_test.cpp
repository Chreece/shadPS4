// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
// General guest HLE module filename contract; not game-specific.
#include <cassert>
#include "core/libraries/kernel/module_filename_validation.h"

using Libraries::Kernel::ValidateModuleFilename;

static_assert(ValidateModuleFilename(nullptr) == ORBIS_KERNEL_ERROR_EFAULT);
static_assert(ValidateModuleFilename("") == ORBIS_KERNEL_ERROR_ENOENT);
static_assert(ValidateModuleFilename("libSceMsgDialog.sprx") == ORBIS_OK);
static_assert(ValidateModuleFilename("/system/common/lib/libSceRtc.sprx") == ORBIS_OK);

int main() {
    assert(ValidateModuleFilename(nullptr) == ORBIS_KERNEL_ERROR_EFAULT);
    assert(ValidateModuleFilename("") == ORBIS_KERNEL_ERROR_ENOENT);
    assert(ValidateModuleFilename("libSceMsgDialog.sprx") == ORBIS_OK);
    return 0;
}
