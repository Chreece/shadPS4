#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build and execute marked or automatic xstate diagnostics in an isolated shadPS4 profile.

Requires a Linux build with ENABLE_EXPERIMENTAL_XSTATE_TRACE=ON and the guest
memory-protection changes from #5329. Does not install an emulator or launch a game.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import time

from compare import compare

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--unmarked", action="store_true")
parser.add_argument("--auto-only", action="store_true")
parser.add_argument("--block-only", action="store_true")
parser.add_argument("--single-step", action="store_true", help="Disable native blocks for comparison")
parser.add_argument("--guest-blocks-only", action="store_true", help="Use the previous guest-only block path")
parser.add_argument("--no-relative-blocks", action="store_true", help="Disable relative-address optimizations")
parser.add_argument("--timeout", type=int, default=180)
parser.add_argument("--emulator", type=Path, required=True)
parser.add_argument("--sdk", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--icd", type=Path)
args = parser.parse_args()
here = Path(__file__).resolve().parent
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
emulator = args.emulator.resolve()
sdk = args.sdk.resolve()
summary = {"emulator_sha256": hashlib.sha256(emulator.read_bytes()).hexdigest()}
display = process = None
status = 1
try:
    with (output / "homebrew-build.log").open("w") as log:
        subprocess.run([sys.executable, str(here / "build_trace_probe.py"), "--sdk", str(sdk),
                        "--output", str(output / "homebrew"),
                        *(["--unmarked"] if args.unmarked else []),
                        *(["--block-only"] if args.block_only else []),
                        *(["--auto-only"] if args.auto_only else [])], check=True, stdout=log,
                       stderr=subprocess.STDOUT, timeout=150)
    if args.unmarked:
        with (output / "branch-native-build.log").open("w") as log:
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                            str(here / "branch_native.cpp"), str(here / "trace_branch.S"),
                            "-o", str(output / "branch-native")], check=True, stdout=log,
                           stderr=subprocess.STDOUT, timeout=60)
        native_branch = subprocess.check_output([str(output / "branch-native")], text=True,
                                                timeout=10).strip()
        (output / "branch-native.txt").write_text(native_branch + "\n")
        if not native_branch.startswith("BRANCH_CASES cases=2146 digest="):
            raise RuntimeError("Native branch comparison did not run all cases")
    game = output / "game"
    (game / "sce_sys").mkdir(parents=True, exist_ok=True)
    shutil.copy2(output / "homebrew/eboot.bin", game / "eboot.bin")
    shutil.copy2(here.parent / "generated_reciprocal/param.sfo", game / "sce_sys/param.sfo")
    shutil.copytree(sdk / "samples/hello_world/sce_module", game / "sce_module", dirs_exist_ok=True)
    runtime = output / "runtime"
    user = runtime / "user"
    users = []
    for i in range(4):
        uid = 1000 + i
        for folder in ("savedata", "trophy", "inputs"):
            (user / "home" / str(uid) / folder).mkdir(parents=True, exist_ok=True)
        users.append({"user_id": uid, "user_name": "CPU State Test", "user_color": i + 1,
                      "player_index": i + 1, "shadnet_enabled": False})
    (user / "users.json").write_text(json.dumps({"Users": {"user": users}}))
    (user / "config.json").write_text(json.dumps({
        "GPU": {"null_gpu": True, "full_screen": False, "window_width": 640, "window_height": 360},
        "Log": {"filter": "*:Info", "flush_level": "info", "sync": True},
        "General": {"show_splash": False, "home_dir": str(user / "home"), "neo_mode": False}}))
    for number in range(101, 151):
        if Path(f"/tmp/.X{number}-lock").exists():
            continue
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", 6000 + number))
                break
            except OSError:
                continue
    else:
        raise RuntimeError("No free diagnostic display")
    env = dict(os.environ, DISPLAY=f"127.0.0.1:{number}", SDL_VIDEODRIVER="x11",
               SDL_AUDIODRIVER="dummy", ALSOFT_DRIVERS="null", SHADPS4_ENABLE_IPC="false")
    env["SHADPS4_XSTATE_TRACE_AUTO"] = "1" if args.unmarked else "0"
    env["SHADPS4_XSTATE_TRACE_BLOCKS"] = "0" if args.single_step else "1"
    env["SHADPS4_XSTATE_TRACE_HOST_BLOCKS"] = "0" if args.guest_blocks_only else "1"
    env["SHADPS4_XSTATE_TRACE_BRANCHES"] = "0" if args.guest_blocks_only else "1"
    env["SHADPS4_XSTATE_TRACE_RELATIVE"] = "0" if args.no_relative_blocks or args.guest_blocks_only else "1"
    summary["no_relative_blocks"] = args.no_relative_blocks or args.guest_blocks_only
    summary["unmarked"] = args.unmarked
    summary["auto_only"] = args.auto_only
    summary["block_only"] = args.block_only
    summary["single_step"] = args.single_step
    summary["guest_blocks_only"] = args.guest_blocks_only
    if args.icd:
        env["VK_DRIVER_FILES"] = str(args.icd.resolve())
    with (output / "display.log").open("w") as log:
        display = subprocess.Popen(["Xvfb", f":{number}", "-screen", "0", "640x360x24",
                                    "-listen", "tcp", "-nolisten", "unix", "-nolisten", "local", "-ac"],
                                   stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if display.poll() is not None:
            raise RuntimeError("Diagnostic display failed; see display.log")
        try:
            with socket.create_connection(("127.0.0.1", 6000 + number), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise TimeoutError("Diagnostic display did not become ready")
    start = time.monotonic()
    print(f"RUNNING=Isolated CPU-state homebrew; {args.timeout}-second limit", flush=True)
    with (output / "launch.log").open("w") as log:
        process = subprocess.Popen([str(emulator), "--ignore-game-patch", str(game / "eboot.bin")],
                                   cwd=runtime, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    while process.poll() is None:
        elapsed = time.monotonic() - start
        if elapsed > args.timeout:
            raise TimeoutError(f"CPU-state homebrew exceeded {args.timeout} seconds")
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            print(f"RUNNING=CPU-state homebrew; {int(time.monotonic() - start)} seconds", flush=True)
    summary["return_code"] = process.returncode
    summary["elapsed_seconds"] = round(time.monotonic() - start, 3)
    for name in ("cpu-xstate-hardware.txt", "xstate-trace-extra.txt", "xstate-auto-extra.txt",
                 "xstate-auto-progress.txt", "xstate-block-extra.txt", "xstate-branch-extra.txt", "xstate-relative-extra.txt"):
        if (user / "data" / name).exists():
            shutil.copy2(user / "data" / name, output / name)
    if process.returncode != 0:
        raise RuntimeError(f"Emulator exited with {process.returncode}")
    if not (args.auto_only or args.block_only):
        result = compare(here / "ps4_reference/cpu-xstate-hardware.txt", output / "cpu-xstate-hardware.txt")
        (output / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
        summary["physical_register_exception_comparison"] = {
            "common_rows": result["common_rows"], "differences": result["differences"]}
        extra = (output / "xstate-trace-extra.txt").read_text().strip()
        summary["extra"] = extra
        if (result["common_rows"] != 224 or result["differences"] or
                extra != "TRACE_EXTRA rewrites=128 nested_callbacks=35 metadata=35 retry=1 errors=0"):
            raise RuntimeError("CPU-state comparison failed; see comparison.json and extra results")
    if args.unmarked and not args.block_only:
        shutil.copy2(user / "data/xstate-auto-extra.txt", output / "xstate-auto-extra.txt")
        auto_extra = (output / "xstate-auto-extra.txt").read_text().strip()
        summary["automatic_extra"] = auto_extra
        if auto_extra != "AUTO_EXTRA flags=64 threads=2 queries=256 redirects=1 explicit_exit=1 errors=0":
            raise RuntimeError("Automatic trace checks failed: " + auto_extra)
    if args.unmarked:
        block_extra = (output / "xstate-block-extra.txt").read_text().strip().splitlines()[-1]
        summary["block_extra"] = block_extra
        if block_extra != "BLOCK_EXTRA copies=3 faults=6 nested=6 rewrites=32 async=8 code_checks=3 errors=0":
            raise RuntimeError("Native block checks failed: " + block_extra)
        branch_extra = (output / "xstate-branch-extra.txt").read_text().strip().splitlines()
        summary["branch_extra"] = branch_extra
        summary["native_branch"] = native_branch
        if branch_extra != [native_branch, "BRANCH_EXTRA interruptible_cycle=1 errors=0"]:
            raise RuntimeError("Branch results differ from the native CPU or signal check failed")
        relative_extra = (output / "xstate-relative-extra.txt").read_text().strip()
        summary["relative_extra"] = relative_extra
        if relative_extra != "RELATIVE_EXTRA lea=1152 errors=0":
            raise RuntimeError("Relative-address checks failed: " + relative_extra)
    status = 0
    print("PASS=Native block faults, rewrites and asynchronous callbacks" if args.block_only else
          "PASS=Automatic flags, threads and redirected signal return" if args.auto_only else
          "PASS=224 register/exception cases; 128 code rewrites; 35 nested callbacks; fault retry", flush=True)
except (Exception, KeyboardInterrupt) as error:
    summary["error"] = f"{type(error).__name__}: {error}"
    print("TEST_ERROR=" + summary["error"], flush=True)
finally:
    for child in (process, display):
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
    (output / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    archive = output.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as packed:
        for path in sorted(output.rglob("*")):
            relative = path.relative_to(output)
            root_result = path.parent == output and path.suffix in (".txt", ".log", ".json")
            emulator_log = relative.parts[:3] == ("runtime", "user", "log")
            probe_source = relative.parts[0] == "homebrew" and path.name in (
                "main.cpp", "cases.S", "trace_extra.inc", "trace_auto.inc", "trace_block.inc",
                "trace_branch.inc", "branch_cases.inc", "trace_relative.inc", "relative_cases.inc")
            if path.is_file() and (root_result or emulator_log or probe_source):
                packed.add(path, arcname=str(path.relative_to(output)))
    print("UPLOAD_ONLY=" + str(archive), flush=True)
raise SystemExit(status)
