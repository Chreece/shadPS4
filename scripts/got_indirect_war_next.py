#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Inspect real GoT native/WAR archives; optionally run one justified guarded A/B.

Never builds, installs, replaces binaries, changes driver settings, or touches
running services directly. Existing isolated runtime owns all GPU/Ollama guards.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

ROOT = Path.home() / "projects" / "shadps4"
LOGS = ROOT / "logs"
SCRIPTS = ROOT / "scripts"
CURRENT_HOST = "got-indirect-war-fix-host-20261019-103954.tar.gz"
CURRENT_RUNTIME = "got-indirect-war-fix-runtime-20261019-104023.tar.gz"
BASELINE_RUNTIME = "got-active-menu-work-runtime-20261019-073905.tar.gz"
PIN = "08b1769773fc84931057d5b529f24ca700499a19"
READ_FILES = (
    "info/final.txt", "info/indirect-war-hazard-summary.txt",
    "info/active-menu-hardware-summary.txt",
    "info/scene-gpu-invocation-summary.txt",
    "info/scene-indirect-gpu-argument-summary.txt",
    "info/stage-pixel-summary.txt",
    "info/scene-raster-viewport-summary.txt",
    "info/texture-source-graph.txt",
    "info/depth-source-provenance.txt",
    "info/scene-raster-ab-activation.log",
)

def locate(explicit: str | None, default_name: str, glob_pattern: str) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    p = LOGS / default_name
    if p.is_file():
        return p
    options = sorted(LOGS.glob(glob_pattern), key=lambda x: x.stat().st_mtime, reverse=True)
    return options[0] if options else p

