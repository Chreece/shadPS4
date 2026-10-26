# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.20)
project(kernel_hle_stack_alignment LANGUAGES CXX ASM)
if(NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|amd64|AMD64)$")
    message(FATAL_ERROR "Guest x86-64 HLE stack alignment fixture requires x86-64")
endif()
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_executable(hle_stack_alignment_test test_stack_alignment.cpp guest_unaligned_call.S)
target_compile_options(hle_stack_alignment_test PRIVATE
    -O2 -fno-omit-frame-pointer -fno-optimize-sibling-calls)
enable_testing()
add_test(NAME generic_guest_hle_stack_alignment COMMAND hle_stack_alignment_test)
