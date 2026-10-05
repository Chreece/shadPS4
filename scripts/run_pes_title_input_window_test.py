#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reuse the verified PES diagnostic binary and capture title input before/after evidence."""

import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import urllib.request

EXPECTED_REVISION = "07b1a5495735af5345680e9f7f29983171047797"
EXPECTED_BINARY_SHA256 = "6bf6a32f4c51fa349f9a2ba8273192be49cfe4f39f01f2f79ae647d478eaf56d"
DIAGNOSTIC_FIX = "dc2bc74e6ab9056c27398015ef8200c505a43e81"
AUTO_CROSS_MS = "45000"
RAW = "https://raw.githubusercontent.com/Chreece/shadPS4/"

HELPERS = {
    "collect_pes_runtime_context.py": (
        "4621958f1e0e749d429245c43e511a87e0a66fd5",
        "58cb93a9b52f2264e11d13259e379d676b7aa940c9522f7e80198943ae6ed612",
    ),
    "trace_video_progress.py": (
        "4621958f1e0e749d429245c43e511a87e0a66fd5",
        "c770af6e639464064ec543f99cb6181d75bf4b3bbe493ae6e8c015652d9b1a6b",
    ),
    "run_pes_startup_test.py": (
        "9cd2d972fb0bbcc687d6fa34ed1c7d0489241adf",
        "dedc6cd156e19799b8814af45e2813b0aa1b824c95abfabcea47b50b623954b3",
    ),
    "pes_test_cleanup.py": (
        "9ff21ec091efa3f0f4889cee60bc79fdb2e2a5f8",
        "fe5d13cf6fe1cee2c1e50c3329c8092f220ec3ca27de7834ab4537d744330b96",
    ),
    "pes_graphics_launch.py": (
        "40bde232e0e9c11b1712c182d28b4b3fbbedf397",
        "43665b3cd96c540cae5bf82cf8b33434119149f5c8b1700c86412bade0bcf570",
    ),
    "pes_current_baseline.py": (
        "334f662444665c4647101ced7a2938082d54fb20",
        "b044656a37aadbb54e27903125cc6e4648fdd6d171741b0039a37f7ff6b66224",
    ),
    "pes_frame_profile.py": (
        "77c9f53539260f4597e27d925bd9cf003600b6f9",
        "500b8fe2f6afd6398c077731892c19ece260e696f29772f3f97ec94bc925ce6f",
    ),
}


def download_helpers(work):
    helper_dir = work / "helpers"
    helper_dir.mkdir()
    print("Preparing checksum-verified capture helpers...", flush=True)
    for name, (revision, expected) in HELPERS.items():
        url = RAW + revision + "/scripts/" + name
        with urllib.request.urlopen(url, timeout=45) as response:
            data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise RuntimeError("Helper unexpectedly large: " + name)
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise RuntimeError(
                f"Helper checksum mismatch: {name}\nexpected={expected}\nactual={actual}"
            )
        (helper_dir / name).write_bytes(data)
    sys.path.insert(0, str(helper_dir))
    print("PES_TITLE_INPUT_HELPERS=" + str(helper_dir), flush=True)


def verify_installed(home):
    baseline = importlib.import_module("pes_current_baseline")
    record = baseline.verify_installed(home)
    if record.get("revision") != EXPECTED_REVISION:
        raise RuntimeError(
            "Installed PES diagnostic revision changed; expected "
            + EXPECTED_REVISION + ", got " + str(record.get("revision"))
        )
    if record.get("binary_sha256") != EXPECTED_BINARY_SHA256:
        raise RuntimeError("Installed PES diagnostic binary checksum changed")
    fixes = record.get("manifest", {}).get("candidate_fixes", {})
    if fixes.get("post_title_cross_diagnostic") != DIAGNOSTIC_FIX:
        raise RuntimeError("Installed baseline is not the verified Cross diagnostic build")
    print("PES_TITLE_INPUT_BINARY=PASS:" + EXPECTED_BINARY_SHA256, flush=True)
    return record


