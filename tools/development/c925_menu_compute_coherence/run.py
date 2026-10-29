#!/usr/bin/env python3
"""C925 menu fills: preserve GPU-produced compute outputs and coherent clear values.

Stacks the exact user-tested backing-complete candidate on the proven C925
source, then changes only the general renderer compute-clear shortcuts.
No title/hash exceptions, shader data fabrication, auto input, CMake regen,
Sunshine/X11/network actions, or main/ES-DE overwrite.
"""
from __future__ import annotations

import datetime as dt
import difflib
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import traceback
from pathlib import Path

P = Path.home() / "projects/shadps4"
BASE = P / "GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z"
SOURCE = BASE / "source"
BUILD = BASE / "build"
ELF = BUILD / "shadps4"
PREVIOUS = P / "logs/got-c925-backing-complete-20261029T194733066277Z"
PRIOR_MENU = P / "logs/got-c925-menu-fills-20261029T154222818563Z"
ACCEPTED = P / "logs/got-c925-sparse-host-limit-20261029T075648223982Z"
PROVEN_ELF = "89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec"
PROVEN_RASTER = "ebed2441ceda3d6b3e6a6ebe1afe7469ec35f9d8a99c3b7f0feb1a3fb533b4a0"
PROVEN_RASTER_OBJECT = "201ca853815ceda82dea3e33857158167d2f9438dd658723cc3f90f83ea18cd0"
PREVIOUS_ELF = "a1925e03dc4483b7c529568b0d4d5e3e61c9d329cf1143288157f4419b63ba7a"
DIFFS = {
    "memory_src": ("memory_src.diff", "555aad1837b6092805769e66c5dcae7bbccb2e54f3d3cf95535131701a096490"),
    "buffer_src": ("buffer_src.diff", "0791e7a8df19c270944c1b5e7c1a1705e6e20df0deea6388dcab058e2d4f04c6"),
}
HELPERS = {
    "direct_ninja.py": "cba0f46505d188c422297f49ad1d03aacad0442dbacca64b2651531b73f77d09",
    "run_draw_probe.py": "049bb4863d20af7da11460367bf786375b3ba7b37738f57f7fe325dad5fadf7a",
    "resume_c925_revalidation.py": "df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250",
    "resume_finite_raw.py": "c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b",
    "menu_validation_runtime.py": "7467dbc881babc9d36d53ff6f169bec112e7f97918a827cc17a0bfde47740f50",
}
FILES = {
    "memory_src": SOURCE / "src/core/memory.cpp",
    "buffer_src": SOURCE / "src/video_core/buffer_cache/buffer_cache.cpp",
    "raster_src": SOURCE / "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
    "memory_obj": BUILD / "CMakeFiles/shadps4.dir/src/core/memory.cpp.o",
    "buffer_obj": BUILD / "CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o",
    "raster_obj": BUILD / "CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o",
    "elf": ELF,
}
LOCKS = (
    ".got-pr95-scalar-bounds-build.lock", ".got-indexed-revalidation.lock",
    ".got-finite-raw-image.lock", ".got-uniform-samples.lock",
    ".got-indirect-materials.lock", ".got-sparse-host-limit.lock",
    ".got-menu-resource-validation.lock", ".got-menu-fills.lock",
    ".got-fragment-draw-trace.lock", ".got-descriptor-fetch-fix.lock",
    ".got-texture-overlap-fix.lock", ".got-gpu-image-resize.lock",
    ".got-gpu-backing-complete.lock", ".got-menu-compute-coherence.lock",
)
VALIDATION = ("vkvalidation_enabled", "vkvalidation_core_enabled",
              "vkvalidation_sync_enabled", "vkvalidation_gpu_enabled")


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load(helper, name):
    file = helper / name
    check(file.is_file() and not file.is_symlink() and sha(file) == HELPERS[name],
          "HELPER_SHA_CHANGED:" + name)
    spec = importlib.util.spec_from_file_location("c925_" + file.stem, file)
    check(spec is not None and spec.loader is not None, "HELPER_IMPORT_INVALID:" + name)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def function_section(src, start, end):
    check(src.count(start) == 1 and src.count(end) == 1,
          "RENDERER_FUNCTION_NOT_UNIQUE:" + start)
    a = src.index(start)
    b = src.index(end, a + len(start))
    check(a < b, "RENDERER_FUNCTION_ORDER_CHANGED")
    return a, b, src[a:b]


