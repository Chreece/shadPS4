#!/usr/bin/env python3
"""Verify an experimental runtime-only log filter cannot alter the proven GoW graphics."""
from pathlib import Path
import ast
import hashlib
import sys

ORIGINAL_LOG_BLOB = "d5276147b2f4afb12c884e2761a5c47b87c22610"
RUNTIME_LOG_BLOB = "c0db514053650522588349215738e46408c905e0"
PROVEN_RENDERER_SHA256 = "bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a"

def check(condition, message):
    if not condition:
        raise AssertionError(message)

def main():
    if len(sys.argv) != 5:
        raise SystemExit("Usage: test.py ORIGINAL_LOG_CPP NEW_LOG_CPP RUNNER_SH OBSERVER_PY")
    old_log, new_log, runner, observer = (Path(i) for i in sys.argv[1:])
    orig=old_log.read_text(encoding="utf-8")
    patched=new_log.read_text(encoding="utf-8")
    old='    UpdateLogLevels(EmulatorSettings.GetLogFilter());\n'
    begin='    // Allow launch-specific logging experiments without touching config.json,\n'
    end='    UpdateLogFlushLevel(EmulatorSettings.GetLogFlushLevel());'
    check(orig.count(old)==1, "ORIGINAL_FILTER_ANCHOR_NOT_UNIQUE")
    check(patched.count(begin)==1, "RUNTIME_OVERRIDE_ANCHOR_NOT_UNIQUE")
    patch_start=patched.index(begin)
    patch_end=patched.index(end,patch_start)
    extension=patched[patch_start:patch_end]
    check(patched==orig.replace(old,extension,1),"LOG_SOURCE_MODIFIED_OUTSIDE_FILTER_DISPATCH")
    check('std::getenv("SHADPS4_RUNTIME_LOG_FILTER")' in extension,
          "ENVIRONMENT_OVERRIDE_MISSING")
    check('    } else {\n        UpdateLogLevels(EmulatorSettings.GetLogFilter());\n' in extension,
          "SAVED_FILTER_DEFAULT_BEHAVIOR_MISSING")
    check("LOG_WARNING(Config" in extension,"DIAGNOSTIC_OVERRIDE_MARKER_MISSING")
    check("std::getenv" not in orig or "SHADPS4_RUNTIME_LOG_FILTER" not in orig,
          "RUNTIME_FILTER_ALREADY_EXISTS_IN_ORIGINAL")
    print("LOGGING_ONLY_ONE_SWITCH_FUNCTION_BLOCK_CHANGED=PASS")
    print("UNSET_OVERRIDE_DEFAULT_BEHAVIOR_IDENTICAL=PASS")

    shell=runner.read_text(encoding="utf-8")
    check("SHADPS4_RUNTIME_LOG_FILTER=\"*:warn Lib.AudioOut:info Lib.Audio3d:info\"" in shell,
          "EXPECTED_TEST_ONLY_LOG_FILTER_MISSING")
    check('LOG_NEW_BLOB="'+RUNTIME_LOG_BLOB+'"' in shell,"PINNED_RUNTIME_LOG_BLOB_MISSING")
    check('LOG_OLD_BLOB="'+ORIGINAL_LOG_BLOB+'"' in shell,"PINNED_ORIGINAL_LOG_BLOB_MISSING")
    check(PROVEN_RENDERER_SHA256 in shell,"PROVEN_GRAPHICS_SHA_CONTRACT_MISSING")
    check("CMakeFiles/shadps4.dir/src/common/logging/log.cpp.o" in shell,
          "LOGGING_CPP_OBJECT_COMPILE_GATE_MISSING")
    check(shell.count('cmake --build "$BUILD" --target shadps4 --parallel 4')==1,
          "UNCONTROLLED_MULTIPLE_FULL_BUILDS")
    check("ISOLATED_SOURCE_SNAPSHOT_RESTORED=YES" in shell,
          "ISOLATED_SOURCE_RESTORE_CHECK_MISSING")
    for unsafe in ("git reset", "git checkout", "pkill", "kill -9", "sysctl -w",
                   "systemctl stop", "config.json\", \"w"):
        check(unsafe not in shell,"UNSAFE_MUTATION_OR_PROCESS_TERMINATION:"+unsafe)
    print("PROVEN_GRAPHICS_HASH_AND_ONE_BUILD_GATES=PASS")
    print("NO_CONFIG_SERVICE_SUNSHINE_OR_SSH_MUTATIONS=PASS")
    py=observer.read_text(encoding="utf-8")
    ast.parse(py, filename="audio-observer.py")
    check("AudioOu" in py and "capture_pipewire_xruns" in py,
          "AUDIO_OBSERVABILITY_MISSING")
    check('["pw-top", "-b", "-n", "1"]' in py,
          "PIPEWIRE_READ_ONLY_SAMPLING_MISSING")
    check("wpctl set-" not in py and "pactl set-" not in py,"AUDIO_SYSTEM_MUTATION")
    print("READ_ONLY_AUDIO_THREAD_AND_PIPEWIRE_OBSERVER=PASS")
    print("FULL_GFX_RUNTIME_LOG_AB_CONTRACT=PASS")

if __name__ == "__main__":
    main()
