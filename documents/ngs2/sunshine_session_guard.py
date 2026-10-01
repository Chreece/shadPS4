#!/usr/bin/env python3
"""Repair the existing Sunshine display watchdog without changing its X11 recovery.

Host deployment helper, independent of the emulator. AI-assisted implementation.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time


ORIGINAL_SHA256 = "07fe31a4d94815c23bbdd0ac0c2a7e25ba09257cab15ee1b7797093a5667876a"
UNIT = "sunshine-display-watchdog.service"
EMULATORS = {
    "es-de", "emulationstation", "shadps4", "rpcs3", "dolphin-emu", "dolphin-emu-nogui",
    "retroarch", "pcsx2", "pcsx2-qt", "cemu", "xemu", "duckstation", "duckstation-qt",
    "ppsspp", "ppssppsdl", "ppssppqt", "mame", "ryujinx", "yuzu", "suyu", "sudachi",
    "eden", "citra", "citra-qt", "azahar", "azahar-qt", "melonds", "desmume", "mgba",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "sunshine-session-guard:", message, flush=True)


def clients_from_log(lines):
    count = None
    awaiting_connect = 0
    for line in lines:
        absolute = re.search(r"New streaming session started \[active sessions: (\d+)\]", line)
        if "Sunshine version:" in line or ": Info: Process terminated" in line:
            count, awaiting_connect = 0, 0
        elif absolute:
            count = int(absolute[1])
            awaiting_connect = min(awaiting_connect + 1, count)
        elif "New streaming session started" in line:
            count = None  # Unrecognized format must not authorize termination.
            awaiting_connect = 0
        elif "CLIENT CONNECTED" in line:
            if awaiting_connect:
                awaiting_connect -= 1  # Already included in the absolute session count.
            elif count is not None:
                count += 1
        elif "CLIENT DISCONNECTED" in line and count is not None:
            count = max(0, count - 1)
            awaiting_connect = min(awaiting_connect, count)
    return count


def client_count(home):
    try:
        with (home / ".config/sunshine/sunshine.log").open(errors="replace") as stream:
            return clients_from_log(stream)
    except OSError:
        return None


def read_process(pid):
    try:
        path = Path("/proc") / str(pid)
        if path.stat().st_uid != os.getuid():
            return None
        stat = (path / "stat").read_text()
        end = stat.rindex(")")
        fields = stat[end + 2:].split()
        if fields[0] in {"Z", "X"}:
            return None
        args = (path / "cmdline").read_bytes().decode(errors="replace").split("\0")
        try:
            exe = os.readlink(path / "exe").removesuffix(" (deleted)")
        except OSError:
            exe = ""
        return {"pid": pid, "ppid": int(fields[1]), "start": int(fields[19]),
                "comm": stat[stat.index("(") + 1:end], "exe": exe, "args": args}
    except (OSError, ValueError, IndexError):
        return None


def snapshot():
    return {int(p.name): item for p in Path("/proc").iterdir() if p.name.isdigit()
            if (item := read_process(int(p.name))) is not None}


def is_root(proc, home):
    # Exact executable names, not substring matching of arbitrary shell commands.
    names = {proc["comm"].lower(), Path(proc["exe"]).name.lower()}
    if names & EMULATORS:
        return True
    if names & {"squashfuse", "squashfuse_ll", "dwarfs", "fusermount", "fusermount3"}:
        for arg in proc["args"][1:]:
            if arg.startswith(str(home / "apps/es/") + "/") or "/rpcs3/rpcs3.AppImage" in arg:
                return True
            if arg.startswith("/tmp/.mount_es-"):
                return True
    return False


def select_processes(processes, previous, home):
    chosen = {pid for pid, proc in processes.items() if is_root(proc, home)
              or previous.get(str(pid)) == proc["start"]}
    while True:
        children = {pid for pid, proc in processes.items() if proc["ppid"] in chosen}
        expanded = chosen | children
        if expanded == chosen:
            break
        chosen = expanded
    # Never include this helper or any of its ancestors, even if launched manually from ES-DE.
    ancestor = os.getpid()
    while ancestor in processes:
        chosen.discard(ancestor)
        ancestor = processes[ancestor]["ppid"]
    return {pid: processes[pid] for pid in chosen}


def atomic_write(path, data, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def tracked_processes(home):
    directory = home / ".local/state/sunshine-session-guard"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = directory / "processes.json"
    with (directory / "processes.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(state.read_text())
            previous = data.get("processes", {}) if data.get("boot") == boot_id() else {}
        except (OSError, ValueError):
            previous = {}
        chosen = select_processes(snapshot(), previous, home)
        atomic_write(state, json.dumps({"boot": boot_id(), "processes": {
            str(pid): proc["start"] for pid, proc in chosen.items()}}).encode())
    return chosen


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def signal_process(proc, sig):
    # Hold a pidfd, then verify ownership and start time; never signal a recycled PID.
    try:
        fd = os.pidfd_open(proc["pid"])
    except ProcessLookupError:
        return False
    try:
        current = read_process(proc["pid"])
        if current is None or current["start"] != proc["start"]:
            return False
        signal.pidfd_send_signal(fd, sig)
        log(f"{sig.name} pid={proc['pid']} name={proc['comm']}")
        return True
    except ProcessLookupError:
        return False
    finally:
        os.close(fd)


def cleanup(home):
    if client_count(home) != 0:
        return
    targets = tracked_processes(home)  # Snapshot descendants before their parent exits.
    if not targets:
        return
    log("No Moonlight clients after grace; closing ES-DE/emulator processes")
    for proc in targets.values():
        if client_count(home) != 0:
            log("Cleanup cancelled: client reconnected or state became unknown")
            return
        signal_process(proc, signal.SIGTERM)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if client_count(home) != 0:
            log("Forced cleanup cancelled: client reconnected or state became unknown")
            return
        if not any(read_process(pid) for pid in targets):
            return
        time.sleep(0.25)
    for proc in targets.values():
        if client_count(home) != 0:
            return
        signal_process(proc, signal.SIGKILL)


def patched_watchdog(original, helper):
    command = "python3 " + shlex.quote(str(helper))
    text = original.decode()
    names = ["moonlight_client_count", "live_es_pids", "cleanup_orphan_es_helpers", "cleanup_es"]
    successors = names[1:] + ["repair_x11"]
    actions = ["clients", "list", "cleanup", "cleanup"]
    for name, successor, action in zip(names, successors, actions):
        start = text.index(name + "()\n{\n")
        end = text.index(successor + "()\n{\n", start)
        redirect = ' >> "$WATCHDOG_LOG" 2>&1' if action == "cleanup" else ' 2>> "$WATCHDOG_LOG"'
        text = text[:start] + f"{name}()\n{{\n    {command} {action}{redirect}\n}}\n\n" + text[end:]
    text = text.replace('            no_client_count=0\n            last_es="$es"',
                        '            last_es="$es"')
    text = text.replace("Live ES-DE renderer:", "Live ES-DE/emulator processes:")
    text = text.replace("No live ES-DE renderer", "No live ES-DE/emulator processes")
    # Explicit UNKNOWN on helper failure so a fault cannot authorize cleanup.
    text = text.replace('clients="$(moonlight_client_count)"',
                        'clients="$(moonlight_client_count)" || clients=UNKNOWN')
    return text.encode()


def restart():
    subprocess.run(["sudo", "systemctl", "restart", UNIT], check=True)
    subprocess.run(["systemctl", "is-active", "--quiet", UNIT], check=True)


def install(home):
    watchdog = home / ".local/bin/sunshine-display-watchdog"
    if watchdog.is_symlink():
        raise RuntimeError("Refusing a symlinked watchdog")
    original = watchdog.read_bytes()
    if digest(original) != ORIGINAL_SHA256:
        raise RuntimeError("Watchdog differs from the reviewed diagnostic; no files changed")
    user = subprocess.check_output(["systemctl", "show", UNIT, "-p", "User", "--value"], text=True).strip()
    import pwd
    if user != pwd.getpwuid(os.getuid()).pw_name:
        raise RuntimeError("Watchdog service is not assigned to the current user")
    exec_start = subprocess.check_output(["systemctl", "show", UNIT, "-p", "ExecStart", "--value"], text=True)
    if not re.search(r"\bpath\s*=\s*" + re.escape(str(watchdog)) + r"\s*;", exec_start):
        raise RuntimeError("Unexpected watchdog ExecStart; no files changed")
    old_active = subprocess.run(["systemctl", "is-active", "--quiet",
                                 "sunshine-esde-idle-guard.service"]).returncode == 0
    if old_active:
        raise RuntimeError("The separate old idle guard is active; refusing competing cleanup")
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("Python with Linux pidfd support is required")
    source = Path(__file__).read_bytes()
    directory = home / ".local/lib/sunshine-session-guard"
    helper = directory / ("guard-" + digest(source)[:12] + ".py")
    patched = patched_watchdog(original, helper)
    subprocess.run(["bash", "-n"], input=patched, check=True)
    subprocess.run(["sudo", "-v"], check=True)
    backup_root = home / ".local/state/sunshine-session-guard"
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix="backup-", dir=backup_root))
    shutil.copy2(watchdog, backup / "watchdog.original")
    mode = watchdog.stat().st_mode & 0o777
    atomic_write(backup / "record.json", json.dumps({"original": digest(original),
                 "installed": digest(patched), "mode": mode}).encode())
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(helper, source, 0o700)
    atomic_write(watchdog, patched, mode)
    try:
        restart()
    except (subprocess.CalledProcessError, OSError):
        atomic_write(watchdog, original, mode)
        restart()
        raise RuntimeError("Restart failed; original watchdog restored")
    print("SUNSHINE_GUARD_RESULT=PASS")
    print("CLIENTS=" + str(client_count(home)))
    print("BACKUP=" + str(backup))
    print("RESTORE=python3 " + shlex.quote(str(helper)) + " --restore " + shlex.quote(str(backup)))


def restore(home, backup):
    watchdog = home / ".local/bin/sunshine-display-watchdog"
    record = json.loads((backup / "record.json").read_text())
    original = (backup / "watchdog.original").read_bytes()
    if digest(original) != ORIGINAL_SHA256 or record["original"] != ORIGINAL_SHA256:
        raise RuntimeError("Backup checksum mismatch")
    if watchdog.is_symlink() or digest(watchdog.read_bytes()) != record["installed"]:
        raise RuntimeError("Watchdog changed after installation; refusing to overwrite")
    subprocess.run(["sudo", "-v"], check=True)
    atomic_write(watchdog, original, record["mode"])
    restart()
    print("SUNSHINE_GUARD_RESTORE=PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=["clients", "list", "cleanup"])
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--restore", type=Path)
    args = parser.parse_args()
    home = Path.home()
    if args.install:
        install(home)
    elif args.restore:
        restore(home, args.restore)
    elif args.action == "clients":
        count = client_count(home)
        print("UNKNOWN" if count is None else count)
    elif args.action == "list":
        for pid in sorted(tracked_processes(home)):
            print(pid)
    elif args.action == "cleanup":
        cleanup(home)
    else:
        parser.error("Choose --install, --restore, clients, list, or cleanup")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"SUNSHINE_GUARD_RESULT=FAIL: {error}", file=sys.stderr)
        sys.exit(1)
