#!/usr/bin/env python3
"""Fail-closed HLE audio trial: verify unchanged GoW graphics and original Audio3D default."""
from pathlib import Path
import ast
import difflib
import hashlib
import sys


def check(condition, name):
    if not condition:
        raise AssertionError(name)


def scope_of(index, scopes):
    for name, start, end in scopes:
        if start <= index <= end:
            return name
    return None


def main():
    if len(sys.argv) != 6:
        raise SystemExit("usage: contract.py ORIGINAL_AUDIO3D.cpp TRIAL_AUDIO3D.cpp RUNNER.sh OBSERVER.py AUDIO3D.h")
    original, trial, runner, observer, header = (
        Path(x).read_text(encoding="utf-8") for x in sys.argv[1:])
    # Compare the code *outside* four approved scopes byte-for-byte. This
    # avoids positional diff ambiguity when the user's local source contains
    # unrelated edits or comments at nearby line numbers.
    scopes = [
        ("includes_globals", "// SPDX-FileCopyrightText", "struct AudioOutBufferInfo {"),
        ("associated_drain", "static s32 DrainAssociatedPorts(Port& port) {",
         "s32 PS4_SYSV_ABI sceAudio3dAudioOutClose("),
        ("associated_enqueue", "s32 PS4_SYSV_ABI sceAudio3dAudioOutOutput(",
         "s32 PS4_SYSV_ABI sceAudio3dAudioOutOutputs("),
        ("port_push", "s32 PS4_SYSV_ABI sceAudio3dPortPush(",
         "s32 PS4_SYSV_ABI sceAudio3dPortQueryDebug("),
    ]

    def approved_parts(src):
        regions = []
        for name, begin, end in scopes:
            check(src.count(begin) == src.count(end) == 1,
                  "AUDIO3D_APPROVED_SCOPE_ABI_DRIFT:" + name)
            i,j = src.index(begin),src.index(end)
            check(i<j,"AUDIO3D_SCOPE_END_PRECEDES_BEGIN:"+name)
            regions.append((i,j,name))
        regions.sort()
        masked = []
        previous = 0
        bodies = {}
        for i,j,name in regions:
            check(previous <= i, "AUDIO3D_APPROVED_SCOPES_OVERLAP")
            masked.append(src[previous:i])
            masked.append("\n__APPROVED_AUDIO3D_SCOPE_" + name + "__\n")
            bodies[name]=src[i:j]
            previous=j
        masked.append(src[previous:])
        return "".join(masked),bodies

    original_outside,original_bodies=approved_parts(original)
    trial_outside,trial_bodies=approved_parts(trial)
    check(original_outside==trial_outside,
          "LOCAL_AUDIO3D_CHANGES_OUTSIDE_APPROVED_QUEUE_SCOPES")
    touched={name for name in original_bodies
             if original_bodies[name]!=trial_bodies[name]}
    check(touched=={s[0] for s in scopes},
          "AUDIO3D_MERGE_MISSING_ONE_OR_MORE_APPROVED_SCOPES:"+repr(touched))
    print("AUDIO3D_SOURCE_DIFF_ONLY_IN_FOUR_APPROVED_HLE_SCOPES=PASS")

    check('std::getenv("SHADPS4_AUDIO3D_DIRECT_DRAIN")' in trial
          and "return p && p[0] == '1' && p[1] == '\\0';" in trial,
          "DIRECT_DRAIN_NOT_STRICTLY_OPT_IN")
    check('std::getenv("SHADPS4_AUDIO3D_QUEUE_PROBE")' in trial,
          "PCM_QUEUE_DIAGNOSTICS_MISSING")
    check(trial.count("AudioOut::sceAudioOutOutput(handle, buffer.data());")
          == original.count("AudioOut::sceAudioOutOutput(handle, buffer.data());") == 1,
          "AUDIO3D_FORWARDED_PCM_COPY_OR_BACKEND_CHANGED")
    check(trial.count('aout.pending.emplace_back(src, src + aout.buffer_bytes);')
          == original.count('aout.pending.emplace_back(src, src + aout.buffer_bytes);') == 1,
          "GUEST_PCM_DATA_COPY_CHANGED")
    check(trial.count("ORBIS_AUDIO3D_ERROR_NOT_READY")
          == original.count("ORBIS_AUDIO3D_ERROR_NOT_READY"),
          "AUDIO3D_BACKPRESSURE_ERROR_SEMANTICS_CHANGED")
    check('if (Audio3dDirectDrainEnabled()) {' in trial
          and 'if (const s32 ret = DrainAssociatedPorts(port); ret < 0)' in trial,
          "OPT_IN_FORWARD_NOT_GUARDED_OR_ERRORS_IGNORED")
    check('std::unique_lock<std::recursive_mutex> serialized(port.mutex, std::defer_lock);'
          in trial and
          'if (Audio3dDirectDrainEnabled()) {\n        serialized.lock();' in trial,
          "DIRECT_DRAIN_MUST_SERIALIZE_PORT_PUSH_FIFO")

    check('const OrbisAudio3dPortId port_id' in trial,
          "AUDIO3D_API_SIGNATURE_MISSING")
    check('mutable std::recursive_mutex mutex;' in header
          and trial.count('std::scoped_lock lock{port.mutex};')
              == original.count('std::scoped_lock lock{port.mutex};'),
          "AUDIO3D_RECURSIVE_MUTEX_CONTRACT")
    # The original Port has a recursive mutex; callers keep that port lock
    # while submitting eagerly, serializing PCM ordering with PortPush.
    check("AUDIO3D_ASSOC operation={}" in trial
          and "AUDIO3D_PUSH count={}" in trial
          and 'ReportAssociatedAudio(handle, "forward"' in trial
          and 'ReportAssociatedAudio(handle, "enqueue"' in trial
          and 'ReportAssociatedAudio(handle, "full"' in trial,
          "ACCEPTED_FORWARDED_BACKPRESSURE_COUNTERS_MISSING")
    check('current == 1 || (current & (current - 1)) == 0 || (current & 255) == 0'
          in trial, "AUDIO3D_PROFILER_MUST_BE_RATE_LIMITED")
    print("DEFAULT_AUDIO3D_OUTPUT_AND_PCM_QUEUE_SEMANTICS=PASS")
    print("OPT_IN_DIRECT_DRAIN_AND_PER_PORT_AUDIO_HEALTH=PASS")

    for proof in (
        "AUDIO3D_TEST_HEAD=0821f7c1d07bbe8cc3daa91e901c573b34500a98",
        "AUDIO3D_ORIGINAL_BLOB=b1b1445eda8823f07b4f66c07afcb175f7ee04b9",
        "AUDIO3D_TEST_BLOB=3b6d5bf02ba954a4e30ce9cd063897a0046bbde0",
        "UFFD_BATCHED_READ_2314_BASELINE_CHANGED",
        "uffd-batched-read-2314-unchanged.txt",
        "SOURCE_MATCHES_GRAPHICS_CORRECT_2059_CANDIDATE=PASS",
        "ISOLATED_SOURCE_SNAPSHOT_RESTORED=YES",
        "STAGE=STAGE_AUDIO3D_PRESERVE_LOCAL_MERGE",
        'LOCAL_MERGER="${4:?pinned source-preserving Audio3D merge helper required}"',
        'python3 -B "$LOCAL_MERGER"',
        'AUDIO3D_LOCAL_SOURCE_CANNOT_BE_SAFELY_MERGED; NO_BUILD',
        'cp -p "$OUT/merged-audio3d.cpp" "$SRC/$AUDIO3D_REL"',
        'LOCAL_PROVEN_AUDIO3D_SHA256=$LOCAL_AUDIO3D_SHA',
        'LOCAL_AUDIO3D_CHANGES_PRESERVED=PASS',

        "src/core/libraries/audio3d/audio3d.cpp.o",
        "export SHADPS4_AUDIO3D_DIRECT_DRAIN=1",
        "export SHADPS4_AUDIO3D_QUEUE_PROBE=1",
        "SSH_SESSION=UNCHANGED",
    ):
        check(proof in runner, "RUNNER_AUDIO3D_CONTRACT_MISSING:"+proof)
    check(runner.count('cmake --build "$BUILD" --target shadps4 --parallel 4') == 1,
          "MULTIPLE_FULL_EMULATOR_BUILDS")
    for forbidden in ("git reset", "git checkout", "pkill", "killall", "kill -9",
                      "systemctl stop", "systemctl restart", "sysctl -w"):
        check(forbidden not in runner, "UNSAFE_HOST_MUTATION:"+forbidden)
    print("FULL_GRAPHICS_UFFD_AUDIO_AND_SOURCE_RESTORE_GUARDS=PASS")

    ast.parse(observer, filename="gow_upstream_gameplay.py")
    for proof in ("AUDIO3D_ASSOC operation=", "AUDIO3D_PUSH count=",
                  "audio3d-queue-summary.json", "audio3d-queue-events.txt",
                  "audio3d_direct_drain", "audio_timing_summary",
                  "uffd-perf-summary.json", "read_calls", "read_events"):
        check(proof in observer, "MISSING_PCM_DELIVERY_OR_FPS_CORRELATION:"+proof)
    print("AUDIO3D_QUEUE_GUEST_SUBMISSION_AND_UFFD_PROBES=PASS")
    print("ALL_AUDIO3D_DIRECT_DRAIN_SOURCE_CONTRACTS=PASS")


if __name__ == "__main__":
    main()