def change_once(section, old, new, tag):
    check(section.count(old) == 1, "RENDERER_SOURCE_GATE_" + tag + "_" + str(section.count(old)))
    return section.replace(old, new, 1)


def patch_renderer(src):
    meta_start = "bool Rasterizer::IsComputeMetaClear("
    meta_end = "bool Rasterizer::IsComputeImageCopy("
    clear_start = "bool Rasterizer::IsComputeImageClear("
    clear_end = "void Rasterizer::BindBuffers("
    if "MetaClearMultiOutputPreserved" in src or "ComputeImageClearSourceGpuSync" in src:
        raise RuntimeError("COMPUTE_COHERENCE_ALREADY_PATCHED")
    a, b, meta = function_section(src, meta_start, meta_end)
    meta_anchor = "    const auto& info = pipeline->GetStage(Shader::SwStage::Compute);"
    meta_guard = """
    // A metadata clear shortcut must not drop independent GPU image/buffer
    // outputs from the same compute dispatch. Decide before calling ClearMeta,
    // because that call mutates tracked state.
    const auto report_multi_output = [&] {
        static std::atomic<u32> count{};
        const u32 n = count.fetch_add(1, std::memory_order_relaxed) + 1;
        if (n <= 8 || (n & (n - 1)) == 0) {
            LOG_INFO(Render_Vulkan,
                     "MetaClearMultiOutputPreserved event={} shader={:#x} buffers={} images={}",
                     n, info.pgm_hash, info.buffers.size(), info.images.size());
        }
    };
    if (!info.images.empty()) {
        report_multi_output();
        return false;
    }
    for (const auto& desc : info.buffers) {
        if (!desc.is_written) continue;
        if (desc.IsSpecial() || !texture_cache.IsMeta(desc.GetSharp(info).base_address)) {
            report_multi_output();
            return false;
        }
    }
"""
    meta = change_once(meta, meta_anchor, meta_anchor + meta_guard, "META_GUARD")
    src = src[:a] + meta + src[b:]
    a, b, clear = function_section(src, clear_start, clear_end)
    clear_anchor = "    // Perform image clear"
    clear_fix = """
    // This 16-byte clear color may have been computed by the guest GPU.
    // Read back its actual bytes before building a host-side vk::ClearValue.
    if (!buf0.base_address || !memory->IsValidMapping(buf0.base_address, 16)) {
        return false;
    }
    if (buffer_cache.IsRegionGpuModified(buf0.base_address, 16)) {
        buffer_cache.ReadMemory(buf0.base_address, 16, false, false, true);
        if (buffer_cache.IsRegionGpuModified(buf0.base_address, 16)) {
            // Incomplete GPU-to-CPU synchronization: execute original shader
            // rather than clearing the image with stale CPU bytes.
            static std::atomic<u32> pending{};
            const u32 n = pending.fetch_add(1, std::memory_order_relaxed) + 1;
            if (n <= 8 || (n & (n - 1)) == 0) {
                LOG_WARNING(Render_Vulkan,
                            "ComputeImageClearSourceUnresolved event={} bytes=16", n);
            }
            return false;
        }
        static std::atomic<u32> synchronized{};
        const u32 n = synchronized.fetch_add(1, std::memory_order_relaxed) + 1;
        if (n <= 8 || (n & (n - 1)) == 0) {
            LOG_INFO(Render_Vulkan, "ComputeImageClearSourceGpuSync event={} bytes=16", n);
        }
    }
"""
    clear = change_once(clear, clear_anchor, clear_fix + "\n" + clear_anchor, "CLEAR_COLOR_SYNC")
    existing = "    runtime.ClearImage(&image1, range, clear);"
    note = """
    static std::atomic<u32> clear_reports{};
    const u32 clear_n = clear_reports.fetch_add(1, std::memory_order_relaxed) + 1;
    if (clear_n <= 8 || (clear_n & (clear_n - 1)) == 0) {
        LOG_INFO(Render_Vulkan, "ComputeImageClearApplied event={} bytes={} shader={:#x}",
                 clear_n, buf1.GetSize(), info.pgm_hash);
    }
"""
    clear = change_once(clear, existing, existing + note, "CLEAR_PATH_COUNT")
    src = src[:a] + clear + src[b:]
    check(src.count("MetaClearMultiOutputPreserved") == 1 and
          src.count("ComputeImageClearSourceGpuSync") == 1 and
          src.count("ComputeImageClearApplied") == 1,
          "RENDERER_PATCH_COMPLETENESS")
    return src


