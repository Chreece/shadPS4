// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <string>
#include <fmt/format.h>
#include "common/logging/log.h"
#include "common/types.h"
#include "core/libraries/error_codes.h"
#include "core/memory_access.h"

namespace {
struct Area {
    uintptr_t base;
    u64 size;
    u32 prot;
    bool IsMapped() const {
        return true;
    }
};
std::map<uintptr_t, Area> areas;
std::string captured_log;
unsigned queries{};
unsigned logs{};

void Check(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}
} // namespace

namespace Core {
enum class MemoryProt : u32 { CpuRead = 1 };
struct Memory {
    static Memory* Instance() {
        static Memory instance;
        return &instance;
    }
    bool IsAccessibleRange(VAddr address, u64 size, MemoryProt required) {
        ++queries;
        return Detail::IsAccessibleRange(areas, address, size, static_cast<u32>(required));
    }
};
} // namespace Core

namespace Common::Log {
std::array<Level, NUM_LOG_CLASSES> g_class_levels{};
void VLog(Class, Level, const char*, int, const char*, fmt::string_view format,
          fmt::format_args args) {
    ++logs;
    captured_log = fmt::vformat(format, args);
}
} // namespace Common::Log

namespace Libraries::Kernel {
#include "regmgr.inc"
} // namespace Libraries::Kernel

int main(int argc, char** argv) {
    Check(argc == 2, "Expected a test case");
    const std::string which = argv[1];
    alignas(8) std::array<u8, 24> storage{};
    storage.fill(0xa5);
    auto* request = storage.data() + (which == "unaligned" ? 1 : 0);
    // Public RPCSX language-query example. This tests formatting and read bounds,
    // not a registry value implementation or a claim that our game uses this ID.
    constexpr u64 encoded_key = 0x12356328ecf5617b;
    constexpr u32 request_word8 = 0x12345678;
    std::memcpy(request, &encoded_key, sizeof(encoded_key));
    std::memcpy(request + 8, &request_word8, sizeof(request_word8));
    const auto original = storage;
    const auto address = reinterpret_cast<uintptr_t>(request);
    areas.emplace(address, Area{address, 16, 1});
    u32 status = 0xdeadbeef;
    void* result = &status;
    u32 op = 0x19;
    u64 len = 16;
    bool detailed = false;
    unsigned expected_queries = 1;

    if (which == "valid" || which == "unaligned") {
        detailed = true;
    } else if (which == "split_mapping") {
        areas.at(address).size = 8;
        areas.emplace(address + 8, Area{address + 8, 8, 1});
        detailed = true;
    } else if (which == "short") {
        len = 15;
        expected_queries = 0;
        request = reinterpret_cast<u8*>(1);
    } else if (which == "oversized") {
        len = 17;
        expected_queries = 0;
        request = reinterpret_cast<u8*>(1);
    } else if (which == "other_op") {
        op = 0x18;
        expected_queries = 0;
        request = reinterpret_cast<u8*>(1);
    } else if (which == "null") {
        request = nullptr;
    } else if (which == "unmapped") {
        request = reinterpret_cast<u8*>(1);
    } else if (which == "partial") {
        areas.at(address).size = 12;
    } else if (which == "unreadable") {
        areas.at(address).prot = 2;
    } else if (which == "mapping_gap") {
        areas.at(address).size = 8;
        areas.emplace(address + 9, Area{address + 9, 7, 1});
    } else if (which == "invalid_result") {
        result = reinterpret_cast<void*>(1);
        detailed = true;
    } else {
        Check(false, "Unknown test case");
    }

    Check(Libraries::Kernel::__sys_regmgr_call(op, 0, result, request, len) == ORBIS_OK,
          "Diagnostic changed the stub return value");
    Check(storage == original && status == 0xdeadbeef, "Diagnostic changed guest memory");
    Check(queries == expected_queries, "Unexpected buffer validation path");
    Check(logs == 1 && captured_log.find("(STUBBED)") != std::string::npos,
          "Diagnostic hid or duplicated the unsupported-call message");
    if (detailed) {
        Check(captured_log.find("encoded_key: 0x12356328ecf5617b") != std::string::npos &&
                  captured_log.find("request_word8: 0x12345678") != std::string::npos &&
                  captured_log.find("outputs unchanged") != std::string::npos,
              "Request input fields were not captured correctly");
    } else {
        Check(captured_log.find("encoded_key:") == std::string::npos,
              "Diagnostic inspected an unsupported request shape or inaccessible buffer");
    }
    std::cout << which << ": PASS\n";
}
