#!/usr/bin/env python3
"""Exact-source c925 19:33 A/B: lower repetitive host logging and align indexed
texture discovery with the sampled-image binding capabilities. No title/shader
exceptions, fake descriptors, GPU driver tweaks or controller injection.

This small wrapper uses the previously proven native runner unmodified on disk.
It pins all source/patch/test bytes and both protected prior executables.
"""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

P = Path.home() / "projects/shadps4"
PREVIOUS = P / "scripts/got-c925-perf-material-bind-native-20261027.py"
PREVIOUS_GIT_BLOB = "f194b5081c1125d24f4113491d7b195bd45a8d27"

FILES = {
    "src/core/libraries/ajm/ajm.cpp":
        ("6672913740d412f6917c87a0f1a61fece59bdb604e460d8d79701d60c153ddfb",
         "9282f98a2a523b02d6d1669f20432f058d19d3cd53148127c94b5deb071ed443"),
    "src/core/libraries/ajm/ajm_context.cpp":
        ("ac391e28932fb2d2f1b23d2031dbc3cc4b26702b4f4d563d5893de58f7d103fc",
         "17ecc3fdd2f0fe8a7079f28f5cb69ba09f22bbede6431a76ed1e3351cebcb086"),
    "src/core/libraries/kernel/memory.cpp":
        ("8116733ec6b54934786dfd2a4fc5ba33517666b142c86a57faf8005266da0064",
         "8b1c2161be93f1620c28c5aeeb891330f366df3ebc4334a6d808b9ccb84075aa"),
    "src/shader_recompiler/ir/passes/resource_discover_pass.cpp":
        ("1651b224bf4764babe46beb9fc7452366a9e9a1addd7a7d43997ff7c3eca943a",
         "afbce4084a993892a0cae1d48f994436eaf102f896f1c9970bf264559ff8ce34"),
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp":
        ("9444beb9716dbd955ef2f5538d24bc08f9ea48620ff8a13d9aaee81587f0a70e",
         "0d390169a2fbec0cc9117cf9a97a959a356756da9f8a80051a08f579b8134d24"),
    "src/video_core/buffer_cache/buffer_cache.cpp":
        ("c18e7ab282c2a6b8d40a152f3df421924fc59b502980dfa73c4b88ec90b34aaa",
         "4c9d2593da604e32ebc22451fe5a33f1d5afed07eb4a7f50827aa0125365667e"),
    "src/shader_recompiler/ir/passes/indexed_sample_eligibility.h":
        (None, "40b27dab45cf2fd4d3b5d000bfd811c6e6bcfe0cda3a60770981b20f4e8fb90d"),
}
OTHER_PROVEN = {
    "src/common/log_occurrence_gate.h":
        "67efcbb9ac6ea2c3c55cb15ab1842491362e9be480e0ffd368d2b69b549aa992",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp":
        "52a3f71010dc69fd9bee56f8aa4d425d93beb646ebeb4af23f71e5260ea523e4",
    "src/core/libraries/gnmdriver/gnmdriver.cpp":
        "53a897224ec1890b78680994f9f7fedab06ac9b7a04cfc407f0ac77165aa353f",
    "src/shader_recompiler/ir/passes/indexed_image_selector.h":
        "5368cf29fcdac40c9273e06e82682c406835dbed234eee7daa5956acd1e0f35c",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":
        "a46aee5980e343fcf37c1962df22a3dced13d193a349e07715e3433154908fba",
    "src/video_core/renderer_vulkan/vk_rasterizer.h":
        "5a99989156ae9310a1812fd5400a0cff0d12f2ed6d618efe98199b6fb5d122ed",
}

def load_verified_previous():
    if not PREVIOUS.is_file() or PREVIOUS.is_symlink():
        raise RuntimeError("Pinned previous native runner missing/symlinked; no source edits")
    value = subprocess.check_output(
        ["git", "hash-object", str(PREVIOUS)], text=True, timeout=12).strip()
    if value != PREVIOUS_GIT_BLOB:
        raise RuntimeError("Previous runner differs from the exact pinned blob; no source edits")
    spec = importlib.util.spec_from_file_location("c925_native_prechecked", PREVIOUS)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load exact prior native runner")
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    return old


