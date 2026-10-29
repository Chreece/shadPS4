#!/usr/bin/env python3
"""GoT C925: synchronize GPU-dirty compute SRT roots before shader specialization.

Reuses retained backing-write and compute-metadata corrections and the complete
source/object/ELF rollback runner. No fabricated material record counts.
"""
from __future__ import annotations
import hashlib
import sys
from pathlib import Path

P = Path.home() / "projects/shadps4"
BASE_SHA = "7c784750aba599f3c0aa5201247a6f98a12c9434"
BASE_SCRIPT = P / "scripts/got-menu-compute-coherence.py"
CALL_MARK = (
    "    scheduler.PopPendingOperations();\n\n"
    "    const auto& cs_program = liverpool->GetCsRegs();\n"
    "    const ComputePipeline* pipeline = pipeline_cache.GetComputePipeline();"
)

CPP = r'''
// SRT_RESOURCE_ROOT_COHERENCE_V1: synchronize actual GPU-written pointers
// before the shader compiler's CPU SRT walker snapshots them.
template <typename CsProgram>
static void SynchronizeComputeSrtRoot(const CsProgram& cs_program,
                                      VideoCore::BufferCache& buffer_cache,
                                      Core::MemoryManager* memory) {
    const auto& ud = cs_program.user_data;
    if (ud.size() < 2) return;
    constexpr u64 GuestAddressMask = 0xFFFFFFFFFFFFull;
    constexpr u64 MaxSrtBytes = 4096;
    const VAddr root = ((u64(ud[1]) << 32) | u64(ud[0])) & GuestAddressMask;
    if (!root || !memory->IsValidMapping(root, 8)) return;

    auto coherent = [&](VAddr address, u64 min_bytes, const char* kind) {
        if (!address || !memory->IsValidMapping(address, min_bytes)) return;
        const u64 length = std::min<u64>(
            MaxSrtBytes, memory->ClampRangeSize(address, MaxSrtBytes));
        if (length < min_bytes || !buffer_cache.IsRegionGpuModified(address, length)) return;
        buffer_cache.ReadMemory(address, length, false, false, true);
        static std::atomic<u32> observed{};
        const u32 n = observed.fetch_add(1, std::memory_order_relaxed) + 1;
        if (n <= 12 || (n & (n - 1)) == 0) {
            LOG_INFO(Render_Vulkan,
                     "ComputeSrtRootGpuSync event={} kind={} bytes={} clean={}",
                     n, kind, length,
                     !buffer_cache.IsRegionGpuModified(address, length));
        }
    };
    coherent(root, 8, "root-pointer");
    if (buffer_cache.IsRegionGpuModified(root, 8)) return;
    std::array<u8, 8> pointer_bytes{};
    memory->CopySparseMemory(root, pointer_bytes.data(), pointer_bytes.size());
    u64 next{};
    std::memcpy(&next, pointer_bytes.data(), pointer_bytes.size());
    next &= GuestAddressMask;
    if (next != root && next && memory->IsValidMapping(next, 4)) {
        coherent(next, 4, "srt-pointee");
    }
}
'''

def inject(program):
    if "SRT_RESOURCE_ROOT_COHERENCE_V1" in program:
        raise RuntimeError("SRT_ROOT_FIX_ALREADY_PRESENT")
    signature = "void Rasterizer::DispatchDirect() {"
    if program.count(signature) != 1 or program.count("void Rasterizer::DispatchIndirect(") != 1:
        raise RuntimeError("EXACT_COMPUTE_DISPATCH_FUNCTIONS_CHANGED")
    if program.count(CALL_MARK) != 2:
        raise RuntimeError("COMPUTE_PIPELINE_ORDER_CHANGED:" + str(program.count(CALL_MARK)))
    program = program.replace(signature, CPP + "\n" + signature, 1)
    after = (
        "    scheduler.PopPendingOperations();\n\n"
        "    const auto& cs_program = liverpool->GetCsRegs();\n"
        "    SynchronizeComputeSrtRoot(cs_program, buffer_cache, memory);\n"
        "    const ComputePipeline* pipeline = pipeline_cache.GetComputePipeline();"
    )
    program = program.replace(CALL_MARK, after)
    if program.count("SynchronizeComputeSrtRoot(cs_program, buffer_cache, memory);") != 2:
        raise RuntimeError("SRT_ROOT_FIX_CALL_COUNT_MISMATCH")
    return program

