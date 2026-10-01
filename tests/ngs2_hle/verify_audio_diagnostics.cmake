# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
execute_process(COMMAND "${CMAKE_COMMAND}" -E env SHADPS4_NGS2_DIAGNOSTICS=1 "${AUDIO_TEST}"
    RESULT_VARIABLE result OUTPUT_VARIABLE output ERROR_VARIABLE trace)
if(NOT result EQUAL 0)
    message(FATAL_ERROR "Audio diagnostic test failed: ${result}\n${output}\n${trace}")
endif()
foreach(pattern
    "blocks-request [^\n]*flags=8 count=1 null-data=1 readable=1 [^\n]*offset=37 bytes=768 skip=9 samples=384"
    "blocks-request [^\n]*flags=8 [^\n]*readable=0"
    "block-rejected [^\n]*reason=flags [^\n]*block=0"
    "block-rejected [^\n]*reason=decoder result=804a0408 flags=4 count=6 block=1 index=5 [^\n]*bytes=576 skip=1 samples=1536 [^\n]*window-error=8 decoder-error=6 unit-bytes=192 unit-samples=512 capacity=1536"
    "block-rejected [^\n]*reason=decoder [^\n]*index=5 [^\n]*bytes=575 skip=0 samples=100 [^\n]*window-error=6 [^\n]*capacity=1024"
    "block-rejected [^\n]*reason=reserved [^\n]*index=5 [^\n]*reserved=42"
    "block-rejected [^\n]*reason=empty-repeat [^\n]*index=5 [^\n]*samples=0 repeats=1"
    "state-query [^\n]*query=flags [^\n]*flags=3 queued=1 samples=256"
    "state-query [^\n]*flags=20 queued=0 samples=768 completed-bytes=1536"
    "stream-block [^\n]*flags=3 bytes=192 requested=4294967295 resolved=512 continuation=1 open=1"
    "voice-commit [^\n]*event=4 reset=0 flags=5"
    "block-ended [^\n]*remaining=0")
    if(NOT trace MATCHES "${pattern}")
        message(FATAL_ERROR "Missing diagnostic: ${pattern}\n${trace}")
    endif()
endforeach()
message(STATUS "Bounded block/event/state diagnostics verified")
