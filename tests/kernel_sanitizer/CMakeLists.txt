# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.22)
project(kernel_sanitizer_exports LANGUAGES CXX)
enable_testing()
get_filename_component(REPO_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
add_executable(kernel_sanitizer_exports_test
    "${REPO_ROOT}/src/core/libraries/kernel/sanitizer.cpp"
    test_sanitizer_exports.cpp)
target_compile_features(kernel_sanitizer_exports_test PRIVATE cxx_std_23)
target_include_directories(kernel_sanitizer_exports_test PRIVATE
    "${REPO_ROOT}/src"
    "${REPO_ROOT}/externals/fmt/include")
add_test(NAME kernel_sanitizer_exports_test COMMAND kernel_sanitizer_exports_test)
