#!/usr/bin/env python3
"""Collect the first RADV failure from the already-built SRT candidate. No rebuild."""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path.home() / 'projects/shadps4'
PACKAGE_REV = '36980d28f9710f0d41afecd991a4d0a0edd53963'
ADAPTER_SHA256 = '0e50a958b68feb6c7b0c4579a35f440168f6fd52acc66cf8340370ccd0c4ff25'
CANDIDATE_REV = 'e5ed7be4c354a3b1c9c0cd508068ec82ebd6cf2b'
CANDIDATE_SHA256 = '5acea207b58834b8cbf8a5c7cf915362c965ba0051d6b9f17ac099768945c541'
BASE_REV = '04d891dc33bb1c8c89789b5ff039049901f95a41'
BASE_SHA256 = '9d45eba10ee27b24c0227a3a2c4775264de3389a19123cc4f3e634be003da820'
FAULT_PATTERN = (r'GPUVM fault detected|VK_ERROR_DEVICE_LOST|device[ _]lost|'
                 r'GPU hang detected|amdgpu.*ring.*timeout|'
                 r'(?:GPU )?context (?:is |was )?lost|'
                 r'The CS has been cancelled|guilty of a soft recovery')

# These functions are inserted into the verified runtime class. Their dependencies
# are imports already present in that runtime; no additional packages are needed.
ENVIRONMENT = r'''    def environment(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("SHADPS4_")}
        for key in ("RADV_DEBUG", "VK_INSTANCE_LAYERS"):
            env.pop(key, None)
        home = Path.home()
        self.driver_home = self.run / "driver-home"
        self.driver_home.mkdir(exist_ok=True)
        # RADV writes hang reports under HOME. Keep X11/audio discovery anchored
        # to the original session, but give the driver a private report/cache path.
        if not env.get("XAUTHORITY") and (home / ".Xauthority").is_file():
            env["XAUTHORITY"] = str(home / ".Xauthority")
        env.setdefault("XDG_CONFIG_HOME", str(home / ".config"))
        env.setdefault("XDG_DATA_HOME", str(home / ".local/share"))
        env["XDG_CACHE_HOME"] = str(self.driver_home / ".cache")
        env["HOME"] = str(self.driver_home)
        env["DISPLAY"] = self.display
        env["RADV_DEBUG"] = "hang,nocache,noumr"
        self.meta.update(DIAGNOSTIC_MODE="RADV_HANG_WITH_SHADER_DUMPS",
                         RENDER_STATE="UNCHANGED_EMULATOR_DRIVER_INSTRUMENTED",
                         RENDERING_FIX_CLAIMED="NO", UMR="DISABLED_NO_PRIVILEGES",
                         DRIVER_REPORT_HOME=str(self.driver_home))
        (self.run / "info/render-environment.json").write_text(json.dumps({
            k: v for k, v in env.items()
            if k.startswith(("SHADPS4_", "VK_", "RADV_", "XDG_")) or k in ("HOME", "DISPLAY", "XAUTHORITY")
        }, indent=2) + "\n")
        return env
'''

