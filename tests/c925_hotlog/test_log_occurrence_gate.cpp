// Production log-occurrence gate tested in Release and across GPU/guest threads.
#include "common/log_occurrence_gate.h"
#include <atomic>
#include <array>
#include <cstdint>
#include <thread>
#include <vector>
#include <cstdio>
#include <cstdlib>

int main() {
    std::atomic<std::uint64_t> sequence{};
    for (std::uint64_t i = 1; i <= 500000; ++i) {
        std::uint64_t event{};
        const bool log = Common::ShouldReportRepeatedEvent(sequence, event);
        if (event != i || log != (i<=4 || (i & (i-1)) == 0)) return 11;
    }
    if (sequence.load() != 500000) return 12;
    std::atomic<std::uint64_t> shared{};
    std::atomic<std::uint64_t> logged{};
    std::array<std::thread, 8> workers;
    for (auto& worker:workers) worker=std::thread([&]{
       for (int i=0;i<20000;++i) {
          std::uint64_t number{};
          if (Common::ShouldReportRepeatedEvent(shared, number)) logged.fetch_add(1);
       }
    });
    for (auto& worker:workers) worker.join();
    if (shared.load()!=160000) return 13;
    std::uint64_t expect=4; // 1,2,3,4
    for (std::uint64_t n=8; n<=160000; n*=2) expect++;
    if (logged.load()!=expect) return 14;
    std::puts("PRODUCTION_LOG_OCCURRENCE_GATE_TEST=PASS");
}
