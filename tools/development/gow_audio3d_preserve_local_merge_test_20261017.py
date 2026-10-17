#!/usr/bin/env python3
"""Run real Audio3D C++ source and three-way merge regression scenarios locally."""
from pathlib import Path
import importlib.util
import sys


def ensure(cond, msg):
    if not cond:
        raise AssertionError(msg)


def get_module(path):
    spec = importlib.util.spec_from_file_location("gow_audio3d_preserve_merge", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_test(mod, original: str, github_trial: str, header: str) -> None:
    # Exact reference == local: Git must produce identical tested trial source.
    merged, detail = mod.merge_sources(original, original, github_trial, header)
    ensure(merged == github_trial, "EXACT_GITHUB_BASELINE_3WAY_MERGE_NOT_IDENTITY")
    ensure(detail["three_way_git_merge"] == "CLEAN", "UNEXPECTED_MERGE_STATUS")
    print("AUDIO3D_REFERENCE_SOURCE_MERGE=PASS")

    # A local change OUTSIDE the trial's approved functions survives unchanged.
    safe_anchor = "s32 PS4_SYSV_ABI sceAudio3dPortFlush(const OrbisAudio3dPortId port_id) {"
    ensure(original.count(safe_anchor) == 1, "PORT_FLUSH_SYMBOL_CHANGED")
    local_with_unrelated_fix = original.replace(
        safe_anchor, safe_anchor + "\n    // KEEP_MY_ALREADY_WORKING_LOCAL_FLUSH_FIX", 1)
    merged2, _ = mod.merge_sources(
        local_with_unrelated_fix, original, github_trial, header)
    ensure("KEEP_MY_ALREADY_WORKING_LOCAL_FLUSH_FIX" in merged2,
           "LOCAL_AUDIO3D_CHANGES_OUTSIDE_TRIAL_DROPPED")
    ensure(mod.mask(local_with_unrelated_fix)[0] == mod.mask(merged2)[0],
           "OUTSIDE_AUDIO3D_CHANGES_CHANGED")
    print("UNRELATED_LOCAL_AUDIO3D_WORK=RETAINED_PASS")

    # A local change inside an approved function but outside the trial's
    # edited hunk must also survive.
    safe_inner = 'LOG_DEBUG(Lib_Audio3d, "called, handle = {}, ptr = {}", handle, ptr);'
    ensure(original.count(safe_inner) == 1, "AUDIO3D_LOGGING_ANCHOR_CHANGED")
    local_inner = original.replace(
        safe_inner,
        'LOG_DEBUG(Lib_Audio3d, "called, handle = {}, ptr = {} (LOCAL)", handle, ptr);',
        1)
    merged3, _ = mod.merge_sources(local_inner, original, github_trial, header)
    ensure('called, handle = {}, ptr = {} (LOCAL)' in merged3,
           "LOCAL_AUDIO3D_CHANGE_IN_APPROVED_SCOPE_DROPPED")
    print("NONOVERLAPPING_LOCAL_AUDIO3D_OUTPUT_CHANGE=RETAINED_PASS")

    # A change in the same PCM copy hunk MUST NOT get overwritten by the
    # trial, whether Git finds a conflict or would otherwise merge it.
    local_pcm = original.replace(
        'aout.pending.emplace_back(src, src + aout.buffer_bytes);',
        'aout.pending.emplace_back(src, src + aout.buffer_bytes + 0);',
        1)
    ensure(local_pcm != original, "OVERLAP_NEGATIVE_FIXTURE_NO_CHANGE")
    try:
        mod.merge_sources(local_pcm, original, github_trial, header)
    except ValueError:
        print("OVERLAPPING_PCM_LOCAL_EDIT_FAIL_CLOSED=PASS")
    else:
        raise AssertionError("OVERLAPPING_LOCAL_PCM_MODIFICATION_WAS_ACCEPTED")

    # Incompatible source ABI must be rejected, never silently transformed.
    incompatible_header = header.replace(
        "mutable std::recursive_mutex mutex;",
        "mutable std::mutex mutex;", 1)
    ensure(incompatible_header != header, "HEADER_ABI_NEGATIVE_FIXTURE_EMPTY")
    try:
        mod.merge_sources(original, original, github_trial, incompatible_header)
    except ValueError as exc:
        ensure("NOT_RECURSIVE" in str(exc), "UNEXPECTED_HEADER_ABI_ERROR")
        print("INCOMPATIBLE_AUDIO3D_HEADER_FAIL_CLOSED=PASS")
    else:
        raise AssertionError("INCOMPATIBLE_HEADER_ACCEPTED")

    # Bad future GitHub diff that changes unrelated audio HLE code is caught.
    modified_trial = github_trial.replace(
        safe_anchor, safe_anchor + "\n    // UNAUTHORIZED_UNRELATED_HLE_EDIT", 1)
    ensure(modified_trial != github_trial, "SCOPE_NEGATIVE_FIXTURE_EMPTY")
    try:
        mod.merge_sources(original, original, modified_trial, header)
    except ValueError as exc:
        ensure("UNRELATED" in str(exc), "UNEXPECTED_UNRELATED_HLE_DIFF_ERROR")
        print("GITHUB_UNRELATED_AUDIO_HLE_CHANGE_FAIL_CLOSED=PASS")
    else:
        raise AssertionError("TRIAL_WITH_UNRELATED_HLE_CHANGE_ACCEPTED")

    print("ALL_AUDIO3D_PRESERVE_LOCAL_MERGE_TESTS=PASS cases=6")


def main():
    if len(sys.argv) != 5:
        raise SystemExit("Usage: tests.py HELPER.py PINNED_BASE.cpp PINNED_TRIAL.cpp LOCAL_AUDIO3D.h")
    helper, base, trial, header = [Path(p) for p in sys.argv[1:]]
    module = get_module(helper)
    run_test(module, base.read_text(), trial.read_text(), header.read_text())


if __name__ == "__main__":
    main()
