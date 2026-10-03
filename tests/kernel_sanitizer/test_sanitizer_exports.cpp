// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cstdlib>
#include <iostream>
#include <string_view>
#include <vector>
#include "core/libraries/kernel/sanitizer.h"
#include "core/libraries/libs.h"

namespace {
struct Export {
    std::string_view nid;
    u64 address;
};
std::vector<Export> exports;
Core::Loader::SymbolsResolver resolver;

void Check(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}
} // namespace

// Capture only the registration sink and logger. Both exported functions and
// their registration (including HOST_CALL) are the production translation unit.
void LinkSymbolImpl(Core::Loader::SymbolsResolver* sym, const char* nid, const char* lib,
                    u16 version, const char* mod, u64 address, Core::Loader::SymbolType type) {
    Check(sym == &resolver, "Wrong resolver");
    Check(std::string_view(lib) == "libkernel" && std::string_view(mod) == "libkernel" &&
              version == 1 && type == Core::Loader::SymbolType::Function,
          "Wrong library, module, version or symbol type");
    Check(address != 0, "Export has no entry point");
    exports.push_back({nid, address});
}

namespace Common::Log {
std::array<Level, NUM_LOG_CLASSES> g_class_levels{};
void VLog(Class, Level, const char*, int, const char*, fmt::string_view, fmt::format_args) {}
} // namespace Common::Log

int main() {
    Libraries::Kernel::RegisterSanitizer(&resolver);
    Check(exports.size() == 2, "Missing or unexpected sanitizer exports");
    using Query = void*(PS4_SYSV_ABI*)();
    for (const auto nid : {"py6L8jiVAN8", "bnZxYgAFeA0"}) {
        int matches = 0;
        for (const auto& entry : exports) {
            if (entry.nid == nid) {
                ++matches;
                const auto query = reinterpret_cast<Query>(entry.address);
                Check(query() == nullptr, "Disabled guest sanitizer advertised a replacement");
            }
        }
        Check(matches == 1, "Missing or duplicate import NID");
    }
    std::cout << "PASS: both libkernel imports register once and return no guest replacement\n";
}
