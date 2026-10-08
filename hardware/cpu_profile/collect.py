#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Compare native host and guest CPU descriptions using an isolated homebrew."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request


HERE = Path(__file__).resolve().parent
SUITE_SHA256 = "e96ea8dfaf0b397a0bbd29fa89193547436e9d479200a262baa3bdb47504b778"
ARTIFACTS = {
    "working-build-95b74819840d/shadps4": "d78c29f93102fa83cfb915a530c71b6a196e7811eeb5757a740427c624af078e",
    "working-build-95b74819840d/src/core/cpu_id_translation/libshadps4_cpu_id.so": "af4b6526a505bd124957584b5c8cb0dca29e85eb0d73ee1a4871f7d1da357861",
    "runtime-build-a522a5055820/bin64/drrun": "e58dcb8cdefec91d144c1a8fa79a7bd1b8a29969c4319f31df4df01644ef0b97",
    "runtime-build-a522a5055820/lib64/release/libdynamorio.so": "f7ab039e3585c78b2bc52bab57de1a61b8ac276ab8c0af52cf17c55b1f2a2233",
}
FEATURES = {
    "SSE3": (1, 0, "ecx", 0), "PCLMULQDQ": (1, 0, "ecx", 1),
    "SSSE3": (1, 0, "ecx", 9), "FMA": (1, 0, "ecx", 12),
    "SSE4.1": (1, 0, "ecx", 19), "SSE4.2": (1, 0, "ecx", 20),
    "MOVBE": (1, 0, "ecx", 22), "POPCNT": (1, 0, "ecx", 23),
    "AES": (1, 0, "ecx", 25), "XSAVE": (1, 0, "ecx", 26),
    "OSXSAVE": (1, 0, "ecx", 27), "AVX": (1, 0, "ecx", 28),
    "F16C": (1, 0, "ecx", 29), "RDRAND": (1, 0, "ecx", 30),
    "BMI1": (7, 0, "ebx", 3), "AVX2": (7, 0, "ebx", 5),
    "BMI2": (7, 0, "ebx", 8), "AVX512F": (7, 0, "ebx", 16),
    "RDSEED": (7, 0, "ebx", 18), "ADX": (7, 0, "ebx", 19),
    "SHA": (7, 0, "ebx", 29), "RDPID": (7, 0, "ecx", 22),
    "LZCNT": (0x80000001, 0, "ecx", 5), "SSE4a": (0x80000001, 0, "ecx", 6),
    "RDTSCP": (0x80000001, 0, "edx", 27),
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def parse_profile(text):
    beginnings = re.findall(r"CPU_PROFILE_BEGIN version=(\d+) platform=(\w+)", text)
    endings = re.findall(r"CPU_PROFILE_END failures=(\d+) rows=(\d+)", text)
    if len(beginnings) != 1 or len(endings) != 1 or beginnings[0][0] != "1":
        raise ValueError("Missing, repeated, or unsupported CPU-profile run markers")
    if int(endings[0][0]) != 0:
        raise ValueError("The CPU-profile probe reported a failure")
    records = {}
    pattern = (r"CPU_PROFILE_CPUID mode=(static|generated) cpu=(\d+) leaf=([0-9a-f]{8}) "
               r"subleaf=([0-9a-f]{8}) eax=([0-9a-f]{8}) ebx=([0-9a-f]{8}) "
               r"ecx=([0-9a-f]{8}) edx=([0-9a-f]{8})")
    for mode, cpu, leaf, subleaf, *values in re.findall(pattern, text):
        key = mode, int(cpu), int(leaf, 16), int(subleaf, 16)
        if key in records:
            raise ValueError("Repeated CPU-profile row")
        records[key] = dict(zip(("eax", "ebx", "ecx", "edx"), (int(x, 16) for x in values)))
    if len(records) != int(endings[0][1]) or not records:
        raise ValueError("CPU-profile row count is incomplete")
    static = {key[1:]: value for key, value in records.items() if key[0] == "static"}
    generated = {key[1:]: value for key, value in records.items() if key[0] == "generated"}
    coverage = {
        "static_only": [{"cpu": key[0], "leaf": f"{key[1]:08x}", "subleaf": key[2]}
                        for key in sorted(set(static) - set(generated))],
        "generated_only": [{"cpu": key[0], "leaf": f"{key[1]:08x}", "subleaf": key[2]}
                           for key in sorted(set(generated) - set(static))],
    }
    differences = [{"cpu": key[0], "leaf": f"{key[1]:08x}", "subleaf": key[2],
                    "static": static[key], "generated": generated[key]}
                   for key in sorted(set(static) & set(generated)) if static[key] != generated[key]]
    states = {}
    for mode, cpu, value in re.findall(
            r"CPU_PROFILE_XCR0 mode=(static|generated) cpu=(\d+) value=([0-9a-f]{16})", text):
        key = mode, int(cpu)
        if key in states:
            raise ValueError("Repeated XCR0 row")
        states[key] = int(value, 16)
    cpus = sorted({key[0] for key in static})
    features = {}
    state_errors = []
    for cpu in cpus:
        for mode in ("static", "generated"):
            one = records[mode, cpu, 1, 0]
            has_xgetbv = one["ecx"] & (3 << 26) == 3 << 26
            if has_xgetbv != ((mode, cpu) in states):
                raise ValueError("XCR0 coverage disagrees with XSAVE/OSXSAVE")
            if has_xgetbv:
                state = states[mode, cpu]
                supported = records.get((mode, cpu, 0xd, 0), {})
                mask = supported.get("eax", 0) | supported.get("edx", 0) << 32
                if state & ~mask or not state & 1 or (state & 4 and not state & 2):
                    state_errors.append({"cpu": cpu, "mode": mode, "xcr0": state,
                                         "supported": mask})
        if states.get(("static", cpu)) != states.get(("generated", cpu)):
            state_errors.append({"cpu": cpu, "error": "XCR0 changed between query paths"})
        features[str(cpu)] = sorted(name for name, (leaf, subleaf, reg, bit) in FEATURES.items()
                                    if static.get((cpu, leaf, subleaf), {}).get(reg, 0) & (1 << bit))
    return {"platform": beginnings[0][1], "cpus": cpus, "rows": len(records),
            "features_by_cpu": features, "static_generated_differences": differences,
            "coverage_differences": coverage,
            "xstate_errors": state_errors,
            "ok": not differences and not state_errors and not any(coverage.values())}


def existing_emulators():
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            names = {(proc / "comm").read_text().strip().lower()}
            try:
                names.add(Path(os.readlink(proc / "exe").removesuffix(" (deleted)")).name.lower())
            except OSError:
                pass
            if names & {"shadps4", "shadps4-bin", "shadps4.exe"}:
                found.append(int(proc.name))
        except OSError:
            continue
    return found


def require_idle():
    active = existing_emulators()
    if active:
        raise RuntimeError("Close shadPS4 before running this test; active PIDs=" + str(active))


def run(command, cwd, env, log, emulator=False):
    require_idle()
    with log.open("w") as output:
        process = subprocess.Popen(list(map(str, command)), cwd=cwd, env=env,
                                   stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        interrupted = False
        try:
            started = time.monotonic()
            while process.poll() is None:
                if time.monotonic() - started > 45:
                    timed_out = True
                    break
                if emulator and any(pid != process.pid for pid in existing_emulators()):
                    raise RuntimeError("Another emulator started; stopping only this test")
                time.sleep(0.1)
        except KeyboardInterrupt:
            interrupted = True
            raise
        finally:
            forced = process.poll() is None
            if forced:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=3)
            status = {"returncode": process.returncode, "timeout": timed_out,
                      "forced_cleanup": forced, "interrupted": interrupted}
            log.with_suffix(".process.json").write_text(json.dumps(status, indent=2) + "\n")
    if timed_out or process.returncode != 0:
        raise RuntimeError("Probe did not finish normally; see " + log.name)


def prepare_runtime(path):
    user = path / "user"
    user.mkdir(parents=True)
    users = []
    for index in range(4):
        user_id = 1000 + index
        for directory in ("savedata", "trophy", "inputs"):
            (user / "home" / str(user_id) / directory).mkdir(parents=True)
        users.append({"user_id": user_id, "user_name": "CPU Profile Test",
                      "user_color": index + 1, "player_index": index + 1,
                      "shadnet_enabled": False})
    (user / "users.json").write_text(json.dumps({"Users": {"user": users}}))
    (user / "config.json").write_text(json.dumps({
        "GPU": {"null_gpu": True, "full_screen": False, "window_width": 640, "window_height": 360},
        "Log": {"filter": "*:Info", "flush_level": "info", "sync": True, "skip_duplicate": False},
        "General": {"show_splash": False, "home_dir": str(user / "home")},
    }))


def display_environment():
    env = os.environ.copy()
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        wanted = {b"DISPLAY", b"XAUTHORITY", b"WAYLAND_DISPLAY", b"XDG_RUNTIME_DIR"}
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                if proc.stat().st_uid != os.getuid():
                    continue
                if (proc / "comm").read_text().strip() not in {
                        "sunshine", "gnome-shell", "kwin_wayland", "xfce4-session"}:
                    continue
                for entry in (proc / "environ").read_bytes().split(b"\0"):
                    key, sep, value = entry.partition(b"=")
                    if sep and key in wanted:
                        env[key.decode()] = value.decode()
                if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
                    break
            except (OSError, UnicodeError):
                continue
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        raise RuntimeError("No display found for the automatic homebrew test")
    env.update(ALSOFT_DRIVERS="null", SDL_AUDIODRIVER="dummy", SHADPS4_ENABLE_IPC="false")
    return env


def unpack_suite(path, destination):
    if digest(path) != SUITE_SHA256:
        raise RuntimeError("CPU-profile suite checksum mismatch")
    with tarfile.open(path, "r:gz") as archive:
        names = set()
        for member in archive.getmembers():
            relative = Path(member.name)
            if (not member.isfile() or relative.is_absolute() or ".." in relative.parts
                    or member.name in names or member.size > 2 * 1024 * 1024):
                raise RuntimeError("Invalid CPU-profile suite entry")
            names.add(member.name)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    (destination / "native-cpu-profile").chmod(0o755)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="Immutable source revision for downloading the probe")
    parser.add_argument("--host-only", action="store_true")
    parser.add_argument("--analyze", type=Path, help="Analyze a saved PS4 or emulator profile")
    args = parser.parse_args()
    if args.analyze:
        result = parse_profile(args.analyze.read_text(errors="replace"))
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if not sys.platform.startswith("linux") or os.uname().machine != "x86_64":
        raise RuntimeError("This collector requires Linux x86-64")
    cache = Path.home() / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "homebrew.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_idle()
        evidence = Path(tempfile.mkdtemp(prefix="shadps4-cpu-profile-", dir=Path.home()))
        summary = {"kind": "cpu-profile-observation", "reference_ps4_validated": False,
                   "tests": {}, "installed_binary_changed": False}
        installed = Path.home() / "Applications/shadps4/shadps4"
        before = digest(installed) if installed.is_file() else None
        success = False
        try:
            with tempfile.TemporaryDirectory(prefix="cpu-profile-", dir=cache) as temporary:
                work = Path(temporary)
                suite = HERE / "suite.tar.gz"
                if not suite.is_file():
                    if not re.fullmatch(r"[0-9a-f]{40}", args.revision or ""):
                        raise RuntimeError("A pinned --revision is required")
                    url = ("https://raw.githubusercontent.com/Chreece/shadPS4/" + args.revision
                           + "/hardware/cpu_profile/suite.tar.gz")
                    suite = work / "suite.tar.gz"
                    with urllib.request.urlopen(url, timeout=30) as response:
                        suite.write_bytes(response.read(2 * 1024 * 1024 + 1))
                unpack_suite(suite, work / "suite")
                print("CPU_PROFILE=Reading the PC's real CPU capabilities", flush=True)
                host_log = evidence / "host.log"
                run([work / "suite/native-cpu-profile"], work, os.environ.copy(), host_log)
                summary["tests"]["host"] = parse_profile(host_log.read_text())
                if not args.host_only:
                    paths = []
                    for relative, expected in ARTIFACTS.items():
                        path = cache / relative
                        if not path.is_file() or digest(path) != expected:
                            raise RuntimeError("The verified candidate is missing or changed: " + str(path))
                        paths.append(path)
                    binary, client, drrun, _runtime_library = paths
                    summary["artifacts"] = ARTIFACTS
                    env = display_environment()
                    modes = {
                        "guest-native": [binary],
                        "guest-translated": [drrun, "-disable_rseq", "-vm_base", "0x710020000000",
                                             "-no_vm_base_near_app", "-c", client, "--", binary],
                    }
                    for name, prefix in modes.items():
                        runtime = work / name
                        prepare_runtime(runtime)
                        output = evidence / (name + ".log")
                        print("CPU_PROFILE=" + name + " (automatic; keep games closed)", flush=True)
                        try:
                            run(prefix + ["--ignore-game-patch", work / "suite/CPUP00001/eboot.bin"],
                                runtime, env, output, emulator=True)
                        finally:
                            for log in (runtime / "user/log").glob("*"):
                                if log.is_file():
                                    shutil.copy2(log, evidence / (name + "-" + log.name))
                            profile = runtime / "user/data/cpu-profile.txt"
                            if profile.is_file():
                                shutil.copy2(profile, evidence / (name + ".profile.txt"))
                        profile = evidence / (name + ".profile.txt")
                        content = profile.read_text() if profile.is_file() else output.read_text(errors="replace")
                        summary["tests"][name] = parse_profile(content)
                host_sets = [set(value) for value in summary["tests"]["host"]["features_by_cpu"].values()]
                host_common = set.intersection(*host_sets)
                host_union = set.union(*host_sets)
                summary["host_guest_comparison"] = {}
                for name, result in summary["tests"].items():
                    if name == "host":
                        continue
                    guest_sets = [set(value) for value in result["features_by_cpu"].values()]
                    guest_common = set.intersection(*guest_sets)
                    guest_union = set.union(*guest_sets)
                    summary["host_guest_comparison"][name] = {
                        "host_features_on_every_cpu": sorted(host_common),
                        "guest_features_on_every_cpu": sorted(guest_common),
                        "host_features_not_advertised_to_guest": sorted(host_union - guest_union),
                        "guest_features_not_native_on_every_host_cpu": sorted(guest_union - host_common),
                    }
                success = all(result["ok"] for result in summary["tests"].values())
        except (Exception, KeyboardInterrupt) as error:
            summary["error"] = type(error).__name__ + ": " + str(error)
            print("CPU_PROFILE_ERROR=" + summary["error"], flush=True)
        finally:
            after = digest(installed) if installed.is_file() else None
            summary["installed_binary_changed"] = before != after
            summary["ok"] = success and before == after
            (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            archive_path = evidence.with_suffix(".tar.gz")
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(evidence, arcname=evidence.name)
            print("UPLOAD_ONLY=" + str(archive_path), flush=True)
            print("SSH stays open. Installed emulator, game profiles and session guard were not edited.", flush=True)
        return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
