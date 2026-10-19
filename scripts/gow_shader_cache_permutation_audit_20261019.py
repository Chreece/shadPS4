#!/usr/bin/env python3
"""Read-only God of War Ragnarök cached shader-permutation evidence.
Finds CUSA34384 cache ZIPs/directories, verifies persisted permutation metadata,
and compares actual SPIR-V descriptor decorations for sibling variations.
Never runs the emulator, mutates a cache, reads saves, or uploads shader binaries.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
from pathlib import Path
import re
import struct
import sys
import zipfile

GAME = "CUSA34384"
PATTERN = re.compile(r"^(0x[0-9a-fA-F]+)_([0-9]+)\.spv$")
PRUNE = {".git", "source", "build", "Build", "node_modules", ".cache", "_deps",
         "externals", "Games", "games", "saves", "save_data", "savedata",
         "screenshots", "logs", "shader-dumps", "dumps", "temp", "temp_data"}
MASK64 = (1 << 64) - 1
MAX_SPV_SIZE = 48 * 1024 * 1024

def discover(home: Path, root: Path):
    bases = [
        Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share"))) / "shadPS4",
        home / ".local/share/shadPS4",
        home / ".config/shadPS4",
        root / "GodOfWarRagnarok",
        root / "ESDE",
    ]
    seen = set()
    results = []
    for base in bases:
        if not base.is_dir() or str(base.resolve()) in seen:
            continue
        seen.add(str(base.resolve()))
        base_depth = len(base.parts)
        for directory, children, files in os.walk(base, followlinks=False):
            path = Path(directory)
            depth = len(path.parts) - base_depth
            if depth > 10:
                children[:] = []
                continue
            if path.name.casefold() == GAME.casefold() and path.parent.name.lower() == "cache":
                results.append(("directory", path))
                children[:] = []
                continue
            for name in files:
                if name.casefold() == (GAME + ".zip").casefold() and path.name.lower() == "cache":
                    results.append(("zip", path / name))
            children[:] = [x for x in children if x not in PRUNE and not x.startswith(".")
                           and x.lower() not in ("source", "build", "_deps")]
    return list(dict.fromkeys(results))

def hash_combine(seed, idx):
    return (seed ^ (idx + 0x9e3779b9 + ((seed << 12) & MASK64) + (seed >> 4))) & MASK64

def reflect_spirv(raw):
    if len(raw) < 20 or len(raw) % 4:
        raise ValueError("Invalid SPIR-V byte length")
    words = struct.unpack(f"<{len(raw)//4}I", raw)
    if words[0] != 0x07230203:
        raise ValueError("Invalid SPIR-V magic")
    decorations = collections.defaultdict(dict)
    pointer_types = {}
    array_types = {}
    constants = {}
    variables = {}
    i = 5
    while i < len(words):
        count, op = words[i] >> 16, words[i] & 0xffff
        if count == 0 or i + count > len(words):
            raise ValueError(f"Malformed SPIR-V instruction at word {i}")
        instruction = words[i:i+count]
        if op == 71 and count >= 4:
            target, kind = instruction[1], instruction[2]
            if kind == 33:
                decorations[target]["binding"] = instruction[3]
            elif kind == 34:
                decorations[target]["set"] = instruction[3]
        elif op == 32 and count >= 4:
            pointer_types[instruction[1]] = (instruction[2], instruction[3])
        elif op == 28 and count >= 4:
            array_types[instruction[1]] = (instruction[2], instruction[3])
        elif op == 29 and count >= 3:
            array_types[instruction[1]] = (instruction[2], None)
        elif op in (43, 50) and count >= 4:
            constants[instruction[2]] = instruction[3]
        elif op == 59 and count >= 4:
            variables[instruction[2]] = (instruction[1], instruction[3])
        i += count
    signature = []
    for var_id, (pointer_id, storage) in variables.items():
        decorated = decorations.get(var_id, {})
        if "binding" not in decorated:
            continue
        ptr = pointer_types.get(pointer_id)
        if ptr is None:
            continue
        dtype = ptr[1]
        count = 1
        runtime = False
        for _ in range(12):
            if dtype not in array_types:
                break
            child, count_id = array_types[dtype]
            if count_id is None or count_id not in constants:
                runtime = True
            else:
                count = min(2**31, count * max(1, constants[count_id]))
            dtype = child
        signature.append((decorated.get("set", 0), decorated["binding"],
                          str(count) if not runtime else "runtime", storage))
    return tuple(sorted(signature))

def examine(label, location):
    z = None
    try:
        if label == "zip":
            z = zipfile.ZipFile(location)
            files = {Path(item.filename).name: item for item in z.infolist()
                     if not item.is_dir() and Path(item.filename).suffix in (".spv", ".meta")}
            def read(name):
                item = files[name]
                if item.file_size > MAX_SPV_SIZE:
                    raise ValueError("oversize shader cache entry")
                return z.read(item)
        else:
            files = {item.name: item for item in location.iterdir()
                     if item.is_file() and item.suffix in (".spv", ".meta")}
            def read(name):
                if files[name].stat().st_size > MAX_SPV_SIZE:
                    raise ValueError("oversize shader cache entry")
                return files[name].read_bytes()

        grouped = collections.defaultdict(dict)
        for name in files:
            match = PATTERN.fullmatch(name)
            if match:
                grouped[int(match.group(1), 16)][int(match.group(2))] = name
        multiple = {root: perms for root, perms in grouped.items()
                    if 0 in perms and any(i > 0 for i in perms)}
        examples = []
        diff_layout = 0
        identical_layout = 0
        unknown = 0
        metadata_ok = 0
        metadata_absent = 0
        metadata_invalid = 0
        nonzero_count = sum(sum(i > 0 for i in x) for x in grouped.values())
        for hash_, perms in sorted(multiple.items()):
            for idx in sorted(i for i in perms if i > 0):
                meta_name = f"0x{hash_combine(hash_, idx):016x}.meta"
                meta_state = "absent"
                if meta_name in files:
                    try:
                        meta = read(meta_name)
                        if len(meta) >= 24:
                            vmeta, vspv, h, index = struct.unpack_from("<IIQQ", meta)
                            meta_state = "valid" if (
                                vmeta == 6 and vspv == 9 and
                                h == hash_combine(hash_, idx) and index == idx
                            ) else f"invalid:{vmeta}/{vspv}/{index}"
                        else:
                            meta_state = "too_short"
                    except (OSError, ValueError, zipfile.BadZipFile) as exc:
                        meta_state = f"unreadable:{str(exc)[:80]}"
                if meta_state == "valid":
                    metadata_ok += 1
                elif meta_state == "absent":
                    metadata_absent += 1
                else:
                    metadata_invalid += 1
                try:
                    left = reflect_spirv(read(perms[0]))
                    right = reflect_spirv(read(perms[idx]))
                    different = left != right
                    if different:
                        diff_layout += 1
                    else:
                        identical_layout += 1
                    if len(examples) < 25 or (different and not any(x["layout_changed"] is True for x in examples)):
                        sample = {
                            "shader": f"0x{hash_:016x}", "variation": idx,
                            "metadata": meta_state, "layout_changed": different,
                            "zero_descriptors": left[:64],
                            "variation_descriptors": right[:64]
                        }
                        if len(examples) < 25:
                            examples.append(sample)
                        elif different:
                            examples[-1] = sample
                except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
                    unknown += 1
                    if len(examples) < 25:
                        examples.append({
                            "shader": f"0x{hash_:016x}", "variation": idx,
                            "metadata": meta_state, "layout_changed": None,
                            "reflection_error": str(exc)[:200]
                        })
        return {
            "cache": str(location), "type": label,
            "spv_files": sum(len(x) for x in grouped.values()),
            "shaders": len(grouped), "shaders_with_nonzero_variations": len(multiple),
            "nonzero_variations": nonzero_count,
            "variation_pairs_different_spirv_descriptor_layout": diff_layout,
            "variation_pairs_same_spirv_descriptor_layout": identical_layout,
            "variation_pairs_unreadable": unknown,
            "variation_meta_valid": metadata_ok,
            "variation_meta_absent": metadata_absent,
            "variation_meta_invalid": metadata_invalid,
            "examples": examples,
        }
    finally:
        if z:
            z.close()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    locations = discover(Path.home(), args.root)
    result = {"run_at": datetime.datetime.now().astimezone().isoformat(),
              "game_serial": GAME, "mode": "READ_ONLY_CACHE_AND_SPIRV",
              "cache_locations": len(locations), "caches": []}
    for kind, loc in locations:
        try:
            result["caches"].append(examine(kind, loc))
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            result["caches"].append({"cache": str(loc), "error": str(exc)[:300]})
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print(f"CACHE_LOCATIONS={len(locations)}")
    for x in result["caches"]:
        if "error" in x:
            print(f"CACHE_ERROR={x['cache']}: {x['error']}")
            continue
        print(f"CACHE={x['cache']}")
        for key in ("spv_files", "shaders_with_nonzero_variations", "nonzero_variations",
                    "variation_pairs_different_spirv_descriptor_layout",
                    "variation_pairs_same_spirv_descriptor_layout",
                    "variation_pairs_unreadable", "variation_meta_valid",
                    "variation_meta_absent", "variation_meta_invalid"):
            print(f"  {key.upper()}={x[key]}")
        for e in x["examples"][:8]:
            print(f"  EXAMPLE={e['shader']}/{e['variation']} changed={e['layout_changed']} metadata={e['metadata']}")
    print("SCENE_RELEVANCE=UNKNOWN_OFFLINE_CACHE_ONLY")
    print("CACHE_AND_SAVES_MODIFIED=NO")
    print("GAMEPLAY_RUNS=ZERO")
    print(f"EVIDENCE_JSON={args.out}")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"CACHE_AUDIT_FAILED={exc}", file=sys.stderr, flush=True)
        raise
