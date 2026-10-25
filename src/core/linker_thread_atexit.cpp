// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "common/singleton.h"
#include "core/linker.h"
#include "core/module_thread_atexit.h"

namespace Core {

// Linker::FindByAddress is defined exactly once in linker.cpp and protects
// its module list while looking up the owning module.

ModuleThreadAtexitRefs* FindModuleThreadAtexitRefs(VAddr address) {
    auto* module = Common::Singleton<Linker>::Instance()->FindByAddress(address);
    return module ? &module->thread_atexit_refs : nullptr;
}

} // namespace Core
