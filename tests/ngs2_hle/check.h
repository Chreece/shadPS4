// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace Test {
using Function = void (*)();
inline std::vector<std::pair<const char *, Function>> &Cases() {
    static std::vector<std::pair<const char *, Function>> cases;
    return cases;
}
struct Register {
    Register(const char *name, Function function) {
        Cases().emplace_back(name, function);
    }
};
inline void Check(bool ok, const char *expression, int line) {
    if (!ok)
        throw std::runtime_error(std::string(expression) + " at line " + std::to_string(line));
}
inline int Run() {
    int failures = 0;
    for (const auto &[name, function] : Cases()) {
        try {
            function();
            std::cout << "PASS " << name << '\n';
        } catch (const std::exception &e) {
            ++failures;
            std::cerr << "FAIL " << name << ": " << e.what() << '\n';
        }
    }
    std::cout << "NGS2_FOUNDATION_TESTS=" << (failures ? "FAIL" : "PASS")
              << " cases=" << Cases().size() << " failed=" << failures << '\n';
    return failures ? 1 : 0;
}
} // namespace Test
#define CHECK(expr) Test::Check(static_cast<bool>(expr), #expr, __LINE__)
#define TEST(name)                                                                                 \
    static void name();                                                                            \
    static Test::Register reg_##name{#name, &name};                                                \
    static void name()
