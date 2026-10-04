#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Observe the pinned Linux build's video APIs without replacing the installed emulator."""

import collections
import fcntl
import hashlib
import json
import math
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
CAPTURE_SECONDS = 20
CAPTURE_GRACE = 5
SETUP_SECONDS = 60
DETACH_SECONDS = 10
VIDEO_APIS = ("sceVideodec2CreateDecoder", "sceVideodec2Decode", "sceVideodec2Flush")
PROBE = r"""
import gdb
import hashlib
import json
from pathlib import Path
import re
import time

entries = []
pending = []
started = None
stop_signal = None
stop_connected = False
result = {"status": "failed", "apis": {}, "records": [], "errors": []}
limit = 32
identity = CONFIG["identity"]
proc = Path("/proc") / str(identity["pid"])


def emit(kind, **fields):
    result["records"].append(dict(event=kind, **fields))


def register(name):
    return int(gdb.parse_and_eval("$" + name))


def memory(address, size):
    if not address:
        raise ValueError("null pointer")
    return bytes(gdb.selected_inferior().read_memory(address, size))


def number(data, offset, size=8):
    return int.from_bytes(data[offset:offset + size], "little")


def fields_before(api, args):
    if api == "sceVideodec2Decode":
        data = memory(args[1], 0x30)
        return {"input_size": number(data, 0), "au_bytes": number(data, 16),
                "pts": number(data, 24), "dts": number(data, 32)}
    if api == "sceVideodec2CreateDecoder":
        data = memory(args[0], 0x48)
        return {"config_size": number(data, 0), "codec": number(data, 12, 4),
                "max_width": number(data, 24, 4), "max_height": number(data, 28, 4)}
    return {}


def fields_after(api, args, rc):
    if rc != 0:
        return {}
    if api == "sceVideodec2Decode":
        frame_address, output_address = args[2], args[3]
    elif api == "sceVideodec2Flush":
        frame_address, output_address = args[1], args[2]
    else:
        return {}
    frame = memory(frame_address, 0x20)
    output = memory(output_address, 0x30)
    valid = bool(output[8])
    values = {"frame_accepted": bool(frame[24]), "valid": valid, "pictures": output[10]}
    if valid:
        values.update(error_frame=bool(output[9]), width=number(output, 16, 4),
                      pitch=number(output, 20, 4), height=number(output, 24, 4))
    return values


def capture_args(api):
    return [register(r) for r in ("rdi", "rsi", "rdx", "rcx")]


def return_kind(api):
    return "error_code"


def profile_snapshot(phase):
    pass


def consume_return(context):
    pass


def resolve_symbols(listing, pattern, allowed=None):
    # Keep full function entries, deduplicate repeated queries, reject real overloads.
    candidates = {}
    skipped = []
    for line in listing.splitlines():
        match = re.match(pattern, line)
        if not match:
            continue
        address, api = match.groups()
        if allowed is not None and api not in allowed:
            continue
        # The pattern ends at the API's opening parenthesis. Find its matching
        # close, not the final ')' in a nested lambda's operator() signature.
        # Parameter types can themselves contain parentheses (e.g. callbacks).
        depth = 1
        suffix = None
        for index in range(match.end(), len(line)):
            if line[index] == "(":
                depth += 1
            elif line[index] == ")":
                depth -= 1
                if depth == 0:
                    suffix = line[index + 1:].rstrip()
                    break
        # Only qualifiers may follow a canonical function's parameter list.
        # Reject nested functions, cold fragments, clones and PLT entries.
        if suffix is None or not re.fullmatch(r"(?: (?:const|volatile|noexcept))*;?", suffix):
            skipped.append(line.strip())
            continue
        candidates.setdefault(api, {})[int(address, 16)] = line.strip()
    for api, addresses in candidates.items():
        if len(addresses) != 1:
            raise RuntimeError("Ambiguous canonical API symbol: " + api + ": " +
                               " | ".join(addresses.values()))
    return {api: next(iter(addresses)) for api, addresses in candidates.items()}, sorted(set(skipped))


class Entry(gdb.Breakpoint):
    def __init__(self, address, api):
        super().__init__("*" + hex(address), internal=True)
        self.silent = True
        self.api = api
        self.calls = 0

    def stop(self):
        pending.append(("entry", self))
        return True


class Return(gdb.FinishBreakpoint):
    def __init__(self, context):
        super().__init__(gdb.newest_frame(), internal=True)
        self.silent = True
        self.context = context

    def stop(self):
        pending.append(("return", self.context))
        return True

    def out_of_scope(self):
        api = self.context["api"]
        result["apis"][api]["out_of_scope"] += 1


def on_stop(event):
    global stop_signal
    stop_signal = getattr(event, "stop_signal", None)


def interrupted_status():
    # The parent owns the timer and records its intent before signalling GDB.
    # Read this only on an actual interrupt, never on an API breakpoint.
    try:
        request = json.loads(Path(CONFIG["stop_request"]).read_text())
    except (OSError, ValueError, KeyError):
        return "interrupted"
    if not isinstance(request, dict):
        return "interrupted"
    if (request.get("reason") == "capture_complete" and started is not None and
            time.monotonic() - started >= 20):
        result["stop_reason"] = "capture_complete"
        return "complete"
    return "interrupted"


if CONFIG.get("profile_source"):
    # Only a bundled, checksum-pinned diagnostic profile is supplied by the parent.
    exec(compile(CONFIG["profile_source"], "<capture-profile>", "exec"))

try:
    print("PES_VIDEO_STAGE=checking_process", flush=True)
    current = (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
    if current != identity["start_ticks"]:
        raise RuntimeError("Process identity changed before attach.")
    with (proc / "exe").open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != identity["sha256"]:
        raise RuntimeError("Executable changed before attach.")
    tracer = re.search(r"^TracerPid:\s*(\d+)", (proc / "status").read_text(), re.M)
    if tracer is None or tracer[1] != "0":
        raise RuntimeError("The emulator already has a debugger attached.")

    print("PES_VIDEO_STAGE=attaching", flush=True)
    gdb.execute("attach " + str(identity["pid"]))
    print("PES_VIDEO_STAGE=resolving_video_symbols", flush=True)
    if "x86-64" not in gdb.selected_frame().architecture().name():
        raise RuntimeError("Unexpected target architecture.")

    symbols = "".join(gdb.execute("info functions " + query, to_string=True)
                      for query in CONFIG.get("symbol_queries", ("sceVideodec", "sceVdecsw")))
    # Keep the actual listing even when resolution fails, so a user need not repeat
    # attachment merely to reveal the names that made resolution ambiguous.
    result["symbol_listing"] = symbols
    pattern = CONFIG.get("symbol_pattern", (r"^\s*(0x[0-9a-fA-F]+)\s+Libraries::"
               r"(?:Videodec2|Videodec|Vdecsw)::(sce[A-Za-z0-9_]+)\("))
    allowed = CONFIG.get("allowed_apis", CONFIG["required_apis"]) if CONFIG.get("only_required") else None
    resolved, skipped = resolve_symbols(symbols, pattern, allowed)
    result["resolved_symbols"] = {api: hex(address) for api, address in resolved.items()}
    result["skipped_symbol_fragments"] = skipped
    for required in CONFIG["required_apis"]:
        if required not in resolved:
            raise RuntimeError("Required API symbol not found: " + required)
    for api, address in resolved.items():
        entries.append(Entry(address, api))
        result["apis"][api] = {"calls": 0, "returns": 0, "errors": {},
                                "valid_frames": 0, "out_of_scope": 0, "capped": False}
    result["unavailable_optional"] = sorted(set(CONFIG.get("allowed_apis", ())) - resolved.keys())

    gdb.execute("handle SIGSEGV nostop noprint pass")
    gdb.execute("handle SIGBUS nostop noprint pass")
    # GDB documents that noprint also implies nostop; keep the interrupt stoppable.
    gdb.execute("handle SIGINT stop print nopass")
    gdb.events.stop.connect(on_stop)
    stop_connected = True
    profile_snapshot("before")
    started = time.monotonic()
    print("PES_VIDEO_ARMED=20_seconds", flush=True)
    while gdb.selected_inferior().pid:
        pending = []
        stop_signal = None
        try:
            gdb.execute("continue", to_string=True)
        except KeyboardInterrupt:
            result["status"] = interrupted_status()
            break
        if not gdb.selected_inferior().pid:
            result["status"] = "target_exited"
            break
        if stop_signal == "SIGINT":
            result["status"] = interrupted_status()
            break
        if not pending:
            result["status"] = "unexpected_stop"
            emit("stop", location=gdb.execute("frame", to_string=True).strip())
            break

        # Tail-called wrappers can share a return PC. Preserve every callback at
        # that stop instead of overwriting one API's return with another's.
        for kind, value in pending:
            if kind == "entry":
                value.calls += 1
                api = value.api
                result["apis"][api]["calls"] += 1
                context = {"api": api, "seq": value.calls,
                           "args": capture_args(api),
                           "time": time.monotonic(), "thread": gdb.selected_thread().global_num}
                details = {}
                try:
                    details = fields_before(api, context["args"])
                except Exception as error:
                    details["read_error"] = str(error)
                emit("enter", api=api, seq=value.calls, thread=context["thread"],
                     seconds=round(context["time"] - started, 3), **details)
                Return(context)
                if value.calls >= limit:
                    value.enabled = False
                    result["apis"][api]["capped"] = True
            else:
                api = value["api"]
                consume_return(value)
                kind = return_kind(api)
                rc = None if kind == "void" else register("rax") & (0xff if kind == "bool" else 0xffffffff)
                stats = result["apis"][api]
                stats["returns"] += 1
                if rc and kind == "error_code":
                    key = hex(rc)
                    stats["errors"][key] = stats["errors"].get(key, 0) + 1
                details = {}
                try:
                    details = fields_after(api, value["args"], rc)
                except Exception as error:
                    details["read_error"] = str(error)
                if details.get("valid"):
                    stats["valid_frames"] += 1
                emit("return", api=api, seq=value["seq"], thread=value["thread"],
                     rc=None if rc is None else hex(rc),
                     elapsed_ms=round((time.monotonic() - value["time"]) * 1000, 3),
                     **details)
except KeyboardInterrupt:
    result["status"] = interrupted_status()
except BaseException as error:
    result["errors"].append(str(error))
finally:
    print("PES_VIDEO_STAGE=detaching", flush=True)
    if stop_connected:
        gdb.events.stop.disconnect(on_stop)
    if started is not None:
        result["seconds"] = round(time.monotonic() - started, 3)
        result["requested_seconds"] = 20
        result["deadline_exceeded"] = result["seconds"] > 25
        if result["deadline_exceeded"]:
            result["status"] = "overrun"
            result["errors"].append("Capture exceeded its 20-second target and 5-second grace.")
    for breakpoint in gdb.breakpoints() or ():
        try:
            if breakpoint.is_valid():
                breakpoint.delete()
        except Exception as error:
            result["errors"].append("breakpoint cleanup: " + str(error))
    if gdb.selected_inferior().pid:
        if started is not None:
            try:
                profile_snapshot("after")
            except Exception as error:
                result["errors"].append("final snapshot: " + str(error))
        try:
            gdb.execute("detach")
            result["detached"] = True
        except Exception as error:
            result["errors"].append("detach: " + str(error))
            result["detached"] = False
    else:
        result["detached"] = True
    print("PES_VIDEO_JSON=" + json.dumps(result, sort_keys=True), flush=True)
"""


