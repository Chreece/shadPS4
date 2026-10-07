#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Play-test the verified affinity candidate using an observed PES launch."""

import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import tempfile
import time


SOURCE = "82d07380b6090149e4df5d8092fd2e7925ad5007"
BINARY_SHA256 = "f26c7cbed2369fa1db0c4bf76c6364629601461fa4d616dadadebb711217eb17"
ENV_KEYS = {"DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_DATA_HOME",
            "DBUS_SESSION_BUS_ADDRESS", "LD_LIBRARY_PATH", "LD_PRELOAD", "LANG", "PATH"}
ENV_PREFIXES = ("PULSE_", "PIPEWIRE_", "ALSOFT_", "SDL_", "VK_", "MESA_", "RADV_",
                "DRI_", "LIBGL_", "SHADPS4_", "SUNSHINE_", "LC_")


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
            if "shadps4" in info["exe"].name.lower():
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
            raise RuntimeError("PES uses a .zar archive; this runner requires an unpacked game: " + str(archive))
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
                raise ValueError("Unexpected PES title ID: " + serial)
            if not re.fullmatch(r"[A-Za-z0-9_-]+", save_serial):
                raise ValueError("Unexpected PES save directory")
            return {"serial": serial, "save_serial": save_serial,
                    "title": metadata["TITLE"], "folder": folder, "sfo": sfo,
                    "boot_path": path, "argument_index": index}
    return None


def capture_launch(evidence):
    preexisting = emulators()
    initial_ids = {(info["pid"], info["start"]) for info in preexisting}
    (evidence / "preexisting-processes.json").write_text(json.dumps(
        preexisting, indent=2, default=str) + "\n")
    if preexisting:
        say("EXISTING_PROCESSES_IGNORED=" + ",".join(str(info["pid"]) for info in preexisting))
    say("OPEN_PES_NOW=Launch PES normally through Moonlight / ES-DE.")
    deadline = time.monotonic() + 300
    next_update = time.monotonic() + 30
    observed = {}
    try:
        while time.monotonic() < deadline:
            for info in emulators():
                if (info["pid"], info["start"]) in initial_ids:
                    continue
                detail = {"exe": str(info["exe"]), "cwd": str(info["cwd"]),
                          "start": info["start"], "inspected": []}
                observed[str(info["pid"])] = detail
                try:
                    context = launch_context(info)
                    detail.update(args=context["args"], profile=str(context["profile"]))
                    game = find_game(context, detail["inspected"])
                    detail["game_recognized"] = game is not None
                    if game:
                        context["preexisting_processes"] = preexisting
                        return context, game
                except (OSError, ValueError, struct.error) as error:
                    detail["error"] = str(error)
                except RuntimeError as error:
                    detail["error"] = str(error)
                    raise
            if time.monotonic() >= next_update:
                (evidence / "observed-launches.json").write_text(json.dumps(observed, indent=2))
                say("Waiting for an unpacked PES game launched by shadPS4...")
                next_update += 30
            time.sleep(0.2)
    finally:
        (evidence / "observed-launches.json").write_text(json.dumps(observed, indent=2))
    raise RuntimeError("No identifiable PES launch captured within five minutes; evidence collected")


def diagnose_launch():
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-pes-detection-", dir=Path.home()))
    report = {"collector_uid": os.getuid(), "processes": [], "errors": []}
    say("CAPTURING=Reading running PES launch details; the game stays open")
    self_proc = Path("/proc/self").resolve()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or proc == self_proc:
            continue
        detail = {"pid": int(proc.name), "errors": []}
        for name in ("comm", "cmdline", "exe", "cwd"):
            try:
                if name in {"exe", "cwd"}:
                    detail[name] = os.readlink(proc / name)
                elif name == "cmdline":
                    detail["args"] = [value.decode(errors="replace") for value in
                                      (proc / name).read_bytes().split(b"\0") if value]
                else:
                    detail[name] = (proc / name).read_text().strip()
            except OSError as error:
                detail["errors"].append(f"{name}: {error}")
        args = detail.get("args", [])
        identity = " ".join([detail.get("comm", ""), detail.get("exe", ""), *args[:1]])
        if "shadps4" not in identity.lower() and not any(
                re.search(r"CUSA\d{5}|(?:^|/)eboot\.bin$|\.zar$", arg, re.I) for arg in args[1:]):
            continue
        report["processes"].append(detail)
        try:
            detail["uid"] = proc.stat().st_uid
            detail["runner_executable_name_match"] = "shadps4" in Path(detail.get("exe", "")).name.lower()
            context = launch_context(process_info(proc.name))
            detail["profile"] = str(context["profile"])
            detail["profile_exists"] = context["profile"].is_dir()
            detail["inspected"] = []
            game = find_game(context, detail["inspected"])
            detail["game_recognized"] = game is not None
            if game:
                detail["game"] = {key: str(value) for key, value in game.items()}
            log_path = context["profile"] / "log/shad_log.txt"
            if log_path.is_file():
                with log_path.open("rb") as stream:
                    stream.seek(max(0, log_path.stat().st_size - 256 * 1024))
                    lines = stream.read(256 * 1024).decode(errors="replace").splitlines()
                detail["game_log_lines"] = [line for line in lines if re.search(
                    r"CUSA\d{5}|eboot\.bin|param\.sfo|\bPES|efootball|pro evolution", line, re.I)][-100:]
        except (OSError, ValueError, RuntimeError, struct.error) as error:
            detail["errors"].append(str(error))
    try:
        previous = sorted(Path.home().glob("shadps4-affinity-pes-*"),
                          key=lambda path: path.stat().st_mtime, reverse=True)
        for folder in [path for path in previous if path.is_dir()][:3]:
            for name in ("observed-launches.json", "summary.json"):
                path = folder / name
                if path.is_file():
                    copy_path(path, evidence / "previous" / folder.name / name)
    except OSError as error:
        report["errors"].append(str(error))
    (evidence / "launch-detection.json").write_text(json.dumps(report, indent=2) + "\n")
    archive_path = evidence.with_suffix(".tar.gz")
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(evidence, arcname=evidence.name)
    say("PROCESSES_CAPTURED=" + str(len(report["processes"])))
    say("UPLOAD_ONLY=" + str(archive_path))
    return 0


