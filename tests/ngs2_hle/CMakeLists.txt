# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.22)
project(ngs2_hle_foundation LANGUAGES CXX)
enable_testing()
find_package(Threads REQUIRED)
get_filename_component(REPO_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
option(NGS2_SANITIZERS "Enable address and undefined-behavior sanitizers" OFF)
add_library(ngs2_hle_foundation STATIC
    "${REPO_ROOT}/src/core/libraries/ngs2/hle/waveform.cpp"
    "${REPO_ROOT}/src/core/libraries/ngs2/hle/handles.cpp"
    "${REPO_ROOT}/src/core/libraries/ngs2/ngs2_impl.cpp")
target_include_directories(ngs2_hle_foundation PUBLIC "${REPO_ROOT}/src")
target_compile_features(ngs2_hle_foundation PUBLIC cxx_std_20)
target_link_libraries(ngs2_hle_foundation PUBLIC Threads::Threads)
foreach(name waveform handles runtime memory_access)
    add_executable(ngs2_${name}_test "test_${name}.cpp")
    target_link_libraries(ngs2_${name}_test PRIVATE ngs2_hle_foundation)
    add_test(NAME ngs2_${name}_test COMMAND ngs2_${name}_test)
endforeach()
foreach(target ngs2_hle_foundation ngs2_waveform_test ngs2_handles_test ngs2_runtime_test ngs2_memory_access_test)
    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic -Werror)
        if(NGS2_SANITIZERS)
            target_compile_options(${target} PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
            target_link_options(${target} PRIVATE -fsanitize=address,undefined)
        endif()
    endif()
endforeach()
