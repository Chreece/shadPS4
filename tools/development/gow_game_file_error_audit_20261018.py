#!/usr/bin/env python3
"""Read-only PS4 Ragnarök game-content and mount-path audit.

Checks real /app0 ENOENT paths seen in shadPS4 19:05 gameplay against
configured install roots, case variants and alternative top-level layouts.
Does not copy, unpack, download, rename, link, edit or delete any game data.
Does not run a game, touch Ollama, or alter SSH/Sunshine.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import datetime
import json
from pathlib import Path
import re
import tarfile
import tempfile

ID = "CUSA34384"
MISSING = (
    "exec/sound/orbis_le/sys_files.audiopack.toc",
    "exec/wad/orbis_le/sys_files.texpack.toc",
    "exec/wad/orbis_le/sys_files.lodpack.toc",
    "exec/wad/orbis_le/mid_chase100_intro.wypsm",
    "exec/wad/orbis_le/mid_for100_frozenriver.wypsm",
    "exec/wad/orbis_le/mid_for101_rivertohouse.wypsm",
    "exec/wad/orbis_le/mid_for200_house.wypsm",
    "exec/wad/orbis_le/mid_for300_sledtunnel.wypsm",
    "exec/wad/orbis_le/mid_for310_toforest.wypsm",
    "exec/wad/orbis_le/mid_for755_cliffsideborder.wypsm",
)
SKIP_DIRS = {".git", "logs", "build", "cache", "node_modules",
             "shader_cache", "source", "tests", "exec", "sce_sys"}


def config_candidates(home: Path) -> list[Path]:
    return [home / part for part in (
        ".local/share/shadPS4/user/config.json",
        ".local/share/shadPS4/config.json",
        ".local/share/shadps4/user/config.json",
        ".local/share/shadps4/config.json",
        ".config/shadPS4/user/config.json",
        ".config/shadPS4/config.json",
        ".config/shadps4/user/config.json",
        ".config/shadps4/config.json",
        ".local/share/shadPS4/user/config.toml",
        ".local/share/shadPS4/config.toml",
        ".config/shadPS4/config.toml",
    )]


def get_install_dirs(config: Path) -> list[Path]:
    try:
        if config.suffix.lower() == ".json":
            data = json.loads(config.read_text())
        else:
            import tomllib
            data = tomllib.loads(config.read_text())
    except (OSError, ValueError, ImportError):
        return []
    out = []

    def walk(item, install_section=False, depth=0):
        if depth > 9:
            return
        if isinstance(item, dict):
            for key, value in item.items():
                key_folded = str(key).replace("_", "").lower()
                inside = install_section or key_folded in {
                    "installdirs", "installdir", "gameinstalldirs",
                }
                if inside and key_folded in {"path", "value"} and isinstance(value, str):
                    out.append(Path(value).expanduser())
                walk(value, inside, depth + 1)
        elif isinstance(item, list):
            for sub in item:
                if install_section and isinstance(sub, str):
                    out.append(Path(sub).expanduser())
                else:
                    walk(sub, install_section, depth + 1)

    walk(data)
    return [item for item in out if str(item) not in ("", ".")]


def candidate_roots(project: Path) -> list[Path]:
    home = Path.home()
    roots = []
    for config in config_candidates(home):
        if config.is_file() and not config.is_symlink():
            roots.extend(get_install_dirs(config))
    roots.extend(home / x for x in (
        "Games", "games", "ROMs", "roms", "Emulation/roms",
        "Emulation/roms/ps4", "ES-DE/roms/ps4",
        "RetroArch/roms/ps4", "Emulators", "Downloads/Games",
    ))
    # Inspect likely project-specific game directories without walking
    # source/build worktrees or other unrelated projects.
    roots.extend((
        project / "GodOfWarRagnarok",
        home / "Games/PS4",
        home / "roms/ps4",
        home / "ROMs/ps4",
    ))
    unique = []
    seen = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved not in seen and resolved.is_dir():
            seen.add(resolved)
            unique.append(resolved)
    return unique


def find_game_roots(roots: list[Path], max_depth=6, max_entries=15000) -> tuple[list[Path], dict]:
    found = set()
    stats = {"searched_roots": [str(r) for r in roots], "visited_directories": 0,
             "scan_capped": False}
    visited = set()
    for root in roots:
        queue = deque([(root, 0)])
        while queue and stats["visited_directories"] < max_entries:
            here, depth = queue.popleft()
            try:
                real = here.resolve()
                if real in visited or here.is_symlink():
                    continue
                visited.add(real)
                if not here.is_dir():
                    continue
                stats["visited_directories"] += 1
                items = list(here.iterdir())
            except (OSError, PermissionError):
                continue
            filenames = {x.name.casefold() for x in items if x.is_file()}
            if "eboot.bin" in filenames or "eboot.elf" in filenames:
                # Prefer an actual CUSA/game-named root, but retain candidates
                # from configured installation directories as well.
                found.add(here.resolve())
                continue
            if depth >= max_depth:
                continue
            for child in items:
                if child.is_dir() and not child.is_symlink() and child.name not in SKIP_DIRS:
                    queue.append((child, depth + 1))
    if stats["visited_directories"] >= max_entries:
        stats["scan_capped"] = True
    ranked = sorted(found, key=lambda x: (
        ID not in str(x).upper(),
        "RAGNAROK" not in str(x).upper() and "GOD" not in str(x).upper(),
        len(str(x)),
    ))
    return ranked[:10], stats


def verify_entry(game: Path, relative: str) -> dict:
    path = game / relative
    result = {"guest": "/app0/" + relative, "relative": relative}
    try:
        if path.is_file():
            result.update(status="EXACT_PRESENT", actual=str(path),
                          bytes=path.stat().st_size)
            return result
        parent = path.parent
        if parent.is_dir():
            alternatives = sorted(
                [p.name for p in parent.iterdir()
                 if p.name.casefold() == path.name.casefold()])
            if alternatives:
                result.update(status="CASE_ONLY_VARIANT",
                              actual=str(parent / alternatives[0]))
                return result
            result.update(status="ABSENT_IN_THIS_DIRECTORY", parent=str(parent))
        else:
            result.update(status="PARENT_DIRECTORY_MISSING", parent=str(parent))
    except (OSError, PermissionError) as e:
        result.update(status="UNREADABLE", error=str(e))
    return result


def directory_context(game: Path) -> dict:
    observations = {}
    for directory in ("exec/wad/orbis_le", "exec/sound/orbis_le"):
        p = game / directory
        entry = {"path": str(p), "is_dir": p.is_dir(), "examples": []}
        if p.is_dir():
            try:
                files = [x.name for x in p.iterdir() if x.is_file()]
                ext = Counter(Path(x).suffix.lower() for x in files)
                entry["total_files"] = len(files)
                entry["extensions"] = dict(ext.most_common(15))
                entry["examples"] = sorted(files)[:35]
                entry["sys_files_variants"] = [
                    x for x in sorted(files) if x.casefold().startswith("sys_files.")
                ][:30]
            except OSError as e:
                entry["error"] = str(e)
        observations[directory] = entry
    return observations


def extract_recent_errors(project: Path) -> tuple[list[str], list[str]]:
    files = []
    for mode in ("A_DENSE_CPU_UPLOAD", "B_SPARSE_CPU_UPLOAD"):
        p = (project / "logs/gow-sparse-upload-20261018-190544/sparse-cpu-upload-ab" /
             mode / "full-console.log")
        if p.is_file():
            files.append(p)
    counters = Counter()
    for file in files:
        try:
            with file.open(errors="replace") as stream:
                for line in stream:
                    match = re.search(r"Opening path (/app0/\S+) failed", line)
                    if match:
                        counters[match.group(1)] += 1
        except OSError:
            continue
    return [str(x) for x in files], [f"{n}x {p}" for p, n in counters.most_common()]


def audit(project: Path) -> Path:
    project_logs = project / "logs"
    project_logs.mkdir(parents=True, exist_ok=True)
    tag = "gow-game-files-audit-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    output = project_logs / tag
    output.mkdir(exist_ok=False)
    roots, stats = find_game_roots(candidate_roots(project))
    source_logs, log_errors = extract_recent_errors(project)
    report = {
        "run_at": datetime.now().astimezone().isoformat(),
        "game_serial": ID,
        "sources": source_logs,
        "previous_missing_file_errors": log_errors,
        "search": stats,
        "candidates": [],
        "meaning": "An ENOENT can be an optional game probe. Do not treat it "
                   "as evidence of an incomplete game until the actual mounted "
                   "app0 root is established. Packed archives need separate inspection.",
        "read_only": True,
        "recompiled": False,
        "ollama_touched": False,
        "game_data_edited": False,
        "ssh_sunshine_modified": False,
    }
    for path in roots:
        report["candidates"].append({
            "root": str(path),
            "candidate_score": int(ID in str(path).upper()) * 2 +
                               int("RAGNAROK" in str(path).upper()),
            "missing_requested_paths": [verify_entry(path, name) for name in MISSING],
            "directory_context": directory_context(path),
        })
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [
        f"GAME={ID}",
        f"GAME_ROOT_CANDIDATES={len(roots)}",
        f"SCANNED_DIRECTORIES={stats['visited_directories']}",
        f"READ_ONLY=YES",
    ]
    for game in report["candidates"]:
        lines.append("\nROOT=" + game["root"])
        for item in game["missing_requested_paths"]:
            lines.append(f"  {item['status']} {item['relative']} "
                         f"{item.get('actual', '')}")
    if not roots:
        lines.append("INSTALL_ROOT_NOT_RESOLVED: examine game install_dirs "
                     "in shadPS4 config; do not infer all reported paths are absent")
    (output / "summary.txt").write_text("\n".join(lines) + "\n")
    archive = project_logs / (tag + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(output, arcname=tag)
    print("\n".join(lines), flush=True)
    print("ARCHIVE="+str(archive), flush=True)
    print("GOW_ASSET_CHECK_COMPLETE_NO_BUILD_OLLAMA_TOUCH_OR_GAME_MODIFICATION=YES", flush=True)
    return archive


def selftest() -> None:
    with tempfile.TemporaryDirectory(prefix=".gow-asset-selftest-") as temp:
        root = Path(temp)
        (root / "exec/sound/orbis_le").mkdir(parents=True)
        file = root / "exec/sound/orbis_le/Sys_Files.AUDIOPACK.TOC"
        file.write_bytes(b"test")
        result = verify_entry(root, MISSING[0])
        assert result["status"] == "CASE_ONLY_VARIANT", result
        assert verify_entry(root, MISSING[1])["status"] == "PARENT_DIRECTORY_MISSING"
        conf = root / "config.json"
        conf.write_text('{"General":{"install_dirs":{"value":[{"path":"'+str(root)+'","enabled":true}]}}}')
        assert root in get_install_dirs(conf)
        file.rename(root / MISSING[0])
        assert verify_entry(root, MISSING[0])["status"] == "EXACT_PRESENT"
    print("GOW_GAME_CONTENT_AUDIT_SELFTEST=PASS")


if __name__ == "__main__":
    arg = argparse.ArgumentParser()
    arg.add_argument("--project-root", type=Path,
                     default=Path.home() / "projects/shadps4")
    arg.add_argument("--selftest", action="store_true")
    opt = arg.parse_args()
    if opt.selftest:
        selftest()
    else:
        audit(opt.project_root)
