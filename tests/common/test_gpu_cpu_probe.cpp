// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Standalone native regression of opt-in cumulative CPU time telemetry.
// The same scoped timer is used by BufferCache, Rasterizer and PipelineCache.
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <thread>

#include "common/gpu_cpu_probe.h"

int main(int argc, char** argv) {
    if (argc != 2) return 2;
#ifdef _WIN32
    if (_putenv_s("SHADPS4_CPU_TIMING_CSV", argv[1]) != 0) return 3;
#else
    if (setenv("SHADPS4_CPU_TIMING_CSV", argv[1], 1) != 0) return 3;
#endif
    {
        Common::GpuCpuProbe::Scope measure{
            Common::GpuCpuProbe::Slot::BindBuffers};
        std::this_thread::sleep_for(std::chrono::milliseconds(26));
    }
    {
        Common::GpuCpuProbe::Scope measure{
            Common::GpuCpuProbe::Slot::ClampRangeSize};
        std::this_thread::sleep_for(std::chrono::milliseconds(7));
    }
    Common::GpuCpuProbe::Recorder::Instance().FlushSnapshot();

    std::ifstream f(argv[1]);
    std::string line;
    if (!std::getline(f, line) ||
        line != "steady_ns,region,calls,total_ns,max_ns,slow_5ms,slow_20ms") {
        return 4;
    }
    unsigned found = 0;
    while (std::getline(f, line)) {
        std::istringstream parts(line);
        std::string val;
        std::string region;
        std::getline(parts, val, ',');
        std::getline(parts, region, ',');
        std::getline(parts, val, ',');
        const auto calls = std::strtoull(val.c_str(), nullptr, 10);
        std::getline(parts, val, ',');
        const auto time_ns = std::strtoull(val.c_str(), nullptr, 10);
        if (region == "BindBuffers") {
            if (calls != 1 || time_ns < 20'000'000ULL) return 5;
            ++found;
        } else if (region == "ClampRangeSize") {
            if (calls != 1 || time_ns < 5'000'000ULL) return 6;
            ++found;
        }
    }
    if (found != 2) return 7;
    std::printf("GPU_CPU_TIMING_NATIVE=PASS paths=2\n");
    return 0;
}