def verify_evidence(work, result):
    if result.get("errors"):
        raise RuntimeError("Startup capture reported errors: " + "; ".join(result["errors"]))
    if not result.get("frames_capture_passed"):
        raise RuntimeError("Frame capture did not complete")
    if not result.get("screenshots_complete"):
        raise RuntimeError("Native screenshot capture did not complete")
    if len(result.get("screenshots", [])) != 4:
        raise RuntimeError(
            "Expected four native screenshots (before/after game+HUD), got "
            + str(len(result.get("screenshots", [])))
        )
    if not result.get("process_cleanup", {}).get("complete"):
        raise RuntimeError("Test-owned PES process cleanup was not verified")
    if not result.get("debugger_cleanup", {}).get("complete"):
        raise RuntimeError("Debugger cleanup was not verified")
    if not result.get("settings_unchanged") or not result.get("launcher_unchanged"):
        raise RuntimeError("Settings or launcher changed during the test")

    frames = json.loads((work / "startup/frames/frames-trace.json").read_text())
    requests = frames.get("screenshot_requests", [])
    observed = sorted((item.get("phase"), item.get("kind")) for item in requests)
    expected = sorted([
        ("before", "game_only"), ("before", "with_overlays"),
        ("after", "game_only"), ("after", "with_overlays"),
    ])
    if observed != expected:
        raise RuntimeError("Pre/post screenshot request evidence is incomplete: " + repr(observed))

    console_files = sorted((work / "startup").glob("console.*.log"))
    if not console_files:
        raise RuntimeError("Console capture missing")
    text = "".join(path.read_text(errors="replace") for path in console_files)
    press = re.search(
        r"event=auto-cross phase=press process_us=(\d+) delay_ms=(\d+) count=(\d+) connected=(\d+)",
        text,
    )
    release = re.search(
        r"event=auto-cross phase=release process_us=(\d+) delay_ms=(\d+) connected=(\d+)",
        text,
    )
    deliveries = re.findall(
        r"event=pad-delivery[^\n]*buttons=00004000[^\n]*connected=1",
        text,
    )
    if press is None or release is None or len(deliveries) < 2:
        raise RuntimeError("Cross press/release or guest delivery evidence missing")
    if press.group(2) != AUTO_CROSS_MS or press.group(4) != "1" or release.group(3) != "1":
        raise RuntimeError("Cross timing/connection evidence differs from the requested test")

    evidence = {
        "schema": 1,
        "binary_revision": EXPECTED_REVISION,
        "binary_sha256": EXPECTED_BINARY_SHA256,
        "cross_delay_ms": int(AUTO_CROSS_MS),
        "cross_press_process_us": int(press.group(1)),
        "cross_release_process_us": int(release.group(1)),
        "cross_delivery_samples": len(deliveries),
        "screenshot_requests": requests,
        "screenshots": [],
    }
    for item in result["screenshots"]:
        path = work / "startup" / item["file"]
        evidence["screenshots"].append({
            **item,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    (work / "title-input-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print("PES_TITLE_INPUT_CROSS=PASS:" + str(len(deliveries)) + " delivered samples", flush=True)
    print("PES_TITLE_INPUT_SCREENSHOTS=PASS:2 before + 2 after", flush=True)
    return evidence


def archive_work(work):
    archive = work.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as output:
        for path in sorted(work.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.name != "emulator.log":
                output.add(path, arcname=str(path.relative_to(work)), recursive=False)
    return archive


def main():
    if os.geteuid() == 0 or sys.argv[1:]:
        raise RuntimeError("Run as chreece without sudo or arguments")
    home = Path.home()
    work = Path(tempfile.mkdtemp(prefix="shadps4-pes-title-input-", dir=home))
    errors = []
    try:
        download_helpers(work)
        verify_installed(home)
        startup = importlib.import_module("run_pes_startup_test")
        previous = os.environ.get("SHADPS4_DIAG_AUTO_CROSS_MS")
        previous_four = os.environ.get("SHADPS4_DIAG_EXPECT_FOUR_SCREENSHOTS")
        os.environ["SHADPS4_DIAG_AUTO_CROSS_MS"] = AUTO_CROSS_MS
        os.environ["SHADPS4_DIAG_EXPECT_FOUR_SCREENSHOTS"] = "1"
        try:
            result = startup.run(
                home,
                profile="frames",
                screenshots=True,
                graphics=True,
                validation=False,
                close_after=True,
                work=work / "startup",
                archive=False,
                trace_delay_seconds=40,
            )
        finally:
            if previous is None:
                os.environ.pop("SHADPS4_DIAG_AUTO_CROSS_MS", None)
            else:
                os.environ["SHADPS4_DIAG_AUTO_CROSS_MS"] = previous
            if previous_four is None:
                os.environ.pop("SHADPS4_DIAG_EXPECT_FOUR_SCREENSHOTS", None)
            else:
                os.environ["SHADPS4_DIAG_EXPECT_FOUR_SCREENSHOTS"] = previous_four
        verify_evidence(work, result)
        print("PES_TITLE_INPUT_TEST=PASS", flush=True)
    except (Exception, KeyboardInterrupt) as error:
        errors.append(type(error).__name__ + ": " + (str(error) or "Interrupted"))
        print("PES_TITLE_INPUT_TEST=FAIL:" + errors[-1], flush=True)
    finally:
        (work / "session.json").write_text(json.dumps({
            "schema": 1,
            "completed": not errors,
            "errors": errors,
            "expected_revision": EXPECTED_REVISION,
            "expected_binary_sha256": EXPECTED_BINARY_SHA256,
            "cross_delay_ms": int(AUTO_CROSS_MS),
        }, indent=2) + "\n")
        archive = archive_work(work)
        print("PES_TITLE_INPUT_ARCHIVE=" + str(archive), flush=True)
        print("Upload that archive only. Returning to your existing SSH prompt.", flush=True)
    return 0 if not errors else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        print("PES_TITLE_INPUT_TEST=FAIL:" + (str(error) or "Interrupted"), flush=True)
        sys.exit(1)
