#!/usr/bin/env python3
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile


ANSI = re.compile(r"\x1b\[[0-9;]*[mK]")
FIELDS = re.compile(r"\b(\w+)=([^\s]+)")
SHADER_FILE = re.compile(r"^([a-z]{2})_0x([0-9a-fA-F]{1,16})(?=[._])")


def shader_id(stage, value):
    return f"{stage}:0x{int(value, 0):016x}"


def select_shaders(log):
    latest = {}
    reasons = defaultdict(set)
    draws = Counter()
    pairs = set()
    for raw in log.open(errors="replace"):
        line = ANSI.sub("", raw)
        row = dict(FIELDS.findall(line))
        if "FrameProbeCompute:" in line:
            reasons[shader_id("cs", row["shader"])].add("frame_compute")
        if "ResourceAuditCompile:" in line:
            key = shader_id(row["stage"], row["shader"])
            latest[key] = row
        if "GotMenuPassTop:" in line:
            draws[shader_id("fs", row["fs"])] += int(row.get("draws", "0"))
        if "GotMenuDrawTrace:" in line and "vs" in row and "fs" in row:
            pairs.add((shader_id("fs", row["fs"]), shader_id("vs", row["vs"])))
        if any(tag in line for tag in ("GotSceneGpuInvocations:", "GotSceneIndirectGpuArgs:",
                                       "FrameProbeDraw:")):
            for stage in ("fs", "vs"):
                if stage in row and int(row[stage], 16):
                    reasons[shader_id(stage, row[stage])].add("sampled_scene_draw")
    for key, row in latest.items():
        for resource in ("buffers", "images", "samplers"):
            if int(row.get("bad_" + resource, "0")):
                reasons[key].add("latest_compiled_unresolved_" + resource)
        if row.get("translation_failed") == "true":
            reasons[key].add("translation_failed")
    for key, _ in draws.most_common(12):
        reasons[key].add("high_volume_fragment")
    for fragment, vertex in pairs:
        if fragment in reasons:
            reasons[vertex].add("paired_vertex")
    return latest, reasons, draws


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def add_text(archive, name, text):
    data = text.encode()
    item = tarfile.TarInfo(name)
    item.size = len(data)
    item.mode = 0o644
    archive.addfile(item, io.BytesIO(data))


def collect(run, output):
    log = run / "game.log"
    dump_dir = run / "profile/user/shader/dumps"
    if not log.is_file():
        raise RuntimeError(f"Saved game log is missing: {log}")
    if not dump_dir.is_dir():
        raise RuntimeError(f"Saved full shader dumps are missing: {dump_dir}")
    latest, reasons, draws = select_shaders(log)
    if not reasons:
        raise RuntimeError("No resource failures or scene draws were found in the saved log")
    files = []
    coverage = defaultdict(Counter)
    total = 0
    for path in sorted(dump_dir.iterdir()):
        match = SHADER_FILE.match(path.name)
        if not match or path.is_symlink() or not path.is_file():
            continue
        key = shader_id(match[1], "0x" + match[2])
        if key not in reasons:
            continue
        size = path.stat().st_size
        if size > 64 * 1024 * 1024:
            raise RuntimeError(f"Selected shader file exceeds 64 MiB: {path.name}")
        total += size
        if total > 1024 * 1024 * 1024:
            raise RuntimeError("Selected shader evidence exceeds the 1 GiB archive input limit")
        kind = (
            "gcn" if re.search(r"_\d+\.bin$", path.name)
            else "userdata" if path.name.endswith(".ud.bin")
            else "spirv" if path.name.endswith(".spv")
            else "srt" if path.name.endswith(".srtprogram.txt")
            else "pre_resource_ir" if path.name.endswith(".pre-res-patch.irprogram.txt")
            else "other"
        )
        coverage[key][kind] += 1
        files.append((path, size, sha256(path)))
    if not files:
        raise RuntimeError("The saved dump directory has no files for the selected shaders")
    unresolved = {
        key for key, values in reasons.items()
        if values & {"latest_compiled_unresolved_images", "latest_compiled_unresolved_samplers"}
    }
    missing_gcn = sorted(key for key in unresolved if not coverage[key]["gcn"])
    missing_replay = sorted(
        key for key in unresolved
        if not all(coverage[key][kind] for kind in ("gcn", "userdata", "srt", "pre_resource_ir"))
    )
    manifest = {
        "run": str(run),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Recover existing shader evidence; no game launch or emulation change",
        "selection": "Latest compiled resource failures, sampled draws, top 12 fragments and paired vertices",
        "compiled_variants_are_not_proof_of_executed_samples": True,
        "dynamic_buffer_fallbacks_may_have_invalid_static_fetches": True,
        "selected_shaders": len(reasons),
        "unresolved_image_sampler_shaders": len(unresolved),
        "missing_unresolved_gcn": missing_gcn,
        "missing_unresolved_replay_inputs": missing_replay,
        "file_count": len(files),
        "uncompressed_shader_bytes": total,
        "shaders": {
            key: {"reasons": sorted(reasons[key]), "coverage": dict(coverage[key]),
                  "latest_compile": latest.get(key), "draw_window_total": draws[key]}
            for key in sorted(reasons)
        },
        "files": [{"name": path.name, "bytes": size, "sha256": digest}
                  for path, size, digest in files],
    }
    status = "COMPLETE" if not missing_replay else "PARTIAL_MISSING_REPLAY_INPUTS"
    summary = (
        f"STATUS={status}\nSELECTED_SHADERS={len(reasons)}\n"
        f"UNRESOLVED_IMAGE_SAMPLER_SHADERS={len(unresolved)}\n"
        f"MISSING_UNRESOLVED_GCN={len(missing_gcn)}\n"
        f"MISSING_UNRESOLVED_REPLAY_INPUTS={len(missing_replay)}\n"
        f"SHADER_FILES={len(files)}\nSHADER_BYTES={total}\n"
        "GAME_LAUNCHED=NO\nEMULATOR_REBUILT=NO\nORIGINAL_RUN_UNCHANGED=YES\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as destination:
        with tarfile.open(fileobj=destination, mode="w:gz", compresslevel=6) as archive:
            add_text(archive, "info/final.txt", summary)
            add_text(archive, "info/shader-manifest.json", json.dumps(manifest, indent=2) + "\n")
            archive.add(log, arcname="game.log", recursive=False)
            for name in ("final.txt", "scene-indirect-gpu-argument-summary.txt",
                         "scene-gpu-invocation-summary.txt", "indexed-image-feedback-summary.txt"):
                path = run / "info" / name
                if path.is_file() and not path.is_symlink():
                    archive.add(path, arcname="original-info/" + name, recursive=False)
            for path, size, digest in files:
                if path.stat().st_size != size or sha256(path) != digest:
                    raise RuntimeError(f"Saved shader changed during collection: {path}")
                archive.add(path, arcname="shaders/" + path.name, recursive=False)
    print(summary, end="")
    print(f"ARCHIVE_SHA256={sha256(output)}")
    print(f"UPLOAD_THIS={output}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    collect(args.run.resolve(), args.output.absolute())


if __name__ == "__main__":
    main()
