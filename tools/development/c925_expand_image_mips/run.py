#!/usr/bin/env python3
"""C925: preserve mip levels and compatible layers during TextureCache::ExpandImage.

General correction derived from upstream shadps4-emu/shadPS4#5282.
Reuses the proven one-object build, private profile, 8-GiB/Ollama guard,
118+2-second game test, restore, and universal local diary.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tarfile
import fcntl
from pathlib import Path

P = Path.home() / "projects/shadps4"
BASE = P / "GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z"
SOURCE = BASE / "source/src/video_core/texture_cache/texture_cache.cpp"
TAG = "EXPANDED_IMAGE_PRESERVE_LEVELS_V1"


def patch_cpp(source: str) -> str:
    anchor = "ImageId TextureCache::ExpandImage("
    if source.count(anchor) != 1:
        raise RuntimeError("EXPAND_IMAGE_FUNCTION_NOT_UNIQUE")
    start = source.index(anchor)
    brace = source.find("{", start)
    if brace < 0:
        raise RuntimeError("EXPAND_IMAGE_BRACE_MISSING")
    depth, end = 0, None
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if not depth:
                end = i + 1
                break
    if end is None:
        raise RuntimeError("EXPAND_IMAGE_UNBALANCED")
    body = source[start:end]
    if TAG in body or ("expanded_info.resources.levels" in body and "expanded_info.resources.layers" in body):
        raise RuntimeError("EXPAND_IMAGE_ALREADY_FIXED")
    original = "const auto new_image_id = slot_images.Insert(instance, runtime, slot_image_views, info);"
    if body.count(original) != 1:
        raise RuntimeError("EXPAND_IMAGE_INSERT_SHAPE_CHANGED")
    replacement = """// EXPANDED_IMAGE_PRESERVE_LEVELS_V1: keep previously resident mip levels.
    ImageInfo expanded_info = info;
    const auto& cached_info = slot_images[image_id].info;
    const u32 max_levels = static_cast<u32>(
        std::bit_width(std::max({info.size.width, info.size.height,
                                 info.type == AmdGpu::ImageType::Color3D ? info.size.depth : 1u})));
    expanded_info.resources.levels = static_cast<u16>(std::min<u32>(
        std::max<u32>(info.resources.levels, cached_info.resources.levels), max_levels));
    if (info.type == cached_info.type && info.type != AmdGpu::ImageType::Color3D) {
        expanded_info.resources.layers =
            std::max(info.resources.layers, cached_info.resources.layers);
    }
    if (expanded_info.resources != info.resources) {
        expanded_info.UpdateSize();
        static std::atomic<u32> preserved{};
        const u32 count = preserved.fetch_add(1, std::memory_order_relaxed) + 1;
        if (count <= 8 || std::has_single_bit(count)) {
            LOG_INFO(Render_Vulkan,
                     "ExpandedImagePreserved event={} old_mips={} requested_mips={} "
                     "new_mips={} old_layers={} requested_layers={} new_layers={}",
                     count, cached_info.resources.levels, info.resources.levels,
                     expanded_info.resources.levels, cached_info.resources.layers,
                     info.resources.layers, expanded_info.resources.layers);
        }
    }
    const auto new_image_id =
        slot_images.Insert(instance, runtime, slot_image_views, expanded_info);"""
    updated_body = body.replace(original, replacement, 1)
    out = source[:start] + updated_body + source[end:]
    for header in ("#include <algorithm>", "#include <bit>", "#include <atomic>"):
        if header not in out:
            if "#include <xxhash.h>" not in out:
                raise RuntimeError("EXPAND_IMAGE_INCLUDE_ANCHOR_CHANGED")
            out = out.replace("#include <xxhash.h>", header + "\n#include <xxhash.h>", 1)
    if out.count(TAG) != 1 or out.count("ExpandedImagePreserved event=") != 1:
        raise RuntimeError("EXPAND_IMAGE_PATCH_OUTPUT_INVALID")
    return out


def unit_tests() -> None:
    fixture = """#include <xxhash.h>
