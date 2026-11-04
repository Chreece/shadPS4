// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <atomic>
#include <cstdlib>
#include <iostream>
#include <string_view>
#include <thread>
#include <vector>
#include "core/libraries/kernel/thread_atexit.h"
#include "core/libraries/libs.h"
#include "core/module_thread_atexit.h"

namespace {
using namespace Libraries::Kernel;

struct Export {
    std::string_view nid;
    u64 address;
};
Core::Loader::SymbolsResolver resolver;
std::vector<Export> exports;
Core::ModuleThreadAtexitRefs first_module;
Core::ModuleThreadAtexitRefs second_module;
constexpr std::array<u64, 2> guest_data{0x123456789abcdef0, 0xfedcba9876543210};
constexpr VAddr other_module_address = 0x200000000;
std::atomic<int> count_calls{};
std::atomic<int> report_calls{};
std::atomic<int> diagnostics{};

void Check(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}

u64 ExportAddress(std::string_view nid) {
    u64 address{};
    for (const auto& entry : exports) {
        if (entry.nid == nid) {
            Check(address == 0, "Duplicate export NID");
            address = entry.address;
        }
    }
    Check(address != 0, "Missing thread-atexit export");
    return address;
}

s32 PS4_SYSV_ABI CountCallback(s32 handle) {
    ++count_calls;
    // Exercise re-entry; callers use a snapshot rather than holding a host lock.
    _sceKernelSetThreadAtexitReport(nullptr);
    return handle + 17;
}

s32 PS4_SYSV_ABI OtherCountCallback(s32 handle) {
    return handle + 31;
}

void PS4_SYSV_ABI ReportCallback(s32 handle) {
    Check(handle == 7, "Report callback received the wrong module handle");
    ++report_calls;
}
} // namespace

// Only ELF lookup, export registration and logging are substituted. Reference
// state, callbacks, public entry points and HOST_CALL wrappers are production code.
namespace Core {
ModuleThreadAtexitRefs* FindModuleThreadAtexitRefs(VAddr address) {
    const auto base = reinterpret_cast<VAddr>(guest_data.data());
    if (address >= base && address - base < sizeof(guest_data)) {
        return &first_module;
    }
    return address == other_module_address ? &second_module : nullptr;
}
} // namespace Core

void LinkSymbolImpl(Core::Loader::SymbolsResolver* sym, const char* nid, const char* lib,
                    u16 version, const char* mod, u64 address, Core::Loader::SymbolType type) {
    Check(sym == &resolver && std::string_view(lib) == "libkernel" &&
              std::string_view(mod) == "libkernel" && version == 1 &&
              type == Core::Loader::SymbolType::Function,
          "Wrong thread-atexit export metadata");
    Check(address != 0, "Null entry point");
    exports.push_back({nid, address});
}

namespace Common::Log {
std::array<Level, NUM_LOG_CLASSES> g_class_levels{};
void VLog(Class, Level level, const char*, int, const char*, fmt::string_view, fmt::format_args) {
    if (level >= Level::Warning) {
        ++diagnostics;
    }
}
} // namespace Common::Log

