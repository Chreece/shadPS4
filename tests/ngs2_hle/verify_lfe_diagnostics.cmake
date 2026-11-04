# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
foreach(enabled 0 1)
    execute_process(COMMAND "${CMAKE_COMMAND}" -E env
        SHADPS4_NGS2_DIAGNOSTICS=0 SHADPS4_NGS2_LFE_DIAGNOSTICS=${enabled} "${AUDIO_TEST}"
        RESULT_VARIABLE result OUTPUT_VARIABLE output${enabled} ERROR_VARIABLE trace${enabled})
    if(NOT result EQUAL 0)
        message(FATAL_ERROR "LFE diagnostic fixture failed: ${result}\n${trace${enabled}}")
    endif()
endforeach()
if(NOT output0 STREQUAL output1 OR trace0 MATCHES "NGS2_LFE")
    message(FATAL_ERROR "LFE opt-in changed fixture results or leaked into normal operation")
endif()
foreach(pattern
    "route [^\n]*source-channels=1 destination-channels=8 matrix=0 volume=0.5 lfe-row=4,0,0,0,0,0,0,0 contribution=1"
    "voice [^\n]*kind=3000 [^\n]*gain=0.5 lfe-gain=0.25"
    "output [^\n]*channels=8 frames=256 [^\n]*lfe-nonzero=256"
    "matrix-staged [^\n]*source-channels=1 levels=8 values=1,2,3,4,5,6,7,8"
    "transaction-rejected [^\n]*command=1000000a"
    "filter-rejected [^\n]*type=20 location=0 mask=0 coefficients="
    "output [^\n]*final=1")
    if(NOT trace1 MATCHES "${pattern}")
        message(FATAL_ERROR "Missing LFE diagnostic: ${pattern}")
    endif()
endforeach()
message(STATUS "LFE routing, output windows, filter requests and opt-in isolation verified")
