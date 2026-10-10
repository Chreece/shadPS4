#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Copy an observed game profile for isolated affinity testing."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct

ENV_KEYS = {"DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_DATA_HOME"}
ENV_PREFIXES = ()


def say(message):
    print(message, flush=True)



def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()



def absolute(value, cwd):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else cwd / path).resolve()



def process_info(pid):
    proc = Path("/proc") / str(pid)
    if proc.stat().st_uid != os.getuid():
        raise ProcessLookupError(pid)
    fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
    if fields[0] == "Z":
        raise ProcessLookupError(pid)
    return {"pid": int(pid), "start": fields[19], "group": int(fields[2]),
            "exe": (proc / "exe").resolve(strict=True),
            "cwd": (proc / "cwd").resolve(strict=True)}



def emulators():
    result = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            info = process_info(proc.name)
            translated_core = False
            if info["exe"].name == "libdynamorio.so":
                args = (proc / "cmdline").read_bytes().split(b"\0")
                translated_core = any(Path(arg.decode(errors="replace")).name.lower() == "shadps4" for arg in args if arg)
            if "shadps4" in info["exe"].name.lower() or translated_core:
                result.append(info)
        except OSError:
            pass
    return result



def launch_context(info):
    proc = Path("/proc") / str(info["pid"])
    args = [s.decode() for s in (proc / "cmdline").read_bytes().split(b"\0") if s]
    env = {}
    for entry in (proc / "environ").read_bytes().split(b"\0"):
        key, sep, value = entry.partition(b"=")
        key = key.decode(errors="replace")
        if sep and (key in ENV_KEYS or key.startswith(ENV_PREFIXES)):
            env[key] = value.decode(errors="replace")
    profile = info["cwd"] / "user"
    if not profile.is_dir():
        data_home = env.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
        profile = absolute(data_home, info["cwd"]) / "shadPS4"
    return {**info, "args": args, "env": env, "profile": profile.resolve()}



def read_sfo(path):
    data = path.read_bytes()
    magic, version, keys, values, count = struct.unpack_from("<5I", data)
    if magic != 0x46535000 or count > 4096:
        raise ValueError("Invalid SFO: " + str(path))
    result = {}
    for index in range(count):
        key, fmt, size, maximum, offset = struct.unpack_from("<HHIII", data, 20 + index * 16)
        end = data.index(0, keys + key)
        name = data[keys + key:end].decode()
        if fmt == 0x0204:
            result[name] = data[values + offset:values + offset + size].rstrip(b"\0").decode()
    return result



def resolve_game_id(context, game_id, inspected):
    config_path = context["profile"] / "config.json"
    config = json.loads(config_path.read_text())
    roots = []
    for entry in config.get("General", {}).get("install_dirs", []):
        if entry["enabled"]:
            root = Path(entry["path"])
            roots.append(root if root.is_absolute() else context["cwd"] / root)
    detail = {"game_id": game_id, "config": str(config_path),
              "enabled_install_dirs": [str(root) for root in roots]}
    if inspected is not None:
        inspected.append(detail)

    def search(root, depth):
        if depth < 0 or not root.is_dir():
            return None
        for folder in ([root] if root.name == game_id else []) + [root / game_id]:
            if (folder / "sce_sys/param.sfo").is_file() and (folder / "eboot.bin").is_file():
                return folder / "eboot.bin"
        archive = root / (game_id + ".zar")
        if archive.is_file():
            raise RuntimeError("The game uses a .zar archive; this runner requires an unpacked game: " + str(archive))
        try:
            for child in root.iterdir():
                if child.is_dir():
                    found = search(child, depth - 1)
                    if found is not None:
                        return found
        except PermissionError:
            return None
        return None

    for root in roots:
        found = search(root, 5)
        if found is not None:
            detail["resolved_path"] = str(found)
            return found
    raise RuntimeError("Could not resolve game ID " + game_id + " in the captured profile's enabled game folders")



