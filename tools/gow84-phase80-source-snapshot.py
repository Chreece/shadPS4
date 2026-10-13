#!/usr/bin/env python3
"""Read-only GoW84 merge evidence: the exact eight locally modified Phase80 sources.
No build, no emulator, no git changes, no save access, no SSH process handling.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

BASE = Path.home() / "projects/shadps4/GodOfWarRagnarok/builds/gow80-sparse-queue-20261013-145953-3525134"
LOGS = Path.home() / "projects/shadps4/logs"
RASTER_SHA = "e20d7f2705e087d7feae54bc9a1a449b6554101c3174f3bb7774e194baf2f212"
BINARY_SHA = "03d9cfad127eb0d08d40fdb7f8dd7494ea0f1e2ade87363afd6b0e929e6960f3"
FILES = (
    "src/shader_recompiler/backend/spirv/emit_spirv_warp.cpp",
    "src/shader_recompiler/frontend/control_flow_graph.cpp",
    "src/shader_recompiler/ir/opcodes.inc",
    "src/shader_recompiler/ir/passes/lower_wave64_pass.cpp",
    "src/video_core/buffer_cache/buffer_cache.h",
    "src/video_core/renderer_vulkan/vk_instance.cpp",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    cli = argparse.ArgumentParser()
    cli.add_argument("--self-test", action="store_true")
    args = cli.parse_args()
    if not (len(FILES) == len(set(FILES)) == 8 and
            all(x.startswith("src/") and ".." not in Path(x).parts for x in FILES)):
        raise SystemExit("GOW84_SOURCE_LIST_INVALID")
    if args.self_test:
        print("GOW84_SOURCE_SNAPSHOT_SELF_TEST=PASS")
        return 0

    src = BASE / "source"
    assert src.is_dir(), "GoW Phase80 source missing"
    assert digest(BASE / "build/shadps4") == BINARY_SHA, "Phase80 binary checkpoint modified"
    assert digest(src / "src/video_core/renderer_vulkan/vk_rasterizer.cpp") == RASTER_SHA, (
        "Phase80 renderer checkpoint modified")
    sources = []
    for name in FILES:
        p = src / name
        if p.is_symlink() or not p.is_file():
            raise SystemExit("GOW84_MISSING_OR_SYMLINKED_SOURCE=" + name)
        if p.stat().st_size > 4000000:
            raise SystemExit("GOW84_UNEXPECTED_SOURCE_SIZE=" + name)
        sources.append((name, p, digest(p)))

    LOGS.mkdir(parents=True, exist_ok=True)
    outfile = LOGS / ("gow84-phase80-merge-input-" +
                      datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".tar.gz")
    manifest = {
        "purpose": "offline manual three-way conflict resolution (no compilation)",
        "phase80_binary_sha256": BINARY_SHA,
        "phase80_renderer_sha256": RASTER_SHA,
        "source_count": len(sources),
        "files": [{"path": name, "sha256": sha, "bytes": path.stat().st_size}
                  for name, path, sha in sources],
    }
    raw = json.dumps(manifest, indent=2, sort_keys=True).encode()
    with tarfile.open(outfile, "w:gz", compresslevel=6) as tar:
        for name, path, sha in sources:
            tar.add(path, arcname="phase80/" + name, recursive=False)
        info = tarfile.TarInfo("phase80/manifest.json")
        info.size = len(raw)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(raw))
    print("GOW84_PHASE80_MERGE_EVIDENCE=" + str(outfile), flush=True)
    print("GOW84_SOURCE_COUNT=8 PHASE80_UNCHANGED=TRUE NO_BUILD=TRUE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