def test():
    skeleton = "\n".join([
        "void Rasterizer::DispatchDirect() {", CALL_MARK, "}",
        "void Rasterizer::DispatchIndirect(VAddr addr, u32 offset, u32 size) {", CALL_MARK, "}"
    ])
    out = inject(skeleton)
    assert out.count("SRT_RESOURCE_ROOT_COHERENCE_V1") == 1
    assert out.count("SynchronizeComputeSrtRoot(cs_program, buffer_cache, memory);") == 2
    assert "ReadMemory(address, length, false, false, true)" in out
    assert "if (buffer_cache.IsRegionGpuModified(root, 8)) return;" in out
    assert "ComputeSrtRootGpuSync" in out
    try:
        inject(out)
    except RuntimeError as e:
        assert str(e) == "SRT_ROOT_FIX_ALREADY_PRESENT"
    else:
        raise AssertionError("DOUBLE_PATCH_WAS_ACCEPTED")
    print("SRT_ROOT_POINTER_COHERENCE_SOURCE_TESTS=PASS", flush=True)

def main():
    test()
    if not BASE_SCRIPT.is_file() or BASE_SCRIPT.is_symlink():
        raise RuntimeError("VERIFIED_COMPUTE_RUNNER_MISSING")
    raw = BASE_SCRIPT.read_bytes()
    def blob(data):
        return hashlib.sha1(
            b"blob " + str(len(data)).encode() + b"\0" + data
        ).hexdigest()
    if blob(raw) != BASE_SHA:
        raise RuntimeError("PREVIOUS_RUNNER_BLOB_CHANGED")
    s = raw.decode()
    metadata = {
        "got-c925-menu-compute-coherence-": "got-c925-srt-root-coherence-",
        "menu-compute-coherence-": "srt-root-coherence-",
        "Preserve compute metadata multi-outputs and coherent GPU clear constants":
            "Preserve backed writes, compute clears and GPU-dirty SRT root contents",
    }
    for old, new in metadata.items():
        if old not in s:
            raise RuntimeError("PREVIOUS_RUNNER_METADATA_CHANGED:" + old)
        s = s.replace(old, new)
    key = 'patterns = ("MetaClearMultiOutputPreserved"'
    if s.count(key) != 1:
        raise RuntimeError("PREVIOUS_RUNNER_EVENT_COUNT_CHANGED")
    s = s.replace(
        key, 'patterns = ("ComputeSrtRootGpuSync", "MetaClearMultiOutputPreserved"', 1
    )
    namespace = {"__name__": "c925_compute_srt_root_trial",
                 "__file__": str(BASE_SCRIPT)}
    exec(compile(s, str(BASE_SCRIPT), "exec"), namespace)
    original = namespace["patch_renderer"]
    actual = namespace["FILES"]["raster_src"].read_text()
    if actual.count(CALL_MARK) != 2:
        raise RuntimeError("ACTUAL_C925_DISPATCH_SHAPE_CHANGED")
    new = inject(original(actual))
    if new == actual:
        raise RuntimeError("NO_ACTUAL_CPP_CORRECTION")
    # The inherited unit fixture has no full Rasterizer::DispatchDirect.
    def patched(source):
        old = original(source)
        return inject(old) if "void Rasterizer::DispatchDirect()" in source else old
    namespace["patch_renderer"] = patched
    print("COMPUTE_SRT_ROOT_ACTUAL_SOURCE_PREFLIGHT=PASS", flush=True)
    print("BUILD=3_EXISTING_OBJECTS_ON_VERIFIED_BACKING_AND_METADATA_FIXES", flush=True)
    print("VRAM=8GIB_MINIMUM_CONDITIONAL_OLLAMA_RESTORE", flush=True)
    print("GAME=MENU_ONLY_NO_CONTINUE_118_PLUS_2_SECONDS", flush=True)
    print("RESTORE=ORIGINAL_SOURCE_OBJECT_ELF_AND_PROTECTED_CANDIDATES", flush=True)
    helper = P / "scripts/got-texture-overlap.Xah6oZUt"
    return namespace["main"](helper)

if __name__ == "__main__":
    sys.exit(main())
