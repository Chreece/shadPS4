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
        if re.search(r"\bPES\b|efootball|pro evolution soccer|persona\s*5\s*strikers", metadata.get("TITLE", ""), re.I):
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



def prepare_profile(context, game, runtime):
    source = context["profile"]
    config_path = source / "config.json"
    if not config_path.is_file() or not (source / "users.json").is_file():
        raise RuntimeError("Captured profile has no current config.json/users.json; preserved")
    config = json.loads(config_path.read_text())
    general = config.setdefault("General", {})
    source_home = absolute(general["home_dir"], context["cwd"]) if general.get("home_dir") else source / "home"
    user = runtime / "user"
    user.mkdir(parents=True)
    protected = [config_path, source / "users.json"]
    for path in source.iterdir():
        if path.is_file() and path.suffix.lower() in {".json", ".toml", ".ini", ".txt"}:
            copy_path(path, user / path.name)
    for name in ["custom_configs", "custom_modules", "licenses",
                 "patches", "cheats", "custom_trophy", "trophy", "data"]:
        copy_path(source / name, user / name)
    copy_path(source / "game_data" / game["serial"], user / "game_data" / game["serial"])
    if not source_home.is_dir():
        raise RuntimeError("Captured save home does not exist; refusing an empty-save test")
    for home in source_home.iterdir():
        if not home.is_dir() or not home.name.isdigit():
            continue
        for serial in {game["serial"], game["save_serial"]}:
            save = home / "savedata" / serial
            copy_path(save, user / "home" / home.name / "savedata" / serial)
            protected.append(save)
        for name in ["inputs", "trophy"]:
            copy_path(home / name, user / "home" / home.name / name)
    users = json.loads((user / "users.json").read_text())["Users"]["user"]
    user_ids = {str(u["user_id"]) for u in users} | {str(n) for n in range(1000, 1004)}
    for user_id in user_ids:
        if not user_id.isdigit():
            raise RuntimeError("Unexpected user ID in captured profile")
        for name in ["savedata", "trophy", "inputs"]:
            (user / "home" / user_id / name).mkdir(parents=True, exist_ok=True)
    general["home_dir"] = str(user / "home")
    for key, default in [("sys_modules_dir", "sys_modules"), ("font_dir", "fonts"),
                         ("addon_install_dir", "addcont")]:
        original = absolute(general[key], context["cwd"]) if general.get(key) else source / default
        if key == "addon_install_dir":
            general[key] = str(original)
        else:
            copy_path(original, user / default)
            general[key] = str(user / default)
    config.setdefault("Log", {}).update(filter="*:Info", flush_level="info", skip_duplicate=False)
    (user / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    custom = user / "custom_configs" / (game["serial"] + ".json")
    if custom.is_file():
        overrides = json.loads(custom.read_text())
        for key in ["home_dir", "sys_modules_dir", "font_dir", "addon_install_dir"]:
            overrides.setdefault("General", {})[key] = general[key]
        overrides.setdefault("Log", {}).update(filter="*:Info", flush_level="info", skip_duplicate=False)
        custom.write_text(json.dumps(overrides, indent=2) + "\n")
    if any(path.is_symlink() for path in user.rglob("*")):
        raise RuntimeError("Profile copy unexpectedly contains a symlink")
    return protected, snapshot(protected), digest(config_path), digest(source / "users.json")



def candidate_arguments(context):
    args = context["args"][1:]
    result = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            result.extend(args[index:])
            break
        if arg == "--mount" or arg.startswith("--mount="):
            raise RuntimeError("Custom guest mounts need explicit isolation; original launch preserved")
        if arg == "--wait-for-pid":
            index += 2
            continue
        if arg.startswith("--wait-for-pid=") or arg in {"--wait-for-debugger", "--log-append"}:
            index += 1
            continue
        game = context.get("game")
        if game and index + 1 == game["argument_index"]:
            prefix = "--game=" if arg.startswith("--game=") else ""
            result.append(prefix + str(game["boot_path"]))
            index += 1
            continue
        prefix, sep, value = arg.partition("=")
        if sep and prefix in {"--game", "--override-root", "--patch"}:
            path = absolute(value, context["cwd"])
            result.append(prefix + "=" + str(path) if path.exists() else arg)
        elif not arg.startswith("-") and absolute(arg, context["cwd"]).exists():
            result.append(str(absolute(arg, context["cwd"])))
        else:
            result.append(arg)
        index += 1
    return result

