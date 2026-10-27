#!/usr/bin/env python3
"""Read-only source-stage snapshot of a known c925/PR75 Git three-way conflict."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import traceback

C925 = "c925523b49ef39434024b16b2b014d120471ee1a"
ELF_SHA = "260ed43abbe538127ab82b3580a48c5f67be19ac4bbdd32ebefd78c73cdaf50d"
ROOT = Path.home() / "projects/shadps4/GhostOfTsushima"
SRC = ROOT / "experiments/c925-typed-native-20261027-083533/source"
GOOD = ROOT / "candidates" / f"{C925}-strided-precise-image-readbacks-{ELF_SHA}" / "shadps4"
LOGS = Path.home() / "projects/shadps4/logs"
EXPECTED = {
    "src/shader_recompiler/backend/spirv/emit_spirv_image.cpp",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp",
    "src/shader_recompiler/profile.h",
    "src/shader_recompiler/resource.h",
    "src/video_core/renderer_vulkan/vk_instance.cpp",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
}
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = LOGS / f"c925-pr75-git-conflicts-{STAMP}"
OUT.mkdir(parents=True, exist_ok=True)
RESULT: dict = {"record_type": "LOCAL_C925_PR75_UNMERGED_STAGE_SNAPSHOT", "status": "PREFLIGHT", "source_commit": C925,
                "tested_games": [], "game_launched": False, "source_modified": False}


def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    run = subprocess.run(["git", "-C", str(SRC), *arguments], capture_output=True, check=False, timeout=25)
    if check and run.returncode:
        raise RuntimeError(f"git {arguments!r} exit={run.returncode}: {run.stderr[:800]!r}")
    return run


def sha256_file(path: Path) -> str:
    hash_obj = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hash_obj.update(block)
    return hash_obj.hexdigest()


def analyze_markers(code: str, context: int = 10) -> list[dict]:
    lines = code.splitlines()
    regions = []
    for start, line in enumerate(lines):
        if not line.startswith("<<<<<<< "):
            continue
        end = next((k for k in range(start + 1, len(lines)) if lines[k].startswith(">>>>>>> ")), None)
        if end is None:
            continue
        regions.append({"start_line": start + 1, "end_line": end + 1,
                        "excerpt": "\n".join(lines[max(0, start - context):min(len(lines), end + context + 1)])})
    return regions


def capture() -> None:
    if not (SRC / ".git").exists() or not GOOD.is_file():
        raise RuntimeError("Missing previous isolated source or protected ELF; no worktree changes made")
    if sha256_file(GOOD) != ELF_SHA:
        raise RuntimeError("Protected menu-positive ELF SHA mismatch; refusing attribution")
    if git("merge-base", "--is-ancestor", C925, "HEAD", check=False).returncode:
        raise RuntimeError("Source HEAD does not descend from original c925 commit")
    head = git("rev-parse", "HEAD").stdout.decode().strip()
    subject = git("show", "-s", "--format=%s", "HEAD").stdout.decode().strip()
    if subject != "source port PR74 from c925":
        raise RuntimeError(f"Unexpected current source commit subject {subject!r}; refusing wrong-stage evidence")
    (OUT / "git-status.txt").write_bytes(git("status", "--porcelain=v1").stdout)
    (OUT / "git-index-unmerged.raw").write_bytes(git("ls-files", "-u").stdout)
    raw = git("ls-files", "-u", "-z").stdout
    paths: dict[str, dict[int, tuple[str, str]]] = {}
    for row in raw.split(b"\0"):
        if not row:
            continue
        header, raw_path = row.split(b"\t", 1)
        mode, blob_sha, stage_number = header.decode().split(" ")
        path = raw_path.decode("utf-8", errors="strict")
        paths.setdefault(path, {})[int(stage_number)] = (mode, blob_sha)
    RESULT.update({"head": head, "unmerged_files": sorted(paths), "unmerged_count": len(paths),
                   "matches_expected_files": set(paths) == EXPECTED})
    records = []
    for path, stages in sorted(paths.items()):
        if not path.startswith("src/") or ".." in Path(path).parts:
            raise RuntimeError(f"Unexpected conflict path: {path}")
        evidence = {"path": path, "stages": {}, "marker_count": 0}
        for number, (mode, blob) in sorted(stages.items()):
            if number not in (1, 2, 3):
                raise RuntimeError("Unexpected Git index stage")
            data = git("show", f":{number}:{path}").stdout
            # git blob object hash is SHA1 of the Git blob header and contents.
            import hashlib as _h
            digest = _h.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            if digest != blob:
                raise RuntimeError(f"Index blob mismatch {path} stage {number}")
            target = OUT / "stages" / str(number) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            evidence["stages"][number] = {"git_sha1": blob, "bytes": len(data), "mode": mode}
        worktree = SRC / path
        if worktree.is_file():
            current = worktree.read_bytes()
            target = OUT / "unmerged-worktree" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(current)
            regions = analyze_markers(current.decode("utf-8", errors="replace"))
            evidence["marker_count"] = len(regions)
            target = OUT / "conflict-windows" / (path.replace("/", "__") + ".json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(regions, indent=2, ensure_ascii=False) + "\n")
        records.append(evidence)
    (OUT / "conflict-index.json").write_text(json.dumps(records, indent=2) + "\n")
    RESULT["status"] = "CONFLICT_STAGES_COLLECTED" if set(paths) == EXPECTED else "CONFLICT_SET_CHANGED_READ_ONLY"
    print(f"HEAD={head}\nCONFLICT_FILES={len(paths)}\nSTAGE_SNAPSHOT={RESULT['status']}", flush=True)


def finish() -> int:
    (OUT / "trial.json").write_text(json.dumps(RESULT, indent=2) + "\n")
    try:
        with (LOGS / "emulator-trials-local.jsonl").open("a") as stream:
            stream.write(json.dumps(RESULT, separators=(",", ":")) + "\n")
    except OSError:
        pass
    path = OUT.with_suffix(".tar.gz")
    with tarfile.open(path, "w:gz", compresslevel=1) as bundle:
        bundle.add(OUT, arcname=".")
    print(f"UPLOAD_THIS={path}\nSSH_SESSION=OPEN", flush=True)
    return 0 if RESULT["status"] == "CONFLICT_STAGES_COLLECTED" else 7


def main() -> int:
    try:
        capture()
    except Exception:
        RESULT["status"] = "CAPTURE_FAILED"
        (OUT / "error.txt").write_text(traceback.format_exc())
        print("CAPTURE_FAILED; see archived error.txt", file=sys.stderr)
    return finish()


if __name__ == "__main__":
    sys.exit(main())
