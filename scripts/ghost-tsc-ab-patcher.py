#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-run diagnostic A/B: native TSC against the Linux TSC-fault path.

Changes CPU-ID source only. The calling playtest script verifies original Git
blobs, builds the variant, and restores source + binary after the game.
Do NOT upstream this as a final RDTSCP solution.
"""
from pathlib import Path
import subprocess
import sys

REL = "src/core/cpu_id.cpp"
EXPECTED = "c095479d97238b726d2ad31d94a54f5c6cfc9e50"
OLD = """    if (prctl(PR_SET_TSC, PR_TSC_SIGSEGV) != 0) {
        static std::atomic warned{false};
        if (!warned.exchange(true)) {
            LOG_WARNING(Core, "TSC faulting unavailable; RDTSCP identity requires static patches");
        }
    }
"""
NEW = """    // GHOST-ONLY A/B EXPERIMENT: do not intercept ordinary RDTSC in this test build.
    // The CPUID fault path and statically patched CPUID/RDTSCP remain unchanged.
    // Dynamically generated RDTSCP will expose native AUX during this experiment.
    // This is NOT suitable for upstream or normal deployment.
    ASSERT_MSG(prctl(PR_SET_TSC, PR_TSC_ENABLE) == 0,
               "GHOST_AB_TSC cannot enable native TSC, errno={}", errno);
    static std::atomic_bool logged_tsc_ab{false};
    if (!logged_tsc_ab.exchange(true)) {
        LOG_WARNING(Core, "GHOST_AB_TSC native RDTSC enabled; CPU-ID faulting retained");
    }
"""

def git_blob(path):
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()

def patch_text(source):
    if source.count(OLD) != 1:
        raise ValueError("expected TSC faulting setup differs from reviewed PR #5304")
    if "GHOST_AB_TSC" in source:
        raise ValueError("already instrumented")
    result = source.replace(OLD, NEW)
    assert result.count("GHOST_AB_TSC native RDTSC enabled") == 1
    assert result.count("ARCH_SET_CPUID, 0") >= 1
    assert "PR_SET_TSC, PR_TSC_ENABLE" in result
    return result

def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: ghost-tsc-ab-patcher.py SOURCE_WORKTREE BACKUP_FOLDER")
    target = Path(sys.argv[1]).resolve() / REL
    backup = Path(sys.argv[2]).resolve() / REL
    assert target.is_file(), "CPU-ID source file missing"
    actual = git_blob(target)
    assert actual == EXPECTED, f"CPU-ID source changed: actual={actual}, expected={EXPECTED}"
    original = target.read_bytes()
    updated = patch_text(original.decode()).encode()
    # Never edit source until the backup is verified.
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(original)
    assert backup.read_bytes() == original
    target.write_bytes(updated)
    assert b"GHOST_AB_TSC native RDTSC enabled" in target.read_bytes()
    print(f"A/B source prepared: {REL}, original blob={actual}")
    print("Mode: native RDTSC; CPUID faulting preserved. Restore required after test.")

if __name__ == "__main__":
    main()
