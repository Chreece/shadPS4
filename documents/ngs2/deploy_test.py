#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Install the pinned Linux NGS2 test core and reversibly select it in ES-DE."""

import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

REPO = "Chreece/shadPS4"
COMMIT = "f00bef80a74e6df0835ea0e9818c71c9d4ddf578"
BUILD = 36789001354
FOCUSED = 36789001319
BASE_URL = f"https://api.github.com/repos/{REPO}"
MARKER = "# NGS2 isolated core selection: " + COMMIT
LAUNCH = re.compile(
    r'(?m)^([ \t]*)(?:(?:exec|setsid)(?:[ \t]+--)?[ \t]+)*'
    r'(?:"\$CORE"|"\$\{CORE\}")[ \t]+(?:--game|-g)[ \t]'
)
PROBE_MARKER = "# NGS2_PROBE_DISPATCH_V1"


def probe_wrapper(home, backup):
    """Known probe dispatcher; keep its fallback and token handling intact."""
    return f'''#!/usr/bin/env bash
# NGS2_PROBE_DISPATCH_V1
_ngs2_dispatch() {{
    local token=""
    if [[ -f "${{1:-}}" ]]; then
        IFS= read -r token < "$1" || true
        token="${{token%$'\\r'}}"
    fi
    if [[ "$token" == "CUSA36843|ngs2probe" ]]; then
        python3 {home}/Applications/shadps4/releases/ngs2-probe/run_probe.py "$@"
    else
        bash {backup} "$@"
    fi
}}
_ngs2_dispatch "$@"
'''


def selected_probe_wrapper(text, binary):
    home = binary.parents[4]
    # Match the complete known dispatcher, allowing only indentation differences.
    # Unknown shell logic must not be silently bypassed.
    normalize = lambda value: "\n".join(line.strip() for line in value.splitlines())
    expected = normalize(probe_wrapper(home, "BACKUP_PATH"))
    backup_pattern = re.escape(str(home / ".local/bin/shadps4-esde.before-ngs2-probe.")) + r"[0-9]{8}-[0-9]{6}"
    pattern = re.escape(expected).replace("BACKUP_PATH", backup_pattern)
    if not re.fullmatch(pattern, normalize(text)):
        raise RuntimeError("The probe dispatcher differs from the recognized template; nothing was switched.")
    command = f'python3 {home}/Applications/shadps4/releases/ngs2-probe/run_probe.py "$@"'
    replacement = MARKER + "\n        " + shlex.quote(str(binary)) + " --game CUSA36843 --fullscreen true"
    return text.replace(command, replacement, 1).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def say(message):
    print(message, flush=True)


