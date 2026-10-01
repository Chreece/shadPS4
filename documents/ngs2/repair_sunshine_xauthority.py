#!/usr/bin/env python3
"""Restore the desktop user's X11 authority from their running headless X server.

AI-assisted host repair. Credentials stay in memory or private local files and are never printed.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def server_record(pid, argv, cgroup, uid):
    if not argv or Path(argv[0]).name != "Xorg" or ":0" not in argv:
        return None
    if f"/user-{uid}.slice/" not in cgroup or "/headless-x.service" not in cgroup:
        return None
    try:
        authority = argv[argv.index("-auth") + 1]
    except (ValueError, IndexError):
        return None
    if not re.fullmatch(r"/tmp/serverauth\.[A-Za-z0-9]+", authority):
        return None
    return {"pid": pid, "argv": argv, "authority": authority}


def find_server():
    candidates = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            argv = (path / "cmdline").read_bytes().decode().rstrip("\0").split("\0")
            record = server_record(int(path.name), argv, (path / "cgroup").read_text(), os.getuid())
            if record:
                candidates.append(record)
        except (OSError, UnicodeError):
            pass
    if len(candidates) != 1:
        raise RuntimeError("Expected exactly one :0 Xorg in your headless-x.service; no changes made")
    return candidates[0]


def read_authority(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Expected a regular authority file: " + str(path))
    try:
        data = path.read_bytes()
    except PermissionError:
        print("sudo is needed to read the running X server's authorization file.", flush=True)
        data = subprocess.run(["sudo", "cat", "--", str(path)], check=True,
                              stdout=subprocess.PIPE).stdout
    if not data or len(data) > 1024 * 1024:
        raise RuntimeError("Unexpected authority file size; no changes made")
    return data


def query(authority):
    return subprocess.run(["xrandr", "--query"],
                          env=dict(os.environ, DISPLAY=":0", XAUTHORITY=str(authority)),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)


def atomic_write(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(prefix=".xauth-repair-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def repair(home):
    for program in ("xauth", "xrandr"):
        if not shutil.which(program):
            raise RuntimeError("Missing required program: " + program)
    target = home / ".Xauthority"
    if target.is_symlink():
        raise RuntimeError("Refusing a symlinked client authority file")
    current = query(target)
    if current.returncode == 0:
        print("X11_AUTH_RESULT=ALREADY_WORKING")
        print(current.stdout.decode(errors="replace"))
        print("Exit ES-DE, then launch it again from Moonlight to leave any offscreen session.")
        return
    original = target.read_bytes() if target.exists() else None
    old_mode = target.stat().st_mode & 0o777 if original is not None else 0o600
    server = find_server()
    source = read_authority(Path(server["authority"]))
    state = home / ".local/state/sunshine-x11-authority"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="candidate-", dir=state) as temporary:
        directory = Path(temporary)
        source_file = directory / "server.auth"
        candidate = directory / "client.auth"
        atomic_write(source_file, source)
        # Extract only :0; preserve other display authorizations in the client database.
        extracted = subprocess.run(["xauth", "-f", str(source_file), "extract", "-", ":0"],
                                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=8).stdout
        if not extracted:
            raise RuntimeError("The server file has no :0 authorization record; no changes made")
        atomic_write(candidate, original or b"")
        subprocess.run(["xauth", "-f", str(candidate), "merge", "-"], input=extracted,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=8)
        verified = query(candidate)
        if verified.returncode:
            raise RuntimeError("The candidate authorization could not open :0; original preserved")
        if find_server() != server:
            raise RuntimeError("The X server changed during repair; original preserved")
        if target.is_symlink() or (target.read_bytes() if target.exists() else None) != original:
            raise RuntimeError("Client authorization changed during repair; preserving newer changes")
        candidate_data = candidate.read_bytes()
        backup = Path(tempfile.mkdtemp(prefix="backup-", dir=state))
        if original is not None:
            atomic_write(backup / "Xauthority.before", original)
        atomic_write(backup / "record.json", json.dumps({
            "existed": original is not None, "mode": old_mode,
            "installed_sha256": hashlib.sha256(candidate_data).hexdigest(),
            "server_pid": server["pid"], "display": ":0",
        }).encode())
        atomic_write(target, candidate_data)
        try:
            result = query(target)
            if result.returncode:
                raise RuntimeError("Final X11 access check failed")
        except (RuntimeError, OSError, subprocess.SubprocessError):
            if target.read_bytes() == candidate_data:
                if original is None:
                    target.unlink()
                else:
                    atomic_write(target, original, old_mode)
            raise
        print("X11_AUTH_RESULT=PASS")
        print("BACKUP=" + str(backup))
        print(result.stdout.decode(errors="replace"))
        print("Now close Moonlight, wait 30 seconds, and launch ES-DE again.")


if __name__ == "__main__":
    try:
        if os.geteuid() == 0:
            raise RuntimeError("Run as your desktop user, without sudo on the whole command")
        repair(Path.home())
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        # Never include subprocess stdout: an xauth extraction can contain credentials.
        print("X11_AUTH_RESULT=FAIL: " + str(error), file=sys.stderr)
        sys.exit(1)
