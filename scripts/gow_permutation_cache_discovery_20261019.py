#!/usr/bin/env python3
"""Locate active GoW Ragnarök shader caches across portable/cloned shadPS4 profiles.
Read only: filesystem metadata, shadPS4 cache settings, existing SPIR-V caches.
Does not change a cache, save, executable, Ollama, Sunshine, or SSH.
"""
from __future__ import annotations
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

GAME = "CUSA34384"
SKIP = {
    ".git", "CMakeFiles", "node_modules", ".cache", "_deps", "externals",
    ".venv", "venv", "build-deps", "shader-dumps", "saves", "savedata",
    "save_data", "games", "Games", "game", "Game", "packages",
    "usr", "lib", "share", "source-deps", "build-tmp",
    "baseline-source", "corrected-source", "backups", "backup"
}
SERIAL = re.compile(r"^(CUSA|PPSA)[0-9]{4,}$", re.IGNORECASE)
KEYS = ("pipelineCacheEnable", "pipelineCacheArchive",
        "pipeline_cache_enabled", "pipeline_cache_archived")
MAX_DEPTH = 16
MAX_DIRS = 180000
MAX_PROBES = 300
MAX_ENTRIES = 20000

def safe_ls(path, max_items=MAX_ENTRIES):
    found = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                if len(found) >= max_items:
                    break
                found.append(entry)
    except OSError:
        pass
    return found

def locate_running_profiles():
    roots = []
    evidence = []
    proc = Path("/proc")
    if not proc.is_dir():
        return roots, evidence
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            cmd = (entry / "cmdline").read_bytes()[:250000].split(b"\0")
            name = os.fsdecode(cmd[0]) if cmd and cmd[0] else ""
            if "shadps4" not in name.lower():
                continue
            cwd = Path(os.readlink(entry / "cwd"))
            env = (entry / "environ").read_bytes()[:250000].split(b"\0")
            data = next((v[14:] for v in env if v.startswith(b"XDG_DATA_HOME=")), None)
            xdg = os.fsdecode(data) if data else None
            evidence.append({"pid": int(entry.name), "cwd": str(cwd),
                             "xdg_data_home": xdg})
            roots.extend([cwd, cwd / "user"])
            if xdg:
                roots.extend([Path(xdg), Path(xdg) / "shadPS4"])
        except (OSError, ValueError):
            continue
    return roots, evidence

def profile_config(directory):
    """Read only selected cache flags; do not collect full configs."""
    flags = {}
    for name in ("config.toml", "config.json", "settings.toml", "settings.json"):
        file = directory / name
        if not file.is_file():
            continue
        try:
            if file.stat().st_size > 5 * 1024 * 1024:
                continue
            data = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        values = {}
        for key in KEYS:
            match = re.search(r"(?m)^[ \t]*[\"']?" + re.escape(key) +
                              r"[\"']?[ \t]*[=:][ \t]*(true|false|0|1)\b", data,
                              flags=re.IGNORECASE)
            if match:
                values[key] = match.group(1).lower() in ("true", "1")
        if values:
            flags[name] = values
    return flags

def cache_probe(cache_path):
    """Inspect only child names/metadata; no game files and no shader data."""
    targets = []
    other_serials = []
    spvs = metas = 0
    entries = safe_ls(cache_path)
    for item in entries:
        name = item.name
        if name.casefold() == GAME.casefold() and item.is_dir(follow_symlinks=True):
            targets.append(("directory", str(Path(item.path))))
        elif name.casefold() == (GAME+".zip").casefold() and item.is_file(follow_symlinks=True):
            targets.append(("zip", str(Path(item.path))))
        elif SERIAL.fullmatch(name) or (name.lower().endswith(".zip") and
             SERIAL.fullmatch(name[:-4])):
            if len(other_serials) < 15:
                other_serials.append(name)
        elif name.endswith(".spv"):
            spvs += 1
        elif name.endswith(".meta"):
            metas += 1
    return targets, {
        "cache_directory": str(cache_path),
        "direct_entries_examined": len(entries),
        "other_game_serials_sample": other_serials,
        "direct_spv_files": spvs,
        "direct_meta_files": metas,
    }