def api(path):
    request = urllib.request.Request(
        BASE_URL + path,
        headers={"User-Agent": "ngs2-test-deployment", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def require_run(run, expected_id):
    if run.get("id") != expected_id or run.get("head_sha") != COMMIT:
        raise RuntimeError("GitHub returned a different build revision; nothing was switched.")


def gate_ready(jobs):
    by_name = {j["name"]: j for j in jobs}
    needed = ["Run C++ Tests on ubuntu-latest", "Run C++ Tests on windows-latest",
              "Run C++ Tests on macos-26", "clang-format", "reuse"]
    for name in needed:
        job = by_name.get(name)
        if job and job["status"] == "completed" and job["conclusion"] != "success":
            raise RuntimeError(f"Build check failed: {name}. Nothing was switched.")
        if not job or job["conclusion"] != "success":
            return False
    linux = by_name.get("linux-sdl")
    if not linux:
        return False
    # The executable is uploaded before AppImage packaging and cache cleanup.
    # Require the complete compile/link and raw-executable upload steps.
    build = next((s for s in linux["steps"] if s["name"] == "Build"), None)
    upload = next((s for s in linux["steps"]
                   if "actions/upload-artifact" in s["name"]), None)
    if linux["status"] == "completed" and linux["conclusion"] != "success":
        raise RuntimeError("Linux build failed; nothing was switched.")
    return bool(build and upload and build["conclusion"] == "success"
                and upload["conclusion"] == "success")


def artifact_metadata(wait_minutes):
    head = api("/git/ref/heads/feat/ngs2-audio-foundation")["object"]["sha"]
    if head != COMMIT:
        raise RuntimeError("The feature branch has moved. This installer is pinned to f00bef80; "
                           "get the current deployment before switching.")
    focused = api(f"/actions/runs/{FOCUSED}")
    require_run(focused, FOCUSED)
    if focused["conclusion"] != "success":
        raise RuntimeError("The exact-revision focused tests have not passed.")
    run = api(f"/actions/runs/{BUILD}")
    require_run(run, BUILD)
    deadline = time.monotonic() + wait_minutes * 60
    while not gate_ready(api(f"/actions/runs/{BUILD}/jobs?per_page=100")["jobs"]):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"The Linux build is not ready yet: https://github.com/{REPO}"
                               f"/actions/runs/{BUILD}. Nothing was switched; rerun this command later.")
        say("BUILD_WAIT: Linux compile/link or upload is still running; existing launcher is unchanged.")
        time.sleep(min(60, max(0, deadline - time.monotonic())))
    artifacts = api(f"/actions/runs/{BUILD}/artifacts?per_page=100")["artifacts"]
    candidates = [a for a in artifacts if a["name"].startswith("shadps4-ubuntu64-")
                  and re.fullmatch(r"[0-9a-f]{7,40}", a["name"].rsplit("-", 1)[-1])
                  and COMMIT.startswith(a["name"].rsplit("-", 1)[-1]) and not a["expired"]]
    if len(candidates) != 1:
        raise RuntimeError("Expected one non-expired Linux executable artifact; nothing was switched.")
    artifact = candidates[0]
    if artifact["workflow_run"]["head_sha"] != COMMIT:
        raise RuntimeError("Artifact revision mismatch.")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", "")):
        raise RuntimeError("GitHub did not provide a SHA-256 checksum; refusing an unverified download.")
    return artifact


def download(artifact, destination):
    # nightly.link provides anonymous access to the public GitHub artifact.
    # Its bytes must match the digest independently obtained from GitHub above.
    url = f"https://nightly.link/{REPO}/actions/artifacts/{artifact['id']}.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "ngs2-test-deployment"})
    checksum = hashlib.sha256()
    count = 0
    with urllib.request.urlopen(request, timeout=90) as response, destination.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > artifact["size_in_bytes"] or count > 1024 * 1024 * 1024:
                raise RuntimeError("Unexpected artifact download size.")
            checksum.update(chunk)
            out.write(chunk)
    if count != artifact["size_in_bytes"] or "sha256:" + checksum.hexdigest() != artifact["digest"]:
        raise RuntimeError("Downloaded artifact checksum does not match GitHub; nothing was switched.")


def executable_bytes(archive):
    with zipfile.ZipFile(archive) as zipped:
        entries = [e for e in zipped.infolist() if not e.is_dir()]
        if len(entries) != 1 or entries[0].filename != "shadps4":
            raise RuntimeError("Unexpected artifact contents.")
        if entries[0].file_size > 512 * 1024 * 1024:
            raise RuntimeError("Unexpected executable size.")
        payload = zipped.read(entries[0])
    if (len(payload) < 64 or payload[:6] != b"\x7fELF\x02\x01"
            or int.from_bytes(payload[18:20], "little") != 62):
        raise RuntimeError("Artifact is not a Linux x86-64 executable.")
    return payload


def selected_wrapper(original, binary):
    text = original.decode("utf-8")
    if "# NGS2 isolated core selection:" in text:
        raise RuntimeError("An NGS2 test selection is already installed. Restore it before switching again.")
    if not text.startswith("#!"):
        raise RuntimeError("The ES-DE launcher is not a shell script; nothing was changed.")
    if PROBE_MARKER in text:
        return selected_probe_wrapper(text, binary)
    found = list(LAUNCH.finditer(text))
    if len(found) != 1:
        raise RuntimeError("The ES-DE wrapper does not have one recognized CORE --game launch. "
                           "The launcher was not modified.")
    match = found[0]
    before = text[:match.start()]
    if before.rstrip("\n").endswith("\\"):
        raise RuntimeError("A continued shell command cannot be safely patched.")
    indent = match.group(1)
    insertion = indent + MARKER + "\n" + indent + "CORE=" + shlex.quote(str(binary)) + "\n"
    return (before + insertion + text[match.start():]).encode()


def atomic_write(path, content, mode):
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".ngs2-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
            os.fchmod(out.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def no_running_core():
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            target = (process / "exe").readlink()
        except OSError:
            continue
        if target.name.lower().removesuffix(" (deleted)") in {"shadps4", "shadps4.exe"}:
            raise RuntimeError(f"A shadPS4 game is running (PID {process.name}). "
                               "Close the game normally and rerun; no process was stopped.")


def smoke(binary, scratch):
    result = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, timeout=30)
    missing = [line.strip() for line in result.stdout.splitlines() if "not found" in line]
    if missing or result.returncode:
        raise RuntimeError("Executable dependency check failed: " + "; ".join(missing or [result.stderr.strip()]))
    (scratch / "user").mkdir()
    result = subprocess.run([str(binary), "--help"], cwd=scratch,
                            capture_output=True, text=True, timeout=30)
    if result.returncode or "shadPS4 Emulator CLI" not in result.stdout:
        raise RuntimeError("Executable startup check failed: " + (result.stderr or result.stdout)[-2000:])


def restore(state_file):
    state = json.loads(state_file.read_text())
    wrapper = Path(state["wrapper"])
    if wrapper.is_symlink() or digest(wrapper.read_bytes()) != state["installed_wrapper_sha256"]:
        raise RuntimeError("The wrapper changed after deployment. Automatic restore stopped to preserve those edits.")
    backup = state_file.parent / "shadps4-esde.before"
    data = backup.read_bytes()
    if digest(data) != state["original_wrapper_sha256"]:
        raise RuntimeError("The launcher backup checksum is invalid.")
    no_running_core()
    atomic_write(wrapper, data, state["original_mode"])
    say("NGS2_RESTORE_RESULT=PASS — previous ES-DE launcher restored.")


def install(home, wait_minutes):
    wrapper = home / ".local/bin/shadps4-esde"
    if wrapper.is_symlink() or not wrapper.is_file():
        raise RuntimeError(f"Expected the existing regular launcher at {wrapper}; nothing was changed.")
    original = wrapper.read_bytes()
    mode = stat.S_IMODE(wrapper.stat().st_mode)
    if not mode & 0o111:
        raise RuntimeError("The existing ES-DE wrapper is not executable.")
    releases = home / "Applications/shadps4/releases"
    release = releases / ("ngs2-" + COMMIT[:8])
    binary = release / "shadps4"
    replacement = selected_wrapper(original, binary)
    no_running_core()
    say("Checking exact-revision tests and Linux build: " + COMMIT)
    artifact = artifact_metadata(wait_minutes)
    releases.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ngs2-download-", dir=releases) as temporary:
        scratch = Path(temporary)
        archive = scratch / "artifact.zip"
        say("Downloading the verified Linux executable...")
        download(artifact, archive)
        payload = executable_bytes(archive)
        staged = scratch / "shadps4"
        staged.write_bytes(payload)
        staged.chmod(0o755)
        smoke(staged, scratch)
        candidate = scratch / "shadps4-esde.candidate"
        candidate.write_bytes(replacement)
        subprocess.run(["bash", "-n", str(candidate)], check=True)
        if release.is_symlink() or (release.exists() and not release.is_dir()):
            raise RuntimeError("The release path is occupied by an unexpected object.")
        if binary.is_symlink() or (binary.exists() and digest(binary.read_bytes()) != digest(payload)):
            raise RuntimeError("The pinned release path already contains another executable.")
        release.mkdir(exist_ok=True)
        if not binary.exists():
            atomic_write(binary, payload, 0o755)
        state_root = home / ".local/state/shadps4-ngs2"
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = state_root / (stamp + "-" + COMMIT[:8])
        backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(wrapper, backup / "shadps4-esde.before")
        shutil.copy2(Path(__file__).resolve(), backup / "deploy_test.py")
        state_file = backup / "deployment.json"
        state = {"commit": COMMIT, "build_run": BUILD, "artifact_id": artifact["id"],
                 "artifact_digest": artifact["digest"], "binary_sha256": digest(payload),
                 "binary": str(binary), "wrapper": str(wrapper), "original_mode": mode,
                 "original_wrapper_sha256": digest(original),
                 "installed_wrapper_sha256": digest(replacement)}
        state_file.write_text(json.dumps(state, indent=2) + "\n")
        restore_command = "python3 " + shlex.quote(str(backup / "deploy_test.py")) + " --restore " + shlex.quote(str(state_file))
        atomic_write(backup / "restore.sh", ("#!/bin/sh\n" + restore_command + "\n").encode(), 0o700)
        # Recheck just before the only change to the active installation.
        no_running_core()
        if wrapper.is_symlink() or wrapper.read_bytes() != original:
            raise RuntimeError("The wrapper changed during download; active launcher was left alone.")
        atomic_write(wrapper, replacement, mode)
        say("NGS2_DEPLOY_RESULT=PASS")
        say("CORE=" + str(binary))
        say("COMMIT=" + COMMIT)
        say("BINARY_SHA256=" + digest(payload))
        say("RESTORE=" + shlex.quote(str(backup / "restore.sh")))
        if PROBE_MARKER.encode() in original:
            say("Launch the existing NGS2 probe/test entry in ES-DE to test the new core. Ordinary entries retain their previous launcher.")
        else:
            say("Launch the game through ES-DE as usual.")
        say("Existing 7.1/audio, GPU and game settings were not edited. In-game audio remains experimental.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-minutes", type=int, default=45)
    parser.add_argument("--restore", type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("Run as your normal desktop user, without sudo.")
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("This deployment is for Linux x86-64.")
    if not 0 <= args.wait_minutes <= 45:
        raise RuntimeError("Wait must be between 0 and 45 minutes.")
    home = Path.home()
    lock_dir = home / ".local/state/shadps4-ngs2"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / "deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.restore:
            restore(args.restore)
        else:
            install(home, args.wait_minutes)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError,
            zipfile.BadZipFile) as error:
        print("NGS2_DEPLOY_RESULT=FAIL: " + str(error), file=sys.stderr)
        sys.exit(1)