def find_game(context, inspected=None):
    args = context["args"]
    for index, arg in enumerate(args[1:], 1):
        if arg == "--":
            break
        value = arg.split("=", 1)[1] if arg.startswith(("--game=", "--override-root=")) else arg
        if value.startswith("-"):
            continue
        path = absolute(value, context["cwd"])
        if not path.exists() and re.fullmatch(r"CUSA\d{5}", value):
            path = resolve_game_id(context, value, inspected)
        folder = path if path.is_dir() else path.parent
        for suffix in ["-UPDATE", "-patch", "-mods"]:
            if folder.name.endswith(suffix):
                base = folder.with_name(folder.name[:-len(suffix)])
                if base.is_dir():
                    folder = base
                break
        sfo = folder / "sce_sys/param.sfo"
        if not any(arg in {"-i", "--ignore-game-patch"} for arg in args):
            patch = Path(str(folder) + "-UPDATE")
            if not patch.is_dir():
                patch = Path(str(folder) + "-patch")
            if (patch / "sce_sys/param.sfo").is_file():
                sfo = patch / "sce_sys/param.sfo"
        mods_sfo = Path(str(folder) + "-mods") / "sce_sys/param.sfo"
        if mods_sfo.is_file():
            sfo = mods_sfo
        detail = {"argument": arg, "resolved_path": str(path), "path_exists": path.exists(),
                  "sfo": str(sfo), "sfo_exists": sfo.is_file()}
        if inspected is not None:
            inspected.append(detail)
        if not sfo.is_file():
            continue
        metadata = read_sfo(sfo)
        detail["metadata"] = metadata
        if re.search(r"\bPES\b|efootball|pro evolution soccer", metadata.get("TITLE", ""), re.I):
            serial = (metadata["CONTENT_ID"][7:16] if metadata.get("CONTENT_ID")
                      else metadata.get("TITLE_ID", ""))
            save_serial = metadata.get("INSTALL_DIR_SAVEDATA", serial)
            if not re.fullmatch(r"CUSA\d{5}", serial):
                raise ValueError("Unexpected game title ID: " + serial)
            if not re.fullmatch(r"[A-Za-z0-9_-]+", save_serial):
                raise ValueError("Unexpected game save directory")
            return {"serial": serial, "save_serial": save_serial,
                    "title": metadata["TITLE"], "folder": folder, "sfo": sfo,
                    "boot_path": path, "argument_index": index}
    return None



def copy_path(source, target):
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=False, dirs_exist_ok=True)
    elif source.is_file():
        shutil.copy2(source, target)
    else:
        raise RuntimeError("Unexpected profile entry: " + str(source))



def snapshot(paths):
    records = {}
    for root in paths:
        files = root.rglob("*") if root.is_dir() else [root]
        for path in files:
            if path.is_file():
                info = path.stat()
                records[str(path)] = [info.st_size, info.st_mtime_ns]
    return records




GUI_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS", "PULSE_SERVER", "PIPEWIRE_REMOTE",
            "SDL_AUDIODRIVER", "SDL_VIDEODRIVER", "XDG_DATA_HOME")

def choose_desktop(candidates, inherited):
    """Use a unique GUI session; never silently choose an SSH-forwarded display."""
    if candidates:
        priority = min(item[0] for item in candidates)
        selected = [item for item in candidates if item[0] == priority]
        sessions = {(item[2].get("DISPLAY"), item[2].get("WAYLAND_DISPLAY"),
                     item[2].get("XAUTHORITY"), item[2].get("XDG_RUNTIME_DIR"))
                    for item in selected}
        if len(sessions) != 1:
            raise RuntimeError("Multiple desktop sessions found; leave only the intended ES-DE session open")
        _, source, values = selected[0]
    else:
        source, values = "current shell", {k: inherited[k] for k in GUI_KEYS if k in inherited}
    display = values.get("DISPLAY", "")
    if display and not re.fullmatch(r"(?:unix)?:\d+(?:\.\d+)?", display):
        raise RuntimeError("Only a local desktop is supported; connect Moonlight and leave ES-DE open")
    if not display and not values.get("WAYLAND_DISPLAY"):
        raise RuntimeError("No desktop session found; connect Moonlight and leave ES-DE open")
    env = dict(inherited)
    for key in GUI_KEYS:
        env.pop(key, None)
    env.update(values)
    return env, source


def desktop_environment():
    candidates = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            name = (proc / "exe").readlink().name.lower()
            if name not in ("es-de", "emulationstation", "sunshine"):
                continue
            raw = (proc / "environ").read_bytes().split(b"\0")
            values = dict(item.decode(errors="replace").split("=", 1) for item in raw if b"=" in item)
            values = {key: values[key] for key in GUI_KEYS if values.get(key)}
            if values.get("DISPLAY") or values.get("WAYLAND_DISPLAY"):
                candidates.append((1 if name == "sunshine" else 0, name + ":" + proc.name, values))
        except OSError:
            pass
    env, source = choose_desktop(candidates, os.environ)
    if env.get("DISPLAY"):
        number = re.search(r":(\d+)", env["DISPLAY"])[1]
        if not Path("/tmp/.X11-unix/X" + number).exists():
            raise RuntimeError("The selected X display is unavailable; connect Moonlight and leave ES-DE open")
    elif not (Path(env.get("XDG_RUNTIME_DIR", "/nonexistent")) / env["WAYLAND_DISPLAY"]).exists():
        raise RuntimeError("The selected Wayland socket is unavailable")
    return env, source
