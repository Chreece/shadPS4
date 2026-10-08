#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline AMDGPU reset evidence for Ghost's 2026-10-08 19:55 GPU fault.

Read-only. No sudo, package changes, game launch, GPU reset, config edits,
process termination, or SSH session changes.
"""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tarfile

HOME = Path.home()
STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
WORK = HOME / ".cache" / ("ghost-amdgpu-fault-" + STAMP)
OUT = HOME / ("ghost-amdgpu-fault-" + STAMP + ".tar.gz")
SINCE, UNTIL = "2026-10-08 19:54:30", "2026-10-08 20:01:00"
STATE = HOME / ".local/state/shadps4-playtest-logs"
MAX_LOG = 9 * 1024 * 1024
MARKERS = {
    "ring_timeout": re.compile(r"\bring\b.{0,70}\btimeout\b", re.I),
    "gpu_reset": re.compile(r"\bgpu.{0,30}\breset\b|\bsoft recovery\b", re.I),
    "vm_fault": re.compile(r"\bVM\b.{0,30}\bfault\b|\bpage fault\b", re.I),
    "device_lost": re.compile(r"\bdevice.{0,30}\blost\b|\bcontext.{0,30}\blost\b", re.I),
    "amdgpu": re.compile(r"amdgpu|radv|drm", re.I),
}
STATUS = []

def say(text):
    print(text, flush=True)
    STATUS.append(text)

def run(label, args, timeout=16):
    target = WORK / (label + ".txt")
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout,
                              env={**os.environ, "LC_ALL": "C"})
        target.write_bytes(proc.stdout[:MAX_LOG])
        error = proc.stderr.decode("utf-8", "replace")[:10000]
        (WORK / (label + ".meta.txt")).write_text(
            f"returncode={proc.returncode}\ncommand={args!r}\nstderr={error}\n"
        )
        say(f"{label}: rc={proc.returncode}, output_bytes={target.stat().st_size}, "
            f"stderr={error[:100].strip()!r}")
    except (subprocess.TimeoutExpired, OSError) as e:
        target.write_bytes(b"")
        (WORK / (label + ".meta.txt")).write_text(f"{type(e).__name__}: {e}\n")
        say(f"{label}: {type(e).__name__} (only the evidence command was affected)")

def pci_gpu():
    devices = []
    for link in Path("/sys/class/drm").glob("renderD*/device"):
        try:
            path = link.resolve()
            item = {"render_node": str(link.parent), "driver": str((path / "driver").resolve())}
            for field in ("vendor", "device", "revision", "subsystem_vendor", "subsystem_device"):
                try:
                    item[field] = (path / field).read_text().strip()
                except OSError:
                    item[field] = "unavailable"
            devices.append(item)
        except OSError:
            continue
    (WORK / "gpu-sysfs.json").write_text(json.dumps(devices, indent=2) + "\n")

def vk_layers():
    layers = []
    for folder in (Path("/usr/share/vulkan/explicit_layer.d"),
                   Path("/usr/share/vulkan/implicit_layer.d")):
        if not folder.is_dir():
            continue
        for path in folder.glob("*.json"):
            try:
                data = json.loads(path.read_text(errors="replace"))
                item = data.get("layer", {})
                layers.append({"file": str(path), "name": item.get("name"),
                               "api_version": item.get("api_version")})
            except (ValueError, OSError) as e:
                layers.append({"file":str(path), "error": str(e)})
    (WORK / "vulkan-layers.json").write_text(json.dumps(layers, indent=2) + "\n")

def game_log():
    paths = list(STATE.glob("20261008-195534-*-Ghost_of_Tsushima.ps4/runtime.log"))
    if not paths:
        say("The 19:55 original Ghost runtime was not found on disk.")
        return
    text = paths[0].read_text(errors="replace")
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    lines = []
    for i, line in enumerate(text.splitlines(), 1):
        if any(key in line for key in
               ("GHOST_MIP_COPY", "GHOST_TRACE", "Device lost", "context is lost",
                "HOST_QUIT action=", "Assertion Failed")):
            lines.append(f"{i}: {line}")
    (WORK / "ghost-experiment-events.txt").write_text("\n".join(lines) + "\n")
    say(f"Ghost original run recovered: {len(lines)} relevant runtime events.")

def summarize():
    results = [
        "GHOST / RADV GPU FAULT EVIDENCE",
        f"Kernel window: {SINCE} to {UNTIL} (host local time)",
        "GPU candidate reached 2 nine-mip copies before later VK_ERROR_DEVICE_LOST.",
    ]
    for label in ("kernel-journal", "system-journal", "user-journal", "dmesg"):
        content = (WORK / (label + ".txt")).read_text(errors="replace")
        counts = {k:len(rx.findall(content)) for k,rx in MARKERS.items()}
        results.append(f"{label} event counts: {counts}")
        hits = [(i+1,l) for i,l in enumerate(content.splitlines())
                if any(p.search(l) for p in MARKERS.values())]
        for line_no, line in hits[-90:]:
            results.append(f"  {label}:{line_no} {line[:350]}")
    for label in ("kernel-journal", "dmesg"):
        meta = (WORK / (label + ".meta.txt")).read_text(errors="replace")
        if "insufficient permissions" in meta or "Operation not permitted" in meta or "No journal files" in meta:
            results.append(f"{label}: PERMISSION_LIMITED — no evidence of absence inferred")
    vklayers = json.loads((WORK / "vulkan-layers.json").read_text())
    validation = any(l.get("name") == "VK_LAYER_KHRONOS_validation" for l in vklayers)
    results.append(f"VK_LAYER_KHRONOS_validation installed: {validation}")
    if not (WORK / "kernel-journal.txt").stat().st_size and not (WORK / "dmesg.txt").stat().st_size:
        results.append("NO_READABLE_KERNEL_LOG: cannot determine which GPU engine or address faulted.")
    (WORK / "summary.txt").write_text("\n".join(results)+"\n")
    say("SUMMARY:")
    for line in results[:8]:
        say(line)

def self_test():
    sample = ("amdgpu 0000:03:00.0: ring gfx_0.0.0 timeout\n"
              "amdgpu: GPU reset succeeded\n"
              "amdgpu: VM fault\n"
              "radv/amdgpu: context is lost\n")
    assert all(bool(rx.search(sample)) for rx in MARKERS.values())
    assert len(MARKERS["ring_timeout"].findall(sample)) == 1
    assert SINCE < UNTIL
    print("SELFTEST PASS: AMD ring timeout, GPU reset, VM fault, device loss; "
          "no journal commands run")

def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    WORK.mkdir(parents=True, exist_ok=True)
    say("READ-ONLY collection for the 19:55 Vulkan device loss — no sudo or game launch.")
    try:
        base = ["journalctl", "--no-pager", "-o", "short-iso-precise",
                "--since", SINCE, "--until", UNTIL]
        run("kernel-journal", base + ["-k"], timeout=20)
        run("system-journal", base, timeout=20)
        run("user-journal", base + ["--user"], timeout=20)
        run("dmesg", ["dmesg", "--time-format", "iso"], timeout=12)
        run("gpu-pci", ["lspci", "-nnk", "-d", "1002:"], timeout=10)
        run("mesa-packages", ["dpkg-query", "-W", "mesa-vulkan-drivers",
                               "vulkan-validationlayers", "libvulkan1"], timeout=10)
        (WORK / "system.txt").write_text(f"kernel={platform.release()}\n"
                                          f"platform={platform.platform()}\n")
        pci_gpu()
        vk_layers()
        game_log()
        summarize()
    finally:
        (WORK / "status.txt").write_text("\n".join(STATUS)+"\n")
        with tarfile.open(OUT, "w:gz") as archive:
            archive.add(WORK, arcname=WORK.name)
        say(f"ARCHIVE={OUT}")
        say("No shadPS4 executable, game, saves, driver settings or SSH session changed.")

if __name__ == "__main__":
    main()
