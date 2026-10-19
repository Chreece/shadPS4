#!/usr/bin/env python3
"""GoW Ragnarök early-startup regression triage; READ ONLY.
Compare a failed candidate console with previously retained successful GoW
console archives, inspect optional ENOENT paths and ELF guest fault mapping.
Does not launch game, edit files, remove archives, or upload game content.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tarfile

GAME_ID = "CUSA34384"
PREFERRED = (
    "gow-srt-wave-uniform-20261019-162036",
    "gow-audio3d-lock-20261019-103258",
    "gow-ssh-safe-20261019-085204-2331337",
    "gow-ssh-safe-20261019-073226-1207417",
    "gow-nav-calibration-20261018-224752",
    "gow-auto-scene-20261018-205053",
    "gow-sparse-upload-20261018-201012",
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
MISSING = re.compile(r"Opening path (/app0/\S+) failed")
FATAL = re.compile(
    r"Unhandled access violation at code address (0x[0-9a-fA-F]+): "
    r"(\w+) (?:to|from) address (0x[0-9a-fA-F]+)",
    re.IGNORECASE,
)
IMPORTANT = (
    "PERM_TRACE_", "lib.audio3d", "lib.ajm", "sys_files.",
    "unreachable code", "access violation", "compiling graphics pipeline",
    "compiling compute pipeline", "videoout_flip_timing", "audio_producer_timing",
    "srt walker", "gpu command processor", "shader resource", "shader dma",
    "unhandled", "scePadInit", "sceUserServiceInitialize",
)
MAX_BYTES = 18 * 1024 * 1024

def read_archive(path: Path, choose: str = "A_DENSE_CPU_UPLOAD") -> list[dict]:
    result = []
    if not path.is_file():
        return result
    try:
        with tarfile.open(path, "r:gz") as bundle:
            matches = []
            for member in bundle:
                if not member.isfile() or not member.name.endswith("full-console.log"):
                    continue
                if member.size > 200 * 1024 * 1024:
                    continue
                matches.append(member)
                if len(matches) >= 10:
                    break
            matches.sort(key=lambda m: (
                0 if choose in m.name else 1, m.name))
            for member in matches[:2]:
                handle = bundle.extractfile(member)
                if handle is None:
                    continue
                data = handle.read(MAX_BYTES)
                result.append({"source": str(path), "member": member.name,
                               "total_bytes": member.size, "sample_truncated": member.size > MAX_BYTES,
                               "console": data.decode("utf-8", errors="replace")})
    except (OSError, tarfile.TarError, EOFError) as exc:
        result.append({"source": str(path), "error": str(exc)[:250]})
    return result

def fingerprint_console(item: dict) -> dict:
    if "console" not in item:
        return item
    raw = item["console"]
    lines = ANSI.sub("", raw).splitlines()
    keys = {
        "startup_game_mount": 0,
        "menu_navigation_logs": 0,
        "video_flip_events": 0,
        "audio_producer_events": 0,
        "permutation_active": 0,
        "permutation_created": 0,
        "permutation_reused": 0,
        "vulkan_device_loss": 0,
        "fatal_guest_crashes": 0,
        "audio3d_terminated": 0,
    }
    missing = {}
    signal = []
    fatal_addresses = []
    for idx, line in enumerate(lines):
        lower = line.lower()
        if "mounted '/app0'" in lower:
            keys["startup_game_mount"] += 1
        if "continue pressed" in lower or "world ready" in lower:
            keys["menu_navigation_logs"] += 1
        if "videoout_flip_timing" in lower:
            keys["video_flip_events"] += 1
        if "audio_producer_timing" in lower:
            keys["audio_producer_events"] += 1
        if "perm_trace_active" in lower:
            keys["permutation_active"] += 1
        if "perm_trace_created" in lower:
            keys["permutation_created"] += 1
        if "perm_trace_reused" in lower:
            keys["permutation_reused"] += 1
        if "vk_error_device_lost" in lower:
            keys["vulkan_device_loss"] += 1
        if "sceaudio3dterminate" in lower:
            keys["audio3d_terminated"] += 1
        if match := MISSING.search(line):
            missing[match[1]] = missing.get(match[1], 0) + 1
        if m := FATAL.search(line):
            keys["fatal_guest_crashes"] += 1
            fatal_addresses.append({
                "code_address": m[1], "operation": m[2],
                "target_address": m[3], "line": idx+1
            })
        if any(k in lower for k in IMPORTANT) and len(signal) < 130:
            signal.append({"line": idx + 1, "text": line[:480]})
    return {
        "source": item["source"], "member": item["member"],
        "total_bytes": item["total_bytes"],
        "sample_truncated": item["sample_truncated"],
        "sample_lines": len(lines),
        "counters": keys, "missing_paths": missing,
        "fatal_address": fatal_addresses,
        "selected_early_and_fatal_events": signal[:65] + signal[-35:] if len(signal) > 100 else signal,
        "gameplay_reached_by_log": keys["video_flip_events"] >= 8,
        "absence_is_inconclusive_if_sample_truncated": item["sample_truncated"]
    }

def locate_baselines(logs: Path, project: Path, exclude: Path) -> list[Path]:
    search = [logs, project / "GodOfWarRagnarok" / "logs"]
    found = {}
    for folder in search:
        if not folder.is_dir():
            continue
        for stem in PREFERRED:
            for p in folder.glob(stem + "*.tar.gz"):
                if p.resolve() == exclude.resolve():
                    continue
                found[str(p.resolve())] = p
        # Only when preferred archives are unavailable, search a small fallback.
        if len(found) < 3:
            for pattern in ("gow-ssh-safe-20261019*.tar.gz",
                            "gow-audio3d-*.tar.gz", "gow-auto-scene-*.tar.gz"):
                for p in list(folder.glob(pattern))[:7]:
                    if p.resolve() != exclude.resolve():
                        found[str(p.resolve())] = p
    ordered = sorted(found.values(), key=lambda p: (
        next((i for i,k in enumerate(PREFERRED) if k in p.name), 999),
        p.name
    ))
    return ordered[:9]

def check_guest_files(root: Path) -> dict:
    known = (
        "exec/wad/orbis_le/sys_files.texpack.toc",
        "exec/wad/orbis_le/sys_files.lodpack.toc",
        "exec/sound/orbis_le/sys_files.audiopack.toc",
    )
    result = {"mounted_game_root": str(root), "root_is_directory": root.is_dir(),
              "files": {}}
    for relative in known:
        target = root / relative
        info = {"exists": target.is_file(), "parent_exists": target.parent.is_dir()}
        if target.is_file():
            try:
                info["bytes"] = target.stat().st_size
            except OSError:
                pass
        result["files"][relative] = info
    return result

def elf_fault_context(root: Path, code_address: int) -> dict:
    binary = root / "eboot.bin"
    info = {"game_eboot": str(binary), "guest_fault_address": hex(code_address)}
    if not binary.is_file():
        info["status"] = "NO_EBOOT_BIN"
        return info
    try:
        with binary.open("rb") as file:
            header = file.read(64)
            if len(header) < 64 or header[:4] != b"\x7fELF":
                info["status"] = "NOT_PLAIN_ELF"
                info["magic"] = header[:8].hex()
                return info
            if header[4] != 2 or header[5] != 1:
                info["status"] = "UNSUPPORTED_ELF_CLASS_OR_ENDIAN"
                return info
            phoff = struct.unpack_from("<Q", header, 32)[0]
            phentsize, phnum = struct.unpack_from("<HH", header, 54)
            if phentsize < 56 or phnum > 256:
                info["status"] = "UNEXPECTED_PROGRAM_HEADERS"
                return info
            file.seek(phoff)
            phdrs = file.read(phentsize * phnum)
            for i in range(phnum):
                phdr = phdrs[i*phentsize:(i+1)*phentsize]
                if len(phdr) < 56:
                    continue
                ptype, flags, offset, vaddr, _, filesz, memsz, _ = struct.unpack("<IIQQQQQQ", phdr[:56])
                if ptype != 1 or not vaddr <= code_address < vaddr + memsz:
                    continue
                relative = code_address - vaddr
                info.update({"segment_index": i, "segment_vaddr": hex(vaddr),
                             "segment_file_offset": hex(offset),
                             "segment_executable": bool(flags & 1),
                             "segment_size": filesz})
                if relative >= filesz:
                    info["status"] = "MEMORY_ONLY_SEGMENT_PORTION"
                    return info
                first = max(0, relative - 32)
                file.seek(offset + first)
                sample = file.read(min(96, filesz - first))
                info["status"] = "ADDRESS_IN_PLAIN_ELF_LOAD_SEGMENT"
                info["read_only_code_window_sha256"] = hashlib.sha256(sample).hexdigest()
                info["code_window_vaddr"] = hex(vaddr + first)
                info["code_window_hex"] = sample.hex()
                return info
            info["status"] = "FAULT_ADDRESS_NOT_IN_PLAIN_ELF_SEGMENT"
            return info
    except (OSError, ValueError, struct.error) as exc:
        info["status"] = "INSPECTION_ERROR"
        info["error"] = str(exc)[:240]
        return info

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--failed-archive", type=Path, required=True)
    parser.add_argument("--game-root", type=Path, default=Path("/mnt/roms-all/ps4-games/CUSA34384"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.failed_archive.is_file():
        raise SystemExit("FAILED_ARCHIVE_MISSING_STOP_NO_GAME")
    failed_entries = read_archive(args.failed_archive)
    if not any("console" in e for e in failed_entries):
        raise SystemExit("FAILED_ARCHIVE_HAS_NO_CONSOLE")
    failed = fingerprint_console(failed_entries[0])
    prior_files = locate_baselines(args.root/"logs", args.root, args.failed_archive)
    baselines = []
    for path in prior_files:
        try:
            for item in read_archive(path):
                if "console" in item:
                    baselines.append(fingerprint_console(item))
                else:
                    baselines.append(item)
        except (OSError, ValueError) as exc:
            baselines.append({"source": str(path), "error": str(exc)[:200]})
    healthy = [row for row in baselines if row.get("gameplay_reached_by_log")]
    files = check_guest_files(args.game_root)
    fatal = failed.get("fatal_address", [])
    guest = elf_fault_context(args.game_root, int(fatal[-1]["code_address"], 16)) if fatal else {
        "status": "NO_GUEST_CRASH_ADDRESS_RECORDED"}
    overlap = {
        name: [row.get("source") for row in healthy if name in row.get("missing_paths", {})]
        for name in failed.get("missing_paths", {})
    }
    report = {
        "date": dt.datetime.now().astimezone().isoformat(),
        "mode": "READ_ONLY_NO_EMULATOR_RUN",
        "failed": failed,
        "prior_archives_examined": len(prior_files),
        "baseline_consoles_examined": len(baselines),
        "log_verified_gameplay_baselines": len(healthy),
        "missing_paths_seen_in_gameplay_baselines": overlap,
        "prior": baselines,
        "game_files": files,
        "guest_elf": guest,
        "interpretation": {
            "shader_fix_in_gameplay": "NOT_TESTED_FAILED_BEFORE_RENDERING",
            "game_file_enoent_is_causal": "NOT_PROVEN",
            "next_action": ("EXAMINE_STARTUP_DELTA_OR_CRASH_INSTRUCTION_BEFORE_ANOTHER_RUN"
                            if healthy else "FIND_PREVIOUS_PROVEN_GAMEPLAY_LOG_ARCHIVE"),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2)+"\n")
    print("READ_ONLY_STARTUP_AUDIT=PASS")
    print("FAILED_GAME_RUNTIME_SECONDS_APPROX=7.5 (from prior runtime report)")
    print("FAILED_GUEST_CRASH="+json.dumps(fatal))
    print("BASELINE_ARCHIVES_EXAMINED="+str(len(prior_files)))
    print("GAMEPLAY_BASELINE_CONSOLES="+str(len(healthy)))
    print("BASELINES_WITH_SAME_ENOENT="+json.dumps(overlap))
    print("ELF_FAULT_MAPPING_STATUS="+guest.get("status","UNKNOWN"))
    print("PERMUTATION_SCENE_RELEVANCE=NOT_MEASURED")
    print("GAME_FILES_AND_SAVES_EDITED=NO")
    print("EMULATOR_GAMEPLAY_RUNS=ZERO")
    print("REPORT="+str(args.out))

if __name__ == "__main__":
    main()