ImageId TextureCache::ExpandImage(const ImageInfo& info, ImageId image_id) {
    const auto new_image_id = slot_images.Insert(instance, runtime, slot_image_views, info);
    RegisterImage(new_image_id);
    auto& src_image = slot_images[image_id];
    return new_image_id;
}
"""
    out = patch_cpp(fixture)
    assert "cached_info.resources.levels" in out
    assert "std::bit_width" in out
    assert "std::max(info.resources.layers, cached_info.resources.layers)" in out
    assert out.count("ExpandedImagePreserved event=") == 1
    assert all(out.count(h) == 1 for h in ("#include <algorithm>", "#include <bit>", "#include <atomic>"))
    try:
        patch_cpp(out)
    except RuntimeError as ex:
        assert str(ex) == "EXPAND_IMAGE_ALREADY_FIXED"
    else:
        raise AssertionError("DUPLICATE_EXPAND_IMAGE_PATCH")
    for old, req, max_levels, expected in ((4, 1, 8, 4), (1, 4, 8, 4),
                                           (7, 3, 4, 4), (1, 1, 1, 1)):
        assert min(max(old, req), max_levels) == expected
    for old, req in ((8, 12), (12, 8), (1, 4)):
        assert max(old, req) >= old and max(old, req) >= req
    print("EXPAND_IMAGE_UNIT_TESTS=PASS", flush=True)


def journal_no_build(reason: str, source: str) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    evidence = P / "logs" / ("got-expand-image-preflight-" + stamp)
    evidence.mkdir(parents=True, exist_ok=False)
    (evidence / "actual-expand-image-context.txt").write_text(
        source[source.index("ImageId TextureCache::ExpandImage("):
               source.index("ImageId TextureCache::ExpandImage(") + 2200]
    )
    record = dict(record_id=evidence.name, journal_scope="UNIVERSAL_SHADPS4",
                  change="Preserve cached mip levels/layers during ExpandImage",
                  status=reason, build="NOT_RUN", game_test="NOT_RUN",
                  source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                  keep_or_revert="NO_CHANGE")
    (evidence / "trial.json").write_text(json.dumps(record, indent=2) + "\n")
    diary = P / "diary/trials.jsonl"
    diary.parent.mkdir(parents=True, exist_ok=True)
    with diary.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(record) + "\n")
        f.flush()
        os.fsync(f.fileno())
    with tarfile.open(str(evidence) + ".tar.gz", "w:gz") as t:
        t.add(evidence, arcname=evidence.name)
    print("RESULT=" + reason + "\nEVIDENCE=" + str(evidence) + ".tar.gz\nSSH_SESSION=OPEN", flush=True)


def main() -> int:
    unit_tests()
    if not SOURCE.is_file() or SOURCE.is_symlink():
        raise RuntimeError("EXPECTED_C925_TEXTURE_SOURCE_MISSING")
    original = SOURCE.read_text()
    try:
        modified = patch_cpp(original)
    except RuntimeError as exc:
        if str(exc) == "EXPAND_IMAGE_ALREADY_FIXED":
            journal_no_build(str(exc), original)
            return 0
        journal_no_build("PREBUILD_REFUSED:" + str(exc), original)
        return 2
    assert modified != original
    candidates = sorted((P / "scripts").glob("got-texture-overlap.*/run.py"))
    candidate = None
    for f in candidates:
        text = f.read_text() if f.is_file() and not f.is_symlink() else ""
        if "def run(work):" in text and "PROVEN_ELF_HASH_CHANGED" in text and "texture-overlap-fix-" in text:
            candidate = f
            break
    if candidate is None:
        raise RuntimeError("PREVIOUS_PROVEN_ROLLBACK_RUNNER_MISSING")
    base_code = candidate.read_text()
    changes = {
        "Componentwise mip/layer image-containment and incompatible-overlap correction":
            "Preserve previous mip levels and compatible layers during image expansion",
        "https://github.com/shadps4-emu/shadPS4/pull/5457":
            "https://github.com/shadps4-emu/shadPS4/pull/5282",
        "texture-overlap-fix-": "expand-image-mip-preservation-",
    }
    for before, after in changes.items():
        if before not in base_code:
            raise RuntimeError("REUSABLE_RUNNER_VERSION_CHANGED:" + before)
        base_code = base_code.replace(before, after)
    d = candidate.parent
    namespace = {"__name__": "got_expand_image_mip_candidate", "__file__": str(candidate)}
    exec(compile(base_code, str(candidate), "exec"), namespace)
    if namespace["SRC"].resolve() != SOURCE.resolve():
        raise RuntimeError("RUNNER_SOURCE_PATH_UNEXPECTED")
    namespace["patch"] = patch_cpp
    namespace["unit"] = unit_tests
    print("ACTUAL_C925_SOURCE_PATCH=PASS", flush=True)
    print("VRAM=8_GiB_MINIMUM_OLLAMA_CONDITIONAL_UNLOAD_RESTORE", flush=True)
    print("BUILD=ONE_OBJECT_RELINK", flush=True)
    print("GAME=GOT_MENU_MAX_120_SECONDS_INPUT_NONE", flush=True)
    print("RESTORE=EXACT_SOURCE_OBJECT_ELF", flush=True)
    print("DIARY=UNIVERSAL", flush=True)
    return namespace["run"](d)


if __name__ == "__main__":
    sys.exit(main())
