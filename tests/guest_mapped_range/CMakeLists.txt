# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.20)
project(shadps4_contiguous_guest_vma LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_executable(guest_mapped_range_test test_guest_mapped_range.cpp)
target_include_directories(guest_mapped_range_test PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/../../src)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(guest_mapped_range_test PRIVATE -O2 -Wall -Wextra -Werror)
endif()
enable_testing()
add_test(NAME generic_contiguous_guest_gpu_vma_range COMMAND guest_mapped_range_test)
