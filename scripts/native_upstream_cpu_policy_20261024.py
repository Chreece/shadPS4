#!/usr/bin/env python3
"""Remove external DynamoRIO CPU translation from the general shadPS4 build.

Preserves the emulator-owned Linux CPUID/TSC fault handler, native instruction
patcher, shared guest CPU identity, and upstream executable architecture.
No title IDs, shader hashes, per-game switches, or guest execution bypasses.
"""
from __future__ import annotations
import re

SOURCE_PATHS=(
    'CMakeLists.txt',
    'src/main.cpp',
    'src/core/cpu_id.cpp',
)
REJECTED_TOKENS=(
    'ENABLE_CPU_ID_TRANSLATION',
    'ShadCpuIdTranslation',
    'StartCpuIdTranslation',
    'cpu_id_translation',
    'CpuIdTranslation.cmake',
    'DynamoRIO',
    '--cpu-id-mode',
    'SHADPS4_CPU_ID_MODE',
)
REJECTED_SOURCE_DIR='src/core/cpu_id_translation/'
REJECTED_CMAKE='cmake/CpuIdTranslation.cmake'

def removed_paths(tracked_paths):
    paths=sorted(p for p in tracked_paths
                 if p==REJECTED_CMAKE or p.startswith(REJECTED_SOURCE_DIR))
    if REJECTED_CMAKE not in paths:
        raise RuntimeError('CPU_TRANSLATION_CMAKE_SOURCE_NOT_PRESENT')
    if not any(p.endswith('/launcher.cpp') for p in paths):
        raise RuntimeError('CPU_TRANSLATION_LAUNCHER_SOURCE_NOT_PRESENT')
    if not any(p.endswith('/client.c') for p in paths):
        raise RuntimeError('CPU_TRANSLATION_DYNAMORIO_CLIENT_SOURCE_NOT_PRESENT')
    return paths

def transform_cmake(t):
    option=(
        r'(?m)^cmake_dependent_option\(ENABLE_CPU_ID_TRANSLATION[^\n]*\n'
        r'[^\n]*\)\n(?:\n)?'
    )
    t,count=re.subn(option,'',t)
    if count!=1:raise RuntimeError('CPU_TRANSLATION_CMAKE_OPTION_CHANGED='+str(count))
    opening='if (ENABLE_CPU_ID_TRANSLATION)\n'
    closing='    include(cmake/CpuIdTranslation.cmake)\nendif()\n'
    if t.count(opening)!=1 or t.count(closing)!=1:
        raise RuntimeError('CPU_TRANSLATION_CMAKE_TARGET_CHANGED')
    begin=t.index(opening)
    end=t.index(closing,begin)+len(closing)
    block=t[begin:end]
    for token in ('target_compile_definitions(shadps4 PRIVATE ENABLE_CPU_ID_TRANSLATION)',
                  'target_sources(shadps4 PRIVATE src/core/cpu_id_translation/launcher.cpp)',
                  'ShadCpuIdTranslationActive','ShadCpuIdTranslationRange'):
        if token not in block:
            raise RuntimeError('CPU_TRANSLATION_CMAKE_BLOCK_DRIFT='+token)
    t=t[:begin]+t[end:]
    if 'list(APPEND CORE src/core/cpu_id.cpp src/core/cpu_id.h)' not in t:
        raise RuntimeError('NATIVE_CPU_ID_SOURCE_REGISTRATION_REMOVED')
    return t

def _remove_preprocessor_blocks(t,expected,label):
    # Anchored at column 0. The CPU-ID conditional blocks in these two files
    # are simple (non-nested) ENABLE_CPU_ID_TRANSLATION sections.
    pattern=r'(?m)^#ifdef ENABLE_CPU_ID_TRANSLATION\n(?:(?!^#endif$).*\n)*^#endif\n'
    t,n=re.subn(pattern,'',t)
    if n!=expected:
        raise RuntimeError(label+'_CONDITIONAL_COUNT='+str(n))
    return t

def transform_main(t):
    t=_remove_preprocessor_blocks(t,4,'MAIN_EXTERNAL_TRANSLATOR')
    for marker in (
        'app.add_option("-g,--game", gamePath',
        'app.add_option("-f,--fullscreen", fullscreenStr',
        'app.add_flag("--show-fps", showFps)',
        'emulator->Run(ebootPath, gameArgs, overrideRoot, mounts, env_vars, append_log)',
    ):
        if marker not in t:
            raise RuntimeError('NATIVE_UPSTREAM_MAIN_CONTRACT_MISSING='+marker)
    return t