def selftest():
    fixture = """#include <atomic>
bool Rasterizer::IsComputeMetaClear(const Pipeline* pipeline) {
    const auto& info = pipeline->GetStage(Shader::SwStage::Compute);
    if (!info.has_bitwise_xor) {
        for (const auto& desc : info.buffers) {
            if (!desc.IsSpecial() && desc.is_written && texture_cache.ClearMeta(desc.GetSharp(info).base_address)) {
                return true;
            }
        }
    }
    return false;
}
bool Rasterizer::IsComputeImageCopy(const Pipeline* pipeline) { return false; }
bool Rasterizer::IsComputeImageClear(const Pipeline* pipeline) {
    const auto& info = pipeline->GetStage(Shader::SwStage::Compute);
    const auto buf0 = desc0.GetSharp(info);
    const auto buf1 = desc1.GetSharp(info);
    // Perform image clear
    const float* values = reinterpret_cast<float*>(buf0.base_address);
    runtime.ClearImage(&image1, range, clear);
    return true;
}
void Rasterizer::BindBuffers(const Shader::Info& stage) {}
"""
    changed = patch_renderer(fixture)
    check(all(x in changed for x in
              ("MetaClearMultiOutputPreserved", "ComputeImageClearSourceGpuSync",
               "ComputeImageClearSourceUnresolved", "ComputeImageClearApplied")),
          "COMPUTE_COHERENCE_FIXTURE_FAILED")
    check(changed.index("if (!info.images.empty())") < changed.index("texture_cache.ClearMeta("),
          "META_CLEAR_SIDE_EFFECT_BEFORE_GUARD")
    try:
        patch_renderer(changed)
    except RuntimeError as exc:
        check(str(exc) == "COMPUTE_COHERENCE_ALREADY_PATCHED", "DOUBLE_PATCH_WRONG_ERROR")
    else:
        raise RuntimeError("DOUBLE_PATCH_ACCEPTED")
    print("COMPUTE_CLEAR_AND_METADATA_FIXTURES=PASS", flush=True)


def backing_sources_preflight(staging, previous):
    for rel in ("src/core/memory.cpp", "src/video_core/buffer_cache/buffer_cache.cpp"):
        src = SOURCE / rel
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    patches = []
    for key, (name, expected) in DIFFS.items():
        patch = PREVIOUS / name
        check(patch.is_file() and not patch.is_symlink() and sha(patch) == expected,
              "PREVIOUS_BACKING_DIFF_MISSING_OR_CHANGED:" + key)
        patches.append(patch)
    for patch in patches:
        check(subprocess.run(["git", "apply", "--check", str(patch)], cwd=staging,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             text=True, timeout=15).returncode == 0,
              "PREVIOUS_CORRECTION_NO_LONGER_APPLIES:" + patch.name)
        result = subprocess.run(["git", "apply", str(patch)], cwd=staging,
                                capture_output=True, text=True, timeout=15)
        check(result.returncode == 0, "BACKING_DIFF_PREVIEW_FAILED:" + result.stderr[:800])
    for key, rel in (("memory_src", "src/core/memory.cpp"),
                     ("buffer_src", "src/video_core/buffer_cache/buffer_cache.cpp")):
        check(sha(staging / rel) == previous["candidate_sha256"][key],
              "PREVIOUS_BACKING_CORRECTION_SOURCE_MISMATCH:" + key)
    print("PREVIOUS_BACKING_CORRECTION_BYTE_IDENTICAL=PASS", flush=True)


def restore_profile(menu, original, destination):
    copied = menu.copy_profile(original, destination)
    matched = []
    for rel in ("config.json", "custom_configs/CUSA11456.json"):
        before = original / rel
        after = destination / rel
        if not after.is_file():
            continue
        control = json.loads(before.read_text()).get("Vulkan", {})
        new = json.loads(after.read_text())
        settings = new.setdefault("Vulkan", {})
        for key in VALIDATION:
            if key in control:
                settings[key] = control[key]
            else:
                settings.pop(key, None)
        check(not any(settings.get(key, False) for key in VALIDATION),
              "MENU_VALIDATION_MUST_MATCH_WORKING_PROFILE")
        save(after, new)
        matched.append(rel)
    check(matched, "ISOLATED_GAME_CONFIG_MISSING")
    return dict(copied=copied, validation_matches_working=matched)


