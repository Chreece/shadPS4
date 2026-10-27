#!/usr/bin/env python3
"""Exact-source c925 GoT color export target compaction A/B.

No per-game/shader hacks. Modifies only a preserved isolated build, retains
all earlier texture, SRT, dynamic V#, Vulkan, GPU/CPU readback, and log fixes.
The trusted previous runner validates its own build, VRAM and bounded game.
"""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

P = Path.home() / "projects/shadps4"
PREVIOUS = P / "scripts/got-c925-strided-material-native-20261027.py"
PREVIOUS_BLOB = "0ab99148c9a7cb5ff61348a7c6117595ae565226"
NEW_ELF_SHA = "395682a478f04e4f4a5a2da89488e90fe54ccf502dc23cbecf716a7adeac4e8b"
FAST_ELF = P / "GhostOfTsushima/candidates/c925-20261027-184324-sharp-provenance-vram-verified/shadps4"
FAST_ELF_SHA = "355613cdd5e83500bc9efd032f1f55e1207f27528b328ee0e5344c9c8296ba0f"
HOTLOG_ELF = P / "GhostOfTsushima/candidates/c925-20261027-193333-hotlog-bindreject/shadps4"
HOTLOG_ELF_SHA = "2681b63b89bf808fa7307d633e9264eb80b7f102c640cbdad8b883dd88dcc7cc"

EXACT_FILES = {
    "src/video_core/amdgpu/regs_color.h":
        ("7bf612871bf93f9bbdf4691253086c8eb67dc170cbea12621e8d0b15dc8464d4",
         "bf563d8182fe7fe14c50c3527475d62332b749d31dccc44396eb471c6e600fca"),
    "src/shader_recompiler/runtime_info.h":
        ("fb1fb798f79817af54630f2ca027c8795454e21c48a5e47d74d3923f73e9ab75",
         "17594ff61944f721e7881104483409fb755ef327751861fee148fc6a8828464b"),
    "src/shader_recompiler/frontend/translate/export.cpp":
        ("57c7e80283176311bdebd969583a12cc6df718307e25e9b424387dbb55fa760c",
         "e452b73b0107e0736eee3ceae12b68c53a4821d5751e8bb76965c3ae481e07e8"),
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp":
        ("7c40b40f0b1389f1fee78746ee6cc0f9e44a9c542fe3d69c9aa99a9225e6b6b4",
         "b70153ad9ca2cb4773fc5f67be4b27b4da65329fab53cdf798845dcd907d395f"),
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":
        ("08fed917f01d6a9143563405a0f31a62f76ea1dfe632965745e70501e3a1f840",
         "64137cfbf2ad47bebe31ece0e05b3355eeab2e0898b913323c3a2564f77604ae"),
}
OTHER_PROVEN = {
    "src/shader_recompiler/ir/passes/resource_discover_pass.cpp":
        "afbce4084a993892a0cae1d48f994436eaf102f896f1c9970bf264559ff8ce34",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp":
        "0d390169a2fbec0cc9117cf9a97a959a356756da9f8a80051a08f579b8134d24",
    "src/shader_recompiler/ir/passes/indexed_image_selector.h":
        "5368cf29fcdac40c9273e06e82682c406835dbed234eee7daa5956acd1e0f35c",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp":
        "52a3f71010dc69fd9bee56f8aa4d425d93beb646ebeb4af23f71e5260ea523e4",
    "src/video_core/renderer_vulkan/vk_rasterizer.h":
        "5a99989156ae9310a1812fd5400a0cff0d12f2ed6d618efe98199b6fb5d122ed",
    "src/video_core/buffer_cache/buffer_cache.cpp":
        "4c9d2593da604e32ebc22451fe5a33f1d5afed07eb4a7f50827aa0125365667e",
    "src/common/log_occurrence_gate.h":
        "67efcbb9ac6ea2c3c55cb15ab1842491362e9be480e0ffd368d2b69b549aa992",
}

def load_previously_verified_runner():
    if not PREVIOUS.is_file() or PREVIOUS.is_symlink():
        raise RuntimeError("Pinned previous runner absent/symlinked; source unchanged")
    blob = subprocess.check_output(
        ["git", "hash-object", str(PREVIOUS)], text=True, timeout=15).strip()
    if blob != PREVIOUS_BLOB:
        raise RuntimeError("Existing native helper SHA differs; no build started")
    spec = importlib.util.spec_from_file_location("c925_native_cbmask", PREVIOUS)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot import verified native helper")
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    return native