COLLECT = r'''    def collect_indirect_snapshot(self):
        self.meta["INDIRECT_ARGUMENT_CAPTURE"] = "DISABLED_IN_CLEAN_BUILD"
        for name in ("build-result.json", "correction.json", "correction.patch", "native-tests.log"):
            source = self.args.host_report.parent / name
            if source.is_file() and source.stat().st_size <= 2 * 1024 * 1024:
                shutil.copyfile(source, self.run / "info" / name)
        # Scan the saved log after the reader joined, so messages flushed at exit
        # cannot produce a false PASS even when the child exit code was zero.
        text = (self.run / "game.log").read_text(errors="replace")
        messages = [line[:8192] for line in text.splitlines() if GPU_FAULT.search(line)]
        (self.run / "info/gpu-context-errors.log").write_text("\n".join(messages[-200:]) + "\n")
        self.meta["GPU_CONTEXT_ERROR_LINES"] = str(len(messages))
        if messages:
            self.log.fault.set()
        records, total, count, reports = [], 0, 0, 0
        roots = []
        private_home = self.run / "driver-home"
        if private_home.is_dir():
            for path in sorted(private_home.glob("radv_dumps_*")):
                if path.is_dir() and not path.is_symlink():
                    roots.append((path, "radv/" + path.name))
                    reports += 1
        shaders = self.run / "profile/user/shader/dumps"
        if shaders.is_dir() and not shaders.is_symlink():
            roots.append((shaders, "shader-dumps"))
        for source, label in roots:
            for directory, dirs, names in os.walk(source, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
                for name in sorted(names):
                    path = Path(directory) / name
                    relative = label + "/" + str(path.relative_to(source))
                    if count >= 4096:
                        raise RuntimeError("TRACE_FILE_COUNT_EXCEEDED_ORIGINALS_RETAINED")
                    count += 1
                    if path.is_symlink() or not path.is_file():
                        records.append({"path": relative, "status": "NOT_A_REGULAR_FILE"})
                        continue
                    size = path.stat().st_size
                    if size > 8 * 1024 * 1024 or total + size > 64 * 1024 * 1024:
                        records.append({"path": relative, "bytes": size, "status": "OMITTED_BUDGET_ORIGINAL_RETAINED"})
                        continue
                    target = self.run / "info/driver-evidence" / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # Read a bounded snapshot; do not chase a growing file.
                    with path.open("rb") as stream:
                        data = stream.read(size)
                    target.write_bytes(data)
                    total += len(data)
                    records.append({"path": relative, "bytes": len(data),
                                    "sha256": hashlib.sha256(data).hexdigest(), "status": "COPIED"})
        (self.run / "info/driver-files.json").write_text(json.dumps(records, indent=2) + "\n")
        self.meta["DRIVER_REPORT_DIRECTORIES"] = str(reports)
        self.meta["DRIVER_EVIDENCE_BYTES"] = str(total)
        self.meta["DRIVER_EVIDENCE_LIMIT"] = "64MiB"
        self.meta["OMITTED_DRIVER_FILES"] = str(sum(r["status"] != "COPIED" for r in records))
        copies = {}
        for path in sorted((self.run / "screenshots").glob("*.png")):
            copies[path.name] = digest(path)
        (self.run / "info/screenshot-hashes.json").write_text(json.dumps(copies, indent=2) + "\n")
        self.meta["DISTINCT_SCREENSHOTS"] = str(len(set(copies.values())))
        try:
            command = ["journalctl", "-k", "-b", "--since", self.started_iso,
                       "--no-pager", "-o", "short-iso", "-n", "2000"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=8)
            (self.run / "info/kernel-visible.log").write_text(result.stdout[-2*1024*1024:])
            (self.run / "info/kernel-access.txt").write_text(
                "exit=" + str(result.returncode) + "\n" + result.stderr[-16384:])
            denied = re.search(r'not seeing messages|permission|access denied|no journal files',
                               result.stderr, re.I)
            self.meta["KERNEL_ACCESS"] = ("LIMITED_OR_UNAVAILABLE" if result.returncode or denied
                                           else "AVAILABLE_VISIBLE_ENTRIES_ONLY")
            kernel_faults = [line for line in result.stdout.splitlines() if re.search(
                r'amdgpu.*(?:reset|timeout|fault|guilty)|GPUVM fault|GPU reset|soft recovery', line, re.I)]
            (self.run / "info/kernel-gpu-errors.log").write_text("\n".join(kernel_faults) + "\n")
            self.meta["KERNEL_GPU_ERRORS"] = str(len(kernel_faults)) if not denied else "UNKNOWN_ACCESS_LIMITED"
            if kernel_faults:
                self.log.fault.set()
        except (OSError, subprocess.TimeoutExpired) as error:
            self.meta["KERNEL_ACCESS"] = "UNAVAILABLE"
            self.meta["KERNEL_GPU_ERRORS"] = "UNKNOWN"
            (self.run / "info/kernel-access.txt").write_text(str(error) + "\n")
'''


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError('TRACE_SOURCE_ANCHOR_NOT_UNIQUE=' + repr(old[:90]))
    return text.replace(old, new, 1)


def replace_assignment(text: str, name: str, expression: str) -> str:
    found = [n for n in ast.parse(text).body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    if len(found) != 1:
        raise ValueError('TRACE_ASSIGNMENT_NOT_UNIQUE=' + name)
    n = found[0]
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:n.lineno-1]) + name + ' = ' + expression + '\n' + ''.join(lines[n.end_lineno:])