def say(text):
    print(text, flush=True)


def capture_profile(profile):
    if profile == "video":
        return {"required_apis": VIDEO_APIS}
    if profile == "frames":
        from pes_frame_profile import PROFILE
        return PROFILE
    raise ValueError("Unknown diagnostic profile: " + profile)


def write_probe(identity, probe_file, *, profile="video", extra_symbol_queries=()):
    config = dict(capture_profile(profile), identity=identity,
                  stop_request=str(probe_file.parent / "stop-request.json"))
    if extra_symbol_queries:
        config['symbol_queries'] = tuple(config.get('symbol_queries', ('sceVideodec', 'sceVdecsw'))) + tuple(extra_symbol_queries)
    probe_file.write_text("CONFIG = " + repr(config) + "\n" + PROBE)


def debugger_command(identity, probe_file, prefix=()):
    return list(prefix) + [
        "gdb", "-q", "-nx", "-nh", "--batch",
        "-iex", "set auto-load off", "-iex", "set debuginfod enabled off",
        "-iex", "set sysroot /", "-iex", "set pagination off",
        "-iex", "set confirm off", "-iex", "set print thread-events off",
        "-iex", "set may-call-functions off",
        "-se", identity["executable"], "-ex", "source " + str(probe_file)]


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


def interrupt_debugger(work, prefix, *, reason="interrupted"):
    # An atomic request avoids partial JSON while GDB processes the signal.
    with tempfile.NamedTemporaryFile(mode="w", prefix=".stop-request-", dir=work,
                                     delete=False) as request:
        json.dump({"reason": reason}, request)
        temporary = Path(request.name)
    temporary.replace(work / "stop-request.json")
    command = prefix + [sys.executable, "-c", INTERRUPT_HELPER, str(work)]
    evidence = {"reason": reason}
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        evidence.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    except (Exception, KeyboardInterrupt) as error:
        evidence["error"] = type(error).__name__ + ": " + str(error)
        for field in ("stdout", "stderr"):
            value = getattr(error, field, None)
            if isinstance(value, bytes):
                value = value.decode(errors="replace")
            evidence[field] = value
        raise
    finally:
        # Retain the exact helper output even when validation or sudo fails.
        with (work / "interrupt-helper.jsonl").open("a") as output:
            output.write(json.dumps(evidence) + "\n")
    if result.stdout:
        say(result.stdout.rstrip())
    if result.returncode:
        raise RuntimeError("Could not interrupt this debugger: " + result.stderr[-1000:])
    if not re.search(r"^MATCHED_TRACE_DEBUGGERS=1$", result.stdout, re.M):
        raise RuntimeError("The interrupt helper did not identify exactly one trace debugger. "
                           "See interrupt-helper.jsonl for its exact output.")


