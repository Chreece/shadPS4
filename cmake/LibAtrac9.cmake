# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

function(shadps4_add_atrac9 target)
    set(codec_dir "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../externals/LibAtrac9/C/src")
    file(GLOB codec_sources "${codec_dir}/*.c")
    if(NOT codec_sources)
        message(FATAL_ERROR "Initialize the pinned codec: git submodule update --init externals/LibAtrac9")
    endif()

    # The pinned codec has two undefined shifts in utility.c: reversing zero bits
    # during table initialization, and signed left shifts in sign extension.
    # Build a corrected copy, preserving the submodule checkout and all sanitizer
    # coverage. Fail explicitly if an upstream update changes these patch sites.
    file(READ "${codec_dir}/utility.c" utility)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${codec_dir}/utility.c")
    string(REPLACE "\r\n" "\n" utility "${utility}")
    set(reverse_before "return value >> (32 - bitCount);")
    set(reverse_after "return bitCount == 0 ? 0 : value >> (32 - bitCount);")
    set(extend_before "const int shift = 8 * sizeof(int) - bits;\n\treturn (value << shift) >> shift;")
    set(extend_after "const unsigned int sign = 1u << (bits - 1);\n\tconst unsigned int low = (unsigned int)value & (sign - 1);\n\treturn (value & sign) ? -1 - (int)((sign - 1) - low) : (int)low;")
    foreach(site reverse extend)
        string(FIND "${utility}" "${${site}_before}" match)
        if(match EQUAL -1)
            message(FATAL_ERROR "LibAtrac9 utility.c changed; review the ${site} shift correction")
        endif()
        string(REPLACE "${${site}_before}" "${${site}_after}" utility "${utility}")
    endforeach()
    set(patched_utility "${CMAKE_CURRENT_BINARY_DIR}/${target}-utility.c")
    file(GENERATE OUTPUT "${patched_utility}" CONTENT "${utility}")
    list(REMOVE_ITEM codec_sources "${codec_dir}/utility.c")
    add_library(${target} STATIC ${codec_sources} "${patched_utility}")
    target_include_directories(${target} PUBLIC "${codec_dir}")
endfunction()
