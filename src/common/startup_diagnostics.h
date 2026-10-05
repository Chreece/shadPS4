// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <type_traits>
#include "common/thread.h"
#include "common/types.h"

namespace Common::StartupDiagnostics {

inline bool Enabled() {
    static const bool enabled = [] {
        const auto* value = std::getenv("SHADPS4_STARTUP_DIAGNOSTICS");
        return value && std::strcmp(value, "1") == 0;
    }();
    return enabled;
}

inline unsigned long long Milliseconds() {
    static const auto start = std::chrono::steady_clock::now();
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now() - start)
        .count();
}

inline bool Sample(unsigned long long count, unsigned long long limit = 32) {
    return count <= limit || (count & (count - 1)) == 0;
}

template <typename... Args>
void Emit(const char* event, const char* format, Args... args) {
    const auto saved_errno = errno;
    char fields[1536]{};
    std::snprintf(fields, sizeof(fields), format, args...);
    std::fprintf(stderr, "STARTUP_DIAG ms=%llu thread=[%s] event=%s %s\n", Milliseconds(),
                 GetCurrentThreadName().c_str(), event, fields);
    errno = saved_errno;
}

template <size_t N>
struct Name {
    char value[N];
    constexpr Name(const char (&text)[N]) {
        for (size_t i = 0; i < N; ++i) {
            value[i] = text[i];
        }
    }
};

template <typename T>
unsigned long long Value(T value) {
    if constexpr (std::is_pointer_v<T>) {
        return reinterpret_cast<uintptr_t>(value);
    } else if constexpr (std::is_integral_v<T> || std::is_enum_v<T>) {
        return static_cast<unsigned long long>(value);
    } else {
        return 0;
    }
}

template <auto Function, Name ApiName>
struct Trace;

template <class ReturnType, class... Args, PS4_SYSV_ABI ReturnType (*Function)(Args...), Name ApiName>
struct Trace<Function, ApiName> {
    static inline std::atomic<unsigned long long> calls{}, failures{};
    static inline auto volatile target = Function;

    static ReturnType PS4_SYSV_ABI wrap(Args... args) {
        const auto count = calls.fetch_add(1, std::memory_order_relaxed) + 1;
        const bool sampled = Sample(count);
        if (sampled) {
            std::array<unsigned long long, sizeof...(Args) + 6> values{Value(args)...};
            Emit("api-enter", "api=%s call=%llu args=%llx,%llx,%llx,%llx,%llx,%llx",
                 ApiName.value, count, values[0], values[1], values[2], values[3], values[4],
                 values[5]);
        }
        if constexpr (std::is_void_v<ReturnType>) {
            target(args...);
            if (sampled) {
                Emit("api-return", "api=%s call=%llu result=void", ApiName.value, count);
            }
        } else {
            auto result = target(args...);
            bool failed = false;
            if constexpr (std::is_signed_v<ReturnType> && std::is_integral_v<ReturnType>) {
                failed = result < 0;
            } else if constexpr (std::is_enum_v<ReturnType>) {
                failed = (Value(result) & 0x80000000ULL) != 0;
            }
            const auto failure_count =
                failed ? failures.fetch_add(1, std::memory_order_relaxed) + 1 : 0;
            if (sampled || (failed && Sample(failure_count, 128))) {
                Emit("api-return", "api=%s call=%llu result=%llx", ApiName.value, count,
                     Value(result));
            }
            return result;
        }
    }
};

} // namespace Common::StartupDiagnostics
