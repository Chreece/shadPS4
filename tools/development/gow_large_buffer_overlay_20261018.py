#!/usr/bin/env python3
"""Opt-in timing of oversized GoW shader-buffer bindings and actual uploads."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path

R="src/video_core/renderer_vulkan/vk_rasterizer.cpp"
B="src/video_core/buffer_cache/buffer_cache.cpp"
HASH={
 R:"9dc9f11d7e8fd4ae2f5ff484381f531f84a1618e7b6c72b6ffebdbfe1e780e86",
 B:"2b8d0e35f45e18ecbfc88ef153665d3e9d713638b1fa988e505ef633a8cc71e0",
}
CALL="""                const auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, size, flags, static_cast<u32>(tracked_write_size));"""
TIMED_CALL=r"""                static const bool big_probe = [] {
                    const char* v = std::getenv("SHADPS4_GOW_BIG_BUFFER_PROBE");
                    return v && v[0] == '1' && v[1] == '\0';
                }();
                const bool measure = big_probe && stage.pgm_hash == 0xdfd7ccccu &&
                                     size >= (64u << 20);
                const auto t0 = measure ? std::chrono::steady_clock::now()
                                        : std::chrono::steady_clock::time_point{};
                const auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, size, flags, static_cast<u32>(tracked_write_size));
                if (measure) {
                    const u64 us = static_cast<u64>(
                        std::chrono::duration_cast<std::chrono::microseconds>(
                            std::chrono::steady_clock::now() - t0).count());
                    static thread_local u64 calls{}, cumulative_us{}, max_us{};
                    ++calls;
                    cumulative_us += us;
                    if (us > max_us) max_us = us;
                    if (calls == 1 || (calls & 255u) == 0) {
                        LOG_WARNING(Render_Vulkan,
                          "GOW_BIG_BUFFER_BIND shader={:#x} calls={} requested={} "
                          "clamped={} written={} tracked_write={} last_us={} "
                          "sum_us={} max_us={}", stage.pgm_hash, calls,
                          vsharp.GetSize(), size, desc.is_written,
                          tracked_write_size, us, cumulative_us, max_us);
                    }
                }"""

START="""    Common::GpuCpuProbe::Scope gpu_cpu_probe{Common::GpuCpuProbe::Slot::SynchronizeMemory};
    boost::container::small_vector<vk::BufferCopy, 4> copies;"""
START_TIMED=r"""    Common::GpuCpuProbe::Scope gpu_cpu_probe{Common::GpuCpuProbe::Slot::SynchronizeMemory};
    static const bool big_probe = [] {
        const char* v = std::getenv("SHADPS4_GOW_BIG_BUFFER_PROBE");
        return v && v[0] == '1' && v[1] == '\0';
    }();
    const bool measure = big_probe && size >= (64u << 20);
    const auto t0 = measure ? std::chrono::steady_clock::now()
                            : std::chrono::steady_clock::time_point{};
    boost::container::small_vector<vk::BufferCopy, 4> copies;"""
SCAN="""    });
    if (!copies.empty()) {
        const auto staging = staging_pool.Request(total_size_bytes, MemoryType::HostUncached);"""
SCAN_TIMED=r"""    });
    const auto t_scan = measure ? std::chrono::steady_clock::now()
                                : std::chrono::steady_clock::time_point{};
    if (!copies.empty()) {
        const auto staging = staging_pool.Request(total_size_bytes, MemoryType::HostUncached);"""
DONE="""    if (is_texel_buffer && !is_written) {
        return SynchronizeMemoryFromImage(arena, device_addr, size);
    }
    return false;"""
DONE_TIMED=r"""    if (measure) {
        const auto done = std::chrono::steady_clock::now();
        const u64 scan_us = static_cast<u64>(
            std::chrono::duration_cast<std::chrono::microseconds>(t_scan-t0).count());
        const u64 all_us = static_cast<u64>(
            std::chrono::duration_cast<std::chrono::microseconds>(done-t0).count());
        static thread_local u64 calls{}, scan_sum{}, all_sum{}, uploaded{}, copies_sum{}, max_us{};
        ++calls;
        scan_sum += scan_us;
        all_sum += all_us;
        uploaded += total_size_bytes;
        copies_sum += copies.size();
        if (all_us > max_us) max_us = all_us;
        if (calls == 1 || (calls & 255u) == 0) {
            LOG_WARNING(Render_Vulkan,
               "GOW_BIG_BUFFER_SYNC calls={} size={} written={} "
               "uploaded_bytes={} dirty_ranges={} scan_sum_us={} "
               "all_sum_us={} max_us={}",
               calls, size, is_written, uploaded, copies_sum,
               scan_sum, all_sum, max_us);
        }
    }
    if (is_texel_buffer && !is_written) {
        return SynchronizeMemoryFromImage(arena, device_addr, size);
    }
    return false;"""

def sha(b):
    return hashlib.sha256(b).hexdigest()

def once(s,a,b):
    n=s.count(a)
    if n!=1:
        raise ValueError("NON_UNIQUE_SOURCE_ANCHOR count="+str(n)+" "+a[:70])
    return s.replace(a,b,1)

def patch_r(s):
    s=once(s,"#include <atomic>\n#include <limits>",
             "#include <atomic>\n#include <chrono>\n#include <cstdlib>\n#include <limits>")
    return once(s,CALL,TIMED_CALL)

def patch_b(s):
    s=once(s,"#include <algorithm>\n#include <atomic>",
             "#include <algorithm>\n#include <atomic>\n#include <chrono>\n#include <cstdlib>")
    i=s.index("bool BufferCache::SynchronizeMemory(")
    j=s.index("bool BufferCache::SynchronizeMemoryFromImage(",i)
    mid=s[i:j]
    mid=once(mid,START,START_TIMED)
    mid=once(mid,SCAN,SCAN_TIMED)
    mid=once(mid,DONE,DONE_TIMED)
    return s[:i]+mid+s[j:]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("root",type=Path)
    ap.add_argument("out",type=Path)
    ap.add_argument("--apply",action="store_true")
    opt=ap.parse_args()
    opt.out.mkdir(parents=True,exist_ok=True)
    updates=[]
    for rel,fun in ((R,patch_r),(B,patch_b)):
        path=opt.root/rel
        if not path.is_file() or path.is_symlink():
            raise SystemExit("UNSAFE_OR_MISSING_PROTECTED_SOURCE="+rel)
        original=path.read_bytes()
        got=sha(original)
        (opt.out/("actual_"+rel.replace("/","_"))).write_bytes(original)
        if got!=HASH[rel]:
            raise SystemExit("EXACT_PREIMAGE_DIFFERENT="+rel+" expected="+HASH[rel]+" actual="+got)
        try:
            new=fun(original.decode()).encode()
        except (ValueError,UnicodeError) as e:
            raise SystemExit("SOURCE_ANCHOR_NOT_APPLICABLE="+rel+" "+str(e))
        updates.append((path,rel,original,new))
    proofs=[]
    for path,rel,old,new in updates:
        diff="".join(difflib.unified_diff(
            old.decode().splitlines(True),new.decode().splitlines(True),
            fromfile="original/"+rel,tofile="probe/"+rel))
        (opt.out/(rel.replace("/","_")+".diff")).write_text(diff)
        proofs.append({"file":rel,"old":sha(old),"new":sha(new)})
    (opt.out/"sha.json").write_text(json.dumps(proofs,indent=2)+"\n")
    if opt.apply:
        for path,rel,old,new in updates:
            path.write_bytes(new)
    print("EXACT_GOW_LARGE_BUFFER_PREIMAGES=PASS")
    print("BUFFER_PROBE_SOURCE_CHANGED="+str(opt.apply))
    print("SHADER_DESCRIPTOR_SIZES_AND_GPU_COHERENCE=UNCHANGED")

if __name__=="__main__":
    main()
