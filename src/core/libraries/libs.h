// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "common/startup_diagnostics.h"
#include "core/loader/elf.h"
#include "core/loader/symbols_resolver.h"
#include "core/tls.h"

void LinkSymbolImpl(Core::Loader::SymbolsResolver* sym, char const* nid, char const* lib,
                    u16 libversion, char const* mod, u64 symbol, Core::Loader::SymbolType sym_type);

#define LIB_FUNCTION(nid, lib, libversion, mod, function)                                          \
    LinkSymbolImpl(sym, nid, lib, libversion, mod, reinterpret_cast<u64>(HOST_CALL(function)),     \
                   Core::Loader::SymbolType::Function)

#define LIB_OBJ(nid, lib, libversion, mod, obj)                                                    \
    LinkSymbolImpl(sym, nid, lib, libversion, mod, reinterpret_cast<u64>(obj),                     \
                   Core::Loader::SymbolType::Object)

#define STARTUP_FUNCTION(nid, lib, libversion, mod, function)                                     \
    LinkSymbolImpl(                                                                               \
        sym, nid, lib, libversion, mod,                                                           \
        reinterpret_cast<u64>(Common::StartupDiagnostics::Enabled()                               \
                                  ? Core::HostCallWrapperImpl<                                    \
                                        Common::StartupDiagnostics::Trace<                        \
                                            function, #function>::wrap>::wrap                     \
                                  : HOST_CALL(function)),                                         \
        Core::Loader::SymbolType::Function)

namespace Libraries {

void InitHLELibs(Core::Loader::SymbolsResolver* sym);

} // namespace Libraries
