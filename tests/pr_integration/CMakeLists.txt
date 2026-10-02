# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.22)
project(reviewed_pr_regressions LANGUAGES CXX)
enable_testing()
get_filename_component(REPO_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(BOOST_HEADERS_PATH "${REPO_ROOT}/externals/ext-boost" CACHE PATH "Pinned Boost headers")
set(MAGIC_ENUM_HEADERS_PATH "${REPO_ROOT}/externals/magic_enum/include" CACHE PATH "Pinned enum headers")
set(SHADER_CFG_SOURCE "${REPO_ROOT}/src/shader_recompiler/frontend/control_flow_graph.cpp"
    CACHE FILEPATH "Production CFG source; may point to the baseline for a negative control")
set(FMT_TEST OFF CACHE BOOL "" FORCE)
set(FMT_INSTALL OFF CACHE BOOL "" FORCE)
add_subdirectory("${REPO_ROOT}/externals/fmt" fmt)
# Reuse the test support shipped in the pinned fmt submodule; no additional download.
add_subdirectory("${REPO_ROOT}/externals/fmt/test/gtest" gtest)

add_executable(pr_integration_test
    test_integration.cpp
    "${REPO_ROOT}/tests/video_core/test_pm4_packet_assembler.cpp"
    "${REPO_ROOT}/src/video_core/amdgpu/pm4_packet_assembler.cpp"
    "${REPO_ROOT}/src/common/slot_vector.cpp"
    "${SHADER_CFG_SOURCE}"
    "${REPO_ROOT}/src/shader_recompiler/frontend/instruction.cpp")
target_compile_features(pr_integration_test PRIVATE cxx_std_23)
target_include_directories(pr_integration_test PRIVATE "${REPO_ROOT}/src" "${BOOST_HEADERS_PATH}"
    "${MAGIC_ENUM_HEADERS_PATH}")
target_link_libraries(pr_integration_test PRIVATE gtest fmt::fmt)
add_test(NAME reviewed_pr_regressions COMMAND pr_integration_test)

# Exercise the production normalization and mount lookup bodies with a minimal mount table.
set(FS_SOURCE "${REPO_ROOT}/src/core/file_sys/fs.cpp")
set(FS_HEADER "${REPO_ROOT}/src/core/file_sys/fs.h")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${FS_SOURCE}" "${FS_HEADER}")
file(READ "${FS_SOURCE}" FS_TEXT)
string(FIND "${FS_TEXT}" "std::optional<std::string> MntPoints::SanitizeGuestPath(" PATH_BEGIN)
string(FIND "${FS_TEXT}" "// Strip the mount prefix" PATH_END)
if(PATH_BEGIN LESS 0 OR PATH_END LESS PATH_BEGIN)
    message(FATAL_ERROR "Cannot locate production guest-path normalization")
endif()
math(EXPR PATH_LENGTH "${PATH_END} - ${PATH_BEGIN}")
string(SUBSTRING "${FS_TEXT}" ${PATH_BEGIN} ${PATH_LENGTH} PATH_BODY)
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/guest_path.inc" "${PATH_BODY}")
file(READ "${FS_HEADER}" FS_TEXT)
string(FIND "${FS_TEXT}" "    const MntPair* GetMount(std::string_view" MOUNT_BEGIN)
string(FIND "${FS_TEXT}" "\nprivate:" MOUNT_END)
if(MOUNT_BEGIN LESS 0 OR MOUNT_END LESS MOUNT_BEGIN)
    message(FATAL_ERROR "Cannot locate production mount lookup")
endif()
math(EXPR MOUNT_LENGTH "${MOUNT_END} - ${MOUNT_BEGIN}")
string(SUBSTRING "${FS_TEXT}" ${MOUNT_BEGIN} ${MOUNT_LENGTH} MOUNT_BODY)
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/mount_lookup.inc" "${MOUNT_BODY}")
target_include_directories(pr_integration_test PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
