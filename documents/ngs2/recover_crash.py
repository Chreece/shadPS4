#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Save existing crash evidence, then restore the verified f00 test launcher."""
import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

WORKING = "f00bef80a74e6df0835ea0e9818c71c9d4ddf578"
DIAGNOSTIC = {"ac36a0edd40409c3c9ed67dc68c630b7d2dcba7e",
              "ca67919dacf2917140fb957142dcd993737d9dd6",
              "59566b916c3ff680616081c9bcde642e70f874a7",
              "66a2ef4d25e2029628dad50f5ec9a308ef072c47",
              "9e95c1727d287514d0e85aef9f863e0b293f6e5b"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_tail(path, limit=2 * 1024 * 1024):
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - limit))
        return stream.read(limit)


def no_running_core():
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            target = (entry / "exe").readlink()
        except OSError:
            continue
        if target.name.lower().removesuffix(" (deleted)") in {"shadps4", "shadps4.exe"}:
            raise RuntimeError("Close the running game normally before recovery.")


def collect(home, states, wrapper):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    archive = home / ("ngs2-startup-crash-" + stamp + ".tar.gz")
    notes = ["Captured before rollback; each log is limited to its last 2 MiB."]
    profiles = [Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "shadPS4",
                home / ".local/share/shadPS4", home / "Applications/shadps4/user", home / "user"]
    with archive.open("xb") as output:
        os.fchmod(output.fileno(), 0o600)
        with tarfile.open(fileobj=output, mode="w:gz") as bundle:
            def add(name, data):
                item = tarfile.TarInfo(name)
                item.size = len(data)
                item.mode = 0o600
                bundle.addfile(item, io.BytesIO(data))

            def copy(path, name, limit=2 * 1024 * 1024):
                if path.is_file() and not path.is_symlink():
                    try:
                        add(name, read_tail(path, limit))
                        notes.append(name + " <- " + str(path))
                    except OSError as error:
                        notes.append(str(error))

            for index, profile in enumerate(dict.fromkeys(profiles)):
                logdir = profile / "log"
                files = sorted((p for p in logdir.glob("*") if p.is_file()),
                               key=lambda p: p.stat().st_mtime, reverse=True)
                for path in files[:12]:
                    copy(path, "logs/%s/%s" % (index, path.name))
            copy(home / "ngs2-diagnostic-ca67919d.log", "diagnostic.log")
            for revision in sorted(DIAGNOSTIC):
                if not revision.startswith("ca67919d"):
                    copy(home / ("ngs2-diagnostic-" + revision[:8] + ".log"),
                         "diagnostic-" + revision[:8] + ".log")
            copy(wrapper, "launcher.before-recovery")
            for state_file, _ in states:
                copy(state_file, "deployments/" + state_file.parent.name + ".json")
            work = home / ".cache/shadps4-ngs2-local/ca67919d-docker"
            copy(work / "build.log", "build-tail.log", 256 * 1024)
            copy(work / "build/CMakeCache.txt", "CMakeCache.txt")
            if shutil.which("coredumpctl"):
                try:
                    current_hash = digest(wrapper.read_bytes())
                    current_state = next((state for _, state in states
                                          if state.get("installed_wrapper_sha256") == current_hash
                                          and state.get("commit") in DIAGNOSTIC), None)
                    current_binary = (current_state["binary"] if current_state else
                                      str(home / "Applications/shadps4/releases/ngs2-ac36a0ed/shadps4"))
                    result = subprocess.run(
                        ["coredumpctl", "--no-pager", "--since=-2h", "info",
                         current_binary],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
                    add("coredump-info.txt", result.stdout[-256 * 1024:])
                except (OSError, subprocess.TimeoutExpired) as error:
                    notes.append("coredumpctl: " + str(error))
            add("capture.txt", ("\n".join(notes) + "\n").encode())
    return archive


def rollback_plan(home, states, current):
    wrapper = home / ".local/bin/shadps4-esde"
    binary = home / "Applications/shadps4/releases/ngs2-f00bef80/shadps4"
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("The previous f00bef80 executable is missing or not executable.")
    working_states = [s for _, s in states if s.get("commit") == WORKING
                      and s.get("wrapper") == str(wrapper) and s.get("binary") == str(binary)]
    binary_hash = digest(binary.read_bytes())
    working_states = [s for s in working_states if s.get("binary_sha256") == binary_hash]
    if not working_states:
        raise RuntimeError("Cannot verify the previous executable against its deployment record.")
    target_hashes = {s["installed_wrapper_sha256"] for s in working_states}
    candidate, mode = current, None
    seen = set()
    for _ in range(8):
        current_hash = digest(candidate)
        if current_hash in target_hashes:
            return candidate, mode
        if current_hash in seen:
            break
        seen.add(current_hash)
        matches = [(p, s) for p, s in states if s.get("commit") in DIAGNOSTIC
                   and s.get("wrapper") == str(wrapper)
                   and s.get("installed_wrapper_sha256") == current_hash]
        if not matches:
            break
        state_file, state = matches[0]
        backup = state_file.parent / "shadps4-esde.before"
        if backup.is_symlink() or not backup.is_file():
            raise RuntimeError("The launcher backup is missing or is a symlink.")
        candidate = backup.read_bytes()
        if digest(candidate) != state.get("original_wrapper_sha256"):
            raise RuntimeError("The launcher backup checksum is invalid.")
        mode = state["original_mode"]
        if not isinstance(mode, int) or mode & ~0o777 or not mode & 0o111:
            raise RuntimeError("Invalid saved launcher permissions.")
    raise RuntimeError("No verified rollback chain to f00bef80; preserving the current launcher.")


def recover(home):
    state_root = home / ".local/state/shadps4-ngs2"
    wrapper = home / ".local/bin/shadps4-esde"
    if not state_root.is_dir() or wrapper.is_symlink() or not wrapper.is_file():
        raise RuntimeError("Expected the existing regular launcher and deployment records.")
    with (state_root / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        no_running_core()
        states = []
        for path in sorted(state_root.glob("*/deployment.json"), reverse=True):
            try:
                states.append((path, json.loads(path.read_text())))
            except (OSError, ValueError):
                continue
        archive = collect(home, states, wrapper)
        print("CRASH_ARCHIVE=" + str(archive), flush=True)
        current = wrapper.read_bytes()
        restored, mode = rollback_plan(home, states, current)
        if restored != current:
            with tempfile.NamedTemporaryFile(dir=wrapper.parent, prefix="ngs2-restore-",
                                             delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write(restored)
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchmod(stream.fileno(), mode)
                    subprocess.run(["bash", "-n", str(temporary)], check=True)
                    no_running_core()
                    if wrapper.is_symlink() or wrapper.read_bytes() != current:
                        raise RuntimeError("Launcher changed during recovery; preserving those edits.")
                    os.replace(temporary, wrapper)
                finally:
                    temporary.unlink(missing_ok=True)
        print("NGS2_RECOVERY=PASS: f00bef80 test launcher selected; settings unchanged.")
        print("Upload CRASH_ARCHIVE. No game was launched.")


if __name__ == "__main__":
    try:
        if os.geteuid() == 0 or sys.platform != "linux":
            raise RuntimeError("Run on your Linux desktop account, without sudo.")
        recover(Path.home())
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("NGS2_RECOVERY=FAIL: " + str(error), file=sys.stderr)
        sys.exit(1)
