#!/usr/bin/env python3
"""Recover the pre-range-change baseline and manually capture two render frames.

This is an audit/recovery package, NOT a verified complete-world graphics fix.
The emulator source transformation is confined to optional diagnostic headers.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tarfile
import time

BASE = "94ed29b7c5767718ead87164558d6ec7523bcc9a"
HOST_SHA = "08cf59c7693265d1b9af7106d18e7a7a720c7e45"
RUNTIME_SHA = "006ca831bbbe4ee149d34c93f895d540dda44025"
ROOT = Path.home() / "projects/shadps4"
WORK = ROOT / "GhostOfTsushima/tests/got-upstream-menu-main-20261017-cached"
SOURCE = WORK / "source"
HEADER_HASHES = {
    "src/video_core/renderer_vulkan/vk_frame_probe.h": "9cb468194e0ede7ce608fef90efe5e3041bb735f",
    "src/video_core/renderer_vulkan/vk_frame_probe_images.h": "879277369e0a4a3296f146e83c914586f1fd6431",
}
READY_FUNCTION = r'''
// Optional manual diagnostic trigger. It never selects shaders or changes draws.
// Latch once armed; poll at most four times per second before the capture.
inline bool FrameProbeManuallyArmed() {
    static const char* path = std::getenv("SHADPS4_FRAME_PROBE_READY_FILE");
    if (!path || !*path) {
        return true;
    }
    static bool armed{};
    static std::chrono::steady_clock::time_point next_check{};
    if (armed) {
        return true;
    }
    const auto now = std::chrono::steady_clock::now();
    if (now < next_check) {
        return false;
    }
    next_check = now + std::chrono::milliseconds(250);
    std::error_code error;
    armed = std::filesystem::is_regular_file(path, error) && !error;
    return armed;
}

'''


def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError("NON_UNIQUE_SOURCE_ANCHOR=" + repr(old[:100]))
    return text.replace(old, new, 1)


def git_bytes(revision, relative):
    return subprocess.run(["git", "-C", str(SOURCE), "show", revision + ":" + relative],
                          check=True, stdout=subprocess.PIPE).stdout


def patch_headers(root, report):
    updates, manifest = {}, {"baseline": BASE, "rendering_changes": "NONE_FROM_BASELINE",
                              "range_shrink": "WITHDRAWN", "files": {}}
    for relative, expected in HEADER_HASHES.items():
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("INVALID_STAGED_HEADER=" + relative)
        data = path.read_bytes()
        if blob(data) != expected:
            raise RuntimeError("STAGED_HEADER_VERSION_MISMATCH=" + relative)
        text = data.decode()
        if relative.endswith("/vk_frame_probe.h"):
            text = once(text, '#include <cstdlib>',
                        '#include <cstdlib>\n#include <filesystem>\n#include <system_error>')
            text = once(text, 'struct FrameProbeState {', READY_FUNCTION + 'struct FrameProbeState {')
            text = once(text, '    void Present(u64 seconds) {',
                        '    void Present(u64 seconds) {\n'
                        '        if (completed == 0 && !FrameProbeManuallyArmed()) {\n'
                        '            return;\n        }')
            text = once(text, '96_MB', '12_MB')
            text = once(text, '128_MB', '16_MB')
        else:
            text = once(text, '128_MB - probe.bytes', '16_MB - probe.bytes')
        updates[relative] = text.encode()
        manifest["files"][relative] = {"original_blob": expected,
                                         "corrected_sha256": sha(updates[relative])}
    # No write until all source checks and substitutions succeed.
    report.mkdir(parents=True, exist_ok=True)
    for relative, data in updates.items():
        (root / relative).write_bytes(data)
    (report / "capture-policy.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("AUDIT_CAPTURE_HEADERS=PASS game_semantics=BASELINE_94ed29b7 image_budget=16MiB")


def source_manifest(root, destination, verify=False):
    """Hash the actual staged source, not merely the package commit label."""
    if verify:
        manifest = json.loads(destination.read_text())
        current = {str(p.relative_to(root)) for p in (root / "src").rglob("*") if p.is_file()}
        current.update(name for name in ("CMakeLists.txt", ".gitmodules") if (root / name).is_file())
        if current != {record["path"] for record in manifest["files"]}:
            raise RuntimeError("SOURCE_FILE_SET_CHANGED_DURING_BUILD")
        for record in manifest["files"]:
            path = root / record["path"]
            if path.is_symlink() != record["symlink"] or sha(path.read_bytes()) != record["sha256"]:
                raise RuntimeError("SOURCE_CHANGED_DURING_BUILD=" + record["path"])
        print("ACTUAL_SOURCE_MANIFEST_VERIFIED=PASS")
        return
    paths = sorted(p for p in (root / "src").rglob("*") if p.is_file())
    for name in ("CMakeLists.txt", ".gitmodules"):
        if (root / name).is_file():
            paths.append(root / name)
    records = [{"path": str(p.relative_to(root)), "sha256": sha(p.read_bytes()),
                "bytes": p.stat().st_size, "symlink": p.is_symlink()} for p in paths]
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    manifest = {"worktree_head": head, "baseline_overlay": BASE, "files": records,
                "scope": "src tree plus root CMakeLists and .gitmodules; not third-party libraries",
                "clean_upstream_build_claimed": False}
    destination.write_text(json.dumps(manifest, separators=(",", ":")) + "\n")
    print("ACTUAL_SOURCE_MANIFEST=PASS files=" + str(len(records)))


def prepare_host(data, script):
    if blob(data) != HOST_SHA:
        raise RuntimeError("HOST_BUILDER_VERSION_MISMATCH")
    text = data.decode()
    # The script pathname is supplied through an environment variable, avoiding shell interpolation.
    anchor = 'STATUS=HOST_TESTS\ngit -C "$SOURCE" show "$CANDIDATE:tests/shader_recompiler/test_sharp_fetch_bounds.cpp"'
    insertion = '''STATUS=PREPARING_AUDIT_CAPTURE
python3 "$SHADPS4_AUDIT_RUNNER" patch "$RUN/tmp/staged" "$RUN/info" | tee -a "$RUN/host-tests.log"
'''
    text = once(text, anchor, insertion + anchor)
    text = once(text, 'STATUS=CONFIGURING_OFFLINE',
                '''STATUS=RECORDING_ACTUAL_SOURCE
python3 "$SHADPS4_AUDIT_RUNNER" manifest "$SOURCE" "$RUN/info/actual-source-manifest.json" | tee -a "$RUN/host-tests.log"
STATUS=CONFIGURING_OFFLINE''')
    text = once(text, 'sha256sum "$BIN" | tee "$RUN/info/candidate-binary.sha256"',
                '''python3 "$SHADPS4_AUDIT_RUNNER" verify-manifest "$SOURCE" "$RUN/info/actual-source-manifest.json" | tee -a "$RUN/host-tests.log"
sha256sum "$BIN" | tee "$RUN/info/candidate-binary.sha256"''')
    subprocess.run(["bash", "-n"], input=text, text=True, check=True)
    script.write_text(text)


def load_base(path):
    if blob(path.read_bytes()) != RUNTIME_SHA:
        raise RuntimeError("RUNTIME_VERSION_MISMATCH")
    spec = importlib.util.spec_from_file_location("verified_world_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_tar(archive, name, limit=2 * 1024 * 1024):
    member = archive.getmember(name)
    if not member.isfile() or member.size > limit:
        raise RuntimeError("INVALID_METADATA=" + name)
    return archive.extractfile(member).read()


def verify_report(base, path, candidate, revision):
    base.verify_host(path, candidate, revision)
    with tarfile.open(path, "r:gz") as archive:
        policy = json.loads(read_tar(archive, "info/capture-policy.json"))
        if policy.get("baseline") != BASE or policy.get("range_shrink") != "WITHDRAWN":
            raise RuntimeError("AUDIT_POLICY_MISMATCH")
        tests = read_tar(archive, "host-tests.log").decode()
        if "ACTUAL_SOURCE_MANIFEST_VERIFIED=PASS" not in tests:
            raise RuntimeError("ACTUAL_SOURCE_NOT_VERIFIED")
        read_tar(archive, "info/actual-source-manifest.json")


def run_audit(base, candidate, revision, report):
    class Tap:
        def __init__(self, stream, runtime):
            self.stream, self.runtime, self.partial = stream, runtime, b""

        def read1(self, count):
            chunk = self.stream.read1(count)
            pieces = (self.partial + chunk).split(b"\n")
            self.partial = pieces.pop()[-65536:]
            for raw in pieces:
                line = base.ANSI.sub("", raw.decode("utf-8", "replace"))
                if "FrameProbeEnd: frame=2 " in line:
                    self.runtime.capture_ended = True
                if any(key in line for key in ("FrameProbe", "ResourceBinding", "Assertion Failed")):
                    encoded = (line[:16384] + "\n").encode()
                    if self.runtime.trace_bytes + len(encoded) <= 8 * 1024 * 1024:
                        self.runtime.trace.write(encoded)
                        self.runtime.trace_bytes += len(encoded)
                    else:
                        self.runtime.trace_truncated = True
            return chunk

    OriginalLog = base.GameLog
    OriginalRuntime = base.Runtime

    class AuditRuntime(OriginalRuntime):
        def __init__(self, args):
            super().__init__(args)
            self.ready_file = self.run / "info/capture-ready"
            self.trace = (self.run / "info/stage-capture.log").open("wb", buffering=0)
            self.trace_bytes = 0
            self.trace_truncated = False
            self.capture_ended = False
            self.capture_end_seen = False
            self.armed_at = None
            self.tty = None
            self.capture_env = None
            parent = self
            class AuditLog(OriginalLog):
                def consume(self, stream):
                    return super().consume(Tap(stream, parent))
            self.log = AuditLog()
            self.meta.update(RENDER_BASELINE=BASE, RANGE_SHRINK="WITHDRAWN",
                             SOURCE_IDENTITY="PACKAGE_PLUS_ACTUAL_SOURCE_MANIFEST",
                             EXPENSIVE_DIAGNOSTICS="TWO_MANUALLY_ARMED_FRAMES_ONLY",
                             FRAME_IMAGE_BUDGET="16MiB", INDIRECT_ARGUMENT_CAPTURE="DISABLED",
                             CAPTURE_TRIGGER="MANUAL_SSH_ENTER", SCREENSHOT_LIMIT="18")

        def preflight(self):
            super().preflight()
            with tarfile.open(self.args.host_report, "r:gz") as archive:
                for name in ("capture-policy.json", "actual-source-manifest.json"):
                    (self.run / "info" / name).write_bytes(read_tar(archive, "info/" + name))
            # A terminal is mandatory; never substitute an automatic timed capture.
            self.tty = open("/dev/tty", "r")
            self.say("MANUAL_CAPTURE: select Continue, reach the broken scene, hold the camera still, then press ENTER in THIS SSH terminal.")
            self.say("No game key is sent. Capture is limited to two frames; the run ends about 30 seconds afterward (maximum 10 minutes).")

        def clone_profile(self):
            super().clone_profile()
            profiles = [self.run / "profile/user/config.json",
                        self.run / "profile/user/custom_configs/CUSA11456.json"]
            for path in profiles:
                if not path.is_file():
                    continue
                data = json.loads(path.read_text())
                data["Log"]["filter"] = "*:warning Config:info Render.Vulkan:info"
                path.write_text(json.dumps(data, indent=2) + "\n")

        def environment(self):
            env = super().environment()
            env.pop("SHADPS4_INDIRECT_ARGUMENT_SNAPSHOT_SECONDS", None)
            env.update(SHADPS4_FRAME_PROBE="1", SHADPS4_FRAME_PROBE_IMAGES="1",
                       SHADPS4_FRAME_PROBE_FRAME_LIMIT="2",
                       SHADPS4_FRAME_PROBE_CHECKPOINTS="0,1,2",
                       SHADPS4_FRAME_PROBE_READY_FILE=str(self.ready_file))
            self.capture_env = env
            (self.run / "info/render-environment.json").write_text(json.dumps(
                {k: v for k, v in env.items() if k.startswith("SHADPS4_")}, indent=2) + "\n")
            return env

        def monitor_vram(self, elapsed, free):
            super().monitor_vram(elapsed, free)
            if self.armed_at is None and self.tty and select.select([self.tty], [], [], 0)[0]:
                line = self.tty.readline()
                if line == "":
                    raise RuntimeError("CAPTURE_TERMINAL_CLOSED")
                if elapsed < 45:
                    self.say("Capture not armed yet: wait until the broken scene is visible, then press ENTER again.")
                else:
                    # Take the reference screenshot BEFORE the readback frame perturbs timing.
                    self.screenshot(elapsed, self.capture_env)
                    self.ready_file.write_text("manual\n")
                    self.armed_at = elapsed
                    self.meta["CAPTURE_ARMED_AT_SECONDS"] = str(elapsed)
                    self.say("CAPTURE_ARMED=YES; keep the view unchanged for a few seconds.")
            if self.capture_ended and not self.capture_end_seen:
                self.capture_end_seen = True
                self.meta["CAPTURE_END_OBSERVED_SECONDS"] = str(elapsed)
                self.args.seconds = min(600, elapsed + 30)
                self.say("TWO_FRAME_CAPTURE_FINISHED; recording 30 seconds without further image readbacks.")

        def collect_indirect_snapshot(self):
            # Called by the original cleanup AFTER owned game shutdown/log-reader join.
            self.trace.close()
            if self.tty:
                self.tty.close()
            self.meta["INDIRECT_ARGUMENT_CAPTURE"] = "DISABLED"
            self.meta["STAGE_TRACE_TRUNCATED"] = str(self.trace_truncated)
            source = self.run / "profile/user/shader/frame-probes"
            destination = self.run / "info/render-targets"
            destination.mkdir(exist_ok=True)
            files = sorted(source.iterdir()) if source.is_dir() else []
            total = 0
            raw_count = 0
            records = []
            for path in files:
                if path.is_symlink() or not path.is_file():
                    raise RuntimeError("INVALID_RENDER_TARGET_CAPTURE")
                if not re.fullmatch(r"f2-p\d+-a\d+\.raw(?:\.txt)?", path.name):
                    continue
                size = path.stat().st_size
                if size > 16 * 1024 * 1024 or total + size > 20 * 1024 * 1024 or len(records) >= 4096:
                    raise RuntimeError("RENDER_TARGET_CAPTURE_BUDGET_EXCEEDED")
                total += size
                data = path.read_bytes()
                (destination / path.name).write_bytes(data)
                raw_count += path.name.endswith(".raw")
                records.append({"name": path.name, "bytes": size, "sha256": sha(data)})
            self.meta["RENDER_TARGET_FILES"] = str(raw_count)
            self.meta["RENDER_TARGET_CAPTURE_BYTES"] = str(total)
            self.meta["MANUAL_FRAME_CAPTURE"] = "COMPLETE" if self.capture_end_seen and raw_count and not self.trace_truncated else "INCOMPLETE"
            (self.run / "info/render-targets-manifest.json").write_text(json.dumps(records, indent=2) + "\n")
            if self.meta["MANUAL_FRAME_CAPTURE"] != "COMPLETE":
                self.meta["STATUS"] = "MANUAL_RENDER_TARGET_CAPTURE_INCOMPLETE"
                raise RuntimeError("MANUAL_RENDER_TARGET_CAPTURE_INCOMPLETE")

    base.Runtime = AuditRuntime
    sys.argv = [str(Path(__file__)), "--candidate", str(candidate), "--source-revision", revision,
                "--host-report", str(report), "--seconds", "600"]
    return base.main()


def build_run(revision):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("INVALID_PACKAGE_REVISION")
    scripts = ROOT / "scripts"
    logs = ROOT / "logs"
    scripts.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(SOURCE), "cat-file", "-e", revision + "^{commit}"], check=True)
    # Verify this installed launcher against the requested package as well.
    if Path(__file__).read_bytes() != git_bytes(revision, "scripts/got-world-audit-recovery-20261021.py"):
        raise RuntimeError("AUDIT_LAUNCHER_PACKAGE_MISMATCH")
    # A package commit cannot silently replace the base host/runtime scripts.
    runtime_data = git_bytes(revision, "scripts/got-world-graphics-runtime-20261021.py")
    if blob(runtime_data) != RUNTIME_SHA:
        raise RuntimeError("RUNTIME_VERSION_MISMATCH")
    ast.parse(runtime_data)
    runtime_path = scripts / ("got-audit-base-runtime-" + revision + ".py")
    runtime_path.write_bytes(runtime_data)
    base = load_base(runtime_path)
    host = scripts / ("got-audit-host-" + revision + ".sh")
    prepare_host(git_bytes(revision, "scripts/got-indexed-sample-feedback-host-20261019.sh"), host)
    candidate = WORK / ("bin-backups/shadps4-got-indexed-sample-feedback-" + revision)

    def find_report():
        if not candidate.is_file():
            return None
        for path in sorted(logs.glob("got-indexed-sample-feedback-host-*.tar.gz"),
                           key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                verify_report(base, path, candidate, revision)
                return path
            except (OSError, ValueError, KeyError, RuntimeError, tarfile.TarError):
                continue
        return None

    report = find_report()
    if report is None:
        env = dict(os.environ, SHADPS4_HOST_CANDIDATE=revision,
                   SHADPS4_HOST_BRANCH="audit/world-recovery-capture-20261021",
                   SHADPS4_AUDIT_RUNNER=str(Path(__file__).resolve()))
        subprocess.run(["bash", str(host)], env=env, check=True)
        report = find_report()
        if report is None:
            raise RuntimeError("AUDIT_HOST_BUILD_NOT_VERIFIED")
    print("VERIFIED_HOST_REPORT=" + str(report), flush=True)
    return run_audit(base, candidate, revision, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-run")
    build.add_argument("revision")
    for name in ("patch", "manifest", "verify-manifest"):
        p = sub.add_parser(name)
        p.add_argument("root", type=Path)
        p.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.command == "build-run":
        return build_run(args.revision)
    if args.command == "patch":
        patch_headers(args.root, args.destination)
    else:
        source_manifest(args.root, args.destination, args.command == "verify-manifest")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        print("AUDIT_RECOVERY_ERROR=" + str(error), file=sys.stderr)
        raise SystemExit(2)