def relevant_script_names(root):
    scriptdir = root / "scripts"
    try:
        names = []
        for f in scriptdir.iterdir():
            if not f.is_file() or f.suffix not in (".sh", ".py"):
                continue
            if "gow" not in f.name.lower() and "ragnarok" not in f.name.lower():
                continue
            if f.stat().st_size > 1024 * 1024:
                continue
            if "XDG_DATA_HOME" in f.read_text(errors="replace"):
                names.append(f.name)
        return sorted(names)[:50]
    except OSError:
        return []

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scanner", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    home = Path.home()
    xdg_home = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share")))
    active_roots, active = locate_running_profiles()
    bases = [
        root / "GodOfWarRagnarok",
        root / "profiles",
        root / "user",
        xdg_home / "shadPS4",
        home / ".local/share/shadPS4",
        home / ".config/shadPS4",
        home / "shadPS4",
    ]
    bases.extend(active_roots)
    search_roots = []
    for base in bases:
        if base.is_dir() and not any(
                str(base.resolve()) == str(previous.resolve()) for previous in search_roots):
            search_roots.append(base)
    candidates = []
    cache_directories = []
    profile_flags = []
    found = set()
    sampled_dirs = 0
    errors = []
    truncated = False

    def register(kind, path):
        try:
            key = (kind, str(path.resolve()))
        except OSError:
            key = (kind, str(path))
        if key not in found:
            found.add(key)
            candidates.append((kind, path))

    for base in search_roots:
        rootdepth = len(base.parts)
        for directory, children, filenames in os.walk(base, followlinks=False, onerror=lambda e: errors.append(str(e)[:200])):
            sampled_dirs += 1
            if sampled_dirs >= MAX_DIRS:
                truncated = True
                children[:] = []
                break
            path = Path(directory)
            depth = len(path.parts) - rootdepth
            if depth >= MAX_DEPTH:
                children[:] = []
                continue
            # An actual cache can be the root or child of any XDG or portable profile.
            if path.name.casefold() == "cache":
                targets, probe = cache_probe(path)
                if len(cache_directories) < MAX_PROBES:
                    cache_directories.append(probe)
                for kind, target in targets:
                    register(kind, Path(target))
                children[:] = []
                continue
            # Recognize a direct game cache supplied as a search root.
            if path.name.casefold() == GAME.casefold() and path.parent.name.casefold() == "cache":
                register("directory", path)
                children[:] = []
                continue
            if path.name.lower() in ("user", "shadps4"):
                flags = profile_config(path)
                if flags:
                    profile_flags.append({"profile": str(path), "cache_flags": flags})
            # If a portable profile is a symlink, os.walk will not follow it;
            # probe its cache via pathlib without following arbitrary cycles.
            for child in children[:]:
                if child.casefold() == "cache":
                    continue
                if child in SKIP or child.startswith("."):
                    children.remove(child)
                    continue
                maybe = path / child
                if maybe.is_symlink() and child.lower() in ("user", "shadps4"):
                    link = maybe / "cache"
                    if link.is_dir():
                        targets, probe = cache_probe(link)
                        if len(cache_directories) < MAX_PROBES:
                            cache_directories.append(probe)
                        for kind, target in targets:
                            register(kind, Path(target))
            for name in filenames:
                if name.casefold() == GAME.lower()+".zip" and path.name.casefold() == "cache":
                    register("zip", path/name)

    if not args.scanner.is_file():
        raise RuntimeError(f"Expected original read-only scanner at {args.scanner}")
    spec = importlib.util.spec_from_file_location("gow_cache_original", args.scanner)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot import original SPIR-V scanner")
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    if not callable(getattr(scanner, "examine", None)):
        raise RuntimeError("Original SPIR-V examine function missing")
    analyses = []
    for kind, path in candidates:
        try:
            analyses.append(scanner.examine(kind, path))
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            analyses.append({"type": kind, "cache": str(path), "error": str(exc)[:220]})

    data = {
        "run_at": datetime.datetime.now().astimezone().isoformat(),
        "game": GAME,
        "mode": "READ_ONLY_CACHE_DISCOVERY_AND_SPIRV_AUDIT",
        "search_roots": [str(x) for x in search_roots],
        "searched_directories": sampled_dirs,
        "search_truncated": truncated,
        "scan_errors_sample": errors[:15],
        "running_emulator_profiles": active,
        "gow_scripts_with_xdg_home": relevant_script_names(root),
        "cache_directories_found": cache_directories,
        "shadps4_profile_cache_flags": profile_flags[:80],
        "matching_cache_locations": len(candidates),
        "caches": analyses,
        "scene_relevance": "UNKNOWN_OFFLINE_CACHE_ONLY",
        "no_cache_found_is_not_evidence_of_no_shader_permutations": True
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, default=str)+"\n")
    print(f"SEARCH_ROOTS={len(search_roots)} SEARCHED_DIRECTORIES={sampled_dirs} SEARCH_TRUNCATED={truncated}")
    print(f"CACHE_DIRECTORIES_DISCOVERED={len(cache_directories)} MATCHING_GAME_CACHE_LOCATIONS={len(candidates)}")
    print(f"GOW_LAUNCH_SCRIPTS_WITH_XDG={len(data['gow_scripts_with_xdg_home'])}")
    for item in cache_directories[:15]:
        print(f"CACHE_DIRECTORY={item['cache_directory']} other_serials={item['other_game_serials_sample']}")
    for f in profile_flags[:20]:
        print(f"PROFILE_CACHE_CONFIGURATION={f['profile']} {f['cache_flags']}")
    for item in analyses:
        if "error" in item:
            print(f"CACHE_ERROR={item['cache']}: {item['error']}")
            continue
        print(f"CACHE={item['cache']}")
        for k in ("spv_files", "shaders_with_nonzero_variations", "nonzero_variations",
                  "variation_pairs_different_spirv_descriptor_layout",
                  "variation_pairs_same_spirv_descriptor_layout",
                  "variation_pairs_unreadable", "variation_meta_valid",
                  "variation_meta_absent", "variation_meta_invalid"):
            print(f"{k.upper()}={item.get(k)}")
        for row in item.get("examples", [])[:8]:
            print(f"EXAMPLE={row.get('shader')}/{row.get('variation')} changed={row.get('layout_changed')}")
    if not candidates:
        print("NEXT_GATE=LOCATE_ACTIVE_PROFILE_OR_RUNTIME_PERMUTATION_COUNTER")
    else:
        print("NEXT_GATE=CORRELATE_VARIATION_HASH_WITH_FAILING_SCENE")
    print("SCENE_RELEVANCE=NOT_YET_PROVEN")
    print("CACHE_SAVES_EXECUTABLE=NOT_MODIFIED")
    print("GAMEPLAY_RUNS=ZERO")
    print(f"REPORT={args.out}")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"CACHE_DISCOVERY_FAILURE={exc}", file=sys.stderr)
        raise