def patch_prepared(text: str, adapter) -> str:
    text = replace_assignment(text, 'GPU_FAULT', 're.compile(' + repr(FAULT_PATTERN) + ', re.I)')
    text = adapter.replace_function(text, 'environment', ENVIRONMENT, True)
    text = adapter.replace_function(text, 'collect_indirect_snapshot', COLLECT, True)
    text = once(text, 'f"shadps4-srt-correction-runtime-{stamp}-{uuid.uuid4().hex[:6]}"',
                'f"shadps4-gpu-context-trace-{stamp}-{uuid.uuid4().hex[:6]}"')
    text = once(text, 'data["GPU"].update(patch_shaders=False,dump_shaders=False)',
                'data["GPU"].update(patch_shaders=False,dump_shaders=True)')
    text = once(text, '        self.started = None',
                '        self.started = None\n        self.fault_seen_at = None\n        self.trace_next_size_check = 0')
    text = once(text, '''                if self.log.fault.is_set():
                    raise RuntimeError("GPU_FAULT_ABORT")''', '''                if self.log.fault.is_set():
                    if self.fault_seen_at is None:
                        self.fault_seen_at = time.monotonic()
                        self.meta["GPU_CONTEXT_ERROR_AT_SECONDS"] = str(elapsed)
                        self.say("GPU_CONTEXT_FAILURE_DETECTED; allowing up to 20 seconds for the driver report, then stopping only this test.")
                    if time.monotonic() - self.fault_seen_at >= 20:
                        raise RuntimeError("GPU_CONTEXT_FAILURE")''')
    text = once(text, '    def monitor_vram(self, elapsed, free):\n', r'''    def monitor_vram(self, elapsed, free):
        if elapsed >= self.trace_next_size_check:
            self.trace_next_size_check = elapsed + 20
            size = 0
            for root in (self.run / "driver-home", self.run / "profile/user/shader"):
                if root.is_dir():
                    for directory, dirs, files in os.walk(root, followlinks=False):
                        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
                        for name in files:
                            path = Path(directory) / name
                            if not path.is_symlink() and path.is_file():
                                try:
                                    size += path.stat().st_size
                                except FileNotFoundError:
                                    pass
            if size > 256 * 1024 * 1024 or shutil.disk_usage(self.run).free < 1024**3:
                raise RuntimeError("DIAGNOSTIC_DISK_BUDGET_STOP")
''')
    # Avoid taking screenshots while the driver is trying to produce its report.
    text = once(text, '                if elapsed >= next_shot and shots < 15:',
                '                if elapsed >= next_shot and shots < 15 and self.fault_seen_at is None:')
    # Remove the older permissive kernel classifier; collection above records
    # visibility explicitly, including successful commands with denied journal access.
    start = text.index('        if shutil.which("journalctl"):')
    end = text.index('        self.meta["EXIT"] = str(code)', start)
    text = text[:start] + '''        if self.log.fault.is_set():
            self.meta["STATUS"] = "GPU_CONTEXT_FAILURE"
            self.meta["GPU_CONTEXT_FAILURE"] = "YES"
            code = max(code, 2)
        elif code == 0:
            self.meta["STATUS"] = "DRIVER_TRACE_COMPLETE_VISUAL_RESULT_UNVERIFIED"
        self.meta["VISUAL_RESULT"] = "NOT_VERIFIED_AS_FIXED"
''' + text[end:]
    ast.parse(text)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--seconds', type=int, default=240)
    args = parser.parse_args()
    if not 60 <= args.seconds <= 300:
        parser.error('seconds must be 60..300')
    root = args.root.resolve()
    package = root / 'scripts' / ('srt-lane-' + PACKAGE_REV)
    source = root / 'clean-rebase-verified/source'
    adapter_path = package / 'runtime_adapter.py'
    if adapter_path.is_symlink() or hashlib.sha256(adapter_path.read_bytes()).hexdigest() != ADAPTER_SHA256:
        raise RuntimeError('RUNTIME_ADAPTER_IDENTITY_MISMATCH')
    spec = importlib.util.spec_from_file_location('verified_srt_adapter', adapter_path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    raw = subprocess.check_output(['git', '-C', str(source), 'show',
                                   adapter.RUNTIME_REF + ':scripts/got-world-graphics-runtime-20261021.py'])
    text = patch_prepared(adapter.prepare_runtime(raw), adapter)
    candidate = root / f'clean-rebase-verified/candidates/{CANDIDATE_REV}-{CANDIDATE_SHA256}/shadps4'
    baseline = root / f'clean-rebase-verified/candidates/{BASE_REV}-{BASE_SHA256}/shadps4'
    # The existing verifier will also check source-tree provenance and restoration.
    report = root / 'logs/srt-lane-build-20261021-190239-457721/info/build-result.json'
    for path in (candidate, baseline, report):
        if not path.is_file():
            raise RuntimeError('REQUIRED_LOCAL_FILE_MISSING=' + str(path))
    script_hash = hashlib.sha256(text.encode()).hexdigest()
    target = root / 'scripts' / ('gpu-context-trace-runtime-' + script_hash[:16] + '.py')
    if target.exists():
        if target.is_symlink() or target.read_text() != text:
            raise RuntimeError('TRACE_SCRIPT_PATH_COLLISION')
    else:
        with target.open('x') as stream:
            stream.write(text)
    print('EMULATOR_SOURCE_AND_BINARY=UNCHANGED; no rebuild', flush=True)
    print('TRACE_SCRIPT_SHA256=' + script_hash, flush=True)
    print('RADV_HANG_DIAGNOSTIC=ON; timing/performance is not comparable with an ordinary run', flush=True)
    argv = [sys.executable, '-B', str(target), '--candidate', str(candidate),
            '--source-revision', CANDIDATE_REV, '--host-report', str(report),
            '--baseline-binary', str(baseline), '--baseline-profile', str(Path.home()/'.local/share/shadPS4'),
            '--game', '/mnt/roms-all/ps4-games/CUSA11456/eboot.bin', '--seconds', str(args.seconds)]
    # Replace this Python process only, not the enclosing SSH shell.
    os.execv(sys.executable, argv)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print('TRACE_PREPARATION_ERROR=' + str(error), file=sys.stderr)
        raise SystemExit(2)
