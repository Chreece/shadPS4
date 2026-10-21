#!/usr/bin/env python3
"""Run a verified candidate with an isolated profile and bounded visual evidence."""

import argparse
import collections
import csv
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import uuid


GIB = 1024**3
API = "http://127.0.0.1:7869"
BASELINE_ENV = {
    "SHADPS4_STRIDED_GUEST_TSHARP": "1",
    "SHADPS4_GUEST_TSHARP_LARGE_ARRAY": "1",
    "SHADPS4_TEXTURE_SLOT_PRESSURE": "1",
    "SHADPS4_GPUVM_SPARSE_MAP": "0",
}
DIAGNOSTICS_OFF = (
    "FRAME_PROBE", "FRAME_PROBE_IMAGES", "FRAME_PROBE_BUFFERS",
    "VERTEX_OUTPUT_FEEDBACK", "RESOURCE_AUDIT", "INDEXED_IMAGE_AUDIT",
    "MENU_PASS_TRACE", "GOT_MENU_STAGE_PIXELS", "GOT_SCENE_GPU_QUERY",
    "TRACE_DISPATCH_INITIATOR", "INDIRECT_WAR_AUDIT", "GOT_INDIRECT_PRODUCERS",
    "GOT_DMA_WORD_PROBE", "GOT_GDS_PRODUCER_PROBE", "GOT_INDIRECT_GPU_ARGUMENTS",
    "GOT_SCENE_DEPTH_AB", "GOT_SCENE_RASTER_AB", "RENDER_AUDIT",
    "INDEXED_TABLE_DUMP", "SRT_SOURCE_AUDIT", "SRT_INLINE_AUDIT",
    "SHARP_MASK_AUDIT", "SHARP_MIXED_AUDIT", "STRIDED_SOURCE_PROOF",
    "GUEST_TSHARP_CENSUS", "IMAGE_PHI_SPLIT_AUDIT", "GAMEPAD_HOTPLUG_AUDIT",
)
ANSI = re.compile(r"\x1b\[[0-9;]*[mK]")
GPU_FAULT = re.compile(
    r"GPUVM fault detected|VK_ERROR_DEVICE_LOST|device lost|GPU hang detected|"
    r"amdgpu.*ring.*timeout", re.I)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fields(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def verify_host(report, candidate, revision):
    with tarfile.open(report, "r:gz") as archive:
        def read(name):
            member = archive.getmember(name)
            if not member.isfile() or member.size > 65536:
                raise RuntimeError("INVALID_HOST_METADATA=" + name)
            return archive.extractfile(member).read().decode("utf-8")
        final = fields(read("info/final.txt"))
        start = fields(read("info/start.txt"))
        if any(final.get(key) != value for key, value in {
            "STATUS": "HOST_ONLY_BUILD_PASS", "EXIT": "0", "CANDIDATE_BUILT": "1",
            "BASELINE_EXECUTABLE_RESTORED": "1",
        }.items()):
            raise RuntimeError("HOST_REPORT_NOT_VERIFIED_PASS")
        if start.get("PINNED_CANDIDATE") != revision:
            raise RuntimeError("HOST_SOURCE_REVISION_MISMATCH")
        expected = read("info/candidate-preserved.sha256").split()[0]
    actual = digest(candidate)
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or actual != expected:
        raise RuntimeError("CANDIDATE_SHA256_MISMATCH")
    return actual, final, start


class GameLog:
    def __init__(self):
        self.head = bytearray()
        self.tail = collections.deque()
        self.tail_size = self.discarded = 0
        self.errors = collections.deque(maxlen=500)
        self.resource_diagnostics = collections.deque(maxlen=512)
        self.partial = ""
        self.fault = threading.Event()
        self.precise = threading.Event()

    def consume(self, stream):
        while True:
            chunk = stream.read1(8192)
            if not chunk:
                break
            first = min(len(chunk), max(0, 1024 * 1024 - len(self.head)))
            self.head.extend(chunk[:first])
            if chunk[first:]:
                self.tail.append(chunk[first:])
                self.tail_size += len(chunk) - first
            while self.tail_size > 6 * 1024 * 1024:
                removed = self.tail.popleft()
                self.tail_size -= len(removed)
                self.discarded += len(removed)
            text = self.partial + ANSI.sub("", chunk.decode("utf-8", "replace"))
            if GPU_FAULT.search(text):
                self.fault.set()
            if re.search(r"GPU readbacksMode: 2(?:\D|$)", text):
                self.precise.set()
            lines = text.split("\n")
            self.partial = lines.pop()[-65536:]
            for line in lines:
                if re.search(r"<(?:Error|Critical|Warning)>|Assertion Failed|GPUVM fault", line):
                    self.errors.append(line[:4096])
                if re.search(r"ResourceBindingDiagnostics|ResourceBindingBdaFault|IndirectArgumentSnapshot", line):
                    self.resource_diagnostics.append(line[:4096])
        if self.partial and re.search(r"error|fatal|assert|fault", self.partial, re.I):
            self.errors.append(self.partial[:4096])

    def save(self, run):
        with (run / "game.log").open("wb") as stream:
            stream.write(self.head)
            if self.discarded:
                stream.write(f"\n[BOUNDED_LOG discarded_middle_bytes={self.discarded}]\n".encode())
            for block in self.tail:
                stream.write(block)
        (run / "info/game-errors.log").write_text("\n".join(self.errors) + "\n")
        (run / "info/resource-bindings.log").write_text("\n".join(self.resource_diagnostics) + "\n")


class Runtime:
    def __init__(self, args):
        self.args = args
        self.root = Path.home() / "projects/shadps4"
        self.got = self.root / "GhostOfTsushima"
        self.work = self.got / "tests/got-upstream-menu-main-20261017-cached"
        self.original_profile = self.got / "play/np-init-affinity-main-20261015-195552/user"
        self.active = self.work / "build/shadps4"
        self.game = Path("/mnt/roms-all/ps4-games/CUSA11456/eboot.bin")
        stamp = datetime.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        self.run = self.root / "logs" / f"got-world-graphics-runtime-{stamp}-{uuid.uuid4().hex[:6]}"
        for name in ("info", "screenshots", "profile"):
            (self.run / name).mkdir(parents=True, exist_ok=True)
        self.report = self.run.with_suffix(".tar.gz")
        self.console = (self.run / "info/console.log").open("a", buffering=1)
        self.proc = self.reader = None
        self.locks = []
        self.stop_signal = 0
        self.ollama_id = None
        self.models = []
        self.ollama_touched = False
        self.vram_recovery = None
        self.log = GameLog()
        self.started = None
        self.started_iso = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        self.meta = {
            "STATUS": "PRECHECK", "SOURCE_REVISION": args.source_revision,
            "GAME_LAUNCHED": "NO", "OLLAMA_ACTION": "UNCHANGED",
            "OLLAMA_RESTORE": "NOT_NEEDED", "AUTOMATED_INPUT": "NONE",
            "ORIGINAL_SAVES": "ISOLATED_COPY_ONLY", "RENDER_STATE": "NATIVE",
            "EXPENSIVE_DIAGNOSTICS": "OFF", "SHADER_HASH_FILTERS": "NONE",
            "SCREENSHOT_LIMIT": "15", "SSH_SESSION": "OPEN",
            "VRAM_RECLAIM_THRESHOLD_GIB": "8", "VRAM_ABORT_THRESHOLD_GIB": "2",
            "INDIRECT_ARGUMENT_CAPTURE": "PENDING_ONE_FRAME_AT_60_SECONDS",
            "INDIRECT_ARGUMENT_BINARY_LIMIT": str(1024 * 1024),
        }
        self.active_sha = None
        self.config_sha = None

    def say(self, text):
        self.console.write(text + "\n")
        try:
            print(text, flush=True)
        except (BrokenPipeError, OSError):
            pass

    def command(self, argv, timeout=15, env=None):
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=timeout, env=env)
        if result.returncode:
            raise RuntimeError(f"COMMAND_FAILED={argv[0]}: {result.stderr.strip()[:400]}")
        return result.stdout

    def lock(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            raise RuntimeError("LOCK_HELD=" + str(path))
        self.locks.append(stream)

    def check_stop(self):
        if self.stop_signal:
            raise InterruptedError("STOP_SIGNAL=" + str(self.stop_signal))

    def preflight(self):
        self.lock(self.work / ".build.lock")
        self.lock(self.got / "tests/.got-runtime.lock")
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat().st_uid == os.getuid() and (entry / "exe").resolve().name.lower().startswith("shadps4"):
                    raise RuntimeError("ANOTHER_EMULATOR_RUNNING=" + entry.name)
            except (OSError, RuntimeError) as error:
                if str(error).startswith("ANOTHER_EMULATOR_RUNNING"):
                    raise
        if not self.args.candidate.is_file() or not os.access(self.args.candidate, os.X_OK):
            raise RuntimeError("CANDIDATE_NOT_EXECUTABLE")
        if not self.game.is_file() or not self.original_profile.is_dir():
            raise RuntimeError("GAME_OR_BASELINE_PROFILE_MISSING")
        if not (self.original_profile / "config.json").is_file():
            raise RuntimeError("BASELINE_CONFIG_JSON_MISSING")
        actual, final, start = verify_host(self.args.host_report, self.args.candidate,
                                           self.args.source_revision)
        self.meta["CANDIDATE_SHA256"] = actual
        self.meta["HOST_REPORT_VERIFIED"] = "PASS"
        (self.run / "info/host-provenance.json").write_text(json.dumps({
            "report": str(self.args.host_report), "candidate": str(self.args.candidate),
            "sha256": actual, "host_final": final, "host_start": start,
        }, indent=2) + "\n")
        self.active_sha = digest(self.active) if self.active.is_file() else None
        self.config_sha = digest(self.original_profile / "config.json")
        if shutil.disk_usage(self.run).free < 2 * GIB:
            raise RuntimeError("LOW_DISK_SPACE")
        if not shutil.which("xdpyinfo") or not shutil.which("import"):
            raise RuntimeError("REQUIRED_TOOL_MISSING=xdpyinfo_or_ImageMagick_import")
        self.display = None
        for display in dict.fromkeys((os.environ.get("DISPLAY"), ":0", ":1", ":2")):
            if not display:
                continue
            try:
                self.command(["xdpyinfo", "-display", display], timeout=4)
                self.display = display
                break
            except (RuntimeError, subprocess.TimeoutExpired):
                continue
        if self.display is None:
            raise RuntimeError("NO_X11_DISPLAY")
        cards = []
        for vendor in Path("/sys/class/drm").glob("card[0-9]*/device/vendor"):
            try:
                if vendor.read_text().strip() == "0x1002":
                    total = int((vendor.parent / "mem_info_vram_total").read_text())
                    used = int((vendor.parent / "mem_info_vram_used").read_text())
                    if 0 <= used <= total:
                        cards.append((total, vendor.parent))
            except (ValueError, OSError):
                continue
        if not cards:
            raise RuntimeError("AMD_GPU_TELEMETRY_UNAVAILABLE")
        self.total, self.gpu = max(cards, key=lambda item: item[0])
        self.meta["GPU_SYSFS"] = str(self.gpu)
        self.check_stop()

    def vram(self):
        used = int((self.gpu / "mem_info_vram_used").read_text())
        if not 0 <= used <= self.total:
            raise RuntimeError("INVALID_GPU_VRAM_TELEMETRY")
        return used, self.total - used

    def ollama_request(self, path, payload=None, timeout=30):
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(API + path, data=body,
                                         headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)

    def prepare_vram(self):
        _, free = self.vram()
        self.meta["FREE_VRAM_BEFORE"] = str(free)
        self.say(f"FREE_VRAM_BEFORE={free / GIB:.2f} GiB REQUIRED=8 GiB")
        if free >= 8 * GIB:
            self.meta["OLLAMA_ACTION"] = "KEPT_RUNNING_SUFFICIENT_VRAM"
            return
        self.suspend_ollama("PRELAUNCH")
        for _ in range(10):
            self.check_stop()
            _, free = self.vram()
            if free >= 8 * GIB:
                break
            time.sleep(1)
        self.meta["FREE_VRAM_AFTER_OLLAMA_STOP"] = str(free)
        if free < 8 * GIB:
            raise RuntimeError("STILL_LOW_VRAM_GAME_NOT_LAUNCHED")

    def suspend_ollama(self, phase):
        if not shutil.which("docker"):
            raise RuntimeError("LOW_VRAM_DOCKER_UNAVAILABLE")
        rows = json.loads(self.command(["docker", "inspect", "ollama"]))
        if len(rows) != 1:
            raise RuntimeError("AMBIGUOUS_OLLAMA_CONTAINER")
        container = rows[0]
        if (container.get("Name") != "/ollama" or
                not container.get("Config", {}).get("Image", "").startswith("ollama/ollama:") or
                not container.get("State", {}).get("Running")):
            raise RuntimeError("EXPECTED_RUNNING_OLLAMA_CONTAINER_NOT_FOUND")
        ports = container.get("NetworkSettings", {}).get("Ports", {}) or {}
        mapped = any(p.get("HostPort") == "7869" for values in ports.values() if values for p in values)
        host_network = container.get("HostConfig", {}).get("NetworkMode") == "host"
        host_api = any(re.fullmatch(r"OLLAMA_HOST=(?:https?://)?[^/]+:7869/?", value)
                       for value in container.get("Config", {}).get("Env", []) or [])
        if not mapped and not (host_network and host_api):
            raise RuntimeError("OLLAMA_API_PORT_CONTAINER_MISMATCH")
        snapshot = self.ollama_request("/api/ps")
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("models"), list):
            raise RuntimeError("INVALID_OLLAMA_MODEL_SNAPSHOT")
        self.models = snapshot["models"]
        if any(not isinstance(m, dict) or not (m.get("name") or m.get("model")) for m in self.models):
            raise RuntimeError("INVALID_OLLAMA_MODEL_ENTRY")
        if not self.models or all(m.get("size_vram") == 0 for m in self.models):
            raise RuntimeError("NO_RELEASABLE_OLLAMA_MODELS")
        self.ollama_id = container["Id"]
        (self.run / "info/ollama-before.json").write_text(json.dumps(snapshot, indent=2) + "\n")
        self.check_stop()
        _, free = self.vram()
        if free >= 8 * GIB:
            self.meta["OLLAMA_ACTION"] = "KEPT_RUNNING_VRAM_RECOVERED"
            return False
        self.meta["OLLAMA_VRAM_TRIGGER_PHASE"] = phase
        self.meta["FREE_VRAM_AT_OLLAMA_STOP"] = str(free)
        self.ollama_touched = True
        self.meta["OLLAMA_RESTORE"] = "PENDING"
        self.say(f"VRAM_LOW_{phase}: temporarily stopping the verified Ollama container; loaded models will be restored.")
        self.command(["docker", "stop", "--time", "20", self.ollama_id], timeout=35)
        self.meta["OLLAMA_ACTION"] = ("STOPPED_LOW_VRAM" if phase == "PRELAUNCH"
                                      else "STOPPED_RUNTIME_LOW_VRAM")
        return True

    def recover_runtime_vram(self):
        try:
            stopped = self.suspend_ollama("RUNTIME")
            _, free = self.vram()
            self.meta["RUNTIME_VRAM_RECLAIM"] = "OLLAMA_STOPPED" if stopped else "NOT_NEEDED"
            self.meta["FREE_VRAM_AFTER_RUNTIME_RECLAIM"] = str(free)
            self.say(f"RUNTIME_VRAM_RECLAIM={self.meta['RUNTIME_VRAM_RECLAIM']} FREE_VRAM={free / GIB:.2f}GiB")
        except Exception as error:
            self.meta["RUNTIME_VRAM_RECLAIM"] = "UNAVAILABLE_OR_FAILED"
            self.meta["RUNTIME_VRAM_RECLAIM_ERROR"] = str(error).replace("\n", " ")[:400]
            self.say("RUNTIME_VRAM_RECLAIM_ERROR=" + str(error))

    def monitor_vram(self, elapsed, free):
        if (self.vram_recovery is not None and not self.vram_recovery.is_alive()
                and self.meta.get("RUNTIME_VRAM_RECLAIM") == "NOT_NEEDED"):
            self.vram_recovery = None
        if free < 8 * GIB and not self.ollama_touched and self.vram_recovery is None:
            self.meta["RUNTIME_VRAM_RECLAIM_STARTED_SECONDS"] = str(elapsed)
            self.meta["FREE_VRAM_AT_RUNTIME_RECLAIM"] = str(free)
            self.meta["RUNTIME_VRAM_RECLAIM"] = "IN_PROGRESS"
            self.say(f"RUNTIME_VRAM_BELOW_8GIB={elapsed}s FREE_VRAM={free / GIB:.2f}GiB")
            self.vram_recovery = threading.Thread(target=self.recover_runtime_vram)
            self.vram_recovery.start()
        if free < 2 * GIB and self.vram()[1] < 2 * GIB:
            raise RuntimeError("RUNTIME_VRAM_CRITICAL_ABORT")

    def clone_profile(self):
        destination = self.run / "profile/user"
        excluded = {"log", "logs", "shader", "cache", "screenshots", "captures", "temp"}
        shutil.copytree(self.original_profile, destination, symlinks=False,
                        ignore=lambda directory, names: excluded.intersection(names)
                        if Path(directory) == self.original_profile else set())
        if any(p.is_symlink() for p in destination.rglob("*")):
            raise RuntimeError("ISOLATED_PROFILE_CONTAINS_SYMLINK")
        configs = [destination / "config.json"]
        override = destination / "custom_configs/CUSA11456.json"
        if override.is_file():
            configs.append(override)
        for path in configs:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise RuntimeError("INVALID_PROFILE_CONFIG")
            for section in ("Log", "GPU", "Vulkan"):
                if not isinstance(data.setdefault(section, {}), dict):
                    raise RuntimeError("INVALID_CONFIG_SECTION=" + section)
            data["Log"].update(filter="*:warning Config:info", sync=False, skip_duplicate=True)
            data["GPU"].update(readbacks_mode=2, dump_shaders=False, patch_shaders=False)
            data["Vulkan"].update(pipeline_cache_enabled=False, pipeline_cache_archived=False,
                                   renderdoc_enabled=False, vkvalidation_enabled=False,
                                   vkvalidation_sync_enabled=False, vkvalidation_gpu_enabled=False,
                                   vkcrash_diagnostic_enabled=False, vkhost_markers=False,
                                   vkguest_markers=False)
            path.write_text(json.dumps(data, indent=2) + "\n")
        self.meta["PROFILE_PATH"] = str(destination)
        self.meta["CACHE_POLICY"] = "FRESH_ISOLATED_PROFILE"
        self.check_stop()

    def environment(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith("SHADPS4_")}
        for key in ("RADV_DEBUG", "VK_DRIVER_FILES", "VK_ICD_FILENAMES", "VK_INSTANCE_LAYERS"):
            env.pop(key, None)
        env.update(BASELINE_ENV)
        env.update({"SHADPS4_" + name: "0" for name in DIAGNOSTICS_OFF})
        env["SHADPS4_INDIRECT_ARGUMENT_SNAPSHOT_SECONDS"] = "60"
        env["SHADPS4_RESOURCE_BINDING_DIAGNOSTICS"] = "1"
        env["DISPLAY"] = self.display
        (self.run / "info/render-environment.json").write_text(json.dumps(
            {key: value for key, value in env.items() if key.startswith("SHADPS4_")}, indent=2) + "\n")
        return env

    def collect_indirect_snapshot(self):
        source = self.run / "profile/user/shader/indirect-arguments"
        if not source.is_dir() or not (source / "summary.txt").is_file():
            self.meta["INDIRECT_ARGUMENT_CAPTURE"] = "NOT_COMPLETED"
            return
        limits = {"summary.txt": 65536, "draws.csv": 4 * 1024 * 1024,
                  "submitted.bin": 1024 * 1024, "cpu.bin": 1024 * 1024}
        for name, limit in limits.items():
            path = source / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
                raise RuntimeError("INVALID_INDIRECT_SNAPSHOT_FILE=" + name)
        summary = fields((source / "summary.txt").read_text())
        if any(summary.get(key) != value for key, value in {
            "STATUS": "COMPLETE", "CAPTURE_FRAMES": "1", "SHADER_FILTERS": "NONE",
            "ARGUMENTS_CHANGED": "NO", "PIPELINE_QUERIES": "NONE",
            "SNAPSHOT_POINT": "BEFORE_DRAW",
        }.items()):
            raise RuntimeError("INVALID_INDIRECT_SNAPSHOT_SUMMARY")
        size = int(summary["BYTES_PER_BINARY"])
        if not 0 <= size <= 1024 * 1024 or any(
                (source / name).stat().st_size != size for name in ("submitted.bin", "cpu.bin")):
            raise RuntimeError("INVALID_INDIRECT_SNAPSHOT_SIZE")
        with (source / "draws.csv").open(newline="") as stream:
            records = list(csv.DictReader(stream))
        if len(records) > 4096 or len(records) != int(summary["RECORDS"]):
            raise RuntimeError("INVALID_INDIRECT_SNAPSHOT_RECORDS")
        for record in records:
            if record["status"] not in ("CAPTURED", "TRUNCATED"):
                continue
            start, length = int(record["data_offset"]), int(record["arg_bytes"])
            if start < 0 or length < 0 or start + length > size:
                raise RuntimeError("INVALID_INDIRECT_SNAPSHOT_RANGE")
            if int(record["count_guest"]):
                offset = int(record["count_data_offset"])
                if offset < 0 or offset + 4 > size:
                    raise RuntimeError("INVALID_INDIRECT_COUNT_RANGE")
        destination = self.run / "indirect-arguments"
        destination.mkdir(exist_ok=True)
        for name in limits:
            shutil.copyfile(source / name, destination / name)
        self.meta["INDIRECT_ARGUMENT_CAPTURE"] = "COMPLETE"
        self.meta["INDIRECT_ARGUMENT_COVERAGE"] = summary["COVERAGE"]
        self.meta["INDIRECT_ARGUMENT_RECORDS"] = str(len(records))
        self.meta["INDIRECT_ARGUMENT_GPU_COPIES"] = summary["GPU_COPIES"]

    def screenshot(self, elapsed, env):
        path = self.run / "screenshots" / f"{elapsed:03d}.png"
        try:
            self.command(["import", "-display", self.display, "-window", "root", str(path)],
                         timeout=6, env=env)
            if not path.is_file() or path.stat().st_size < 100 or path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
                raise RuntimeError("INVALID_SCREENSHOT")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            path.unlink(missing_ok=True)
            with (self.run / "info/screenshot-errors.log").open("a") as stream:
                stream.write(f"elapsed={elapsed} {error}\n")

    def launch(self):
        env = self.environment()
        if digest(self.args.candidate) != self.meta["CANDIDATE_SHA256"]:
            raise RuntimeError("CANDIDATE_CHANGED_AFTER_PREFLIGHT")
        self.check_stop()
        self.say("Use your controller to choose Continue and play the affected scene. No buttons or keys are sent automatically.")
        self.say(f"RUN_DURATION={self.args.seconds}s SCREENSHOTS=every_20s_max15")
        self.proc = subprocess.Popen(
            [str(self.args.candidate), "--game", str(self.game), "--fullscreen", "true", "--show-fps"],
            cwd=self.run / "profile", env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        self.meta["GAME_LAUNCHED"] = "YES"
        self.meta["STATUS"] = "GAME_RUNNING"
        self.reader = threading.Thread(target=self.log.consume, args=(self.proc.stdout,), daemon=True)
        self.reader.start()
        self.started = time.monotonic()
        next_shot = next_update = 20
        shots = 0
        with (self.run / "info/gpu-samples.csv").open("w", buffering=1) as telemetry:
            telemetry.write("elapsed_seconds,vram_used_bytes,vram_free_bytes,gpu_busy_percent\n")
            while self.proc.poll() is None:
                self.check_stop()
                elapsed = int(time.monotonic() - self.started)
                used, free = self.vram()
                try:
                    busy = int((self.gpu / "gpu_busy_percent").read_text())
                except (OSError, ValueError):
                    busy = -1
                telemetry.write(f"{elapsed},{used},{free},{busy}\n")
                if self.log.fault.is_set():
                    raise RuntimeError("GPU_FAULT_ABORT")
                self.monitor_vram(elapsed, free)
                if elapsed >= next_shot and shots < 15:
                    self.screenshot(elapsed, env)
                    shots += 1
                    next_shot += 20
                if elapsed >= next_update:
                    self.say(f"GAME_RUNNING={elapsed}s/{self.args.seconds}s FREE_VRAM={free / GIB:.2f}GiB GPU_BUSY={busy}%")
                    next_update += 20
                if elapsed >= self.args.seconds:
                    self.meta["STOP_REASON"] = "TIME_LIMIT"
                    break
                time.sleep(2)
        if self.proc.poll() is not None:
            self.meta["STOP_REASON"] = "GAME_EXITED"
            if self.proc.returncode != 0:
                raise RuntimeError("GAME_ABNORMAL_EXIT=" + str(self.proc.returncode))
        self.meta["STATUS"] = "RUNTIME_COMPLETE_VISUAL_REVIEW_REQUIRED"

    def stop_game(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            try:
                if os.getpgid(self.proc.pid) != self.proc.pid or self.proc.pid == os.getpgrp():
                    raise RuntimeError("OWNED_PROCESS_GROUP_CHECK_FAILED")
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=8)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=5)
        self.meta["GAME_EXIT"] = str(self.proc.wait(timeout=5))
        self.meta["DURATION_SECONDS"] = str(int(time.monotonic() - self.started)) if self.started else "0"
        if self.reader is not None:
            self.reader.join(timeout=5)
            if self.reader.is_alive():
                raise RuntimeError("GAME_LOG_READER_DID_NOT_FINISH")

    def restore_ollama(self):
        if self.vram_recovery is not None:
            waits = 0
            while self.vram_recovery.is_alive():
                self.vram_recovery.join(timeout=2)
                waits += 1
                if waits % 5 == 0:
                    self.say("WAITING_FOR_OLLAMA_VRAM_ACTION_BEFORE_RESTORE")
        if not self.ollama_touched:
            return
        self.say("Restoring the original Ollama container and previously loaded models.")
        running = self.command(["docker", "inspect", "-f", "{{.State.Running}}", self.ollama_id]).strip()
        if running != "true":
            self.command(["docker", "start", self.ollama_id], timeout=30)
        for _ in range(30):
            try:
                self.ollama_request("/api/ps", timeout=3)
                break
            except Exception:
                time.sleep(2)
        else:
            raise RuntimeError("OLLAMA_API_RESTORE_UNAVAILABLE")
        for model in self.models:
            name = model.get("name") or model["model"]
            request = {"model": name, "prompt": "", "stream": False, "keep_alive": "30m"}
            context = model.get("context_length")
            if isinstance(context, int) and context > 0:
                request["options"] = {"num_ctx": context}
            self.say("RELOADING_MODEL=" + name)
            self.ollama_request("/api/generate", request, timeout=180)
        after = self.ollama_request("/api/ps")
        present = {m.get("name") or m.get("model"): m for m in after.get("models", [])}
        for model in self.models:
            name = model.get("name") or model["model"]
            if name not in present:
                raise RuntimeError("OLLAMA_MODEL_RESTORE_INCOMPLETE=" + name)
            context = model.get("context_length")
            if isinstance(context, int) and context > 0 and present[name].get("context_length") != context:
                raise RuntimeError("OLLAMA_CONTEXT_RESTORE_MISMATCH=" + name)
        (self.run / "info/ollama-after.json").write_text(json.dumps(after, indent=2) + "\n")
        self.meta["OLLAMA_RESTORE"] = "PASS"

    def finish(self, code):
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, signal.SIG_IGN)
        for operation, label in ((self.stop_game, "GAME_CLEANUP"), (self.restore_ollama, "OLLAMA_RESTORE")):
            try:
                operation()
            except Exception as error:
                self.meta[label] = "FAILED"
                if code == 0:
                    self.meta["STATUS"] = label + "_FAILED"
                self.say(f"{label}_ERROR={error}")
                code = 3
        self.log.save(self.run)
        try:
            self.collect_indirect_snapshot()
        except Exception as error:
            self.meta["INDIRECT_ARGUMENT_CAPTURE"] = "INVALID"
            self.say("INDIRECT_ARGUMENT_CAPTURE_ERROR=" + str(error))
            code = max(code, 2)
        self.meta["BOUNDED_LOG_DISCARDED_BYTES"] = str(self.log.discarded)
        self.meta["SCREENSHOTS"] = str(len(list((self.run / "screenshots").glob("*.png"))))
        self.meta["GPU_READBACKS_VERIFIED"] = "PASS" if self.log.precise.is_set() else "NOT_OBSERVED"
        if self.meta["GAME_LAUNCHED"] == "YES":
            self.meta["VISUAL_RESULT"] = "REQUIRES_SCREENSHOT_AND_USER_REVIEW"
            if not self.log.precise.is_set() or self.meta["SCREENSHOTS"] == "0":
                self.meta["STATUS"] = "RUNTIME_EVIDENCE_INCOMPLETE" if code == 0 else self.meta["STATUS"]
                code = max(code, 2)
        for path, before, label in (
            (self.active, self.active_sha, "ACTIVE_BINARY_UNCHANGED"),
            (self.original_profile / "config.json", self.config_sha, "ORIGINAL_CONFIG_UNCHANGED"),
        ):
            if before is not None:
                try:
                    unchanged = digest(path) == before
                except OSError:
                    unchanged = False
                self.meta[label] = "PASS" if unchanged else "FAIL"
                if not unchanged:
                    if code == 0:
                        self.meta["STATUS"] = "POST_RUN_INTEGRITY_CHECK_FAILED"
                    code = 3
        if shutil.which("journalctl"):
            try:
                journal = self.command(["journalctl", "-k", "-b", "--since", self.started_iso,
                                        "--no-pager", "-o", "short-iso"], timeout=10)
                faults = [line for line in journal.splitlines() if re.search(
                    r"amdgpu.*(?:reset|timeout|fault)|GPUVM fault|soft lockup|MES failed", line, re.I)]
                (self.run / "info/kernel-gpu-errors.log").write_text("\n".join(faults[-200:]) + "\n")
                self.meta["KERNEL_GPU_ERRORS"] = str(len(faults))
                if faults:
                    if code == 0:
                        self.meta["STATUS"] = "KERNEL_GPU_FAULT_RECORDED"
                    code = max(code, 2)
            except Exception as error:
                self.meta["KERNEL_LOG"] = "UNAVAILABLE"
                (self.run / "info/kernel-permission.txt").write_text(str(error) + "\n")
        self.meta["EXIT"] = str(code)
        (self.run / "info/final.txt").write_text("".join(f"{key}={value}\n" for key, value in self.meta.items()))
        self.console.flush()
        with tarfile.open(self.report, "w:gz", compresslevel=6) as archive:
            for name in ("info", "screenshots", "game.log"):
                archive.add(self.run / name, arcname=name, recursive=True)
            if (self.run / "indirect-arguments").is_dir():
                archive.add(self.run / "indirect-arguments", arcname="indirect-arguments")
        self.say("STATUS=" + self.meta["STATUS"])
        self.say("OLLAMA_RESTORE=" + self.meta["OLLAMA_RESTORE"])
        self.say("INDIRECT_ARGUMENT_CAPTURE=" + self.meta["INDIRECT_ARGUMENT_CAPTURE"])
        self.say("REPORT=" + str(self.report))
        self.say("UPLOAD_THIS=" + str(self.report))
        self.say("SSH_SESSION=OPEN")
        for stream in self.locks:
            stream.close()
        self.console.close()
        return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=lambda value: Path(value).expanduser().resolve(), required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--host-report", type=lambda value: Path(value).expanduser().resolve(), required=True)
    parser.add_argument("--seconds", type=int, default=300)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_revision):
        parser.error("--source-revision must be the exact 40-character Git commit SHA")
    if not 40 <= args.seconds <= 900:
        parser.error("--seconds must be between 40 and 900")
    run = Runtime(args)
    def stop(signum, _frame):
        run.stop_signal = signum
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, stop)
    code = 0
    try:
        run.say("RUN=" + str(run.run))
        run.preflight()
        run.clone_profile()
        run.prepare_vram()
        run.launch()
    except InterruptedError as error:
        run.meta["STATUS"] = "USER_OR_SESSION_STOP"
        run.say(str(error))
        code = 128 + run.stop_signal
    except Exception as error:
        run.meta["STATUS"] = str(error).split("=", 1)[0].split(":", 1)[0].replace(" ", "_")[:100]
        run.say("ERROR=" + str(error))
        code = 2
    finally:
        code = run.finish(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
