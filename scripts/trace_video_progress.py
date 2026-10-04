#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Observe the pinned Linux build's video APIs without replacing the installed emulator."""

import collections
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

REVISION = "0539f6dba2a1b075aa017c691b2c8955258e5a1e"
BINARY_SHA256 = "370c0c31b36b1afa464c67cf30974cb19fe21dd3b13cb07bb35d9d8c45f79800"
PROBE = "import collections\nimport gdb\nimport hashlib\nimport json\nimport os\nfrom pathlib import Path\nimport re\nimport threading\nimport time\n\nentries = []\npending = None\nfinished = threading.Event()\nexpired = threading.Event()\nstarted = None\nresult = {\"status\": \"failed\", \"apis\": {}, \"records\": [], \"errors\": []}\nlimit = 32\nidentity = CONFIG[\"identity\"]\nproc = Path(\"/proc\") / str(identity[\"pid\"])\n\n\ndef emit(kind, **fields):\n    result[\"records\"].append(dict(event=kind, **fields))\n\n\ndef register(name):\n    return int(gdb.parse_and_eval(\"$\" + name))\n\n\ndef memory(address, size):\n    if not address:\n        raise ValueError(\"null pointer\")\n    return bytes(gdb.selected_inferior().read_memory(address, size))\n\n\ndef number(data, offset, size=8):\n    return int.from_bytes(data[offset:offset + size], \"little\")\n\n\ndef fields_before(api, args):\n    if api == \"sceVideodec2Decode\":\n        data = memory(args[1], 0x30)\n        return {\"input_size\": number(data, 0), \"au_bytes\": number(data, 16),\n                \"pts\": number(data, 24), \"dts\": number(data, 32)}\n    if api == \"sceVideodec2CreateDecoder\":\n        data = memory(args[0], 0x48)\n        return {\"config_size\": number(data, 0), \"codec\": number(data, 12, 4),\n                \"max_width\": number(data, 24, 4), \"max_height\": number(data, 28, 4)}\n    return {}\n\n\ndef fields_after(api, args, rc):\n    if rc != 0:\n        return {}\n    if api == \"sceVideodec2Decode\":\n        frame_address, output_address = args[2], args[3]\n    elif api == \"sceVideodec2Flush\":\n        frame_address, output_address = args[1], args[2]\n    else:\n        return {}\n    frame = memory(frame_address, 0x20)\n    output = memory(output_address, 0x30)\n    valid = bool(output[8])\n    values = {\"frame_accepted\": bool(frame[24]), \"valid\": valid, \"pictures\": output[10]}\n    if valid:\n        values.update(error_frame=bool(output[9]), width=number(output, 16, 4),\n                      pitch=number(output, 20, 4), height=number(output, 24, 4))\n    return values\n\n\nclass Entry(gdb.Breakpoint):\n    def __init__(self, address, api):\n        super().__init__(\"*\" + hex(address), internal=True)\n        self.silent = True\n        self.api = api\n        self.calls = 0\n\n    def stop(self):\n        global pending\n        pending = (\"entry\", self)\n        return True\n\n\nclass Return(gdb.FinishBreakpoint):\n    def __init__(self, context):\n        super().__init__(gdb.newest_frame(), internal=True)\n        self.silent = True\n        self.context = context\n\n    def stop(self):\n        global pending\n        pending = (\"return\", self.context)\n        return True\n\n    def out_of_scope(self):\n        api = self.context[\"api\"]\n        result[\"apis\"][api][\"out_of_scope\"] += 1\n\n\ndef timer():\n    if not finished.wait(20):\n        expired.set()\n        if hasattr(gdb, \"interrupt\"):\n            gdb.interrupt()\n        else:\n            os.kill(os.getpid(), 2)\n\n\ntry:\n    print(\"PES_VIDEO_STAGE=checking_process\", flush=True)\n    if not hasattr(gdb, \"Thread\"):\n        raise RuntimeError(\"This trace needs a GDB version providing gdb.Thread.\")\n    current = (proc / \"stat\").read_text().rsplit(\")\", 1)[1].split()[19]\n    if current != identity[\"start_ticks\"]:\n        raise RuntimeError(\"Process identity changed before attach.\")\n    with (proc / \"exe\").open(\"rb\") as stream:\n        actual = hashlib.file_digest(stream, \"sha256\").hexdigest()\n    if actual != identity[\"sha256\"]:\n        raise RuntimeError(\"Executable changed before attach.\")\n    tracer = re.search(r\"^TracerPid:\\s*(\\d+)\", (proc / \"status\").read_text(), re.M)\n    if tracer is None or tracer[1] != \"0\":\n        raise RuntimeError(\"The emulator already has a debugger attached.\")\n\n    print(\"PES_VIDEO_STAGE=attaching\", flush=True)\n    gdb.execute(\"attach \" + str(identity[\"pid\"]))\n    print(\"PES_VIDEO_STAGE=resolving_video_symbols\", flush=True)\n    if \"x86-64\" not in gdb.selected_frame().architecture().name():\n        raise RuntimeError(\"Unexpected target architecture.\")\n\n    symbols = gdb.execute(\"info functions sceVideodec\", to_string=True)\n    symbols += gdb.execute(\"info functions sceVdecsw\", to_string=True)\n    pattern = (r\"^\\s*(0x[0-9a-fA-F]+)\\s+Libraries::\"\n               r\"(?:Videodec2|Videodec|Vdecsw)::(sce[A-Za-z0-9_]+)\\(\")\n    seen = set()\n    for address, api in re.findall(pattern, symbols, re.M):\n        if api in seen:\n            raise RuntimeError(\"Ambiguous API symbol: \" + api)\n        seen.add(api)\n        entries.append(Entry(int(address, 16), api))\n        result[\"apis\"][api] = {\"calls\": 0, \"returns\": 0, \"errors\": {},\n                                \"valid_frames\": 0, \"out_of_scope\": 0, \"capped\": False}\n    for required in (\"sceVideodec2CreateDecoder\", \"sceVideodec2Decode\", \"sceVideodec2Flush\"):\n        if required not in seen:\n            raise RuntimeError(\"Required API symbol not found: \" + required)\n\n    gdb.execute(\"handle SIGSEGV nostop noprint pass\")\n    gdb.execute(\"handle SIGBUS nostop noprint pass\")\n    started = time.monotonic()\n    gdb.Thread(target=timer, daemon=True).start()\n    print(\"PES_VIDEO_ARMED=20_seconds\", flush=True)\n    while not expired.is_set() and gdb.selected_inferior().pid:\n        pending = None\n        try:\n            gdb.execute(\"continue\", to_string=True)\n        except KeyboardInterrupt:\n            result[\"status\"] = \"complete\" if expired.is_set() else \"interrupted\"\n            break\n        if expired.is_set():\n            result[\"status\"] = \"complete\"\n            break\n        if not gdb.selected_inferior().pid:\n            result[\"status\"] = \"target_exited\"\n            break\n        if pending is None:\n            result[\"status\"] = \"unexpected_stop\"\n            emit(\"stop\", location=gdb.execute(\"frame\", to_string=True).strip())\n            break\n\n        kind, value = pending\n        if kind == \"entry\":\n            value.calls += 1\n            api = value.api\n            result[\"apis\"][api][\"calls\"] += 1\n            context = {\"api\": api, \"seq\": value.calls,\n                       \"args\": [register(r) for r in (\"rdi\", \"rsi\", \"rdx\", \"rcx\")],\n                       \"time\": time.monotonic(), \"thread\": gdb.selected_thread().global_num}\n            details = {}\n            try:\n                details = fields_before(api, context[\"args\"])\n            except Exception as error:\n                details[\"read_error\"] = str(error)\n            emit(\"enter\", api=api, seq=value.calls, thread=context[\"thread\"],\n                 seconds=round(context[\"time\"] - started, 3), **details)\n            Return(context)\n            if value.calls >= limit:\n                value.enabled = False\n                result[\"apis\"][api][\"capped\"] = True\n        else:\n            api = value[\"api\"]\n            rc = register(\"rax\") & 0xffffffff\n            stats = result[\"apis\"][api]\n            stats[\"returns\"] += 1\n            if rc:\n                key = hex(rc)\n                stats[\"errors\"][key] = stats[\"errors\"].get(key, 0) + 1\n            details = {}\n            try:\n                details = fields_after(api, value[\"args\"], rc)\n            except Exception as error:\n                details[\"read_error\"] = str(error)\n            if details.get(\"valid\"):\n                stats[\"valid_frames\"] += 1\n            emit(\"return\", api=api, seq=value[\"seq\"], thread=value[\"thread\"],\n                 rc=hex(rc), elapsed_ms=round((time.monotonic() - value[\"time\"]) * 1000, 3),\n                 **details)\n    if expired.is_set():\n        result[\"status\"] = \"complete\"\nexcept KeyboardInterrupt:\n    result[\"status\"] = \"complete\" if expired.is_set() else \"interrupted\"\nexcept BaseException as error:\n    result[\"errors\"].append(str(error))\nfinally:\n    finished.set()\n    print(\"PES_VIDEO_STAGE=detaching\", flush=True)\n    if started is not None:\n        result[\"seconds\"] = round(time.monotonic() - started, 3)\n    for breakpoint in gdb.breakpoints() or ():\n        try:\n            if breakpoint.is_valid():\n                breakpoint.delete()\n        except Exception as error:\n            result[\"errors\"].append(\"breakpoint cleanup: \" + str(error))\n    if gdb.selected_inferior().pid:\n        try:\n            gdb.execute(\"detach\")\n            result[\"detached\"] = True\n        except Exception as error:\n            result[\"errors\"].append(\"detach: \" + str(error))\n            result[\"detached\"] = False\n    else:\n        result[\"detached\"] = True\n    print(\"PES_VIDEO_JSON=\" + json.dumps(result, sort_keys=True), flush=True)\n"