def transform_cpu(t):
    t=_remove_preprocessor_blocks(t,3,'CPU_EXTERNAL_TRANSLATOR')
    for marker in ('void InitializeCpuId()', 'void EnableCpuIdFaulting()',
                   'void SetCpuIdGuestAddressRange(', 'HandleCpuIdFault(',
                   'GuestCpuid(', 'RestoreHostNativeTsc()',
                   'PR_SET_TSC', 'ARCH_SET_CPUID'):
        if marker not in t:
            raise RuntimeError('NATIVE_CPU_ID_CONTRACT_MISSING='+marker)
    return t

def transform_source(path,text):
    if path=='CMakeLists.txt':
        final=transform_cmake(text)
    elif path=='src/main.cpp':
        final=transform_main(text)
    elif path=='src/core/cpu_id.cpp':
        final=transform_cpu(text)
    else:raise RuntimeError('UNREVIEWED_NATIVE_TRANSFORM_PATH='+path)
    for token in REJECTED_TOKENS:
        if token in final:
            raise RuntimeError('EXTERNAL_CPU_TRANSLATION_RETAINED='+path+':'+token)
    return final

def assert_native_tree(read_source,exists):
    for path in SOURCE_PATHS:
        text=read_source(path)
        for token in REJECTED_TOKENS:
            if token in text:
                raise RuntimeError('DYNAMORIO_REMAINS_IN_BUILD_SOURCE='+path+':'+token)
    if exists(REJECTED_CMAKE):
        raise RuntimeError('CPU_TRANSLATION_CMAKE_STILL_TRACKED')
    if any(exists(REJECTED_SOURCE_DIR+suffix) for suffix in (
            'launcher.cpp','launcher.h','client.c','CMakeLists.txt','build.py',
            'package.cmake')):
        raise RuntimeError('CPU_TRANSLATION_SOURCE_STILL_TRACKED')
    if 'ENABLE_CPU_ID_TRANSLATION' in read_source('CMakeLists.txt'):
        raise RuntimeError('CPU_TRANSLATION_REENABLED_IN_CMAKE')
    return True

if __name__=='__main__':
    import sys
    if sys.argv[1:]==['--self-test']:
        cmake='''cmake_dependent_option(ENABLE_CPU_ID_TRANSLATION "Bundle Linux CPU identity translation" ON
    "CMAKE_SYSTEM_NAME STREQUAL Linux;ARCHITECTURE STREQUAL x86_64" OFF)
list(APPEND CORE src/core/cpu_id.cpp src/core/cpu_id.h)
if (ENABLE_CPU_ID_TRANSLATION)
    target_compile_definitions(shadps4 PRIVATE ENABLE_CPU_ID_TRANSLATION)
    target_sources(shadps4 PRIVATE src/core/cpu_id_translation/launcher.cpp)
    target_link_options(shadps4 PRIVATE
        "-Wl,--export-dynamic-symbol=ShadCpuIdTranslationActive"
        "-Wl,--export-dynamic-symbol=ShadCpuIdTranslationRange")
    include(cmake/CpuIdTranslation.cmake)
endif()
'''
        m='''#ifdef ENABLE_CPU_ID_TRANSLATION
#include "core/cpu_id_translation/launcher.h"
#endif
#ifdef ENABLE_CPU_ID_TRANSLATION
std::string cpuIdMode = "auto";
#endif
#ifdef ENABLE_CPU_ID_TRANSLATION
--cpu-id-mode
#endif
#ifdef ENABLE_CPU_ID_TRANSLATION
StartCpuIdTranslation
#endif
app.add_option("-g,--game", gamePath
app.add_option("-f,--fullscreen", fullscreenStr
app.add_flag("--show-fps", showFps)
emulator->Run(ebootPath, gameArgs, overrideRoot, mounts, env_vars, append_log)
'''
        c='''#ifdef ENABLE_CPU_ID_TRANSLATION
ShadCpuIdTranslationActive()
#endif
#ifdef ENABLE_CPU_ID_TRANSLATION
ShadCpuIdTranslationActive()
#endif
#ifdef ENABLE_CPU_ID_TRANSLATION
ShadCpuIdTranslationRange()
#endif
void InitializeCpuId() {}
void EnableCpuIdFaulting() {}
void SetCpuIdGuestAddressRange(uintptr_t begin, uintptr_t end) {}
void HandleCpuIdFault() {}
void GuestCpuid() {}
void RestoreHostNativeTsc() {}
PR_SET_TSC ARCH_SET_CPUID
'''
        assert 'ENABLE_CPU_ID_TRANSLATION' not in transform_source('CMakeLists.txt',cmake)
        assert '--cpu-id-mode' not in transform_source('src/main.cpp',m)
        assert 'ShadCpuIdTranslation' not in transform_source('src/core/cpu_id.cpp',c)
        print('UPSTREAM_NATIVE_CPU_POLICY_TEST=PASS')
    else:raise SystemExit('Usage: --self-test')
