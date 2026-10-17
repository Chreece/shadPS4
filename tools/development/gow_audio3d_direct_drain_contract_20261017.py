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
    orig_lines, trial_lines = original.splitlines(True), trial.splitlines(True)
    find = lambda phrase: next((i for i, line in enumerate(orig_lines)
                                if phrase in line), -1)
    scopes = [
        ("includes_globals", 0, find("struct AudioOutBufferInfo")),
        ("associated_drain", find("static s32 DrainAssociatedPorts("),
         find("s32 PS4_SYSV_ABI sceAudio3dAudioOutClose(")),
        ("associated_enqueue", find("s32 PS4_SYSV_ABI sceAudio3dAudioOutOutput("),
         find("s32 PS4_SYSV_ABI sceAudio3dAudioOutOutputs(")),
        ("port_push", find("s32 PS4_SYSV_ABI sceAudio3dPortPush("),
         find("s32 PS4_SYSV_ABI sceAudio3dPortQueryDebug(")),
    ]
    check(all(start >= 0 and end > start for _,start,end in scopes),
          "ORIGINAL_SOURCE_FUNCTION_ANCHORS_CHANGED")
    touched = set()
    for opcode,i1,i2,j1,j2 in difflib.SequenceMatcher(
            None,orig_lines,trial_lines,autojunk=False).get_opcodes():
        if opcode == "equal":
            continue
        begin_scope = scope_of(i1, scopes)
        end_scope = scope_of(i2 if i1!=i2 else i1, scopes)
        check(begin_scope and begin_scope == end_scope,
              "CHANGE_OUTSIDE_ALLOWED_AUDIO3D_HLE_PATH:"
              + repr((opcode,i1,i2,j1,j2,begin_scope,end_scope)))
        touched.add(begin_scope)
    check(touched == {s[0] for s in scopes},
          "UNEXPECTED_AUDIO3D_HLE_CHANGE_SCOPES:"+repr(touched))
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
        "AUDIO3D_TEST_HEAD=67dce2ddcea88e092b82c8fe550c8c815cfecbe1",
        "AUDIO3D_ORIGINAL_BLOB=b1b1445eda8823f07b4f66c07afcb175f7ee04b9",
        "AUDIO3D_TEST_BLOB=837686242b59e10b33ec5e5c42892dd7d37f8b8c",
        "UFFD_BATCHED_READ_2314_BASELINE_CHANGED",
        "uffd-batched-read-2314-unchanged.txt",
        "SOURCE_MATCHES_GRAPHICS_CORRECT_2059_CANDIDATE=PASS",
        "ISOLATED_SOURCE_SNAPSHOT_RESTORED=YES",
        "STAGE=STAGE_AUDIO3D_EXACT_SOURCE",
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
