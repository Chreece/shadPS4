#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ghost reversible sparse-buffer maxBufferSize Vulkan validation trial.

CUSA11456 observed VUID-VkBufferCreateInfo-size-06409:
  VkBufferCreateInfo.size = 4294967296
  VkPhysicalDeviceMaintenance4Properties.maxBufferSize = 4294967292

The current sparse buffer cache uses 2^32 byte arena pages. In this isolated
trial, use 2^30-byte pages, query the actual host limit, and fail *before*
vkCreateBuffer if a requested/merged sparse arena is still too large.
This experimental split is NOT a complete bridging implementation: large or
straddling ranges can legitimately hit the guards, which is useful evidence.
Do NOT upstream this tentative policy without real cross-boundary coverage.
"""
from __future__ import annotations
from pathlib import Path
import sys

REL_H="src/video_core/buffer_cache/buffer_cache.h"
REL_C="src/video_core/buffer_cache/buffer_cache.cpp"
HEADER_ANCHOR=(
    "    static constexpr u64 ADDRESS_SPACE_BITS = 40;\n"
    "    static constexpr u64 ARENA_PAGE_BITS = 32;\n"
    "    static constexpr u64 ARENA_PAGE_SIZE = u64{1} << ARENA_PAGE_BITS;"
)
HEADER_REPL=(
    "    static constexpr u64 ADDRESS_SPACE_BITS = 40;\n"
    "    // GHOST_ARENA_1G_TRIAL: RADV maxBufferSize is 4GiB minus four bytes.\n"
    "    // Experimental 1GiB arenas keep isolated and 2-page merges below that\n"
    "    // limit. More extensive arena merges are rejected with an explicit\n"
    "    // assertion rather than submitting an invalid vkCreateBuffer request.\n"
    "    static constexpr u64 ARENA_PAGE_BITS = 30;\n"
    "    static constexpr u64 ARENA_PAGE_SIZE = u64{1} << ARENA_PAGE_BITS;"
)
MEMBER_ANCHOR=(
    "    u32 arena_memory_type_index{};\n"
    "    u32 block_size{};"
)
MEMBER_REPL=(
    "    u64 max_sparse_buffer_size{};\n"
    "    u32 arena_memory_type_index{};\n"
    "    u32 block_size{};"
)
INIT_ANCHOR=(
    "    const auto device = instance.GetDevice();\n"
    "    const auto reqs = device.getBufferMemoryRequirements(req_info).memoryRequirements;"
)
INIT_REPL=(
    "    const auto device = instance.GetDevice();\n"
    "    const auto max_buffer_props = instance.GetPhysicalDevice().getProperties2<\n"
    "        vk::PhysicalDeviceProperties2, vk::PhysicalDeviceVulkan13Properties>();\n"
    "    max_sparse_buffer_size =\n"
    "        max_buffer_props.get<vk::PhysicalDeviceVulkan13Properties>().maxBufferSize;\n"
    "    LOG_INFO(Render, \"GHOST_ARENA_1G_CONFIG arena_page={} max_buffer_size={}\",\n"
    "             ARENA_PAGE_SIZE, max_sparse_buffer_size);\n"
    "    ASSERT_MSG(ARENA_PAGE_SIZE <= max_sparse_buffer_size,\n"
    '               "GHOST_ARENA_1G host maxBufferSize={} smaller than arena page={}",\n'
    "               max_sparse_buffer_size, ARENA_PAGE_SIZE);\n"
    "    const auto reqs = device.getBufferMemoryRequirements(req_info).memoryRequirements;"
)
NEW_ANCHOR=(
    "            const u64 num_pages = last_page - first_page + 1;\n"
    "            const auto* new_arena =\n"
    "                &arenas.emplace_back(instance, base_block << block_shift,\n"
    "                                     num_pages << ARENA_PAGE_BITS, MemoryType::Sparse);"
)
NEW_REPL=(
    "            const u64 num_pages = last_page - first_page + 1;\n"
    "            const u64 arena_size = num_pages << ARENA_PAGE_BITS;\n"
    "            ASSERT_MSG(arena_size <= max_sparse_buffer_size,\n"
    '                       "GHOST_ARENA_1G oversized initial sparse buffer={} max={}",\n'
    "                       arena_size, max_sparse_buffer_size);\n"
    "            const auto* new_arena =\n"
    "                &arenas.emplace_back(instance, base_block << block_shift,\n"
    "                                     arena_size, MemoryType::Sparse);"
)
MERGE_ANCHOR=(
    "    const u64 total_size = first_size + last_size;\n"
    "    const u64 end_block = (first_addr + total_size) >> block_shift;"
)
MERGE_REPL=(
    "    const u64 total_size = first_size + last_size;\n"
    "    ASSERT_MSG(total_size <= max_sparse_buffer_size,\n"
    '               "GHOST_ARENA_1G sparse arena merge={} exceeds Vulkan maxBufferSize={} "\n'
    '               "first_page={} last_page={}",\n'
    "               total_size, max_sparse_buffer_size, first_page, last_page);\n"
    "    const u64 end_block = (first_addr + total_size) >> block_shift;"
)

def modified(h: bytes,c: bytes)->tuple[bytes,bytes]:
    ht=h.decode("utf-8")
    ct=c.decode("utf-8")
    for label, src, needle in (
        ("arena page bits",ht,HEADER_ANCHOR),
        ("header state",ht,MEMBER_ANCHOR),
        ("device properties",ct,INIT_ANCHOR),
        ("sparse initial allocation",ct,NEW_ANCHOR),
        ("sparse arena merge",ct,MERGE_ANCHOR),
    ):
        count=src.count(needle)
        if count!=1:
            raise ValueError(f"Unexpected {label} code anchor count={count}")
    if "GHOST_ARENA_1G_TRIAL" in ht or "GHOST_ARENA_1G_CONFIG" in ct:
        raise ValueError("Arena patch already present")
    result_h=ht.replace(HEADER_ANCHOR,HEADER_REPL,1).replace(MEMBER_ANCHOR,MEMBER_REPL,1)
    result_c=(ct.replace(INIT_ANCHOR,INIT_REPL,1)
                .replace(NEW_ANCHOR,NEW_REPL,1)
                .replace(MERGE_ANCHOR,MERGE_REPL,1))
    assert result_h.count("static constexpr u64 ARENA_PAGE_BITS = 30;")==1
    assert result_c.count("GHOST_ARENA_1G_CONFIG arena_page=")==1
    assert result_c.count("oversized initial sparse buffer=")==1
    assert result_c.count("sparse arena merge=")==1
    return result_h.encode(),result_c.encode()

def selftest():
    h=(HEADER_ANCHOR+"\n...\n"+MEMBER_ANCHOR).encode()
    c=(INIT_ANCHOR+"\n...\n"+NEW_ANCHOR+"\n...\n"+MERGE_ANCHOR).encode()
    ah,ac=modified(h,c)
    assert b"ARENA_PAGE_BITS = 30" in ah
    assert b"max_sparse_buffer_size" in ah
    assert b"max_buffer_props.get<vk::PhysicalDeviceVulkan13Properties>().maxBufferSize" in ac
    assert b"arena_size <= max_sparse_buffer_size" in ac
    assert b"total_size <= max_sparse_buffer_size" in ac
    try:
        modified(ah,ac)
    except ValueError:
        pass
    else:
        raise AssertionError("Did not reject already patched sparse cache")
    assert (1 << 30) < (1<<32)-4
    assert (2 << 30) < (1<<32)-4
    print("SELFTEST PASS: 1GiB pages, 2GiB pairs below RADV limit, Vulkan13 limit "
          "query, initial/merge guards, exact anchor checks and double-patch rejection")

def main():
    if sys.argv[1:]==["--self-test"]:
        selftest()
        return
    if len(sys.argv) not in (3,4) or (len(sys.argv)==4 and sys.argv[3]!="--check-only"):
        raise SystemExit("Usage: patcher ROOT BACKUP_DIR [--check-only]")
    root,backups=map(lambda x:Path(x).resolve(),sys.argv[1:3])
    head=root/REL_H
    impl=root/REL_C
    before_h,before_c=head.read_bytes(),impl.read_bytes()
    after_h,after_c=modified(before_h,before_c)
    selftest()
    if len(sys.argv)==4:
        print("GHOST_ARENA_1G_SOURCE_CHECK_ONLY_PASS")
        return
    # Back up *both* files before writing either one so the trial runner's
    # EXIT trap always has a complete rollback set.
    for rel,bytes_ in ((REL_H,before_h),(REL_C,before_c)):
        dest=backups/rel
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(bytes_)
        if dest.read_bytes()!=bytes_:
            raise IOError("Original sparse arena source backup verification failed")
    head.write_bytes(after_h)
    impl.write_bytes(after_c)
    if head.read_bytes()!=after_h or impl.read_bytes()!=after_c:
        raise IOError("Arena trial patch verification failed; caller must rollback")
    print("GHOST_ARENA_1G_PATCHED: both header and implementation")
    print("This is a bounded experimental split, NOT complete sparse arena bridging.")

if __name__=="__main__":
    main()
