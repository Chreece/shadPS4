// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "common/assert.h"
#include "common/logging/log.h"
#include "core/libraries/error_codes.h"
#include "core/libraries/libs.h"
#include <cerrno>
#include <cstdint>
#include "mspace_allocator.h"
#include "libc_internal_memory.h"

namespace Libraries::LibcInternal {

void* PS4_SYSV_ABI internal_memset(void* s, int c, size_t n) {
    return std::memset(s, c, n);
}

void* PS4_SYSV_ABI internal_memcpy(void* dest, const void* src, size_t n) {
    return std::memcpy(dest, src, n);
}

s32 PS4_SYSV_ABI internal_memcpy_s(void* dest, size_t destsz, const void* src, size_t count) {
#ifdef _WIN64
    return memcpy_s(dest, destsz, src, count);
#else
    std::memcpy(dest, src, count);
    return 0; // ALL OK
#endif
}

s32 PS4_SYSV_ABI internal_memcmp(const void* s1, const void* s2, size_t n) {
    return std::memcmp(s1, s2, n);
}

void* PS4_SYSV_ABI internal_memmove(void* dest, const void* src, size_t n) {
    return std::memmove(dest, src, n);
}

static u64 g_mspace_atomic_id_mask = 0;
static u64 g_mstate_table[64] = {0};

struct HeapInfoInfo {
    u64 size = sizeof(HeapInfoInfo);
    u32 flag;
    u32 getSegmentInfo;
    u64* mspace_atomic_id_mask;
    u64* mstate_table;
};

void PS4_SYSV_ABI sceLibcHeapGetTraceInfo(HeapInfoInfo* info) {
    info->mspace_atomic_id_mask = &g_mspace_atomic_id_mask;
    info->mstate_table = g_mstate_table;
    info->getSegmentInfo = 0;
}

static Mspace::Registry g_mspace_registry;

// PS4 mspaces own a guest-mapped region. Creating a real bounded allocator is
// necessary: NP Toolkit treats the old null stub result as an initialization error.
void* PS4_SYSV_ABI internal_mspace_create(const char* name, void* base, u64 bytes, u32 flags) {
    void* mspace = g_mspace_registry.Create(base, bytes);
    if (mspace) {
        LOG_INFO(Lib_LibcInternal, "Created mspace {} base={} bytes={} flags={}",
                 name ? name : "<unnamed>", base, bytes, flags);
    } else {
        LOG_ERROR(Lib_LibcInternal, "Failed to create mspace base={} bytes={} flags={}",
                  base, bytes, flags);
    }
    return mspace;
}

s32 PS4_SYSV_ABI internal_mspace_destroy(void* mspace) {
    return g_mspace_registry.Destroy(mspace) ? 0 : -1;
}

void* PS4_SYSV_ABI internal_mspace_malloc(void* mspace, u64 bytes) {
    return g_mspace_registry.Malloc(mspace, bytes);
}

s32 PS4_SYSV_ABI internal_mspace_free(void* mspace, void* pointer) {
    if (!g_mspace_registry.Free(mspace, pointer)) {
        LOG_WARNING(Lib_LibcInternal, "mspace free invalid pointer {}", pointer);
        return -1;
    }
    return 0;
}

void* PS4_SYSV_ABI internal_mspace_calloc(void* mspace, u64 count, u64 bytes) {
    return g_mspace_registry.Calloc(mspace, count, bytes);
}

void* PS4_SYSV_ABI internal_mspace_memalign(void* mspace, u64 alignment, u64 bytes) {
    return g_mspace_registry.Malloc(mspace, bytes, alignment);
}

void* PS4_SYSV_ABI internal_mspace_realloc(void* mspace, void* pointer, u64 bytes) {
    return g_mspace_registry.Realloc(mspace, pointer, bytes);
}

void* PS4_SYSV_ABI internal_mspace_reallocalign(void* mspace, void* pointer,
                                                 u64 alignment, u64 bytes) {
    return g_mspace_registry.Realloc(mspace, pointer, bytes, alignment);
}

s32 PS4_SYSV_ABI internal_mspace_posix_memalign(void* mspace, void** out,
                                                u64 alignment, u64 bytes) {
    if (!out) {
        return EINVAL;
    }
    if (alignment < sizeof(void*) || (alignment & (alignment - 1)) != 0) {
        return EINVAL;
    }
    void* p = g_mspace_registry.Malloc(mspace, bytes, alignment);
    if (!p) {
        return ENOMEM;
    }
    *out = p;
    return 0;
}

struct MspaceManagedSize {
    std::uint16_t size;
    std::uint16_t version;
    std::uint32_t reserved;
    std::uint64_t max_system_size;
    std::uint64_t current_system_size;
    std::uint64_t max_inuse_size;
    std::uint64_t current_inuse_size;
};
static_assert(sizeof(MspaceManagedSize) == 40);

s32 PS4_SYSV_ABI internal_mspace_stats(void* mspace, MspaceManagedSize* result) {
    if (!result || result->size != 40 || result->version != 1) {
        return 1;
    }
    const auto stats = g_mspace_registry.GetStats(mspace);
    if (!stats) {
        return 1;
    }
    result->reserved = 0;
    result->max_system_size = stats->max_system_size;
    result->current_system_size = stats->current_system_size;
    result->max_inuse_size = stats->max_inuse_size;
    result->current_inuse_size = stats->current_inuse_size;
    return 0;
}

u64 PS4_SYSV_ABI internal_mspace_malloc_usable_size(void* pointer) {
    return g_mspace_registry.UsableSize(pointer);
}

s32 PS4_SYSV_ABI internal_mspace_is_heap_empty(void* mspace) {
    return g_mspace_registry.IsHeapEmpty(mspace) ? 1 : 0;
}

void RegisterlibSceLibcInternalMemory(Core::Loader::SymbolsResolver* sym) {

    LIB_FUNCTION("-hn1tcVHq5Q", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_create);
    LIB_FUNCTION("W6SiVSiCDtI", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_destroy);
    LIB_FUNCTION("OJjm-QOIHlI", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_malloc);
    LIB_FUNCTION("Vla-Z+eXlxo", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_free);
    LIB_FUNCTION("LYo3GhIlB38", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_calloc);
    LIB_FUNCTION("iF1iQHzxBJU", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_memalign);
    LIB_FUNCTION("gigoVHZvVPE", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_realloc);
    LIB_FUNCTION("p6lrRW8-MLY", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_reallocalign);
    LIB_FUNCTION("qWESlyXMI3E", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_posix_memalign);
    LIB_FUNCTION("mfHdJTIvhuo", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_stats);
    LIB_FUNCTION("k04jLXu3+Ic", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_stats);
    LIB_FUNCTION("fEoW6BJsPt4", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_malloc_usable_size);
    LIB_FUNCTION("pzUa7KEoydw", "libSceLibcInternal", 1, "libSceLibcInternal", internal_mspace_is_heap_empty);



    LIB_FUNCTION("NFLs+dRJGNg", "libSceLibcInternal", 1, "libSceLibcInternal", internal_memcpy_s);
    LIB_FUNCTION("Q3VBxCXhUHs", "libSceLibcInternal", 1, "libSceLibcInternal", internal_memcpy);
    LIB_FUNCTION("+P6FRGH4LfA", "libSceLibcInternal", 1, "libSceLibcInternal", internal_memmove);
    LIB_FUNCTION("8zTFvBIAIN8", "libSceLibcInternal", 1, "libSceLibcInternal", internal_memset);
    LIB_FUNCTION("DfivPArhucg", "libSceLibcInternal", 1, "libSceLibcInternal", internal_memcmp);

    LIB_FUNCTION("NWtTN10cJzE", "libSceLibcInternalExt", 1, "libSceLibcInternal",
                 sceLibcHeapGetTraceInfo);
}

} // namespace Libraries::LibcInternal
