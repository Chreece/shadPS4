# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
file(REMOVE "${TRIGGER}")
execute_process(COMMAND "${CMAKE_COMMAND}" -E env SHADPS4_NGS2_DIAGNOSTICS=1
    "SHADPS4_NGS2_DIAGNOSTICS_TRIGGER=${TRIGGER}" "${DIAGNOSTIC_TEST}" "${TRIGGER}"
    RESULT_VARIABLE result OUTPUT_VARIABLE output ERROR_VARIABLE trace)
file(REMOVE "${TRIGGER}")
if(NOT result EQUAL 0)
    message(FATAL_ERROR "Diagnostic capture failed: ${result}\n${output}\n${trace}")
endif()
string(REGEX MATCHALL "before-trigger number=" before "${trace}")
string(REGEX MATCHALL "after-trigger number=" after "${trace}")
string(REGEX MATCHALL "capture-rearmed window=2" markers "${trace}")
list(LENGTH before before_count)
list(LENGTH after after_count)
list(LENGTH markers marker_count)
if(NOT before_count EQUAL 1792 OR NOT after_count EQUAL 1792 OR
   NOT marker_count EQUAL 1 OR trace MATCHES "unexpected-third-window")
    message(FATAL_ERROR "Capture bounds failed: ${before_count}/${after_count}/${marker_count}")
endif()
