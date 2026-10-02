# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.22)
project(texture_layout_regression LANGUAGES CXX)
enable_testing()
get_filename_component(DEFAULT_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(SOURCE_ROOT "${DEFAULT_ROOT}" CACHE PATH "Production source tree under test")
set(EXTERNALS_ROOT "${SOURCE_ROOT}/externals" CACHE PATH "Initialized dependency headers")
if(NOT CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang" OR NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "This standalone test uses Linux section garbage collection")
endif()
add_executable(texture_layout_test test_texture_layout.cpp
    "${SOURCE_ROOT}/src/video_core/texture_cache/image_info.cpp"
    "${SOURCE_ROOT}/src/video_core/amdgpu/tiling.cpp")
target_compile_features(texture_layout_test PRIVATE cxx_std_23)
target_compile_definitions(texture_layout_test PRIVATE FMT_HEADER_ONLY)
target_include_directories(texture_layout_test PRIVATE "${SOURCE_ROOT}/src"
    "${EXTERNALS_ROOT}/vulkan-headers/include" "${EXTERNALS_ROOT}/fmt/include"
    "${EXTERNALS_ROOT}/magic_enum/include" "${EXTERNALS_ROOT}/ext-boost")
target_compile_options(texture_layout_test PRIVATE -ffunction-sections -fdata-sections)
target_link_options(texture_layout_test PRIVATE -Wl,--gc-sections)
foreach(case IN ITEMS macro_mips block_mips thick_array xthick_mip prt_mapping)
    add_test(NAME texture_layout_${case} COMMAND texture_layout_test ${case})
endforeach()