def main(helper):
    os.umask(0o077)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    evidence = P / "logs" / ("got-c925-menu-compute-coherence-" + stamp)
    evidence.mkdir(parents=True, exist_ok=False)
    checkpoint = BASE / "checkpoints" / ("menu-compute-coherence-" + stamp)
    state = dict(record_id=evidence.name, journal_scope="UNIVERSAL_SHADPS4",
                 game="CUSA11456", build="NOT_RUN", game_test="NOT_RUN",
                 change="Preserve compute metadata multi-outputs and coherent GPU clear constants",
                 stacked_existing_fix="backing-complete-20261029T194733066277Z",
                 promoted=False, graphics="PENDING_VISUAL_REVIEW",
                 keep_or_revert="KEEP_VERIFIED_CORRECTNESS_CANDIDATE_SEPARATE_FROM_BASELINE")
    before, after = {}, {}
    locks, pending = [], []
    touched, rc = False, 1
    print("EVIDENCE_DIRECTORY=" + str(evidence), flush=True)
    try:
        selftest()
        direct = load(helper, "direct_ninja.py")
        draw = load(helper, "run_draw_probe.py")
        u = load(helper, "resume_c925_revalidation.py")
        hr = load(helper, "resume_finite_raw.py")
        menu = load(helper, "menu_validation_runtime.py")
        api = u.Ollama(evidence)
        for name in LOCKS:
            f = (P / "logs" / name).open("a+")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                f.close()
                raise RuntimeError("SHARED_BUILD_LOCK_BUSY:" + name)
            locks.append(f)
        check(not u.emulator_pids(), "OTHER_EMULATOR_RUNNING")
        for name, path in FILES.items():
            check(path.is_file() and not path.is_symlink(), "CHECKPOINT_FILE_MISSING:" + name)
            before[name] = sha(path)
        check(len(before) == len(set(before)), "CHECKPOINT_NAME_COLLISION")
        check(before["elf"] == PROVEN_ELF, "PROVEN_ELF_CHANGED")
        check(before["raster_src"] == PROVEN_RASTER and
              before["raster_obj"] == PROVEN_RASTER_OBJECT,
              "PROVEN_RASTER_SOURCE_OR_OBJECT_CHANGED")
        accepted = json.loads((ACCEPTED / "trial.json").read_text())
        check(accepted["build"] == "PASS" and accepted["candidate_sha256"] == PROVEN_ELF,
              "ACCEPTED_SOURCE_MANIFEST_INVALID")
        for rel, meta in accepted["source_manifest"].items():
            p = SOURCE / rel
            check(p.is_file() and sha(p) == meta["after"], "ACCEPTED_FIX_CHANGED:" + rel)
        prior = json.loads((PREVIOUS / "trial.json").read_text())
        check(prior.get("build") == "PASS" and
              prior["candidate_sha256"]["elf"] == PREVIOUS_ELF and
              prior["game_test"]["time_limit_reached"], "PREVIOUS_BACKING_RUN_NOT_VERIFIED")
        retained = Path(prior["candidate_directory"]) / "shadps4"
        check(retained.is_file() and sha(retained) == PREVIOUS_ELF,
              "RETAINED_BACKING_CANDIDATE_CHANGED")
        protected = BASE.parent / "c925-integrated-gpu-20261027-122701/build/shadps4"
        check(protected.is_file(), "SECONDARY_PROVEN_ELF_MISSING")
        protected_hash = sha(protected)
        staging = evidence / "patch-preflight"
        backing_sources_preflight(staging, prior)
        raster_preimage = FILES["raster_src"].read_text()
        raster_after = patch_renderer(raster_preimage)
        (evidence / "raster.patch").write_text("".join(difflib.unified_diff(
            raster_preimage.splitlines(True), raster_after.splitlines(True),
            "a/src/video_core/renderer_vulkan/vk_rasterizer.cpp",
            "b/src/video_core/renderer_vulkan/vk_rasterizer.cpp")))
        state["source_manifest"] = {"raster_before": before["raster_src"],
                                    "memory_after": prior["candidate_sha256"]["memory_src"],
                                    "buffer_after": prior["candidate_sha256"]["buffer_src"]}
        # Check compile/link commands for ALL three objects before editing.
        plans = {}
        for name, object_name in (("memory_src", "memory_obj"),
                                  ("buffer_src", "buffer_obj"),
                                  ("raster_src", "raster_obj")):
            plans[name] = direct.parse(BUILD, FILES[name], FILES[object_name], ELF, evidence)
        check(plans["memory_src"][1] == plans["buffer_src"][1] ==
              plans["raster_src"][1], "INCONSISTENT_NINJA_LINK_PLANS")
        menu_trial = json.loads((PRIOR_MENU / "trial.json").read_text())
        old_profile = Path(menu_trial["private_profile"]).resolve()
        check(old_profile.is_relative_to((BASE / "runs").resolve()) and
              old_profile.name == "user" and
              menu_trial["game"]["time_limit_reached"],
              "PROVEN_PRIVATE_MENU_PROFILE_INVALID")
        eboot = Path("/mnt/roms-all/ps4-games/CUSA11456/eboot.bin")
        check(eboot.is_file(), "GAME_EBOOT_MISSING")
        display = os.getenv("DISPLAY") or ":0"
        u.command(["env", "DISPLAY=" + display, "xdpyinfo"], timeout=8)
        checkpoint.mkdir(parents=True, exist_ok=False)
        for name, path in FILES.items():
            shutil.copy2(path, checkpoint / name)
            check(sha(checkpoint / name) == before[name], "CHECKPOINT_FAILED:" + name)
        # Only after every gate passes: install exactly the tested diffs.
        replacements = {
            "memory_src": (staging / "src/core/memory.cpp").read_text(),
            "buffer_src": (staging / "src/video_core/buffer_cache/buffer_cache.cpp").read_text(),
            "raster_src": raster_after,
        }
        touched = True
        for name, text in replacements.items():
            path = FILES[name]
            temp = path.with_name(path.name + ".menu-coherence-pending")
            check(not temp.exists() and not temp.is_symlink(), "TEMP_SOURCE_COLLISION:" + name)
            temp.write_text(text)
            shutil.copymode(path, temp)
            os.replace(temp, path)
            after[name] = sha(path)
        print("STAGE=COMPILE_3_EXISTING_OBJECTS", flush=True)
        for name, obj_name in (("memory_src", "memory_obj"),
                               ("buffer_src", "buffer_obj"),
                               ("raster_src", "raster_obj")):
            menu.logged(u, ["bash", "-c", direct.command_string(plans[name][0])],
                        evidence / (obj_name + "-compile.log"),
                        "COMPILE_" + obj_name, timeout=900)
            after[obj_name] = sha(FILES[obj_name])
        print("STAGE=RELINK_TEST_BINARY", flush=True)
        menu.logged(u, ["bash", "-c", direct.command_string(plans["raster_src"][1])],
                    evidence / "link.log", "RELINK_MENU_COMPUTE_CORRECTION", timeout=900)
        after["elf"] = sha(ELF)
        check(after["elf"] != PROVEN_ELF, "CANDIDATE_ELF_UNCHANGED")
        state["build"] = "PASS"
        state["candidate_sha256"] = after.copy()
        candidate = BASE / "candidates" / ("menu-compute-coherence-" + stamp)
        candidate.mkdir(parents=True, exist_ok=False)
        shutil.copy2(ELF, candidate / "shadps4")
        state["candidate_directory"] = str(candidate)
        profile = BASE / "runs" / ("menu-compute-coherence-" + stamp) / "user"
        state["private_profile"] = str(profile)
        state["profile_check"] = restore_profile(menu, old_profile, profile)
        state["vram_before_bytes"] = u.free_vram()
        print("STAGE=VRAM_8_GIB_CONDITIONAL_OLLAMA", flush=True)
        hr.verified_headroom(u, api, evidence, pending, state)
        check(u.free_vram() >= hr.MIN_FREE, "GPU_HEADROOM_BELOW_8_GIB")
        check(not u.emulator_pids(), "OTHER_EMULATOR_STARTED")
        print("STAGE=GAME_118_PLUS_2_NO_INPUT_MENU_ONLY", flush=True)
        state["game_test"] = u.run_game(ELF, eboot, profile.parent, evidence, display)
        log = (evidence / "game.log").read_text(errors="replace")
        patterns = ("MetaClearMultiOutputPreserved", "ComputeImageClearSourceGpuSync",
                    "ComputeImageClearSourceUnresolved", "ComputeImageClearApplied",
                    "BackingWriteCompleteVerified", "BackingWriteIncomplete",
                    "BackingReadbackDirtyRetained", "GpuDescriptorTableCpuCoherence",
                    "INDEXED_MATERIAL_BIND_REJECT", "Sharp source was not flatenned",
                    "SYNC-HAZARD", "ErrorDeviceLost")
        state["runtime_counts"] = {x: log.count(x) for x in patterns}
        state["screenshots"] = {
            str(t): {"size": (evidence / ("menu-" + str(t) + ".png")).stat().st_size}
            for t in (40, 55, 85, 112) if (evidence / ("menu-" + str(t) + ".png")).is_file()
        }
        state["status"] = "CANDIDATE_TESTED_VISUAL_PENDING" if state["game_test"]["time_limit_reached"] else "GAME_EXITED_EARLY"
        rc = 0 if state["game_test"]["time_limit_reached"] else 1
    except (Exception, KeyboardInterrupt) as exc:
        state["status"] = "FAILED:" + str(exc)
        (evidence / "failure.txt").write_text(traceback.format_exc())
        print("STOP=" + str(exc), flush=True)
    finally:
        if touched:
            try:
                print("STAGE=RESTORE_SOURCE_OBJECT_ELF", flush=True)
                # Refuse to overwrite an independent change to ANY of the
                # three source files, not only files literally named "source".
                unexpected = [
                    name for name in ("memory_src", "buffer_src", "raster_src")
                    if sha(FILES[name]) not in (before[name], after.get(name))
                ]
                if unexpected:
                    raise RuntimeError("EXTERNAL_SOURCE_CHANGE_REFUSED:" + ",".join(unexpected))
                state["restoration"] = draw.restore(tuple(FILES.items()), checkpoint, before, after)
                if any(v != "RESTORED_VERIFIED" for v in state["restoration"].values()):
                    rc = 1
            except Exception as exc:
                state["restore_exception"] = str(exc)
                rc = 1
        for name, path in FILES.items():
            if name in before:
                state[name + "_unchanged"] = path.is_file() and sha(path) == before[name]
                if not state[name + "_unchanged"]:
                    rc = 1
        if pending and "api" in locals():
            try:
                state["ollama_restore"] = u.restore_models(api, pending, evidence)
                if any(x["result"] in ("RESTORE_FAILED", "RESTORE_NOT_VERIFIED")
                       for x in state["ollama_restore"]):
                    rc = 1
            except Exception as exc:
                state["ollama_restore_exception"] = str(exc)
                rc = 1
        if "retained" in locals() and sha(retained) != PREVIOUS_ELF:
            state["previous_candidate_changed"] = True
            rc = 1
        if "protected_hash" in locals() and sha(protected) != protected_hash:
            state["secondary_proven_elf_changed"] = True
            rc = 1
        state["runner_exit_code"] = rc
        save(evidence / "trial.json", state)
        diary = P / "diary/trials.jsonl"
        diary.parent.mkdir(parents=True, exist_ok=True)
        with diary.open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(state, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        # Do not ship full source copies or private profile inside the archive.
        staging = evidence / "patch-preflight"
        if staging.is_dir():
            shutil.rmtree(staging)
        with tarfile.open(str(evidence) + ".tar.gz", "w:gz") as archive:
            archive.add(evidence, arcname=evidence.name)
        print("RESULT=" + state.get("status", "INCOMPLETE"), flush=True)
        print("UPLOAD_THIS=" + str(evidence) + ".tar.gz", flush=True)
        print("SSH_SESSION=OPEN", flush=True)
        for f in locks:
            f.close()
    return rc


if __name__ == "__main__":
    signal.signal(signal.SIGTERM,
                  lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if len(sys.argv) != 2:
        raise SystemExit("VERIFIED_HELPER_DIRECTORY_REQUIRED")
    sys.exit(main(Path(sys.argv[1])))
