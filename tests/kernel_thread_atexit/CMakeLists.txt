# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.22)
project(kernel_thread_atexit LANGUAGES CXX)
enable_testing()
find_package(Threads REQUIRED)
option(KERNEL_THREAD_ATEXIT_SANITIZERS "Enable host ASan/UBSan" OFF)
get_filename_component(REPO_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)

add_executable(kernel_thread_atexit_test
    "${REPO_ROOT}/src/core/libraries/kernel/thread_atexit.cpp"
    test_thread_atexit.cpp)
target_link_libraries(kernel_thread_atexit_test PRIVATE Threads::Threads)

# Compile the real loader adapter against the real Module/Linker declarations.
# The executable supplies a synthetic module-address lookup, so it can exercise
# the exports without loading a proprietary guest ELF or the whole renderer.
add_library(kernel_thread_atexit_adapter OBJECT
    "${REPO_ROOT}/src/core/linker_thread_atexit.cpp")
foreach(target kernel_thread_atexit_test kernel_thread_atexit_adapter)
    target_compile_features(${target} PRIVATE cxx_std_23)
    target_include_directories(${target} PRIVATE
        "${REPO_ROOT}/src"
        "${REPO_ROOT}/externals/fmt/include"
        "${REPO_ROOT}/externals/json/include")
    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        target_compile_options(${target} PRIVATE -Wall -Wextra -Werror)
        if(target STREQUAL "kernel_thread_atexit_adapter")
            # Existing Linker::GetModule compares an int handle with size_t.
            target_compile_options(${target} PRIVATE -Wno-sign-compare)
        endif()
        if(KERNEL_THREAD_ATEXIT_SANITIZERS)
            target_compile_options(${target} PRIVATE
                -fsanitize=address,undefined -fno-omit-frame-pointer)
            target_link_options(${target} PRIVATE -fsanitize=address,undefined)
        endif()
    endif()
endforeach()
add_test(NAME kernel_thread_atexit_test COMMAND kernel_thread_atexit_test)
