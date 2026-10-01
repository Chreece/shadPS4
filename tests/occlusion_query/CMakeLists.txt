# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.22)
project(occlusion_counter_regression LANGUAGES CXX)
enable_testing()
add_executable(occlusion_counter_test test_occlusion_counter.cpp)
target_compile_features(occlusion_counter_test PRIVATE cxx_std_20)
target_include_directories(occlusion_counter_test PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src")
add_test(NAME occlusion_counter_layout COMMAND occlusion_counter_test)