def say(text):
    print(text, flush=True)


INTERRUPT_HELPER = r"""
import json
import os
from pathlib import Path
import signal
import sys

work = Path(sys.argv[1])
identity = json.loads((work / "identity.json").read_text())
source_argument = ("source " + str(work / "probe.py")).encode()
binary_argument = identity["executable"].encode()
matched = 0
for proc in Path("/proc").iterdir():
    if not proc.name.isdigit():
        continue
    handle = None
    try:
        handle = os.pidfd_open(int(proc.name))
        executable = (proc / "exe").readlink().name
        if executable != "gdb" and not executable.endswith("-gdb"):
            continue
        arguments = (proc / "cmdline").read_bytes().split(b"\0")
        if (source_argument not in arguments or binary_argument not in arguments or
                b"--batch" not in arguments):
            continue
        signal.pidfd_send_signal(handle, signal.SIGINT)
        matched += 1
        print("INTERRUPTED_TRACE_GDB=" + proc.name, flush=True)
    except (ProcessLookupError, FileNotFoundError, PermissionError):
        continue
    finally:
        if handle is not None:
            os.close(handle)
print("MATCHED_TRACE_DEBUGGERS=" + str(matched), flush=True)
"""


def interrupt_debugger(work, prefix):
    command = prefix + [sys.executable, "-c", INTERRUPT_HELPER, str(work)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if result.stdout:
        say(result.stdout.rstrip())
    if result.returncode:
        raise RuntimeError("Could not interrupt this debugger: " + result.stderr[-1000:])


def wait_for_debugger(child, work, prefix):
    position = 0
    partial_line = ""
    armed = False
    deadline = time.monotonic() + 60
    interrupted = False
    while child.poll() is None:
        try:
            with (work / "gdb.txt").open() as output:
                output.seek(position)
                data = partial_line + output.read()
                position = output.tell()
            complete_lines = data.split("\n")
            partial_line = complete_lines.pop()
            for line in complete_lines:
                if line.startswith("PES_VIDEO_STAGE="):
                    say(line)
                elif line.startswith("PES_VIDEO_ARMED=") and not armed:
                    armed = True
                    deadline = time.monotonic() + 25
                    say("VIDEO_TRACE=armed; capturing for 20 seconds now")
            if time.monotonic() >= deadline:
                phase = "capture" if armed else "setup"
                say("VIDEO_TRACE_TIMEOUT=" + phase + "; requesting debugger detach")
                interrupt_debugger(work, prefix)
                interrupted = True
                break
            time.sleep(0.25)
        except KeyboardInterrupt:
            say("VIDEO_TRACE=interrupt requested; requesting debugger detach")
            interrupt_debugger(work, prefix)
            interrupted = True
            break
    if interrupted:
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            say("VIDEO_TRACE_DETACH_PENDING=" + str(work / "gdb.txt"))
    return child.poll() is not None


def interrupt_existing(work):
    work = work.expanduser().resolve()
    if not (work / "identity.json").is_file() or not (work / "probe.py").is_file():
        raise RuntimeError("Not a trace directory: " + str(work))
    if work.stat().st_uid != os.getuid():
        raise RuntimeError("Trace directory is owned by another user.")
    log = work / "gdb.txt"
    if log.is_file():
        say("TRACE_LOG_BEFORE_INTERRUPT")
        for line in log.read_text(errors="replace").splitlines()[-18:]:
            say(line[:500])
    scope = Path("/proc/sys/kernel/yama/ptrace_scope")
    prefix = []
    if scope.exists() and int(scope.read_text()) > 0 and os.geteuid() != 0:
        subprocess.run(["sudo", "-v"], check=True)
        prefix = ["sudo", "-n"]
    interrupt_debugger(work, prefix)
    say("Return to the original SSH tab for the capture result.")
    say("If it still waits, paste the TRACE_LOG output above.")


def find_process():
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            executable = (proc / "exe").readlink()
            if executable.name.lower() != "shadps4":
                continue
            args = (proc / "cmdline").read_bytes().split(b"\0")
            found.append((proc, executable, args))
        except (OSError, ValueError):
            continue
    if len(found) != 1:
        raise RuntimeError("Expected one running shadPS4 process; found " + str(len(found)) +
                           ". Launch PES once from ES-DE, then rerun.")
    proc, executable, args = found[0]
    if b"CUSA18676" not in args:
        raise RuntimeError("The running emulator was not launched with --game CUSA18676.")
    with (proc / "exe").open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if checksum != BINARY_SHA256:
        raise RuntimeError("Running binary differs from the examined build; preserved. SHA256=" +
                           checksum)
    tracer = re.search(r"^TracerPid:\s*(\d+)", (proc / "status").read_text(), re.M)
    if tracer is None or tracer[1] != "0":
        raise RuntimeError("Another debugger is already attached; preserved.")
    start = (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
    return {"pid": int(proc.name), "start_ticks": start, "executable": str(executable),
            "sha256": checksum, "revision": REVISION}


def collect_log_lines(identity, work):
    proc = Path("/proc") / str(identity["pid"])
    paths = set()
    try:
        for fd in (proc / "fd").iterdir():
            try:
                path = fd.readlink()
                if path.is_absolute() and path.name in (
                        "shad_log.txt", "shadps4.log", "emulator.log"):
                    paths.add(path)
            except OSError:
                pass
    except OSError:
        pass
    evidence = []
    match = re.compile(r"videodec|vdec2|vdecsw|avplayer|crimana|startup.?loading", re.I)
    for path in sorted(paths):
        selected = collections.deque(maxlen=400)
        matches = 0
        scanned = 0
        truncated = False
        try:
            with path.open("rb") as stream:
                for line in stream:
                    scanned += len(line)
                    if scanned > 64 * 1024 * 1024:
                        truncated = True
                        break
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if match.search(text):
                        matches += 1
                        selected.append(text)
            evidence.append({"path": str(path), "matches": matches,
                             "scan_truncated": truncated, "lines": list(selected)})
        except OSError as error:
            evidence.append({"path": str(path), "error": str(error)})
    (work / "existing-video-logs.json").write_text(json.dumps(evidence, indent=2))
    return evidence


def run():
    if os.geteuid() == 0:
        raise RuntimeError("Run as chreece, without sudo in front of this script.")
    if shutil.which("gdb") is None:
        raise RuntimeError("gdb is not installed.")
    compile(PROBE, "<video-probe>", "exec")
    identity = find_process()
    scope_path = Path("/proc/sys/kernel/yama/ptrace_scope")
    scope = int(scope_path.read_text()) if scope_path.exists() else 0
    if scope >= 3:
        raise RuntimeError("ptrace_scope disables attachment; no security setting was changed.")
    prefix = []
    if scope:
        if shutil.which("sudo") is None:
            raise RuntimeError("sudo is needed for debugger attachment on this system.")
        say("sudo is used only to attach GDB; it may ask for your password.")
        subprocess.run(["sudo", "-v"], check=True)
        prefix = ["sudo", "-n"]
    with tempfile.TemporaryDirectory(prefix="shadps4-video-gdb-check-") as check:
        check_code = (Path(check) / "check.py")
        check_code.write_text(
            "import gdb\n"
            "if not hasattr(gdb, 'Thread'):\n"
            "    raise RuntimeError('GDB lacks gdb.Thread')\n"
            "print('PES_VIDEO_GDB_CHECK=PASS')\n")
        checked = subprocess.run(
            ["gdb", "-q", "-nx", "-nh", "--batch", "-iex", "set auto-load off",
             "-ex", "source " + str(check_code)],
            capture_output=True, text=True, timeout=20)
        if checked.returncode or "PES_VIDEO_GDB_CHECK=PASS" not in checked.stdout:
            raise RuntimeError("GDB preflight failed: " + checked.stdout + checked.stderr)

    work = Path(tempfile.mkdtemp(prefix="shadps4-pes-video-", dir=Path.home()))
    (work / "identity.json").write_text(json.dumps(identity, indent=2))
    before = collect_log_lines(identity, work)
    (work / "before-video-logs.json").write_text(json.dumps(before, indent=2))
    probe_file = work / "probe.py"
    probe_file.write_text("CONFIG = " + repr({"identity": identity}) + "\n" + PROBE)
    command = prefix + [
        "gdb", "-q", "-nx", "-nh", "--batch",
        "-iex", "set auto-load off", "-iex", "set debuginfod enabled off",
        "-iex", "set sysroot /", "-iex", "set pagination off",
        "-iex", "set confirm off", "-iex", "set print thread-events off",
        "-iex", "set may-call-functions off",
        "-se", identity["executable"], "-ex", "source " + str(probe_file)]
    say("Preparing GDB; setup has a 60-second deadline. Keep the game open.")
    say("TRACE_DIRECTORY=" + str(work))
    with (work / "gdb.txt").open("w") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        debugger_exited = wait_for_debugger(child, work, prefix)
    log = (work / "gdb.txt").read_text(errors="replace")
    lines = [line for line in log.splitlines() if line.startswith("PES_VIDEO_JSON=")]
    report = json.loads(lines[-1].split("=", 1)[1]) if lines else {
        "status": "failed", "errors": ["GDB did not produce a result."], "apis": {}, "records": []}
    if not debugger_exited:
        report["status"] = "detach_pending"
        report["errors"].append("Debugger did not exit after the interrupt request.")
    (work / "video-trace.json").write_text(json.dumps(report, indent=2))
    evidence = collect_log_lines(identity, work)
    archive = work.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as output:
        for path in sorted(work.iterdir()):
            output.add(path, arcname=work.name + "/" + path.name, recursive=False)
    say("PES_VIDEO_REPORT=" + str(archive))
    say("PES_VIDEO_EXCERPT_BEGIN")
    say("STATUS=" + report["status"] + " DETACHED=" + str(report.get("detached", False)) +
        " SECONDS=" + str(report.get("seconds", 0)))
    say("HOOKED_APIS=" + str(len(report["apis"])))
    for api, stats in sorted(report["apis"].items()):
        if stats["calls"]:
            say(api + " " + json.dumps(stats, sort_keys=True))
    if report["apis"] and not any(stats["calls"] for stats in report["apis"].values()):
        say("NO_OBSERVED_VIDEO_API_CALLS_IN_THIS_WINDOW")
    for record in report["records"][:24]:
        say(json.dumps(record, sort_keys=True))
    for source in evidence:
        say("EXISTING_LOG=" + source["path"] + " MATCHES=" + str(source.get("matches", "?")) +
            " SCAN_TRUNCATED=" + str(source.get("scan_truncated", False)))
        for line in source.get("lines", [])[-24:]:
            say(line[:600])
    if not evidence:
        say("EXISTING_LOG=unavailable; earlier decoder activity cannot be inferred.")
    for error in report.get("errors", []):
        say("TRACE_ERROR=" + error)
    if not lines:
        say(log[-3000:])
    say("NOTE=HLE APIs only. Each API stops tracing after 32 calls; capped counts are lower bounds.")
    say("NOTE=No calls in this window does not establish that decoding never started.")
    say("PES_VIDEO_EXCERPT_END")
    if report["status"] != "complete" or not report.get("detached") or report.get("errors"):
        raise RuntimeError("Trace incomplete; preserve the report above.")
    say("PES_VIDEO_RESULT=PASS (capture completed, not a game-fix verdict)")


def main():
    lock_root = Path.home() / ".cache"
    lock_root.mkdir(exist_ok=True)
    with (lock_root / "shadps4-video-trace.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run()


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--interrupt-trace":
            interrupt_existing(Path(sys.argv[2]))
        else:
            main()
    except (Exception, KeyboardInterrupt) as error:
        say("PES_VIDEO_RESULT=FAIL: " + str(error))
        sys.exit(1)
