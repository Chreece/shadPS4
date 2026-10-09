#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Transactional capture of native shadPS4 GCN, IR/ASL and SPIR-V shader dumps.

Never modifies global settings, game saves, installed binaries, SSH or drivers.
Only target hashes seen in evidence are collected; snapshots existing dump
files byte-for-byte and restores them, removing newly created trial files.
The parent owns the game and must stop it before calling 'finish'.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

HASHES = ("361a48f5", "9911cadd", "8e743c8e")
SUFFIXES = (".bin", ".spv", ".irprogram.txt", ".asl.txt", ".srtprogram.txt")
MAX_FILES = 90
MAX_BYTES = 110 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def roots(home: Path):
    all_roots = [
        home / ".local/share/shadPS4/shader/dumps",
        home / "Applications/shadps4/user/shader/dumps",
        home / ".cache/shadps4-ghost-fullstack-20261008-131621/source/user/shader/dumps",
    ]
    if os.environ.get("XDG_DATA_HOME"):
        all_roots.append(Path(os.environ["XDG_DATA_HOME"]) / "shadPS4/shader/dumps")
    return list(dict.fromkeys(all_roots))


def selected(home: Path):
    for index, root in enumerate(roots(home)):
        if root.is_symlink() or not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if (p.is_symlink() or not p.is_file() or
                not any(h in p.name.lower() for h in HASHES) or
                not p.name.lower().endswith(SUFFIXES)):
                continue
            yield index, p


def before(work: Path, home: Path):
    work.mkdir(parents=True, exist_ok=True)
    manifest = work / "shader-originals-manifest.json"
    if manifest.exists():
        raise ValueError("Original snapshot already exists; refusing overwrite")
    entries = []
    for i, p in selected(home):
        if len(entries) >= MAX_FILES:
            raise ValueError("Too many pre-existing matching shader files")
        if p.stat().st_size > MAX_FILE:
            raise ValueError("Existing shader file exceeds safe backup limit: " + str(p))
        saved = work / "_shader-originals" / str(i) / p.name
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, saved)
        if sha(saved) != sha(p):
            raise IOError("Shader backup checksum mismatch")
        entries.append({
            "path": str(p), "backup": str(saved), "sha": sha(saved),
            "root": i, "mtime_ns": p.stat().st_mtime_ns,
        })
    manifest.write_text(json.dumps({"started": time.time(), "originals": entries}, indent=2) + "\n")
    print("SHADER_ORIGINAL_FILES_BACKED_UP=" + str(len(entries)), flush=True)


def after(work: Path, home: Path):
    manifest_path = work / "shader-originals-manifest.json"
    if not manifest_path.is_file():
        print("SHADER_PROOF_NOT_ARMED=no_original_snapshot", flush=True)
        return
    state = json.loads(manifest_path.read_text())
    started = state["started"]
    originals = {item["path"]: item for item in state["originals"]}
    found = list(selected(home))
    output = work / "native-guest-shaders"
    output.mkdir(exist_ok=True)
    total, count, records = 0, 0, []
    try:
        for i, p in found:
            try:
                size = p.stat().st_size
                if (count >= MAX_FILES or size > MAX_FILE or total + size > MAX_BYTES):
                    records.append({"file": str(p), "skipped": "capture_limit", "size": size})
                    continue
                dest = output / str(i) / p.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest)
                if sha(dest) != sha(p):
                    raise IOError("Captured shader checksum mismatch")
                count += 1
                total += size
                records.append({"file": str(p), "captured": str(dest.relative_to(work)),
                                "bytes": size, "sha256": sha(dest)})
            except (OSError, PermissionError) as exc:
                records.append({"file": str(p), "error": repr(exc)})
    finally:
        # Restore files, even if copying captured data failed.
        stopped = time.time()
        for i, p in found:
            key = str(p)
            old = originals.get(key)
            try:
                if p.is_symlink() or not p.is_file():
                    continue
                if old:
                    backup = Path(old["backup"])
                    if not backup.is_file() or sha(backup) != old["sha"]:
                        print("SHADER_ORIGINAL_BACKUP_UNVERIFIED=" + key, flush=True)
                        continue
                    if sha(p) != old["sha"]:
                        if p.stat().st_mtime > stopped + 3:
                            print("SHADER_EXTERNAL_CHANGE_PRESERVED=" + key, flush=True)
                            continue
                        tmp = p.with_name("." + p.name + ".ghost-restore")
                        if tmp.exists():
                            print("SHADER_RESTORE_TEMP_COLLISION=" + str(tmp), flush=True)
                            continue
                        shutil.copy2(backup, tmp)
                        os.replace(tmp, p)
                        print("SHADER_ORIGINAL_RESTORED=" + key, flush=True)
                elif started - 3 <= p.stat().st_mtime <= stopped + 3:
                    p.unlink()
                    print("TRIAL_SHADER_ARTIFACT_CLEANED=" + key, flush=True)
            except (OSError, PermissionError) as exc:
                print("SHADER_CLEANUP_FAILED=" + key + " " + repr(exc), flush=True)
    (work / "shader-proof-results.json").write_text(json.dumps({
        "hashes": list(HASHES), "files_captured": count,
        "bytes_captured": total, "records": records,
        "previous_files": len(originals)
    }, indent=2) + "\n")
    print(f"NATIVE_SHADER_PROOF_CAPTURED={count} bytes={total}", flush=True)


def selftest():
    with tempfile.TemporaryDirectory(prefix="ghost-shader-proof-test-") as d:
        home, work = Path(d) / "home", Path(d) / "work"
        folder = roots(home)[0]
        folder.mkdir(parents=True)
        old = folder / "fs_0x00000000361a48f5_0.bin"
        new = folder / "vs_0x000000009911cadd_0.spv"
        unrelated = folder / "secret.txt"
        old.write_bytes(b"original-user-dump")
        unrelated.write_text("must not touch")
        before(work, home)
        assert json.loads((work / "shader-originals-manifest.json").read_text())["originals"]
        old.write_bytes(b"trial-new-contents")
        new.write_bytes(b"\x03\x02\x23\x07" * 4)
        after(work, home)
        assert old.read_bytes() == b"original-user-dump"
        assert not new.exists()
        assert unrelated.read_text() == "must not touch"
        assert (work / "native-guest-shaders" / "0" / old.name).is_file()
        assert (work / "native-guest-shaders" / "0" / new.name).is_file()
        assert json.loads((work / "shader-proof-results.json").read_text())["files_captured"] == 2
    print("SELFTEST PASS: native GCN/SPIR-V capture, verified original restoration, "
          "new trial file cleanup, unrelated files preserved", flush=True)


def main():
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    if len(sys.argv) != 3 or sys.argv[1] not in ("before", "after"):
        raise SystemExit("Usage: collector.py --self-test | before|after WORK")
    work = Path(sys.argv[2]).resolve()
    if not work.is_dir():
        raise ValueError("Work directory missing; refusing to create outside parent")
    if sys.argv[1] == "before":
        before(work, Path.home())
    else:
        after(work, Path.home())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