def wait_for_close(context, evidence):
    say(f"SETTINGS_CAPTURED=New PES PID {context['pid']}. Close PES normally now; the candidate will start automatically.")
    deadline = time.monotonic() + 600
    next_update = time.monotonic() + 15
    with (evidence / "waiting-for-close.jsonl").open("w") as records:
        while time.monotonic() < deadline:
            try:
                current = process_info(context["pid"])
                if current["start"] != context["start"]:
                    break
            except OSError:
                break
            if time.monotonic() >= next_update:
                records.write(json.dumps(current, default=str) + "\n")
                records.flush()
                say(f"WAITING_FOR_CLOSE=PES PID {context['pid']} is still present; Ctrl+C collects evidence")
                next_update += 15
            time.sleep(0.25)
        else:
            raise RuntimeError(f"Captured PES PID {context['pid']} is still running; left untouched")
    time.sleep(3)
    initial_ids = {(info["pid"], info["start"]) for info in context["preexisting_processes"]}
    if any((info["pid"], info["start"]) not in initial_ids for info in emulators()):
        raise RuntimeError("Another shadPS4 process is running; left untouched")


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


def group_processes(group):
    return [info for info in emulators() if info["group"] == group]


def process_snapshot(infos, target):
    records = []
    for info in infos:
        proc = Path("/proc") / str(info["pid"])
        paths = [proc / "status", proc / "maps"]
        for task in (proc / "task").glob("*"):
            paths += [task / name for name in ["status", "wchan", "syscall", "stack"]]
        for path in paths:
            try:
                records.append(str(path) + "\n" + path.read_text(errors="replace"))
            except OSError:
                pass
    target.write_text("\n\n".join(records))


