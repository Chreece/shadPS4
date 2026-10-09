#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build, validate and round-trip the physical PS4 probes as installable FPKGs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
HARDWARE = ROOT.parent
PACKAGE_VERSION = "01.01"
RUNTIME_MODULES = ("libc", "libSceFios2")
TESTS = (
    ("cpu_identity", "CPUI00001", "CPU number hardware probe", "CPUIDENTITY00000", "eboot.bin",
     ("main.cpp", "Makefile", "INSTRUCTIONS.txt", "musl-COPYRIGHT")),
    ("sse4a", "SSEA00002", "SSE4a hardware probe", "SSE4AHARDWARE000", "console.bin",
     ("native_probe.cpp", "cases.S", "Makefile", "INSTRUCTIONS.txt", "musl-COPYRIGHT")),
    ("cpu_profile", "CPUP00001", "CPU profile readout", "CPUPROFILE000000", "eboot.bin",
     ("main.cpp", "Makefile", "musl-COPYRIGHT")),
    ("reciprocal", "RCPR00001", "Reciprocal hardware probe", "RECIPROCAL000000", "eboot.bin",
     ("main.cpp", "cases.S", "Makefile", "compare.py", "INSTRUCTIONS.txt", "musl-COPYRIGHT")),
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_identity(title_id, content_id, version):
    if not re.fullmatch(r"[A-Z]{4}[0-9]{5}", title_id):
        raise ValueError("Invalid title ID: " + title_id)
    if not re.fullmatch(r"[A-Z]{2}[0-9]{4}-[A-Z]{4}[0-9]{5}_00-[A-Z0-9]{16}", content_id):
        raise ValueError("Invalid content ID: " + content_id)
    if content_id[7:16] != title_id:
        raise ValueError("Content ID and title ID disagree")
    if not re.fullmatch(r"[0-9]{2}\.[0-9]{2}", version):
        raise ValueError("Invalid application version: " + version)


def sfo(title_id, title, content_id, version=PACKAGE_VERSION):
    validate_identity(title_id, content_id, version)
    values = {
        "APP_TYPE": (1, 4), "APP_VER": (version, 8), "ATTRIBUTE": (0, 4),
        "CATEGORY": ("gd", 4), "CONTENT_ID": (content_id, 48),
        "DOWNLOAD_DATA_SIZE": (0, 4), "SYSTEM_VER": (0, 4),
        "TITLE": (title, 128), "TITLE_ID": (title_id, 12), "VERSION": (version, 8),
    }
    keys, data, entries = bytearray(), bytearray(), bytearray()
    for key, (value, capacity) in sorted(values.items()):
        integer = isinstance(value, int)
        raw = struct.pack("<I", value) if integer else value.encode() + b"\0"
        if len(raw) > capacity:
            raise ValueError("SFO field too long: " + key)
        entries += struct.pack("<HHIII", len(keys), 0x404 if integer else 0x204,
                               len(raw), capacity, len(data))
        keys += key.encode() + b"\0"
        data += raw + bytes(capacity - len(raw))
    key_offset = 20 + len(entries)
    data_offset = (key_offset + len(keys) + 3) & ~3
    return (struct.pack("<5I", 0x46535000, 0x101, key_offset, data_offset, len(values))
            + entries + keys + bytes(data_offset - key_offset - len(keys)) + data)


def run(args, cwd, env):
    print("STEP=" + " ".join(map(str, args)), flush=True)
    result = subprocess.run(list(map(str, args)), cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=180)
    print(result.stdout, end="", flush=True)
    result.check_returncode()
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, default=os.environ.get("OO_PS4_TOOLCHAIN"))
    parser.add_argument("--cxx", default="clang++-19")
    parser.add_argument("--ld", default="ld.lld-19")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--only", choices=[test[0] for test in TESTS])
    parser.add_argument("--app-version", default=PACKAGE_VERSION)
    args = parser.parse_args()
    if not args.sdk:
        parser.error("set --sdk or OO_PS4_TOOLCHAIN to OpenOrbis v0.5.4")
    sdk, destination = args.sdk.resolve(), args.output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, DOTNET_SYSTEM_GLOBALIZATION_INVARIANT="1")
    pkgtool = sdk / "bin/linux/PkgTool.Core"
    manifest = {"format": 1, "packaging_revision": 3, "sdk": "OpenOrbis v0.5.4",
                "physical_ps4_tested": False,
                "tests": [], "tools": {}}
    for name in ("PkgTool.Core", "create-gp4", "create-fself"):
        manifest["tools"][name] = digest(sdk / "bin/linux" / name)
    with tempfile.TemporaryDirectory(prefix="ps4-fpkg-") as temporary:
        work = Path(temporary)
        bundle = work / "bundle"
        bundle.mkdir()
        source = bundle / "source"
        shutil.copytree(ROOT, source / "hardware/ps4_fpkg",
                        ignore=shutil.ignore_patterns("dist", "__pycache__"))
        licenses = source / "LICENSES"
        licenses.mkdir()
        shutil.copy2(HARDWARE.parent / "LICENSES/GPL-2.0-or-later.txt", licenses)
        shutil.copy2(sdk / "LICENSE", licenses / "OpenOrbis-GPL-3.0.txt")
        shutil.copytree(sdk / "src/modules/right", source / "OpenOrbis-right")
        for module in RUNTIME_MODULES:
            module_source = source / "OpenOrbis-modules" / module
            (module_source / module).mkdir(parents=True)
            shutil.copy2(sdk / "src/modules" / module / "Makefile", module_source)
            shutil.copy2(sdk / "src/modules" / module / module / "lib.c",
                         module_source / module)
        shutil.copytree(sdk / "src/crt", source / "OpenOrbis-crt",
                        ignore=shutil.ignore_patterns("*.o", "*.elf", "*.prx"))
        for directory, title_id, title, suffix, executable, inputs in TESTS:
            if args.only and directory != args.only:
                continue
            content_id = f"IV0000-{title_id}_00-{suffix}"
            validate_identity(title_id, content_id, args.app_version)
            test_source = source / "hardware" / directory
            test_source.mkdir()
            for name in inputs:
                shutil.copy2(HARDWARE / directory / name, test_source / name)
            build = work / directory
            shutil.copytree(test_source, build)
            run(["make", executable, f"OO_PS4_TOOLCHAIN={sdk}",
                 f"ORBIS_CXX={args.cxx}", f"ORBIS_LD={args.ld}"], build, env)
            package_root = build / title_id
            system = package_root / "sce_sys"
            (system / "about").mkdir(parents=True)
            shutil.copy2(build / executable, package_root / "eboot.bin")
            (system / "param.sfo").write_bytes(sfo(title_id, title, content_id, args.app_version))
            shutil.copy2(ROOT / (directory + "-icon.png"), system / "icon0.png")
            shutil.copy2(sdk / "samples/hello_world/sce_sys/about/right.sprx",
                         system / "about/right.sprx")
            modules = package_root / "sce_module"
            modules.mkdir()
            for module in RUNTIME_MODULES:
                shutil.copy2(sdk / "samples/hello_world/sce_module" / (module + ".prx"),
                             modules / (module + ".prx"))
            files = sorted(p.relative_to(package_root).as_posix()
                           for p in package_root.rglob("*") if p.is_file())
            run([sdk / "bin/linux/create-gp4", "-out", "package.gp4",
                 "--content-id=" + content_id, "--files", " ".join(files)], package_root, env)
            build_log = run([pkgtool, "pkg_build", "package.gp4", bundle], package_root, env)
            if "WARNING" in build_log or "ERROR" in build_log:
                raise RuntimeError("Package build reported a metadata issue")
            (bundle / (title_id + "-build.txt")).write_text(build_log)
            package = bundle / (content_id + ".pkg")
            with package.open("rb") as stream:
                header = stream.read(0x70)
            if header[:4] != b"\x7fCNT":
                raise RuntimeError("Not a PS4 package: " + str(package))
            if header[0x40:0x70] != content_id.encode() + bytes(48 - len(content_id)):
                raise RuntimeError("Package header content ID mismatch")
            validation = run([pkgtool, "pkg_validate", "--verbose", package], work, env)
            if "[ERROR]" in validation or "[OK]" not in validation:
                raise RuntimeError("Package validation failed: " + package.name)
            (bundle / (title_id + "-validation.txt")).write_text(validation)
            extracted = work / (title_id + "-extracted")
            run([pkgtool, "pkg_makegp4", package, extracted], work, env)
            hashes = {}
            for name in files:
                original, restored = package_root / name, extracted / name
                if not restored.is_file() or digest(original) != digest(restored):
                    raise RuntimeError("FPKG round-trip mismatch: " + name)
                hashes[name] = digest(original)
            manifest["tests"].append({"title_id": title_id, "title": title,
                                      "content_id": content_id, "version": args.app_version,
                                      "package": package.name, "sha256": digest(package),
                                      "size": package.stat().st_size, "round_trip": True,
                                      "files": hashes})
        instructions = (HARDWARE / args.only / "INSTRUCTIONS.txt") if args.only else None
        if not instructions or not instructions.is_file():
            instructions = ROOT / "INSTRUCTIONS.txt"
        shutil.copy2(instructions, bundle / "INSTRUCTIONS.txt")
        (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (bundle / "SHA256SUMS.txt").write_text("".join(
            f"{item['sha256']}  {item['package']}\n" for item in manifest["tests"]))
        name = (f"ps4-{args.only}-hardware-test-v3.zip" if args.only
                else "ps4-cpu-tests-all-fpkg-v3.zip")
        output = destination / name
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(bundle))
        print("READY=" + str(output))
        print("SHA256=" + digest(output))


if __name__ == "__main__":
    main()