def fields(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith((" ", "\t")):
            k, val = line.split("=", 1)
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", k):
                result[k] = val.strip()
    return result

def read_archive(path: Path) -> tuple[dict[str, str], list[str]]:
    if not path.is_file():
        raise FileNotFoundError(f"REPORT_MISSING={path}")
    result = {}
    images = []
    with tarfile.open(path, "r:gz") as tf:
        for entry in tf:
            name = entry.name.removeprefix("./")
            if name.startswith("screenshots/") and name.endswith(".png"):
                images.append(name)
            if name not in READ_FILES:
                continue
            if not entry.isfile() or entry.size > 2_000_000:
                continue
            f = tf.extractfile(entry)
            if f:
                result[name] = f.read().decode("utf-8", errors="replace")
    return result, images

def metric_lines(text: str, *prefixes: str, limit: int = 12) -> list[str]:
    result = [l.strip() for l in text.splitlines()
              if l.strip().startswith(prefixes)]
    return result[:limit]

def num(source: dict[str, str], key: str) -> int:
    try:
        return int(source.get(key, "0"))
    except (ValueError, TypeError):
        return 0

def get_field(line: str, key: str) -> str:
    m = re.search(r"(?:^|\s)" + re.escape(key) + r"=([^\s]+)", line)
    return m.group(1) if m else ""

def classifications(summaries: dict[str, str]) -> Counter[str]:
    out: Counter[str] = Counter()
    for line in summaries.get("info/active-menu-hardware-summary.txt", "").splitlines():
        if line.startswith("ACTIVE_GPU_CLASSIFICATION "):
            result = get_field(line, "RESULT")
            if result:
                out[result] += 1
    return out

def choose_next_mode(results: Counter[str]) -> tuple[str | None, str]:
    if not results:
        return None, "NO_VALID_HARDWARE_CLASSIFICATION"
    # A depth/culling test cannot make an indirect draw with zero vertices run.
    no_geometry = ("GPU_INDIRECT_DRAW_EXECUTES_NO_GEOMETRY_CHECK_GPU_ARGS",
                   "COUNTERS_INCONCLUSIVE",
                   "VERTEX_OUTPUT_DISCARDED_BEFORE_PRIMITIVE_RASTERIZATION")
    if any(results[x] for x in no_geometry):
        return None, "TRACE_GPU_INDIRECT_PRODUCER_AND_ARGUMENT_ORIGIN_NOT_RASTER_STATE"
    if results["REAL_SCENE_FRAGMENT_AND_COVERAGE_WORK_CONFIRMED"]:
        return None, "GEOMETRY_EXECUTES_INSPECT_PRESENTED_PIXEL_CHAIN"
    if results["CLIPPED_PRIMITIVES_BUT_NO_FRAGMENT_COVERAGE"] and \
            len(results) == 1:
        return "raster", "PRIMITIVES_REACH_RASTERIZER_BUT_NO_FRAGMENTS"
    if results["FRAGMENT_SHADER_EXECUTES_WITH_ZERO_VISIBLE_SAMPLES"] and \
            len(results) == 1:
        return "depth", "FRAGMENTS_EXECUTE_BUT_DEPTH_OR_STENCIL_REJECTS_SAMPLES"
    return None, "MIXED_GPU_RESULTS_REQUIRE_SOURCE_LEVEL_CORRELATION"

def print_head(name: str, value: str) -> None:
    print(f"\n=== {name} ===")
    print(value if value else "NOT_AVAILABLE")

def snapshot_image(path: Path, images: list[str]) -> None:
    if not images:
        print("SCREENSHOTS=NONE")
        return
    print(f"SCREENSHOTS={','.join(images)}")
    image_name = sorted(images, key=lambda x: int(Path(x).stem)
                        if Path(x).stem.isdigit() else -1)[-1]
    try:
        from PIL import Image
        with tarfile.open(path, "r:gz") as tf:
            f = tf.extractfile(image_name)
            if f is None:
                return
            with Image.open(io.BytesIO(f.read())) as img:
                img = img.convert("RGB")
                img.thumbnail((160, 120))
                samples = list(img.getdata())
        white = sum(min(rgb) > 235 for rgb in samples)
        black = sum(max(rgb) < 25 for rgb in samples)
        print(f"LATE_SCREENSHOT={image_name} WHITE_PIXEL_PERCENT={100*white/len(samples):.1f} "
              f"BLACK_PIXEL_PERCENT={100*black/len(samples):.1f} "
              "NOTE=ONLY_PIXEL_STATISTICS_NOT_VISUAL_VERIFICATION")
    except (ImportError, OSError, KeyError):
        print("LATE_SCREENSHOT_PIXEL_ANALYSIS=UNAVAILABLE_PNG_STILL_IN_ARCHIVE")

def already_tested(mode: str, current: Path) -> bool:
    for path in LOGS.glob("got-indirect-war-fix-runtime-*.tar.gz"):
        if path == current:
            continue
        try:
            parts, _ = read_archive(path)
            final = fields(parts.get("info/final.txt", ""))
            if (final.get("GOT_SCENE_AB_MODE") == mode and
                    final.get("VULKAN_INDIRECT_WAR_FIX_SOURCE_PIN") == PIN and
                    final.get("GAME_LAUNCHED") == "YES"):
                return True
        except (OSError, tarfile.TarError):
            continue
    return False

def self_test() -> None:
    assert fields("STATUS=CAPTURE_COMPLETE\nGAME_EXIT=124\n") == {
        "STATUS": "CAPTURE_COMPLETE", "GAME_EXIT": "124"}
    assert choose_next_mode(Counter({"GPU_INDIRECT_DRAW_EXECUTES_NO_GEOMETRY_CHECK_GPU_ARGS": 2}))[0] is None
    assert choose_next_mode(Counter({"CLIPPED_PRIMITIVES_BUT_NO_FRAGMENT_COVERAGE": 2}))[0] == "raster"
    assert choose_next_mode(Counter({"FRAGMENT_SHADER_EXECUTES_WITH_ZERO_VISIBLE_SAMPLES": 2}))[0] == "depth"
    assert choose_next_mode(Counter({"REAL_SCENE_FRAGMENT_AND_COVERAGE_WORK_CONFIRMED": 2}))[0] is None
    print("GOT_ARCHIVE_ANALYSIS_SELF_TEST=PASS cases=5")

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--run-safe-followup", action="store_true")
    parser.add_argument("--host")
    parser.add_argument("--runtime")
    parser.add_argument("--baseline")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    host_path = locate(args.host, CURRENT_HOST, "got-indirect-war-fix-host-*.tar.gz")
    current_path = locate(args.runtime, CURRENT_RUNTIME, "got-indirect-war-fix-runtime-*.tar.gz")
    prior_path = locate(args.baseline, BASELINE_RUNTIME, "got-active-menu-work-runtime-*.tar.gz")
    LOGS.mkdir(parents=True, exist_ok=True)
    output = LOGS / ("got-indirect-war-evidence-next-" +
                     datetime.now().strftime("%Y%m%d-%H%M%S") + ".log")

    class Tee:
        def __init__(self, file):
            self.file = file
        def write(self, data):
            sys.__stdout__.write(data)
            self.file.write(data)
        def flush(self):
            sys.__stdout__.flush()
            self.file.flush()

    with output.open("w", encoding="utf-8") as log:
        old_stdout = sys.stdout
        sys.stdout = Tee(log)
        try:
            print("GOT_WAR_EVIDENCE_COMPARISON=START")
            print(f"HOST_ARCHIVE={host_path}")
            print(f"NATIVE_RUNTIME_ARCHIVE={current_path}")
            h, _ = read_archive(host_path)
            r, images = read_archive(current_path)
            hf = fields(h.get("info/final.txt", ""))
            rf = fields(r.get("info/final.txt", ""))
            print_head("HOST RESULT", "\n".join(f"{k}={hf.get(k,'MISSING')}" for k in (
                "STATUS", "EXIT", "CANDIDATE_BUILT",
                "BASELINE_EXECUTABLE_RESTORED", "GAME_LAUNCHED")))
            print_head("RUNTIME RESULT", "\n".join(f"{k}={rf.get(k,'MISSING')}" for k in (
                "STATUS", "EXIT", "GAME_EXIT", "GAME_LAUNCHED",
                "ACTIVE_BINARY_UNCHANGED", "OLLAMA_ACTION", "OLLAMA_RESTORE",
                "GOT_SCENE_AB_MODE", "GPUVM_FAULT_EVENTS",
                "VULKAN_INDIRECT_READ_TRACKED",
                "VULKAN_INDIRECT_WAR_OVERLAP_EVENTS",
                "GPU_QUERY_RESULTS", "ACTIVE_MENU_LATE_QUERIES",
                "GPU_INDIRECT_ARG_READBACKS", "ACTIVE_MENU_8192_CHECKPOINTS")))
            if prior_path.is_file():
                old, previous_images = read_archive(prior_path)
                baseline_final = fields(old.get("info/final.txt", ""))
                print_head("PREVIOUS ACTIVE MENU BASELINE (different instrumentation)",
                           "\n".join(f"{k}: before={baseline_final.get(k,'NOT_MEASURED')} "
                                     f"new={rf.get(k,'NOT_MEASURED')}" for k in (
                                         "STATUS", "ACTUAL_PRESENT_FRAMES",
                                         "GPU_QUERY_RESULTS", "ACTIVE_MENU_LATE_QUERIES",
                                         "GPU_INDIRECT_ARG_READBACKS",
                                         "GPU_SCENE_READBACK_SAVED")))
                print("BASELINE_SCREENSHOT_FILES=" + str(len(previous_images)))
            else:
                print("PREVIOUS_RUNTIME_ARCHIVE=NOT_FOUND_NO_COMPARISON_CLAIM")

            for name, keys in [
                ("VULKAN INDIRECT READ / WAR", ("RESULT=", "READ_EVENT_", "WRITE_AFTER_", "ARG_BUFFER_", "ARG_GPU_", "WAR_OVERLAP=")),
                ("GPU INDIRECT ARGUMENTS", ("GPU_ARGUMENT_DIAGNOSIS=", "FOLLOWUP=", "GPU_ARGUMENT_CLASSIFICATION=", "GPU_READBACK_")),
                ("HIGH-VOLUME HARDWARE QUERY", ("ACTIVE_COMPLETE_COUNTERS=", "ACTIVE_8192_", "ACTIVE_GPU_CLASSIFICATION ", "RESULT=")),
                ("GPU PIXEL PIPELINE", ("STAGE=", "STAGE_CAPTURED_COUNT=", "POSTCOMPOSITOR_", "HOST_PP_GAIN_", "NO_GPU_STAGE_")),
                ("PRESENTED SOURCE", ("PRESENTED=", "KEY_TARGET=", "SOURCE_EDGE=", "SCENE_COLOR_WRITER=")),
            ]:
                path = {
                    "VULKAN INDIRECT READ / WAR": "info/indirect-war-hazard-summary.txt",
                    "GPU INDIRECT ARGUMENTS": "info/scene-indirect-gpu-argument-summary.txt",
                    "HIGH-VOLUME HARDWARE QUERY": "info/active-menu-hardware-summary.txt",
                    "GPU PIXEL PIPELINE": "info/stage-pixel-summary.txt",
                    "PRESENTED SOURCE": "info/texture-source-graph.txt",
                }[name]
                print_head(name, "\n".join(metric_lines(r.get(path, ""), *keys,
                                                       limit=24)))
            snapshot_image(current_path, images)
            classification = classifications(r)
            mode, reason = choose_next_mode(classification)
            print_head("EVIDENCE-BASED NEXT ACTION",
                       f"GPU_CLASSIFICATIONS={dict(classification)}\n"
                       f"NEXT_ACTION={reason}\nSUGGESTED_GUARDED_AB_MODE={mode or 'NONE'}")
            safe = (hf.get("STATUS") == "HOST_ONLY_BUILD_PASS"
                    and hf.get("BASELINE_EXECUTABLE_RESTORED") == "1"
                    and rf.get("STATUS") == "CAPTURE_COMPLETE"
                    and rf.get("GOT_SCENE_AB_MODE") == "native"
                    and rf.get("VULKAN_INDIRECT_WAR_FIX_SOURCE_PIN") == PIN
                    and rf.get("ACTIVE_BINARY_UNCHANGED") in ("YES", "PASS")
                    and rf.get("OLLAMA_RESTORE") != "FAILED"
                    and num(rf, "GPUVM_FAULT_EVENTS") == 0
                    and num(rf, "GPU_INDIRECT_ARG_READBACKS") > 0
                    and num(rf, "VULKAN_INDIRECT_READ_TRACKED") > 0)
            if not args.run_safe_followup:
                print("FOLLOWUP_LAUNCH=NOT_REQUESTED_OFFLINE_ONLY")
            elif not safe or not mode:
                print("FOLLOWUP_LAUNCH=SKIPPED_NOT_EVIDENCE_JUSTIFIED_OR_SAFETY_GATE")
            elif already_tested(mode, current_path):
                print(f"FOLLOWUP_LAUNCH=SKIPPED_{mode.upper()}_ALREADY_TESTED")
            else:
                runtime_script = SCRIPTS / "got-indirect-war-runtime-20261019.sh"
                if not runtime_script.is_file():
                    print(f"FOLLOWUP_LAUNCH=SKIPPED_SCRIPT_MISSING:{runtime_script}")
                else:
                    env = dict(os.environ)
                    env.update(GOT_SCENE_AB_MODE=mode,
                               GOT_INDIRECT_WAR_HOST_REPORT=str(host_path))
                    print(f"FOLLOWUP_LAUNCH=START mode={mode} no_rebuild=YES")
                    sys.stdout.flush()
                    rc = subprocess.call(["bash", str(runtime_script)], env=env)
                    print(f"FOLLOWUP_RUNTIME_EXIT={rc}")
                    print("FOLLOWUP_RUNTIME_ARCHIVE=CHECK_LATEST_got-indirect-war-fix-runtime")
            print(f"EVIDENCE_REPORT={output}")
            print("SSH_SESSION=OPEN")
        except (OSError, tarfile.TarError) as exc:
            print(f"EVIDENCE_READ_ERROR={exc}")
            print("NO_GAME_LAUNCHED_BY_ANALYZER=YES")
            return 2
        finally:
            sys.stdout = old_stdout
    return 0

if __name__ == "__main__":
    sys.exit(main())
