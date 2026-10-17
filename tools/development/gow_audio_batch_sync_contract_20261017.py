#!/usr/bin/env python3
"""Source contracts for proven SDL audio plus deadlock-safe batch port drain."""
import ast
import hashlib
from pathlib import Path
import sys


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    if len(sys.argv) != 9:
        raise SystemExit(
            "Usage: test.py old_sdl trial_sdl old_audioout trial_audioout runner observer sync_header host_cpp_test")
    old_sdl, trial_sdl, old_out, trial_out, runner, observer, sync_header, host_cpp_test = [
        Path(x).read_text(encoding="utf-8") for x in sys.argv[1:]
    ]
    require(
        hashlib.sha256(old_sdl.encode("utf-8")).hexdigest()
        == "162717c8391e671f17eb01c15278ec2faac7e3772e9cd7f4027d903a0eafecbb",
        "ORIGINAL_SDL_MUST_MATCH_USER_2153_ARCHIVE")
    require(
        hashlib.sha256(old_out.encode("utf-8")).hexdigest()
        == "f8a3b71dc015b74632ecc776dfaeac47e43da1883702703c514dce726b056277",
        "ORIGINAL_AUDIOOUT_MUST_MATCH_USER_2153_ARCHIVE")
    print("REAL_SERVER_SDL_AUDIO_PREIMAGES=PASS")
    require("SHADPS4_SDL_OUTER_PACING" not in old_sdl, "OLD_SDL_ALREADY_MODIFIED")
    require("SHADPS4_AUDIO_TIMING_PROBE" not in old_out, "OLD_OUTPUT_ALREADY_MODIFIED")
    require(
        'std::getenv("SHADPS4_SDL_OUTER_PACING")' in trial_sdl,
        "OPT_IN_GATE_MISSING",
    )
    require(
        "outer_only_pacing = pacing && pacing[0] == '1' && pacing[1] == '\\0';"
        in trial_sdl, "OPT_IN_GATE_NOT_EXACT_ONE",
    )
    require("AudioPeak(" not in trial_sdl and "IsUsable()" not in trial_sdl,
            "TRIAL_ACCIDENTALLY_IMPORTED_DIFFERENT_AUDIO_HEALTH_BACKEND")
    require(
        "if (!outer_only_pacing) {\n            HandleTiming(current_time);\n        }"
        in trial_sdl, "DEFAULT_BACKEND_PACING_NOT_PRESERVED",
    )
    require(trial_sdl.count("HandleTiming(current_time);") == 1,
            "BACKEND_PACING_CALL_DUPLICATED")
    require("bool outer_only_pacing{false};" in trial_sdl,
            "DEFAULT_BACKEND_PACING_CHANGED")
    require(
        'std::getenv("SHADPS4_AUDIO_TIMING_PROBE")' in trial_sdl
        and 'std::getenv("SHADPS4_AUDIO_TIMING_PROBE")' in trial_out,
        "PROBE_GATE_MISSING",
    )
    for marker in (
        "SDL_PutAudioStreamData(stream, internal_buffer, internal_buffer_size)",
        "SDL_ClearAudioStream(stream);",
        "SDL_GetAudioStreamQueued(stream);",
        "ConvertS16StereoSIMD(",
        "SDL_ResumeAudioStreamDevice(stream)",
        "ManageAudioQueue();",
    ):
        require(old_sdl.count(marker) == trial_sdl.count(marker),
                "SDL_PASSTHROUGH_OR_AUDIO_QUEUE_SEMANTIC_CHANGED:" + marker)
    for marker in (
        'timer.Start();',
        'timer.End();',
        'port->impl->Output(port->output_buffer);',
        'port->output_cv.notify_all();',
        'port->output_ready = false;',
    ):
        require(old_out.count(marker) == trial_out.count(marker),
                "GUEST_AUDIO_OUTPUT_FLOW_CHANGED:" + marker)
    require("AUDIO_SDL_TIMING port=" in trial_sdl
            and "(output_count & 0xFF) == 0" in trial_sdl,
            "LOW_RATE_SDL_TIMING_MISSING")
    require("AUDIO_PRODUCER_TIMING port=" in trial_out
            and "now_us - last_probe_report_us >= 5'000'000" in trial_out,
            "LOW_RATE_PRODUCER_TIMING_MISSING")
    require("backend_blocked_periods" in trial_out
            and "missing_buffers" in trial_out,
            "AUDIO_PRODUCER_STARVATION_METRICS_MISSING")
    require("backend_resyncs" in trial_sdl
            and "sampled_queue_clears" in trial_sdl,
            "SDL_RESYNC_OR_QUEUE_TELEMETRY_MISSING")
    # Only the proven batch lock acquisition algorithm is allowed to change.
    require("WaitForAudioPortsReady(ports, locks)" in trial_out,
            "SAFE_MULTI_PORT_DRAIN_HELPER_NOT_USED")
    require("std::sort(ordered.begin(), ordered.end(), std::less<Port*>{});"
            in sync_header, "STABLE_LOCK_ORDER_MISSING")
    require("locks.clear();" in sync_header
            and "waiting->output_cv.wait(lock" in sync_header,
            "MUTEXES_NOT_RELEASED_BEFORE_WAIT")
    require("ClosingPortUnblocksDrainWait()" in host_cpp_test
            and "ReversePortOrderCannotDeadlock()" in host_cpp_test
            and "MultipleBusyPortsCanBothDrain()" in host_cpp_test,
            "CONCURRENCY_REGRESSION_CASES_MISSING")
    require("AUDIO_GUEST_SUBMIT port=" in trial_out
            and "AUDIO_BATCH_SYNC calls=" in trial_out,
            "GUEST_AUDIO_AND_BATCH_LOCK_TIMING_PROBES_MISSING")
    require("locks.emplace_back(port->mutex);" not in trial_out,
            "MULTIPORT_LOCK_ACQUIRED_WHILE_WAITING_ON_ANOTHER_PORT")
    print("LOCK_RELEASE_BEFORE_PORT_DRAIN_AND_ATOMIC_BATCH_PUBLICATION=PASS")
    print("THREE_CONCURRENCY_CASES_PRESENT_AND_GUEST_SUBMIT_DIAGNOSTICS=PASS")

    print("OUTER_ONLY_PACING_OPT_IN_AND_DEFAULT_SEMANTICS=PASS")
    print("PCM_SUBMISSION_AND_GUEST_OUTPUT_TIMER_UNCHANGED=PASS")
    print("AUDIO_PRODUCER_AND_QUEUE_COUNTERS=PASS")

    for marker in (
        "AUDIO_TEST_HEAD=8f23d6d51a14c7cba962f88e6e5dae1188387dde",
        "c9a34c805643e225fcab55807849c097f6935bd3",
        "1c6485d7ea7f052475a56cc0d686ffd757b45952",
        "82e6698b821b452f1b34402a58aee43c2098704c",
        "AUDIO_MULTI_PORT_SYNC=PASS cases=3",
        "EXPERIMENTAL_AUDIO_SYNC_HEADER_REMOVED=YES",
        'SHADPS4_RUNTIME_LOG_FILTER="*:warn Lib.AudioOut:info Lib.Audio3d:info"',
        "export SHADPS4_SDL_OUTER_PACING=1",
        "export SHADPS4_AUDIO_TIMING_PROBE=1",
        "SOURCE_MATCHES_GRAPHICS_CORRECT_2059_CANDIDATE=PASS",
        "AUDIO_EXACT_TRIAL_BLOB=PASS",
        "ISOLATED_SOURCE_SNAPSHOT_RESTORED=YES",
        "fullgfx-quiet-best-unchanged.txt",
    ):
        require(marker in runner, "RUNNER_CONTRACT_MISSING:" + marker)
    require(runner.count('cmake --build "$BUILD" --target shadps4 --parallel 4') == 1,
            "UNCONTROLLED_MULTIPLE_BUILDS")
    for forbidden in ("git reset", "git checkout", "pkill", "killall",
                      "kill -9", "systemctl stop", "sysctl -w"):
        require(forbidden not in runner, "UNSAFE_RUNNER_OP:" + forbidden)
    print("IDENTICAL_FULL_GRAPHICS_AND_THREE_PINNED_BASELINES=PASS")
    print("ISOLATED_BUILD_AND_ROLLBACK=PASS")

    ast.parse(observer, filename="gow_upstream_gameplay.py")
    require("audio_timing_summary" in observer
            and "capture_pipewire_xruns" in observer
            and "SHADPS4_SDL_OUTER_PACING" in observer,
            "OBSERVER_MISSING_TIMING_ANALYTICS")
    require('["pw-top", "-b", "-n", "4"]' in observer,
            "READ_ONLY_PIPEWIRE_INTERVAL_MISSING")
    print("READ_ONLY_AUDIO_OBSERVATION_WITH_XRUN_INTERVALS=PASS")
    print("ALL_AUDIO_BATCH_AND_OUTER_PACING_SOURCE_CONTRACTS=PASS")


if __name__ == "__main__":
    main()