def wait_for_debugger(child, work, prefix, *, setup_seconds=SETUP_SECONDS,
                      capture_seconds=CAPTURE_SECONDS, grace=CAPTURE_GRACE,
                      detach_seconds=DETACH_SECONDS, poll_seconds=0.25):
    position = 0
    partial_line = ""
    started = time.monotonic()
    armed_at = None
    deadline = started + setup_seconds
    outcome = {"reason": "exited", "errors": []}
    while True:
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
                elif line.startswith("PES_VIDEO_ARMED=") and armed_at is None:
                    armed_at = time.monotonic()
                    deadline = armed_at + capture_seconds
                    say("VIDEO_TRACE=armed; capturing for 20 seconds now")
            # Drain the final output even if the child exited between polls.
            if child.poll() is not None:
                break
            if time.monotonic() >= deadline:
                if armed_at is not None:
                    say("VIDEO_TRACE=20-second window ended; requesting debugger detach")
                    outcome["reason"] = "capture_complete"
                else:
                    say("VIDEO_TRACE_TIMEOUT=setup; requesting debugger detach")
                    outcome["reason"] = "setup_timeout"
                break
            time.sleep(poll_seconds)
        except KeyboardInterrupt:
            say("VIDEO_TRACE=interrupt requested; requesting debugger detach")
            outcome["reason"] = "interrupted"
            break
        except OSError as error:
            outcome["reason"] = "monitor_failed"
            outcome["errors"].append("monitor: " + str(error))
            break
    if child.poll() is None:
        try:
            interrupt_debugger(work, prefix, reason=outcome["reason"])
        except (Exception, KeyboardInterrupt) as error:
            # Preserve the report even if sudo expires or the helper times out.
            outcome["errors"].append("interrupt request: " + str(error))
        try:
            child.wait(timeout=detach_seconds)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            say("VIDEO_TRACE_DETACH_PENDING=" + str(work / "gdb.txt"))
    ended = time.monotonic()
    outcome.update(debugger_exited=child.poll() is not None,
                   returncode=child.poll(), elapsed_seconds=round(ended - started, 3),
                   armed=armed_at is not None,
                   deadline_exceeded=(armed_at is not None and
                                      ended - armed_at > capture_seconds + grace),
                   capture_and_cleanup_seconds=(round(ended - armed_at, 3)
                                                if armed_at is not None else None))
    return outcome


