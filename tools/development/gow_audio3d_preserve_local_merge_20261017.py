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


def approved_scope(src: str, name: str) -> str:
    matches = [(start, end) for key, start, end in SCOPES if key == name]
    require(len(matches) == 1, "APPROVED_AUDIO3D_SCOPE_NOT_FOUND:" + name)
    start, end = matches[0]
    require(src.count(start) == src.count(end) == 1,
            "NON_UNIQUE_LOCAL_AUDIO3D_SCOPE:" + name)
    return src[src.index(start):src.index(end)]


def scope_add(src: str, name: str, old: str, replacement: str) -> str:
    # This cannot delete or rewrite an unrelated local function. Only a
    # verified, unique anchor inside the declared function may be extended.
    current = approved_scope(src, name)
    require(current.count(old) == 1,
            "SAFE_AUDIO3D_ADD_ONLY_ANCHOR_MISSING:" + name +
            " " + old[:80].replace("\n", " "))
    newer = current.replace(old, replacement, 1)
    return src.replace(current, newer, 1)


def anchored_add_only_overlay(local: str, base: str, trial: str) -> str:
    """Fallback if Git's overlapping context hunks conflict.

    This version adds the trial code at exact, unique semantic anchors but
    preserves all existing lines from the user's local source verbatim.
    Existing PCM queuing or forwarding code must be intact.
    """
    require(mask(base)[0] == mask(trial)[0],
            "UNAPPROVED_GITHUB_AUDIO3D_TRIAL_DIFF")
    value = local
    include_anchor = "#include <algorithm>\n"
    require(value.count(include_anchor) == 1,
            "LOCAL_AUDIO3D_INCLUDE_ANCHOR_MISSING")
    for inc in ("<array>", "<atomic>", "<cstdlib>", "<mutex>", "<string_view>"):
        include = "#include " + inc + "\n"
        if include not in value:
            value = scope_add(value, "audio3d_globals",
                              include_anchor, include_anchor + include)
    state = "static std::unique_ptr<Audio3dState> state;"
    base_middle = base.split(state, 1)[1].split("struct AudioOutBufferInfo {", 1)[0]
    trial_middle = trial.split(state, 1)[1].split("struct AudioOutBufferInfo {", 1)[0]
    require(not base_middle.strip() and
            "Audio3dDirectDrainEnabled()" in trial_middle and
            "ReportAssociatedAudio(" in trial_middle,
            "PINNED_AUDIO3D_GLOBAL_INSERTION_NOT_VERIFIED")
    value = scope_add(value, "audio3d_globals", state,
                      state + "\n\n" + trial_middle.strip("\n") + "\n")

    drain_head = "static s32 DrainAssociatedPorts(Port& port) {\n"
    trial_drain = approved_scope(trial, "associated_pcm_drain")
    split_head = trial_drain.split(drain_head, 1)
    require(len(split_head) == 2 and "    while (true) {" in split_head[1],
            "PINNED_DIRECT_DRAIN_HEAD_CHANGED")
    locking = split_head[1].split("    while (true) {", 1)[0]
    require("serialized.lock();" in locking and
            "std::unique_lock<std::recursive_mutex>" in locking,
            "PINNED_FIFO_LOCK_INSERTION_MISSING")
    value = scope_add(value, "associated_pcm_drain",
                      drain_head, drain_head + locking)
    forward = (
        "        const s32 ret = AudioOut::sceAudioOutOutput(handle, buffer.data());\n"
        "        if (ret < 0) {\n"
        "            return ret;\n"
        "        }\n"
    )
    require('ReportAssociatedAudio(handle, "forward"' in trial_drain,
            "PINNED_FORWARD_COUNTER_MISSING")
    value = scope_add(
        value, "associated_pcm_drain", forward,
        forward + '        ReportAssociatedAudio(handle, "forward", 0, port.parameters.queue_depth);\n')

    trial_output = approved_scope(trial, "associated_pcm_enqueue")
    limit = "                if (aout.pending.size() >= port.parameters.queue_depth) {\n"
    ret = "return ORBIS_AUDIO3D_ERROR_NOT_READY;"
    require(trial_output.count(limit) == trial_output.count(ret) == 1,
            "PINNED_AUDIO3D_BACKPRESSURE_HUNK_CHANGED")
    added_full = trial_output.split(limit, 1)[1].split(ret, 1)[0].rstrip(" \t")
    require('ReportAssociatedAudio(handle, "full"' in added_full,
            "PINNED_BACKPRESSURE_COUNT_MISSING")
    value = scope_add(value, "associated_pcm_enqueue", limit, limit + added_full)

    enq = "aout.pending.emplace_back(src, src + aout.buffer_bytes);"
    comment = "// Mirror sceAudioOutOutput's return of samples sent."
    require(trial_output.count(enq) == trial_output.count(comment) == 1,
            "PINNED_AUDIO3D_PCM_ENQUEUE_HUNK_CHANGED")
    after_enq = trial_output.split(enq, 1)[1].split(comment, 1)[0]
    require('ReportAssociatedAudio(handle, "enqueue"' in after_enq
            and 'if (Audio3dDirectDrainEnabled())' in after_enq,
            "PINNED_EAGER_DRAIN_INSERTION_MISSING")
    value = scope_add(value, "associated_pcm_enqueue",
                      enq, enq + after_enq)

    trial_push = approved_scope(trial, "audio3d_port_push")
    logend = "              magic_enum::enum_name(blocking));"
    next_step = "    if (!state->ports.contains(port_id))"
    require(trial_push.count(logend) == trial_push.count(next_step) == 1,
            "PINNED_AUDIO3D_PUSH_MARKERS_CHANGED")
    extra = trial_push.split(logend, 1)[1].split(next_step, 1)[0]
    require("audio3d_push_calls.fetch_add(" in extra,
            "PINNED_AUDIO3D_PUSH_COUNTER_MISSING")
    value = scope_add(value, "audio3d_port_push",
                      logend, logend + extra)
    return value


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
    if process.returncode == 0:
        result = process.stdout
        strategy = "CLEAN"
    elif 1 <= process.returncode <= 127:
        # Git's context-hunk conflict may be caused by an unrelated local
        # edit near an insertion. Try only verified add-only insertions;
        # never drop the local line or resolve a true PCM overlap by guessing.
        result = anchored_add_only_overlay(local, base, trial)
        strategy = "CONTEXT_CONFLICT_SAFELY_RESOLVED_BY_EXACT_ADD_ONLY_ANCHORS"
    else:
        raise ValueError("GIT_AUDIO3D_MERGE_PROCESS_FAILED:"
                         + str(process.returncode))
    require(not any(token in result for token in (
        "<<<<<<< LOCAL_PROVEN_AUDIO3D", ">>>>>>> PINNED_AUDIO3D_TRIAL")),
        "CONFLICT_MARKER_IN_TRIAL_OUTPUT")
    details = inspect(local, base, trial, result, header)
    details["three_way_git_merge"] = strategy
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
