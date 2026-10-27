#!/usr/bin/env python3
"""Fix a concrete missing-texture-cache invalidation path and test ONLY GoT menu.

The trusted prior c925 native runner handles exact pinned source/build, protected
ELFs, 118+2-second game, verified Ollama VRAM offload/restore, and universal diary.
No game input, no title/shader execution hacks, no forced texture bindings.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

PROJECT = Path.home() / "projects/shadps4"
PREVIOUS_RUNNER = PROJECT / "scripts/got-c925-strided-material-native-20261027.py"
PREVIOUS_RUNNER_BLOB = "0ab99148c9a7cb5ff61348a7c6117595ae565226"
BASE_ELF_SHA = "63fe630faed40faf2a7dddde87d1084e5dade151add8f4c0b206f86d9caa69b9"
MENU_1959 = PROJECT / "GhostOfTsushima/candidates/c925-20261027-195934-performance-positive/shadps4"
MENU_1959_SHA = "395682a478f04e4f4a5a2da89488e90fe54ccf502dc23cbecf716a7adeac4e8b"
NEW_SOURCE = "src/video_core/texture_cache/texture_cache.cpp"
BEFORE_SHA = "678aed5ee2196246e6f6eb118fe79fea9408610b551f9575a4af6d36030df0be"
AFTER_SHA = "237950c841ee99dcfdc3a49d80657a4e53cc374c97ff48ba1d3ef83449f7f4f3"
PATCH_COMMIT = "a06d8a4f6b14d94d28fc7ae53ac4a5dd38fee152"
PATCH_PATH = "scripts/patchsets/c925-menu-gpu-texture-alias-coherence-20261027.patch"
PATCH_GIT_BLOB = "bd772e5d67a1eea45f48f18a18574111d84171ab"
UNCHANGED = {
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp":
       "52a3f71010dc69fd9bee56f8aa4d425d93beb646ebeb4af23f71e5260ea523e4",
    "src/shader_recompiler/ir/passes/resource_discover_pass.cpp":
       "afbce4084a993892a0cae1d48f994436eaf102f896f1c9970bf264559ff8ce34",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp":
       "0d390169a2fbec0cc9117cf9a97a959a356756da9f8a80051a08f579b8134d24",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":
       "64137cfbf2ad47bebe31ece0e05b3355eeab2e0898b913323c3a2564f77604ae",
    "src/shader_recompiler/frontend/translate/export.cpp":
       "e452b73b0107e0736eee3ceae12b68c53a4821d5751e8bb76965c3ae481e07e8",
    "src/video_core/amdgpu/regs_color.h":
       "bf563d8182fe7fe14c50c3527475d62332b749d31dccc44396eb471c6e600fca",
    "src/shader_recompiler/runtime_info.h":
       "17594ff61944f721e7881104483409fb755ef327751861fee148fc6a8828464b",
    "src/video_core/renderer_vulkan/vk_rasterizer.h":
       "5a99989156ae9310a1812fd5400a0cff0d12f2ed6d618efe98199b6fb5d122ed",
}

def verified_base_runner():
    if not PREVIOUS_RUNNER.is_file() or PREVIOUS_RUNNER.is_symlink():
        raise RuntimeError("EXACT_C925_NATIVE_RUNNER_MISSING")
    value = subprocess.check_output(
        ["git", "hash-object", str(PREVIOUS_RUNNER)], text=True, timeout=15).strip()
    if value != PREVIOUS_RUNNER_BLOB:
        raise RuntimeError("EXACT_C925_NATIVE_RUNNER_MODIFIED")
    spec = importlib.util.spec_from_file_location("c925_menu_texture_native", PREVIOUS_RUNNER)
    if not spec or not spec.loader:
        raise RuntimeError("CANNOT_LOAD_EXACT_C925_NATIVE_HELPER")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def extract_function(source: str, name: str) -> str:
    marker = "void TextureCache::" + name + "("
    start = source.find(marker)
    if start < 0 or source.count(marker) != 1:
        raise ValueError("Missing or duplicated production texture cache method: " + name)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise ValueError("Unbalanced production texture cache braces: " + name)

# The C++ fixture calls the REAL new methods, extracted verbatim from the
# SHA-verified production source, with a lightweight image/region mock.
# This does not claim the complete Vulkan emulator has compiled locally.
PREFIX = r"""
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <vector>
#include <algorithm>
#include <iostream>
#include <cstdlib>
using u64=std::uint64_t;
using u32=std::uint32_t;
using VAddr=u64;
using ImageId=std::size_t;
static int logs=0, hashes=0;
#define LOG_INFO(...) (++logs)
#define Render_Vulkan 0
enum class ImageFlagBits:u32 { GpuDirty=4, GpuModified=8, CpuDirty=2, Dirty=7 };
constexpr u32 operator&(u32 value,ImageFlagBits flag){return value & u32(flag);}
constexpr u32& operator|=(u32& value,ImageFlagBits flag){value |= u32(flag);return value;}
constexpr bool True(u32 value){return value!=0;}
constexpr bool False(u32 value){return value==0;}
struct ImageInfo { VAddr guest_address{};u64 guest_size{};};
struct Image {
    ImageInfo info{};u32 flags{};u64 gpu_hash{};
    bool HasGpuValidData() const {
       return True(flags & ImageFlagBits::GpuModified) && False(flags & ImageFlagBits::Dirty);
    }
};
u64 HashBackingMemory(VAddr addr,u64 size){++hashes;return addr^size^0x1f3ull;}
class TextureCache {
public:
    std::vector<Image> imgs;
    template<class F> void ForEachImageInRegion(VAddr addr,u64 size,F&& callback){
      for(ImageId i=0;i<imgs.size();++i){
        auto& im=imgs[i];
        if(!size || !im.info.guest_size)continue;
        if(addr < im.info.guest_address+im.info.guest_size &&
           im.info.guest_address < addr+size)callback(i,im);
      }
    }
    void InvalidateMemoryFromGPU(VAddr,size_t);
    void MarkAsMaybeGpuDirty(VAddr,size_t);
};
"""

SUFFIX = r"""
static void require(bool valid,const char* description) {
    if(!valid) {std::cerr<<"FAIL "<<description<<"\n";std::exit(5);}
}
int main(){
    TextureCache c;
    c.imgs={
      {{0x1000,0x1000},0,0},
      {{0x3000,0x1000},8,0},
      {{0x5000,0x1000},8,0},
      {{0x7000,0x1000},2,0},
      {{0x9000,0x1000},8,0},
    };
    c.InvalidateMemoryFromGPU(0x1700,0x100);
    require((c.imgs[0].flags&4)!=0,"gpu partial image write must dirty cached image");
    c.InvalidateMemoryFromGPU(0x3700,0x100);
    require((c.imgs[1].flags&4)==0,"partial write must preserve independently gpu-owned pixels");
    c.InvalidateMemoryFromGPU(0x5000,0x80);
    require((c.imgs[2].flags&4)!=0,"same-base gpu buffer write must invalidate");
    c.InvalidateMemoryFromGPU(0x1700,0);
    require(logs==1,"zero-size or redundant partial write must not log as new invalidation");
    c.MarkAsMaybeGpuDirty(0x7000,0x100);
    require(c.imgs[3].gpu_hash==0,"cpu dirty buffer must not acquire valid GPU hash");
    c.MarkAsMaybeGpuDirty(0x9000,0x100);
    require(c.imgs[4].gpu_hash!=0&&hashes==1,"valid gpu image baseline must be kept");
    c.MarkAsMaybeGpuDirty(0x5000,0x100);
    require(c.imgs[2].gpu_hash==0&&hashes==1,"already gpu dirty image hash cannot be used");
    for(u64 i=0;i<100000;++i){
      c.InvalidateMemoryFromGPU(0x1000+i%0x500,1);
      require((c.imgs[0].flags&4)!=0,"repeat GPU subresource write invalidation");
    }
    std::cout<<"REAL_TEXTURE_CACHE_ALIAS_INVALIDATION_100006_CASES_PASS\n";
}
"""

def source_fixture(m, scratch: Path):
    m.stage("TEST_ACTUAL_PATCHED_TEXTURE_CACHE_METHODS_GCC_CLANG_RELEASE")
    new_file = scratch / NEW_SOURCE
    source = new_file.read_text()
    if (m.sha256(new_file) != AFTER_SHA or
            "GPU_SUBRESOURCE_IMAGE_INVALIDATED" not in source or
            "info.MipOf(image.info)" not in source or
            "image.HasGpuValidData()" not in source):
        raise m.Abort("PRODUCTION_TEXTURE_CACHE_SOURCE_IS_NOT_EXPECTED_CORRECTION", 2)
    extracted = (extract_function(source, "InvalidateMemoryFromGPU") + "\n" +
                 extract_function(source, "MarkAsMaybeGpuDirty"))
    cpp = m.OUT / "texture-cache-real-methods.cpp"
    cpp.write_text(PREFIX + "\n" + extracted + "\n" + SUFFIX)
    for compiler in ("g++", "clang++"):
        if not shutil.which(compiler):
            if compiler == "g++":
                raise m.Abort("GCC_NOT_INSTALLED_FOR_NATIVE_TEXTURE_CORRECTION", 2)
            continue
        suffix = compiler.replace("+", "p")
        binary = m.OUT / ("actual-gpu-alias-regression-" + suffix)
        m.command("production-texture-test-build-" + suffix,
                  [compiler, "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra",
                   "-Werror", "-Wno-unused-parameter", "-pedantic", "-pthread",
                   str(cpp), "-o", str(binary)], timeout=120)
        m.command("production-texture-test-execute-" + suffix,
                  [str(binary)], timeout=20)
        log = (m.OUT / ("production-texture-test-execute-" + suffix + ".log")).read_text()
        if "REAL_TEXTURE_CACHE_ALIAS_INVALIDATION_100006_CASES_PASS" not in log:
            raise m.Abort("REAL_PRODUCTION_TEXTURE_CACHE_METHOD_REGRESSION_FAILED", 2)
        print("GPU_TEXTURE_ALIAS_NATIVE_RELEASE_TEST=PASS " + compiler, flush=True)
    m.STATE["real_texture_cache_methods_cxx_release_test"] = "PASS"

def prepare(native):
    native.CURRENT_ELF_SHA = BASE_ELF_SHA
    native.OLD_ELF_SAVE = native.ROOT / (
        "candidates/c925-20261027-202701-cb-mask-before-texture-alias/shadps4")
    native.PATCH_REV = PATCH_COMMIT
    native.PATCH_PATH = PATCH_PATH
    native.PATCH_BLOB = PATCH_GIT_BLOB
    native.FILES = {NEW_SOURCE: (BEFORE_SHA, AFTER_SHA)}
    native.OTHER_PROVEN = UNCHANGED

    existing_preflight = native.preflight
    def verified_menu_preflight(m):
        old = existing_preflight(m)
        if not MENU_1959.is_file() or MENU_1959.is_symlink() or m.sha256(MENU_1959) != MENU_1959_SHA:
            raise m.Abort("LAST_KNOWN_GOOD_MENU_ELF_MISSING_OR_CHANGED", 2)
        m.STATE.update({
            "record_type": "C925_GOT_MENU_GPU_IMAGE_ALIAS_STALE_TEXTURE_CORRECTION",
            "acceptance": "MENU_ONLY_GRAPHICS_RESOURCE_CORRECTNESS",
            "gameplay_requested": False, "gamepad_injected": False,
            "user_input_required": False,
            "baseline_current_elf_sha256": BASE_ELF_SHA,
            "protected_menu_1959_elf_sha256": MENU_1959_SHA,
            "texture_alias_patch_git_blob": PATCH_GIT_BLOB,
            "changes": "Only texture_cache.cpp: overlap GPU write invalidation, valid baseline hashing, final mip/slice",
        })
        return old
    native.preflight = verified_menu_preflight

    native.native_matcher_fixture = source_fixture

    previous_summary = native.summarize_runtime
    def menu_rendering_summary(m):
        previous_summary(m)
        path = m.OUT / "game-console.log"
        if not path.is_file():
            return
        raw = path.read_text(errors="replace")
        probes = ("GPU_SUBRESOURCE_IMAGE_INVALIDATED",
                  "GuestIndexedImageDimensions:", "Sharp source was not flatenned",
                  "SHARP_RESOURCE_PROVENANCE", "ErrorDeviceLost",
                  "context is lost", "SRT dependency resolution stalled",
                  "IndexedStridedSource:", "CB_SHADER_MASK_EXPORT_REMAP")
        counts = {name: raw.count(name) for name in probes}
        m.STATE["menu_gpu_alias_runtime_evidence"] = counts
        (m.OUT / "menu-gpu-alias-runtime-evidence.json").write_text(
            json.dumps(counts, indent=2) + "\n")
        lines = [line[:1500] for line in raw.splitlines()
                 if "GPU_SUBRESOURCE_IMAGE_INVALIDATED" in line]
        (m.OUT / "gpu-image-subresource-invalidations.log").write_text(
            "\n".join(lines[:120]) + "\n")
        print("MENU_GPU_SUBRESOURCE_INVALIDATIONS=" +
              str(counts["GPU_SUBRESOURCE_IMAGE_INVALIDATED"]), flush=True)
        print("MENU_SHARP_UNFLATTENED_WARNINGS=" +
              str(counts["Sharp source was not flatenned"]), flush=True)
        print("MENU_TYPED_IMAGE_ARRAY_EVENTS=" +
              str(counts["GuestIndexedImageDimensions:"]), flush=True)
    native.summarize_runtime = menu_rendering_summary
    return native

def main():
    try:
        native = prepare(verified_base_runner())
    except BaseException as error:
        print("MENU_TEXTURE_PRE_RUN_INTEGRITY_STOP=" + str(error), file=sys.stderr, flush=True)
        return 2
    # Original pinned runner launches the standalone GoT binary and captures
    # 40/55/85/112-second screenshots; it never presses Continue.
    return native.main()

if __name__ == "__main__":
    sys.exit(main())
