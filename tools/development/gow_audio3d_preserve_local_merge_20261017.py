#!/usr/bin/env python3
"""Conservatively 3-way merge opt-in Audio3D queue changes into local source.

Preserve every local-only hunk from the proven game tree. Never write the
original source, create a guessed patch, or run a best-effort conflicted build.
"""
from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys


SCOPES = (
    ("audio3d_globals",
     "// SPDX-FileCopyrightText",
     "struct AudioOutBufferInfo {"),
    ("associated_pcm_drain",
     "static s32 DrainAssociatedPorts(Port& port) {",
     "s32 PS4_SYSV_ABI sceAudio3dAudioOutClose("),
    ("associated_pcm_enqueue",
     "s32 PS4_SYSV_ABI sceAudio3dAudioOutOutput(",
     "s32 PS4_SYSV_ABI sceAudio3dAudioOutOutputs("),
    ("audio3d_port_push",
     "s32 PS4_SYSV_ABI sceAudio3dPortPush(",
     "s32 PS4_SYSV_ABI sceAudio3dPortQueryDebug("),
)

PASS_THROUGH_OPERATIONS = (
    "AudioOut::sceAudioOutOutput(handle, buffer.data());",
    "aout.pending.emplace_back(src, src + aout.buffer_bytes);",
    "return ORBIS_AUDIO3D_ERROR_NOT_READY;",
)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def require(ok: bool, why: str) -> None:
    if not ok:
        raise ValueError(why)


def mask(src: str) -> tuple[str, dict[str, int]]:
    regions = []
    for name, start, end in SCOPES:
        require(src.count(start) == 1, "UNIQUE_SCOPE_BEGIN_MISSING:" + name)
        require(src.count(end) == 1, "UNIQUE_SCOPE_END_MISSING:" + name)
        a, b = src.index(start), src.index(end)
        require(0 <= a < b, "INVALID_SCOPE_BOUNDARY:" + name)
        regions.append((a, b, name))
    regions.sort()
    for (_, prev_end, _), (next_begin, _, _) in zip(regions, regions[1:]):
        require(prev_end <= next_begin, "OVERLAPPING_AUDIO3D_SCOPES")
    ret = []
    previous = 0
    counts = {}
    for first, last, name in regions:
        ret.append(src[previous:first])
        ret.append(f"\n__MASKED_APPROVED_AUDIO3D_SCOPE_{name}__\n")
        counts[name] = src[first:last].count("\n")
        previous = last
    ret.append(src[previous:])
    return "".join(ret), counts


def inspect(local: str, base: str, trial: str, merged: str, header: str) -> dict:
    require("mutable std::recursive_mutex mutex;" in header,
            "LOCAL_AUDIO3D_PORT_MUTEX_ABI_IS_NOT_RECURSIVE")
    require("std::vector<AssociatedAudioOutPort> audioout_ports;" in header,
            "LOCAL_AUDIO3D_ASSOCIATED_OUTPUT_ABI_CHANGED")
    require("SHADPS4_AUDIO3D_DIRECT_DRAIN" not in local,
            "LOCAL_AUDIO3D_HAS_ITS_OWN_EAGER_DRAIN_IMPLEMENTATION")
    base_rest, _ = mask(base)
    trial_rest, _ = mask(trial)
    require(base_rest == trial_rest,
            "GITHUB_AUDIO3D_TRIAL_CHANGES_UNRELATED_FUNCTIONS")
    local_rest, original_lengths = mask(local)
    merged_rest, merged_lengths = mask(merged)
    require(local_rest == merged_rest,
            "MERGED_LOCAL_CODE_OUTSIDE_APPROVED_AUDIO3D_SCOPES_CHANGED")
    for token in PASS_THROUGH_OPERATIONS:
        count = local.count(token)
        require(count >= 1 and merged.count(token) == count,
                "LOCAL_PCM_GUEST_DATA_OR_AUDIOOUT_FORWARDING_CHANGED:" + token)
    require("if (Audio3dDirectDrainEnabled()) {" in merged,
            "EAGER_AUDIO3D_ONLY_WHEN_OPTED_IN")
    require(
        "std::unique_lock<std::recursive_mutex> serialized(port.mutex, std::defer_lock);"
        in merged,
        "OPT_IN_SERIALIZATION_MISSING")
    require(
        "if (Audio3dDirectDrainEnabled()) {\n        serialized.lock();"
        in merged, "AUDIO3D_DIRECT_DRAIN_FIFO_LOCK_MISSING")
    require("if (const s32 ret = DrainAssociatedPorts(port); ret < 0)" in merged,
            "PCM_DRAIN_ERRORS_NOT_PROPAGATED")
    for label in (
        'ReportAssociatedAudio(handle, "enqueue"',
        'ReportAssociatedAudio(handle, "forward"',
        'ReportAssociatedAudio(handle, "full"',
        'AUDIO3D_PUSH count={}',
    ):
        require(label in merged, "AUDIO3D_TELEMETRY_MISSING:" + label)
    require('std::getenv("SHADPS4_AUDIO3D_DIRECT_DRAIN")' in merged,
            "AUDIO3D_DIRECT_DRAIN_ENV_GATE_MISSING")
    require(merged.count("AudioOut::sceAudioOutOutput(handle, buffer.data());") == 1,
            "AUDIO3D_BACKEND_PCM_SUBMISSION_MODIFIED")
    return {
        "local_scope_lines": original_lengths,
        "merged_scope_lines": merged_lengths,
        "local_only_changed": local != base,
        "preserved_outside_scope": True,
        "pcm_operation_counts": {key: merged.count(key)
                                 for key in PASS_THROUGH_OPERATIONS},
    }