def inspect_target(identity):
    """Observe cleanup without sending any signal to the target or its shell."""
    proc = Path("/proc") / str(identity["pid"])
    try:
        ticks = (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
        if ticks != identity["start_ticks"]:
            return {"status": "identity_changed"}
        status = (proc / "status").read_text()
        tracer = re.search(r"^TracerPid:\s*(\d+)", status, re.M)
        state = re.search(r"^State:\s*(\S+)", status, re.M)
        if tracer is None or state is None:
            return {"status": "unverified"}
        return {"status": "observed", "tracer_pid": int(tracer[1]), "state": state[1]}
    except FileNotFoundError:
        return {"status": "exited"}
    except OSError as error:
        return {"status": "unverified", "error": str(error)}


def build_report(log, outcome, target, *, profile="video"):
    """Independently reject partial/overlong captures, retaining raw evidence."""
    lines = [line for line in log.splitlines() if line.startswith("PES_VIDEO_JSON=")]
    report = {"status": "failed", "errors": [], "apis": {}, "records": []}
    try:
        if not lines:
            raise ValueError("GDB did not produce a result.")
        parsed = json.loads(lines[-1].split("=", 1)[1])
        if not isinstance(parsed, dict):
            raise ValueError("GDB result is not an object.")
        for key, kind in (("status", str), ("errors", list), ("apis", dict), ("records", list)):
            if not isinstance(parsed.get(key), kind):
                raise ValueError("Invalid GDB result field: " + key)
        if any(not isinstance(stats, dict) or not isinstance(stats.get("calls"), int)
               for stats in parsed["apis"].values()):
            raise ValueError("Invalid API counters.")
        report = parsed
    except (ValueError, TypeError) as error:
        report["errors"].append("result: " + str(error))
    report["watchdog"] = outcome
    report["target_after"] = target
    report["errors"].extend(outcome["errors"])
    seconds = report.get("seconds")
    valid_seconds = (isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
                     and math.isfinite(seconds) and seconds >= 0)
    cleanup_seconds = outcome.get("capture_and_cleanup_seconds")
    overrun = ((valid_seconds and seconds > CAPTURE_SECONDS + CAPTURE_GRACE)
               or (cleanup_seconds is not None and
                   cleanup_seconds > CAPTURE_SECONDS + CAPTURE_GRACE)
               or outcome["reason"] == "capture_timeout"
               or outcome.get("deadline_exceeded") is True
               or report.get("deadline_exceeded") is True)
    if overrun:
        report["status"] = "overrun"
        report["deadline_exceeded"] = True
        report["errors"].append("Capture or cleanup exceeded the 20-second target and 5-second grace.")
    elif outcome["reason"] not in ("exited", "capture_complete"):
        report["status"] = outcome["reason"]
        report["errors"].append("Watchdog ended capture: " + outcome["reason"])
    elif outcome["returncode"] != 0:
        report["status"] = "failed"
        report["errors"].append("GDB exit code: " + str(outcome["returncode"]))
    if report["status"] == "complete":
        required = set(capture_profile(profile)["required_apis"])
        if not valid_seconds or seconds < CAPTURE_SECONDS or not outcome["armed"]:
            report["status"] = "failed"
            report["errors"].append("A full 20-second armed window was not established.")
        if not required.issubset(report["apis"]):
            report["status"] = "failed"
            report["errors"].append("Required " + profile + " APIs were not all hooked.")
        if report.get("stop_reason") != "capture_complete":
            report["status"] = "failed"
            report["errors"].append("A scheduled external stop was not established.")
        if profile == "frames" and any(isinstance(record, dict) and "read_error" in record
                                       for record in report["records"]):
            report["status"] = "failed"
            report["errors"].append("Frame arguments or status could not be read; inspect raw records.")
    report["cleanup_verified"] = (target.get("status") == "observed" and
                                  target.get("tracer_pid") == 0 and
                                  target.get("state") not in ("T", "t", "Z", "X"))
    if not report["cleanup_verified"]:
        report["errors"].append("Target cleanup was not verified: " + json.dumps(target))
    if report["status"] == "complete" and (report["errors"] or report.get("detached") is not True):
        report["status"] = "failed"
    if not outcome["debugger_exited"]:
        report["status"] = "detach_pending"
        report["errors"].append("Debugger did not exit after the interrupt request.")
    return report


def report_passed(report):
    return (report["status"] == "complete" and report.get("detached") is True and
            report.get("cleanup_verified") is True and not report.get("errors"))


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
            "if not hasattr(gdb.events, 'stop'):\n"
            "    raise RuntimeError('GDB lacks stop events')\n"
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
    write_probe(identity, probe_file)
    command = debugger_command(identity, probe_file, prefix)
    say("Preparing GDB; setup has a 60-second deadline. Keep the game open.")
    say("TRACE_DIRECTORY=" + str(work))
    with (work / "gdb.txt").open("w") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        outcome = wait_for_debugger(child, work, prefix)
    log = (work / "gdb.txt").read_text(errors="replace")
    report = build_report(log, outcome, inspect_target(identity))
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
    say("CLEANUP_VERIFIED=" + str(report["cleanup_verified"]) +
        " GDB_EXIT_CODE=" + str(outcome["returncode"]) +
        " WATCHDOG=" + outcome["reason"])
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
        say("TRACE_ERROR=" + str(error))
    if "PES_VIDEO_JSON=" not in log:
        say(log[-3000:])
    say("NOTE=HLE APIs only. Each API stops tracing after 32 calls; capped counts are lower bounds.")
    say("NOTE=No calls in this window does not establish that decoding never started.")
    say("PES_VIDEO_EXCERPT_END")
    if not report_passed(report):
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
