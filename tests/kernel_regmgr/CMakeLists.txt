# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.22)
project(kernel_regmgr_diagnostic LANGUAGES CXX)
enable_testing()
option(KERNEL_REGMGR_SANITIZERS "Enable host ASan/UBSan" OFF)
get_filename_component(REPO_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)

# Compile the exact syscall body with a synthetic guest mapping table. The
# production permission/range helper and log formatting are exercised below.
set(source "${REPO_ROOT}/src/core/libraries/kernel/kernel.cpp")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${source}")
file(READ "${source}" body)
string(FIND "${body}" "s32 PS4_SYSV_ABI __sys_regmgr_call(" begin)
string(FIND "${body}" "// Nominally: long sysconf" end)
if(begin LESS 0 OR end LESS begin)
    message(FATAL_ERROR "Cannot extract production registry syscall")
endif()
math(EXPR length "${end} - ${begin}")
string(SUBSTRING "${body}" ${begin} ${length} body)
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/regmgr.inc" "${body}")

add_executable(kernel_regmgr_test test_regmgr.cpp)
target_compile_features(kernel_regmgr_test PRIVATE cxx_std_23)
target_compile_definitions(kernel_regmgr_test PRIVATE FMT_HEADER_ONLY)
target_include_directories(kernel_regmgr_test PRIVATE "${CMAKE_CURRENT_BINARY_DIR}"
    "${REPO_ROOT}/src" "${REPO_ROOT}/externals/fmt/include")
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    # The existing result pointer is deliberately not accessed by this diagnostic.
    target_compile_options(kernel_regmgr_test PRIVATE -Wall -Wextra -Werror -Wno-unused-parameter)
    if(KERNEL_REGMGR_SANITIZERS)
        target_compile_options(kernel_regmgr_test PRIVATE
            -fsanitize=address,undefined -fno-omit-frame-pointer)
        target_link_options(kernel_regmgr_test PRIVATE -fsanitize=address,undefined)
    endif()
endif()
foreach(case valid unaligned split_mapping short oversized other_op null unmapped partial
        unreadable mapping_gap invalid_result)
    add_test(NAME kernel_regmgr_${case} COMMAND kernel_regmgr_test ${case})
endforeach()
