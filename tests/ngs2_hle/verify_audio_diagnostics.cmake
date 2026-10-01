# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
execute_process(COMMAND "${CMAKE_COMMAND}" -E env SHADPS4_NGS2_DIAGNOSTICS=1 "${AUDIO_TEST}"
    RESULT_VARIABLE result OUTPUT_VARIABLE output ERROR_VARIABLE trace)
if(NOT result EQUAL 0)
    message(FATAL_ERROR "Audio diagnostic test failed: ${result}\n${output}\n${trace}")
endif()
foreach(pattern
    "blocks-request [^\n]*flags=1 count=1 null-data=1 readable=1 [^\n]*offset=37 bytes=768 skip=9 samples=384"
    "blocks-request [^\n]*flags=1 [^\n]*readable=0"
    "state-query [^\n]*query=flags [^\n]*flags=3 queued=1 samples=256"
    "state-query [^\n]*flags=20 queued=0 samples=768 completed-bytes=1536"
    "voice-commit [^\n]*event=4 reset=0 flags=5"
    "block-ended [^\n]*remaining=0")
    if(NOT trace MATCHES "${pattern}")
        message(FATAL_ERROR "Missing diagnostic: ${pattern}\n${trace}")
    endif()
endforeach()
message(STATUS "Bounded block/event/state diagnostics verified")