def stop_group(process):
    if group_processes(process.pid):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        while group_processes(process.pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if group_processes(process.pid):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    process.wait(timeout=10)


def play(binary, context, runtime, evidence, summary):
    env = os.environ.copy()
    env.update(context["env"])
    env["SHADPS4_ENABLE_IPC"] = "false"
    args = [str(binary), *candidate_arguments(context)]
    summary["candidate_args"] = args
    summary["audio_environment_keys"] = sorted(k for k in context["env"]
                                                  if k.startswith(("PULSE_", "PIPEWIRE_", "ALSOFT_")))
    command = [str(binary), "--add-game-folder", str(context["game"]["folder"])]
    with (evidence / "startup-check.log").open("w") as log:
        subprocess.run(command, cwd=runtime, env=env, stdin=subprocess.DEVNULL,
                       stdout=log, stderr=subprocess.STDOUT, timeout=20, check=True)
    with (evidence / "candidate-console.log").open("w") as log:
        process = subprocess.Popen(args, cwd=runtime, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        summary["candidate_process_group"] = process.pid
        say("CANDIDATE_RUNNING=Play PES through to a controllable match, then close its window.")
        say("Ctrl+C here stops only this test and collects the archive. Maximum play time: 20 minutes.")
        started = time.monotonic()
        update = started
        missing_since = None
        try:
            with (evidence / "thread-masks.jsonl").open("w") as masks:
                while True:
                    process.poll()
                    infos = group_processes(process.pid)
                    if not infos and process.returncode is not None:
                        missing_since = missing_since or time.monotonic()
                        if time.monotonic() - missing_since > 2:
                            break
                    else:
                        missing_since = None
                    record = {"seconds": round(time.monotonic() - started, 1), "threads": []}
                    for info in infos:
                        for path in Path(f"/proc/{info['pid']}/task").glob("*/status"):
                            try:
                                fields = dict(line.split(":", 1) for line in path.read_text().splitlines() if ":" in line)
                                record["threads"].append({"pid": info["pid"], "tid": path.parent.name,
                                                           "name": fields.get("Name", "").strip(),
                                                           "host_cpus": fields.get("Cpus_allowed_list", "").strip()})
                            except OSError:
                                pass
                    masks.write(json.dumps(record) + "\n")
                    if time.monotonic() - started >= 1200:
                        summary["timed_out"] = True
                        process_snapshot(infos, evidence / "timeout-process.txt")
                        break
                    if time.monotonic() - update >= 30:
                        say(f"PLAYTEST_RUNNING={int(time.monotonic() - started)}s; close PES when finished")
                        update = time.monotonic()
                    time.sleep(1)
        except KeyboardInterrupt:
            summary["stopped_from_terminal"] = True
            process_snapshot(group_processes(process.pid), evidence / "stopped-process.txt")
        finally:
            stop_group(process)
            summary["initial_process_returncode"] = process.returncode
            summary["duration_seconds"] = round(time.monotonic() - started, 1)


def ask_result():
    say("MATCH_RESULT=Did the candidate reach a controllable match? Type y, n, or u (unsure), then Enter.")
    try:
        with open("/dev/tty") as tty:
            if select.select([tty], [], [], 90)[0]:
                answer = tty.readline().strip().lower()
                return {"y": "yes", "yes": "yes", "n": "no", "no": "no"}.get(answer, "unconfirmed")
    except (OSError, KeyboardInterrupt):
        pass
    return "unconfirmed"


def main():
    cache = Path.home() / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-affinity-pes-", dir=Path.home()))
    runtime = Path(tempfile.mkdtemp(prefix="pes-runtime-", dir=cache))
    binary = cache / ("build-" + SOURCE[:12]) / "shadps4"
    summary = {"source_commit": SOURCE, "candidate_binary": str(binary),
               "runner_sha256": digest(__file__), "installed_binary_replaced": False,
               "match_playable_user_report": "unconfirmed"}
    context = protected = before = None
    try:
        if digest(binary) != BINARY_SHA256:
            raise RuntimeError("Cached candidate differs from the homebrew-tested binary; preserved")
        summary["candidate_sha256"] = BINARY_SHA256
        say("CANDIDATE_VERIFIED=" + SOURCE[:12] + " (the same binary used for the affinity homebrew)")
        context, game = capture_launch(evidence)
        context["game"] = game
        summary.update(baseline_binary=str(context["exe"]), baseline_sha256=digest(context["exe"]),
                       baseline_pid=context["pid"], baseline_process_start=context["start"],
                       source_profile=str(context["profile"]), game_title=game["title"], game_id=game["serial"],
                       host_cpus=sorted(os.sched_getaffinity(0)), uname=list(os.uname()))
        candidate_arguments(context)
        wait_for_close(context, evidence)
        say("COPYING_PROFILE=Creating an isolated copy of PES settings, controls, and saves")
        protected, before, config_hash, users_hash = prepare_profile(context, game, runtime)
        play(binary, context, runtime, evidence, summary)
        summary["match_playable_user_report"] = ask_result()
    except KeyboardInterrupt:
        summary["error"] = "Cancelled before candidate launch; original session left untouched"
    except Exception as error:
        summary["error"] = str(error)
        say("PLAYTEST_ERROR=" + str(error))
    finally:
        if context is not None and "candidate_process_group" not in summary and "error" in summary:
            process_snapshot([context], evidence / "baseline-wait-process.txt")
        if protected is not None:
            try:
                summary["source_files_unchanged"] = before == snapshot(protected)
                summary["source_config_unchanged"] = config_hash == digest(context["profile"] / "config.json")
                summary["source_users_unchanged"] = users_hash == digest(context["profile"] / "users.json")
                summary["installed_binary_unchanged"] = summary["baseline_sha256"] == digest(context["exe"])
            except OSError as error:
                summary["preservation_check_error"] = str(error)
        for path in (runtime / "user/log").glob("*"):
            if path.is_file():
                shutil.copy2(path, evidence / ("candidate-" + path.name))
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        archive_path = evidence.with_suffix(".tar.gz")
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(evidence, arcname=evidence.name)
        if group_processes(summary.get("candidate_process_group", -1)):
            say("TEST_PROFILE_RETAINED=" + str(runtime))
        else:
            shutil.rmtree(runtime)
        say("UPLOAD_ONLY=" + str(archive_path))
        say("The installed emulator was not replaced. Test save changes were discarded. SSH remains open.")
    return 1 if "error" in summary else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--diagnose"]:
        sys.exit(diagnose_launch())
    elif sys.argv[1:]:
        raise SystemExit("Usage: test_affinity_pes.py [--diagnose]")
    else:
        sys.exit(main())
