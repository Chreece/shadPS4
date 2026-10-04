# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.22)
project(startup_loading LANGUAGES CXX)
enable_testing()
find_package(Threads REQUIRED)
set(IMGUI_SOURCE_PATH "${CMAKE_CURRENT_LIST_DIR}/../../externals/imgui"
    CACHE PATH "Pinned Dear ImGui source")
set(SHADPS4_SOURCE "${CMAKE_CURRENT_LIST_DIR}/../../src")
add_executable(startup_loading_test test_startup_loading.cpp
    "${SHADPS4_SOURCE}/imgui/startup_loading.cpp"
    "${IMGUI_SOURCE_PATH}/imgui.cpp" "${IMGUI_SOURCE_PATH}/imgui_draw.cpp"
    "${IMGUI_SOURCE_PATH}/imgui_tables.cpp" "${IMGUI_SOURCE_PATH}/imgui_widgets.cpp")
target_compile_features(startup_loading_test PRIVATE cxx_std_20)
target_compile_definitions(startup_loading_test PRIVATE IMGUI_USER_CONFIG="imgui/imgui_config.h")
target_include_directories(startup_loading_test PRIVATE "${SHADPS4_SOURCE}" "${IMGUI_SOURCE_PATH}")
target_link_libraries(startup_loading_test PRIVATE Threads::Threads)
add_test(NAME startup_loading_lifecycle COMMAND startup_loading_test lifecycle)
add_test(NAME startup_loading_black_frames COMMAND startup_loading_test black_frames)
add_test(NAME startup_loading_ui COMMAND startup_loading_test ui)
