// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "common/singleton.h"
#include "core/linker.h"
#include "core/module_thread_atexit.h"

namespace Core {

Module* Linker::FindByAddress(VAddr address) {
    // Runtime thread-atexit hooks may run while another thread loads a module.
    // Module objects remain alive after releasing this lock: unload is unsupported.
    std::scoped_lock lock{mutex};
    for (auto& module : m_modules) {
        const VAddr base = module->GetBaseAddress();
        if (address >= base && address - base < module->aligned_base_size) {
            return module.get();
        }
    }
    return nullptr;
}

ModuleThreadAtexitRefs* FindModuleThreadAtexitRefs(VAddr address) {
    auto* module = Common::Singleton<Linker>::Instance()->FindByAddress(address);
    return module ? &module->thread_atexit_refs : nullptr;
}

} // namespace Core
