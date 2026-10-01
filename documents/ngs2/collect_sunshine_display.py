#!/usr/bin/env python3
"""Read-only capture of a live Sunshine/ES-DE black screen; no sudo required."""

import datetime
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


ENV_KEYS = {"DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE",
            "XDG_RUNTIME_DIR", "SDL_VIDEODRIVER", "QT_QPA_PLATFORM"}


def collect(home, output):
    displays = {(':0', str(home / '.Xauthority')), (':1', str(home / '.Xauthority'))}

    def section(title, text):
        output.write("\n=== " + title + " ===\n" + str(text) + "\n")
        output.flush()

    def run(args, env=None, timeout=6):
        try:
            completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, errors="replace", timeout=timeout, env=env)
            section(shlex.join(args), completed.stdout[-160000:] + f"\nrc={completed.returncode}")
            return completed.stdout
        except (OSError, subprocess.TimeoutExpired) as error:
            section(shlex.join(args), error)
            return ""

    def tail(path, size=100000):
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - size))
                data = stream.read(size).decode(errors="replace")
            section(str(path), data)
        except OSError as error:
            section(str(path), error)

    section("CAPTURE", datetime.datetime.now().astimezone().isoformat())
    for unit in ("sunshine.service", "sunshine-display-watchdog.service",
                 "sunshine-esde-idle-guard.service"):
        run(["systemctl", "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID"])
        environment = subprocess.run(["systemctl", "show", unit, "-p", "Environment", "--value"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, timeout=5).stdout
        values = {k: v for item in shlex.split(environment) if "=" in item
                  for k, v in [item.split("=", 1)] if k in ENV_KEYS}
        section(unit + " display environment", json.dumps(values))
        if values.get("DISPLAY"):
            displays.add((values["DISPLAY"], values.get("XAUTHORITY", str(home / ".Xauthority"))))

    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            comm = (path / "comm").read_text().strip()
            if comm.lower() not in {"sunshine", "es-de", "xorg", "xwayland", "shadps4",
                                    "openbox", "kwin_x11", "xfwm4", "mutter"}:
                continue
            record = {"pid": int(path.name), "name": comm, "uid": path.stat().st_uid}
            for name in ("cmdline", "cgroup"):
                try:
                    record[name] = (path / name).read_bytes().decode(errors="replace").replace("\0", " ")
                except OSError as error:
                    record[name] = str(error)
            try:
                environ = (path / "environ").read_bytes().decode(errors="replace").split("\0")
                values = {k: v for item in environ if "=" in item
                          for k, v in [item.split("=", 1)] if k in ENV_KEYS}
                record["display_environment"] = values
                if values.get("DISPLAY"):
                    displays.add((values["DISPLAY"], values.get("XAUTHORITY", str(home / ".Xauthority"))))
            except OSError as error:
                record["display_environment"] = str(error)
            section("PROCESS", json.dumps(record, indent=2))
        except OSError:
            continue

    for display, authority in sorted(displays):
        if not re.fullmatch(r":\d+(?:\.\d+)?", display):
            continue
        env = dict(os.environ, DISPLAY=display, XAUTHORITY=authority)
        section("X DISPLAY", f"DISPLAY={display} XAUTHORITY={authority}")
        run(["xrandr", "--query"], env)
        run(["wmctrl", "-lpG"], env)
        run(["xprop", "-root", "_NET_ACTIVE_WINDOW", "_NET_CURRENT_DESKTOP"], env)
        clients = run(["xprop", "-root", "_NET_CLIENT_LIST"], env)
        for window in re.findall(r"0x[0-9a-fA-F]+", clients)[:20]:
            run(["xprop", "-id", window, "WM_CLASS", "_NET_WM_NAME", "_NET_WM_PID",
                 "_NET_WM_STATE", "_NET_WM_DESKTOP"], env)
            run(["xwininfo", "-id", window], env)

    config = home / ".config/sunshine/sunshine.conf"
    try:
        selected = []
        for line in config.read_text().splitlines():
            if line.split("=", 1)[0].strip() in {"capture", "encoder", "adapter_name",
                                                 "output_name", "min_log_level"}:
                selected.append(line)
        section("SUNSHINE VIDEO CONFIG", "\n".join(selected))
    except OSError as error:
        section(str(config), error)
    try:
        data = json.loads((home / ".config/sunshine/apps.json").read_text())
        apps = [app for app in data.get("apps", []) if any(word in json.dumps(app).lower()
                for word in ("es-de", "esde", "sunshine-es-min"))]
        section("SUNSHINE ES-DE APP ENTRIES", json.dumps(apps, indent=2))
        section("SUNSHINE APP DISPLAY ENV", json.dumps({k: v for k, v in data.get("env", {}).items()
                                                        if k in ENV_KEYS}))
    except (OSError, ValueError) as error:
        section("SUNSHINE APPS", error)
    tail(home / ".local/bin/sunshine-kms-guard.sh", 64000)
    tail(home / ".local/state/sunshine-display-watchdog.log", 30000)
    tail(home / ".config/sunshine/sunshine.log", 160000)
    tail(home / "ES-DE/logs/es_log.txt", 40000)
    for card in sorted(Path("/sys/class/drm").glob("card*-*")):
        values = {}
        for name in ("status", "enabled", "modes"):
            try:
                values[name] = (card / name).read_text().strip()
            except OSError:
                pass
        if values:
            section(str(card), json.dumps(values))


if __name__ == "__main__":
    if os.geteuid() == 0:
        sys.exit("Run as your normal desktop user, without sudo.")
    home = Path.home()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    report = home / f"sunshine-black-screen-{stamp}-{os.getpid()}.txt"
    with report.open("x") as output:
        os.fchmod(output.fileno(), 0o600)
        try:
            collect(home, output)
        except Exception as error:
            output.write(f"\nCOLLECTOR_ERROR={error}\n")
    print("DISPLAY_REPORT=" + str(report))
