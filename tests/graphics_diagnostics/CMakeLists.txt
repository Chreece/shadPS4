# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.22)
project(graphics_diagnostics LANGUAGES CXX)
enable_testing()
add_executable(graphics_diagnostics_test test_graphics_diagnostics.cpp)
target_compile_features(graphics_diagnostics_test PRIVATE cxx_std_20)
target_include_directories(graphics_diagnostics_test PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src")
add_test(NAME graphics_trace_enabled COMMAND graphics_diagnostics_test enabled)
set_tests_properties(graphics_trace_enabled PROPERTIES ENVIRONMENT "SHADPS4_GRAPHICS_DIAGNOSTICS=1")
add_test(NAME graphics_trace_disabled COMMAND graphics_diagnostics_test disabled)
set_tests_properties(graphics_trace_disabled PROPERTIES ENVIRONMENT "SHADPS4_GRAPHICS_DIAGNOSTICS=0")
