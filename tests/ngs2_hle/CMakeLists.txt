# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.22)
project(ngs2_hle_foundation LANGUAGES C CXX)
enable_testing()
find_package(Threads REQUIRED)
get_filename_component(REPO_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
option(NGS2_SANITIZERS "Enable address and undefined-behavior sanitizers" OFF)
include("${REPO_ROOT}/cmake/LibAtrac9.cmake")
shadps4_add_atrac9(ngs2_atrac9)
add_library(ngs2_hle_foundation STATIC
    "${REPO_ROOT}/src/core/libraries/ngs2/hle/waveform.cpp"
    "${REPO_ROOT}/src/core/libraries/ngs2/hle/decoder.cpp"
    "${REPO_ROOT}/src/core/libraries/ngs2/hle/playback.cpp"
    "${REPO_ROOT}/src/core/libraries/ngs2/hle/handles.cpp"
    "${REPO_ROOT}/src/core/libraries/ngs2/ngs2_impl.cpp")
target_include_directories(ngs2_hle_foundation PUBLIC "${REPO_ROOT}/src")
target_compile_features(ngs2_hle_foundation PUBLIC cxx_std_20)
target_link_libraries(ngs2_hle_foundation PUBLIC Threads::Threads ngs2_atrac9)
foreach(name waveform handles runtime memory_access decoder playback)
    add_executable(ngs2_${name}_test "test_${name}.cpp")
    target_link_libraries(ngs2_${name}_test PRIVATE ngs2_hle_foundation)
    add_test(NAME ngs2_${name}_test COMMAND ngs2_${name}_test)
endforeach()
foreach(target ngs2_hle_foundation ngs2_waveform_test ngs2_handles_test ngs2_runtime_test ngs2_memory_access_test ngs2_decoder_test ngs2_playback_test ngs2_atrac9)
    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        if(NOT target STREQUAL "ngs2_atrac9")
            target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic -Werror)
        endif()
        if(NGS2_SANITIZERS)
            target_compile_options(${target} PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
            target_link_options(${target} PRIVATE -fsanitize=address,undefined)
        endif()
    endif()
endforeach()