def merge_sources(local: str, base: str, trial: str, header: str) -> tuple[str, dict]:
    # Source semantic checks are done before the actual merge to ensure even a
    # clean exit from Git does not justify unrelated source replacement.
    mask(local)
    require(mask(base)[0] == mask(trial)[0],
            "UNRELATED_CHANGES_IN_PROPOSED_AUDIO3D_TRIAL")
    require("mutable std::recursive_mutex mutex;" in header,
            "AUDIO3D_LOCAL_HEADER_NOT_RECURSIVE")
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(prefix="gow-audio3d-merge-") as tmp:
        root = Path(tmp)
        input_local = root / "local.cpp"
        input_base = root / "git-baseline.cpp"
        input_trial = root / "git-trial.cpp"
        for filename, source in (
            (input_local, local), (input_base, base), (input_trial, trial)
        ):
            filename.write_text(source, encoding="utf-8")
        process = subprocess.run(
            ["git", "merge-file", "--stdout",
             "-L", "LOCAL_PROVEN_AUDIO3D",
             "-L", "GITHUB_REFERENCE_AUDIO3D",
             "-L", "PINNED_AUDIO3D_TRIAL",
             str(input_local), str(input_base), str(input_trial)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=15)
    if process.returncode != 0:
        raise ValueError(
            "LOCAL_AUDIO3D_SOURCE_MERGE_CONFLICT:"
            + f"status={process.returncode} "
            + process.stderr[:500].replace("\n", " ") +
            " (original and executable preserved)")
    result = process.stdout
    require(not any(token in result for token in (
        "<<<<<<< LOCAL_PROVEN_AUDIO3D", ">>>>>>> PINNED_AUDIO3D_TRIAL")),
        "CONFLICT_MARKER_IN_TRIAL_OUTPUT")
    details = inspect(local, base, trial, result, header)
    details["three_way_git_merge"] = "CLEAN"
    details["merged_hunks"] = len([
        op for op in difflib.SequenceMatcher(
            None, local.splitlines(), result.splitlines(), autojunk=False
        ).get_opcodes() if op[0] != "equal"])
    return result, details


def main() -> None:
    if len(sys.argv) != 7:
        raise SystemExit(
            "Usage: merger.py LOCAL.cpp BASE.cpp TRIAL.cpp LOCAL.h OUTPUT.cpp REPORT.json")
    local, base, trial, header, output, report = map(Path, sys.argv[1:])
    manifest = {
        "status": "FAILED",
        "local_original_path": str(local),
        "merge_mode": "NON_MUTATING_GIT_THREE_WAY",
    }
    try:
        require(local.resolve() != output.resolve(), "WILL_NOT_OVERWRITE_PROVEN_LOCAL_AUDIO3D")
        require(local.is_file() and not local.is_symlink(), "LOCAL_AUDIO3D_SOURCE_NOT_REGULAR")
        require(header.is_file() and not header.is_symlink(), "LOCAL_AUDIO3D_HEADER_NOT_REGULAR")
        require(not output.exists(), "REFUSING_TO_REPLACE_PREEXISTING_OUTPUT")
        inputs = [p.read_bytes() for p in (local, base, trial, header)]
        manifest["input_sha256"] = dict(zip(
            ("local", "base", "trial", "header"), map(sha, inputs)))
        result, detail = merge_sources(*[s.decode("utf-8") for s in inputs])
        require(local.read_bytes() == inputs[0], "LOCAL_AUDIO3D_CHANGED_DURING_MERGE")
        encoded = result.encode("utf-8")
        output.write_bytes(encoded)
        require(output.read_bytes() == encoded, "TRIAL_MERGE_WRITE_MISMATCH")
        manifest.update({
            "status": "PASS",
            "output_sha256": sha(encoded),
            "source_preserved_sha256": sha(inputs[0]),
            **detail,
        })
        print("LOCAL_AUDIO3D_DIFFERENCES=PRESERVED")
        print("APPROVED_FOUR_AUDIO3D_SCOPES_ONLY=PASS")
        print("AUDIO3D_OPT_IN_MERGE=PASS")
        print("LOCAL_ORIGINAL_SOURCE_UNCHANGED=YES")
        print(f"MERGED_AUDIO3D_SHA256={manifest['output_sha256']}")
    except (ValueError, OSError, UnicodeError, subprocess.TimeoutExpired) as error:
        manifest["error"] = str(error)
        print("AUDIO3D_SAFE_MERGE_FAILED:" + str(error), file=sys.stderr)
        raise SystemExit(2)
    finally:
        report.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