int main() {
    using namespace Libraries::Kernel;
    RegisterThreadAtexit(&resolver);
    Check(exports.size() == 4, "Expected count/report/increment/decrement exports");
    using SetCount = s32 PS4_SYSV_ABI (*)(ThreadAtexitCount);
    using SetReport = s32 PS4_SYSV_ABI (*)(ThreadAtexitReport);
    using Increment = s32 PS4_SYSV_ABI (*)(VAddr);
    using Decrement = s32 PS4_SYSV_ABI (*)(VAddr);
    const auto set_count = reinterpret_cast<SetCount>(ExportAddress("pB-yGZ2nQ9o"));
    const auto set_report = reinterpret_cast<SetReport>(ExportAddress("WhCc1w3EhSI"));
    const auto increment = reinterpret_cast<Increment>(ExportAddress("Tz4RNUCBbGI"));
    const auto decrement = reinterpret_cast<Decrement>(ExportAddress("8OnWXlgQlvo"));

    Check(!GetThreadAtexitCallbacks().count && !GetThreadAtexitCallbacks().report,
          "Callbacks must initially be absent");
    Check(set_count(CountCallback) == 0 && set_report(ReportCallback) == 0,
          "Callback registration changed the compatibility return value");
    const auto snapshot = GetThreadAtexitCallbacks();
    Check(snapshot.count == CountCallback && snapshot.report == ReportCallback,
          "Setters discarded the callback pointers");
    Check(count_calls == 0 && report_calls == 0, "Registration executed a guest callback");
    Check(snapshot.count(7) == 24, "Count callback ABI changed the handle or result");
    snapshot.report(7);
    Check(count_calls == 1 && report_calls == 1 && !GetThreadAtexitCallbacks().report,
          "Snapshot or callback re-entry failed");
    set_count(nullptr);
    Check(!GetThreadAtexitCallbacks().count, "Null callback was not retained");

    const auto start = reinterpret_cast<VAddr>(guest_data.data());
    const auto last_byte = start + sizeof(guest_data) - 1;
    Check(increment(start) == 0 && increment(last_byte) == 0,
          "Valid module addresses were rejected");
    Check(first_module.Count() == 2, "Two module references were not recorded");
    Check(increment(other_module_address) == 0 && second_module.Count() == 1,
          "A second module did not get independent reference state");
    Check(guest_data[0] == 0x123456789abcdef0 && guest_data[1] == 0xfedcba9876543210,
          "The module address was used as a writable counter");

    const int before_invalid = diagnostics.load();
    for (auto address : {VAddr{0}, start - 1, start + sizeof(guest_data)}) {
        Check(increment(address) == -1, "Unknown module did not return -1");
        Check(decrement(address) == 0, "Unknown decrement changed the compatibility return");
    }
    Check(first_module.Count() == 2 && second_module.Count() == 1,
          "An invalid address altered live module references");
    Check(diagnostics == before_invalid + 6, "Invalid-address diagnostics disappeared");
    Check(decrement(last_byte) == 0 && decrement(start) == 0, "Valid decrement changed its return");
    const int before_underflow = diagnostics.load();
    decrement(start);
    Check(first_module.Count() == 0 && diagnostics == before_underflow + 1,
          "Unmatched decrement wrapped or lost its diagnostic");
    Check(second_module.Count() == 1, "Releasing one module affected another");
    decrement(other_module_address);

    constexpr int num_threads = 8;
    constexpr int repetitions = 4000;
    auto parallel = [&](auto task) {
        std::vector<std::thread> threads;
        for (int i = 0; i < num_threads; ++i) {
            threads.emplace_back(task);
        }
        for (auto& thread : threads) {
            thread.join();
        }
    };
    parallel([&] {
        for (int i = 0; i < repetitions; ++i) {
            Check(increment(start) == 0, "Concurrent increment failed");
        }
    });
    Check(first_module.Count() == num_threads * repetitions, "Concurrent increments were lost");
    parallel([&] {
        for (int i = 0; i < repetitions; ++i) {
            decrement(start);
        }
    });
    Check(first_module.Count() == 0, "Concurrent releases were lost");
    const int before_balanced = diagnostics.load();
    parallel([&] {
        for (int i = 0; i < repetitions; ++i) {
            Check(increment(start) == 0, "Mixed concurrent increment failed");
            decrement(start);
        }
    });
    Check(first_module.Count() == 0 && diagnostics == before_balanced,
          "Balanced concurrent use leaked references or reported underflow");

    parallel([&] {
        for (int i = 0; i < 100; ++i) {
            set_count(i % 2 ? CountCallback : OtherCountCallback);
            const auto current = GetThreadAtexitCallbacks();
            Check(current.count == CountCallback || current.count == OtherCountCallback,
                  "Concurrent registration corrupted a callback pointer");
        }
    });
    Check(count_calls == 1 && report_calls == 1, "Reference operations executed callbacks");
    set_count(nullptr);
    set_report(nullptr);
    std::cout << "PASS: four exports, callback ABI/storage/re-entry, module isolation, invalid "
                 "addresses, underflow and concurrent reference/registration operations\n";
}