def patch_and_validate(native):
    native.CURRENT_ELF_SHA = NEW_ELF_SHA
    native.OLD_ELF_SAVE = (
        native.ROOT / "candidates/c925-20261027-195934-performance-positive/shadps4")
    native.PATCH_REV = "89ef49653a3bcb5d2cec5a94bfdea569ceec9046"
    native.PATCH_PATH = "scripts/patchsets/c925-color-shader-mask-export-remap-20261027.patch"
    native.PATCH_BLOB = "1863de74154e250a1f72bd4141230a55e757ffd3"
    native.FILES = EXACT_FILES
    native.OTHER_PROVEN = OTHER_PROVEN

    initial_preflight = native.preflight
    def verified_preflight(m):
        result = initial_preflight(m)
        for path, sha in ((FAST_ELF, FAST_ELF_SHA), (HOTLOG_ELF, HOTLOG_ELF_SHA)):
            if not path.is_file() or path.is_symlink() or m.sha256(path) != sha:
                raise m.Abort("OLDER_POSITIVE_GO_T_ELF_CHANGED_" + path.parent.name, 2)
        m.STATE.update({
            "record_type": "C925_CB_SHADER_MASK_MRT_EXPORT_COMPACTION_NATIVE_A_B",
            "tested_games": [],
            "source_changes": "five exact source files, physical MRT mapping only when mask compacted",
            "before_elf": NEW_ELF_SHA,
            "shader_binary_cache_version": "0x4352000a",
            "no_game_id_or_shader_hash_exceptions": True,
            "all_previous_typed_texture_work_preserved": True,
        })
        return result
    native.preflight = verified_preflight

    def test_production_mask_method(m, scratch):
        m.stage("GCC_CLANG_VALIDATE_EXACT_CB_SHADER_MASK_METHOD_2048_CASES")
        header = scratch / "src/video_core/amdgpu/regs_color.h"
        source = header.read_text()
        start = source.find("struct ColorBufferMask {")
        end = source.find("struct ColorBuffer {", start)
        if start == -1 or end == -1 or source.count("GetTarget(u32 export_index)") != 1:
            raise m.Abort("EXACT_CB_SHADER_MASK_PRODUCTION_SOURCE_UNAVAILABLE", 2)
        body = source[start:end]
        fixture = r'''#include <cstdint>
#include <iostream>
using u32 = std::uint32_t;
namespace AmdGpu {
static constexpr u32 NUM_COLOR_BUFFERS = 8;
''' + body + r'''
}
constexpr bool CheckAllMasks() {
    AmdGpu::ColorBufferMask mask{};
    for (u32 bits = 0; bits < 256; ++bits) {
        mask.raw = 0;
        for (u32 bit=0; bit<8; ++bit) {
            if (bits & (1u << bit)) mask.SetMask(bit, 0xfu);
        }
        for (u32 ordinal = 0; ordinal < 8; ++ordinal) {
            u32 expected = 8, count = 0;
            if (bits == 0) expected = ordinal;
            else for (u32 phys = 0; phys < 8; ++phys) {
                if (bits & (1u << phys)) {
                    if (count++ == ordinal) { expected = phys; break; }
                }
            }
            if (mask.GetTarget(ordinal) != expected) return false;
        }
    }
    return true;
}
static_assert(CheckAllMasks());
int main() {
    if (!CheckAllMasks()) return 4;
    std::cout << "CB_SHADER_MASK_PRODUCTION_MAPPING_2048_PASS\n";
}
'''
        test = m.OUT / "cb-shader-mask-production.cpp"
        test.write_text(fixture)
        for compiler in ("g++", "clang++"):
            if not shutil.which(compiler):
                if compiler == "g++":
                    raise m.Abort("GCC_FOR_REAL_CB_MASK_SOURCE_NOT_AVAILABLE", 2)
                continue
            suffix = compiler.replace("+", "p")
            binary = m.OUT / ("cb-shader-mask-" + suffix)
            m.command("cb-mask-build-" + suffix,
                      [compiler, "-std=c++20", "-O2", "-DNDEBUG",
                       "-Wall", "-Wextra", "-Werror", "-pedantic",
                       str(test), "-o", str(binary)], timeout=90)
            m.command("cb-mask-test-" + suffix, [str(binary)], timeout=25)
            actual = (m.OUT / ("cb-mask-test-" + suffix + ".log")).read_text()
            if "CB_SHADER_MASK_PRODUCTION_MAPPING_2048_PASS" not in actual:
                raise m.Abort("REAL_CB_SHADER_MASK_TEST_FAILED_" + suffix, 2)
            print("CB_SHADER_MASK_PRODUCTION_MAPPING_PASS=" + compiler, flush=True)
        m.STATE["real_cb_shader_mask_mapping_fixture"] = "GCC_CLANG_PASS"
    native.native_matcher_fixture = test_production_mask_method

    previous_summary = native.summarize_runtime
    def audit_compacted_outputs(m):
        previous_summary(m)
        path = m.OUT / "game-console.log"
        if not path.is_file(): return
        text = path.read_text(errors="replace")
        remaps = [line[:1200] for line in text.splitlines()
                  if "CB_SHADER_MASK_EXPORT_REMAP" in line]
        m.STATE["real_shader_export_compaction"] = {
            "events": len(remaps),
            "shader_ids": sorted(set(re.findall(r"shader=(0x[0-9a-f]+)", "\n".join(remaps)))),
            "samples": remaps[:24]
        }
        (m.OUT / "physical-render-target-remap.json").write_text(
            json.dumps(m.STATE["real_shader_export_compaction"], indent=2) + "\n")
        print("CB_SHADER_MASK_REAL_SHADER_REMAPS=" + str(len(remaps)), flush=True)
    native.summarize_runtime = audit_compacted_outputs
    return native

def main() -> int:
    try:
        previous = patch_and_validate(load_previously_verified_runner())
    except BaseException as error:
        print("CB_SHADER_MASK_PRE_RUN_INTEGRITY_FAILURE=" + str(error),
              file=sys.stderr, flush=True)
        return 2
    return previous.main()

if __name__ == "__main__":
    sys.exit(main())