def install_compatibility_guards(old):
    old.CURRENT_ELF_SHA = "2681b63b89bf808fa7307d633e9264eb80b7f102c640cbdad8b883dd88dcc7cc"
    old.OLD_ELF_SAVE = (old.ROOT /
        "candidates/c925-20261027-193333-hotlog-bindreject/shadps4")
    old.PATCH_REV = "b1989ff34605b60adefb40ee6737bf84cd5ac3a9"
    old.PATCH_PATH = "scripts/patchsets/c925-indexed-mode-hotlog-clean-20261027.patch"
    old.PATCH_BLOB = "aa57c341c9375cea53e87e66981c21290c0bcd4c"
    old.TEST_REV = "dc4ce0f72d5a83d18df973e5647ae7d22a32d30d"
    old.TEST_PATH = "tests/c925_indexed_sample/test_mode_and_perf.cpp"
    old.TEST_BLOB = "032b1e4563657283a7e2da49399e086de0a0829d"
    old.FILES = FILES
    old.OTHER_PROVEN = OTHER_PROVEN

    saved_preflight = old.preflight
    def checked_preflight(m):
        binary = saved_preflight(m)
        m.STATE["record_type"] = "GOT_C925_193333_STRICT_INDEXED_MODE_AND_AJM_VMM_RESIDENCY_PERF"
        m.STATE["source_semantics"] = ("Unsupported sampled-image modes are excluded from "
            "indexed resource discovery only, matching existing Vulkan binding restrictions")
        m.STATE["source_patch_git_blob"] = old.PATCH_BLOB
        return binary
    old.preflight = checked_preflight

    saved_scratch = old.scratch_patch_roundtrip
    def checked_scratch(m, patch):
        scratch = saved_scratch(m, patch)
        gate = old.SOURCE / "src/common/log_occurrence_gate.h"
        if not gate.is_file() or gate.is_symlink() or m.sha256(gate) != OTHER_PROVEN[
                "src/common/log_occurrence_gate.h"]:
            raise m.Abort("MISSING_OR_CHANGED_EXISTING_LOG_OCCURRENCE_GATE", 2)
        destination = scratch / "src/common/log_occurrence_gate.h"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise m.Abort("SCRATCH_LOG_GATE_COLLISION", 2)
        shutil.copyfile(gate, destination)
        return scratch
    old.scratch_patch_roundtrip = checked_scratch

    def tested_eligibility_fixture(m, scratch):
        m.stage("TEST_REAL_INDEXED_SAMPLE_MODE_HEADER_AND_HOST_HOTLOG_GCC_CLANG")
        fixture = old.stage_file(m, "indexed-mode-perf.cpp", old.TEST_REV,
                                  old.TEST_PATH, old.TEST_BLOB)
        for compiler in ("g++", "clang++"):
            if not shutil.which(compiler):
                if compiler == "g++":
                    raise m.Abort("GCC_COMPILER_FOR_SHADER_FIXTURE_NOT_AVAILABLE", 2)
                continue
            name = compiler.replace("+", "p")
            exe = m.OUT / ("indexed-mode-perf-" + name)
            m.command("index-perf-build-" + name, [
                compiler, "-std=c++20", "-O2", "-DNDEBUG", "-Wall",
                "-Wextra", "-Werror", "-pedantic", "-pthread",
                "-I", str(scratch / "src"), str(fixture), "-o", str(exe)], timeout=90)
            m.command("index-perf-test-" + name, [str(exe)], timeout=25)
            output = (m.OUT / ("index-perf-test-" + name + ".log")).read_text()
            if ("INDEXED_SAMPLE_MODE_256_CASES=PASS" not in output or
                    "HOTLOG_500000_SERIAL_160000_PARALLEL=PASS" not in output):
                raise m.Abort("INDEXED_ELIGIBILITY_NATIVE_CXX_RELEASE_TEST_FAILED", 2)
            print("INDEXED_ELIGIBILITY_CXX_RELEASE_PASS=" + compiler, flush=True)
        m.STATE["genuine_sample_mode_and_hotlog_release_regression"] = "PASS"
    old.native_matcher_fixture = tested_eligibility_fixture

    saved_summary = old.summarize_runtime
    def detailed_summary(m):
        saved_summary(m)
        path = m.OUT / "game-console.log"
        if not path.is_file():
            return
        raw = path.read_text(errors="replace")
        counts = {
            "ajm_create": raw.count("sceAjmInstanceCreate: called context"),
            "ajm_destroy": raw.count("sceAjmInstanceDestroy: called context"),
            "ajm_context_create": raw.count("InstanceCreate: instance ="),
            "vmm_mapping": raw.count("sceKernelMapNamedDirectMemory: in_addr"),
            "buffer_residency": raw.count("EnsureResident: Making range start"),
            "indexed_source": raw.count("IndexedStridedSource:"),
            "binding_rejections": raw.count("INDEXED_MATERIAL_BIND_REJECT"),
            "sharp_unflattened": raw.count("Sharp source was not flatenned"),
            "srt_stalls": raw.count("SRT dependency resolution stalled"),
            "vulkan_device_lost": raw.count("ErrorDeviceLost") + raw.count("context is lost"),
            "log_lines": len(raw.splitlines())
        }
        m.STATE["full_performance_counters"] = counts
        (m.OUT / "indexed-mode-performance-comparison.json").write_text(
            json.dumps(counts, indent=2) + "\n")
        print("INDEXED_MODE_AND_HOST_PERF=" + json.dumps(counts), flush=True)
    old.summarize_runtime = detailed_summary
    return old


def main():
    try:
        old = install_compatibility_guards(load_verified_previous())
    except BaseException as error:
        print("PRE_RUN_INTEGRITY_FAILURE=" + str(error), file=sys.stderr, flush=True)
        return 2
    return old.main()


if __name__ == "__main__":
    sys.exit(main())
