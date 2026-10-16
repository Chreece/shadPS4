// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Standalone Linux test: no shadPS4 dependencies or DynamoRIO installation.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <thread>
#include <vector>

#include "core/cpu_id_trap_table.h"

int main() {
    Core::CpuId::RdpidTrapTable table;
    assert(table.Lookup(0x12340) == nullptr);
    assert(!table.Register(0, 4, 0));
    assert(!table.Register(0x12340, 3, 0));
    assert(!table.Register(0x12340, 16, 0));
    assert(!table.Register(0x12340, 4, 16));

    assert(table.Register(0x12340, 4, 0));
    assert(table.Register(0x12345, 5, 15));
    // A reloaded executable module must publish the most recent instruction.
    assert(table.Register(0x12340, 6, 9));
    const auto* first = table.Lookup(0x12340);
    assert(first != nullptr && first->address == 0x12340 &&
           first->size == 6 && first->destination == 9);
    const auto* second = table.Lookup(0x12345);
    assert(second != nullptr && second->size == 5 && second->destination == 15);
    assert(table.Lookup(0x12341) == nullptr);

    // Eight independent patching threads publish immutable site metadata
    // while another thread probes the table as a simulated signal reader.
    constexpr unsigned Threads = 8;
    constexpr unsigned PerThread = 1000;
    constexpr uintptr_t Base = 0x710000000000ULL;
    std::vector<std::thread> workers;
    for (unsigned tid = 0; tid < Threads; ++tid) {
        workers.emplace_back([&table, tid] {
            for (unsigned index = 0; index < PerThread; ++index) {
                const auto addr = Base + tid * 0x100000 + index * 16;
                assert(table.Register(addr, 4 + index % 2, tid));
            }
        });
    }
    std::thread reader([&table] {
        for (unsigned iteration = 0; iteration < 100000; ++iteration) {
            const auto tid = iteration % Threads;
            const auto index = iteration % PerThread;
            const auto addr = Base + tid * 0x100000 + index * 16;
            const auto* entry = table.Lookup(addr);
            if (entry) {
                assert(entry->address == addr);
                assert(entry->destination == tid);
            }
        }
    });
    for (auto& thread : workers) {
        thread.join();
    }
    reader.join();
    for (unsigned tid = 0; tid < Threads; ++tid) {
        for (unsigned index = 0; index < PerThread; ++index) {
            const auto addr = Base + tid * 0x100000 + index * 16;
            const auto* entry = table.Lookup(addr);
            assert(entry && entry->address == addr);
            assert(entry->size == 4 + index % 2);
            assert(entry->destination == tid);
        }
    }
    puts("BUILTIN_RDPID_TRAP_TABLE=PASS concurrency=8 sites=8002 reloaded_sites=1");
    return 0;
}
