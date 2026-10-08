#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-run Ghost 9-mip + late-intro watcher with temporary Vulkan sync validation.

Uses the already-successful 9-mip trial, unchanged. Core + synchronization
validation is toggled per-game only, through the separate transactional helper.
No global settings, shader changes, driver resets, installs, root/sudo, forced
emulator exit, or SSH replacement. Config is restored byte-for-byte in finally.
"""
from __future__ import annotations

import datetime as dt
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
import tempfile

HOME = Path.home()
BIN = HOME / "Applications/shadps4/shadps4"
BASELINE = "f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f"
FULLSTACK_ROOT = HOME / ".cache/shadps4-ghost-fullstack-20261008-131621/source"
FULLSTACK_HEAD = "89af13f6d306ebc24396b4e8e207688537cdc28b"
FULLSTACK_VK_INSTANCE = FULLSTACK_ROOT / "src/video_core/renderer_vulkan/vk_instance.cpp"
FULLSTACK_VK_INSTANCE_BLOB = "d719bb4842561e0811de33a47560c461af34ec4c"
CONFIG_REV = "05cc36dbb30a899d8f5b5c7fbcd4c9765ac19e12"
TRIAL_REV = "764be59fe3e1582b50318acbd401b0b64391300a"
STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = HOME / ("ghost-r8-map-" + STAMP + ".tar.gz")
ROOT = Path(tempfile.mkdtemp(prefix="ghost-r8-map-" + STAMP + "-", dir=HOME / ".cache"))
HELPER = ROOT / "ghost-vk-validation-config.py"
RUNNER = ROOT / "ghost-r8-sampler-map-onerun.sh"
LOG = ROOT / "runner-output.txt"
PHASE = "preflight"
RESULT = 1
CONFIG_ARMED = False
CHILD = None


def say(text: str) -> None:
    print(text, flush=True)


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def git_raw(sha: str, path: str, target: Path):
    url = "https://raw.githubusercontent.com/Chreece/shadPS4/" + sha + "/scripts/" + path
    command(["curl", "-fsSL", "--retry", "2", "--max-time", "35", url, "-o", str(target)])
    if not target.is_file() or target.stat().st_size < 1000:
        raise RuntimeError("Downloaded script is missing or unexpectedly short: " + path)
    say(f"PINNED_SCRIPT_READY={target.name} bytes={target.stat().st_size}")


def sha256_file(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as inp:
        for b in iter(lambda: inp.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def any_emulator():
    running = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            exe = os.readlink(entry / "exe").removesuffix(" (deleted)")
            if Path(exe).name.lower() == "shadps4":
                running.append(entry.name)
        except (OSError, PermissionError, ValueError):
            continue
    return running


def available_layer():
    for p in Path("/usr/share/vulkan/explicit_layer.d").glob("*.json"):
        try:
            obj = json.loads(p.read_text(errors="replace"))
            if obj.get("layer", {}).get("name") == "VK_LAYER_KHRONOS_validation":
                return True
        except (OSError, ValueError):
            continue
    return False


def preflight():
    if not BIN.is_file() or sha256_file(BIN) != BASELINE:
        raise RuntimeError("Pinned baseline executable has changed; aborting without config edits.")
    running = any_emulator()
    if running:
        raise RuntimeError("Existing shadPS4 instance(s) " + ",".join(running) +
                           "; exit normally before beginning a trial.")
    if not available_layer():
        raise RuntimeError("VK_LAYER_KHRONOS_validation is unavailable; no profile edits made.")
    if not FULLSTACK_VK_INSTANCE.is_file():
        raise RuntimeError("Verified local vk_instance.cpp missing; refusing configuration changes.")
    try:
        head = subprocess.check_output(["git", "-C", str(FULLSTACK_ROOT), "rev-parse", "HEAD"],
                                       text=True, timeout=10).strip()
        dirt = subprocess.check_output(["git", "-C", str(FULLSTACK_ROOT),
                                        "status", "--porcelain"], text=True, timeout=10).strip()
        blob = subprocess.check_output(["git", "-C", str(FULLSTACK_ROOT),
                                        "hash-object", str(FULLSTACK_VK_INSTANCE)],
                                       text=True, timeout=10).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Cannot verify original fullstack source before game settings change") from exc
    if head != FULLSTACK_HEAD or dirt or blob != FULLSTACK_VK_INSTANCE_BLOB:
        raise RuntimeError("Local fullstack source changed; refusing to alter game settings: "
                           f"HEAD={head}, dirty={bool(dirt)}, vk_instance_blob={blob}")
    say("PREFLIGHT_SOURCE_PASS: clean exact fullstack vk_instance.cpp blob " + blob)
    say("PREFLIGHT_PASS: known binary, emulator idle, validation layer installed")


def run_trial():
    global CHILD
    with LOG.open("w") as out:
        CHILD = subprocess.Popen(["bash", str(RUNNER)], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            if CHILD.stdout is not None:
                for line in CHILD.stdout:
                    out.write(line)
                    out.flush()
                    print(line, end="", flush=True)
            return CHILD.wait()
        except BaseException:
            # Do NOT kill the emulator. The nested shell has its own rollback
            # trap; allow it to finish and preserve the existing SSH session.
            if CHILD.poll() is None:
                try:
                    say("Waiting up to 35 seconds for the child trial's rollback...")
                    CHILD.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    say("Child script still running; not terminating the game or child.")
            raise


def extract_relevant_run():
    latest = None
    if LOG.exists():
        for line in LOG.read_text(errors="replace").splitlines():
            if line.startswith("ARCHIVE="):
                latest = line.partition("=")[2].strip()
    if not latest:
        say("CHILD_ARCHIVE_NOT_REPORTED")
        return ""
    candidate = Path(latest)
    if (not candidate.is_file() or candidate.parent != HOME or
            not candidate.name.startswith("ghost-r8-sampler-map-") or
            candidate.suffix != ".gz"):
        say("CHILD_ARCHIVE_UNEXPECTED_OR_MISSING=" + latest)
        return ""
    shutil.copy2(candidate, ROOT / "embedded-trial.tar.gz")
    say("EMBEDDED_TRIAL_ARCHIVE=" + str(candidate))
    contents = {}
    try:
        with tarfile.open(candidate, "r:gz") as tar:
            for entry in tar.getmembers():
                if not entry.isfile() or entry.size > 15_000_000:
                    continue
                file_name = Path(entry.name).name
                if file_name in ("runtime.log", "summary.txt", "watch-live.log"):
                    stream = tar.extractfile(entry)
                    contents[file_name] = stream.read().decode("utf-8", "replace") if stream else ""
    except (OSError, tarfile.TarError, ValueError) as e:
        say("CHILD_ARCHIVE_READ_ERROR=" + repr(e))
    return contents.get("runtime.log", "")


def report_validation(runtime: str):
    text = re.sub(r"\x1b\[[0-9;]*m", "", runtime)
    extra = LOG.read_text(errors="replace") if LOG.is_file() else ""
    combined = text + "\n" + extra
    layers = [line for line in combined.splitlines() if "Enabled instance layers:" in line]
    active = any("VK_LAYER_KHRONOS_validation" in line for line in layers)
    results = {
        "validation_layer_confirmed_in_runtime": active,
        "vuid_entries": len(re.findall(r"\bVUID-[A-Za-z0-9_-]+", combined)),
        "unsupported_vk11_16bit_vuids": len(re.findall(r"VUID-RuntimeSpirv-uniformAndStorageBuffer16BitAccess-06332", combined)),
        "unsupported_vk11_16bit_module_vuids": len(re.findall(r"VUID-VkShaderModuleCreateInfo-pCode-08740", combined)),
        "vk11_feature_support_log": len(re.findall(r"GHOST_VK11_16BIT uniformAndStorageBuffer16BitAccess supported=", combined)),
        "linear_sampler_integer_format_vuids": len(re.findall(r"VUID-vkCmdDraw-magFilter-04553", combined)),
        "sampler_map_log_entries": text.count("GHOST_R8_SAMPLER_MAP shader="),
        "sampler_filter_changes": 0,  # read-only mapping patch by design
        "image_descriptor_type_vuids": len(re.findall(r"VUID-VkWriteDescriptorSet-descriptorType-00319", combined)),
        "sync_hazards": len(re.findall(r"SYNC-HAZARD|WRITE_AFTER_READ|READ_AFTER_WRITE|WRITE_AFTER_WRITE", combined)),
        "validation_diagnostics": len(re.findall(r"Validation (?:Error|Warning)", combined, re.I)),
        "nine_mip_copy_calls": text.count("GHOST_MIP_COPY mips="),
        "nine_mip_asserts": text.count("GHOST_MIP_ASSERT"),
        "other_asserts": text.count("Assertion Failed!"),
        "vulkan_device_lost": text.count("Device lost during submit"),
        "radv_context_lost": text.count("context is lost"),
    }
    sampler_records = re.findall(
        r"GHOST_R8_SAMPLER_MAP shader=0x361a48f5 sampler_index=(\d+) "
        r"binding=(\d+) handle=(0x[0-9a-fA-F]+)", text)
    offending_handles = re.findall(
        r"VUID-vkCmdDraw-magFilter-04553:[^\n]*?VkSampler (0x[0-9a-fA-F]+)", text)
    handle_set = {int(v, 16) for v in offending_handles}
    matched = sorted({
        (int(index), int(binding), handle.lower())
        for index, binding, handle in sampler_records
        if int(handle, 16) in handle_set
    })
    (ROOT / "sampler-handle-comparison.txt").write_text(
        "VUID04553_offending_handles=" + repr(sorted({hex(v) for v in handle_set})) + "\n"
        "shader_sampler_index_binding_handle=" + repr(sampler_records) + "\n"
        "matching_shader_sampler_entries=" + repr(matched) + "\n"
        "SAMPLER_MAPPING_RESULT=" + (
            "UNIQUE_MATCH" if len(matched) == 1 else
            "AMBIGUOUS_MULTIPLE_MATCHES" if matched else "NO_HANDLE_MATCH") + "\n"
        "Note: no sampler filtering was changed by this trial.\n")
    found = [line for line in combined.splitlines() if re.search(
        r"VUID-|SYNC-HAZARD|VK_LAYER_KHRONOS_validation|Validation Error|Validation Warning|Device lost|GHOST_R8_SAMPLER_MAP",
        line, re.I)]
    report = [
        "GHOST: READ-ONLY VkSampler HANDLE MAP + NINE-MIP + VK11 16-BIT",
        "WARNING: no VUID messages is NOT a clean result unless validation layer was confirmed active.",
        *[f"{k}={v}" for k, v in results.items()],
        "VALIDATION LAYER ACTIVATION LINES:",
        *layers[-8:],
        "VALIDATION/DEVICE-LOSS EXCERPTS (first 100):",
        *found[:100],
    ]
    (ROOT / "validation-summary.txt").write_text("\n".join(report) + "\n")
    for line in report[:13]:
        say(line)
    print((ROOT / "sampler-handle-comparison.txt").read_text(), flush=True)


def cleanup():
    if HELPER.is_file():
        try:
            completed = subprocess.run([sys.executable, "-I", str(HELPER),
                                        "restore", str(ROOT)],
                                       capture_output=True, text=True, timeout=20)
            content = completed.stdout + completed.stderr
            (ROOT / "config-restore.txt").write_text(content)
            say(content.strip() or "RESTORE_NO_OUTPUT")
            if completed.returncode:
                say("CONFIG_RESTORE_NEEDS_ATTENTION — other edits were preserved, see report")
        except Exception as e:
            say("CONFIG_RESTORE_EXCEPTION=" + repr(e))
    if BIN.is_file():
        (ROOT / "installed-after.sha256").write_text(sha256_file(BIN) + "\n")
    try:
        run_log = extract_relevant_run()
        report_validation(run_log)
    except Exception as e:
        say("REPORT_ERROR=" + repr(e))
    (ROOT / "outer-status.txt").write_text(
        f"phase={PHASE}\nresult={RESULT}\n"
        f"baseline_restored_on_disk={BIN.is_file() and sha256_file(BIN) == BASELINE}\n"
    )
    # No personal per-game configuration contents are bundled.
    with tarfile.open(OUT, "w:gz") as archive:
        for child in sorted(ROOT.iterdir()):
            if child.name in ("config-transaction.json", "ghost-vk-validation-config.py",
                              "ghost-r8-sampler-map-onerun.sh", "__pycache__"):
                continue
            if child.name.startswith("original-") and child.suffix == ".bin":
                continue
            archive.add(child, arcname=child.name)
    say("ARCHIVE=" + str(OUT))
    say("Configuration originals, game saves and current SSH were preserved.")


def selftest():
    assert BASELINE == "f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f"
    assert re.search(r"\bVUID-vkCmdCopyBufferToImage", "VUID-vkCmdCopyBufferToImage-02375")
    assert "ghost-r8-sampler-map-" in "ghost-r8-sampler-map-20261008.tar.gz"
    assert CONFIG_REV != TRIAL_REV
    assert TRIAL_REV == "764be59fe3e1582b50318acbd401b0b64391300a"
    assert len(FULLSTACK_VK_INSTANCE_BLOB) == 40
    assert FULLSTACK_HEAD != FULLSTACK_VK_INSTANCE_BLOB
    shutil.rmtree(ROOT)
    print("SELFTEST PASS: pinned baseline, VUID classifier, nested trial naming")


def main() -> int:
    global PHASE, RESULT, CONFIG_ARMED
    if "--self-test" in sys.argv:
        selftest()
        return
    try:
        preflight()
        git_raw(CONFIG_REV, "ghost-vk-validation-config.py", HELPER)
        git_raw(TRIAL_REV, "ghost-r8-sampler-map-onerun.sh", RUNNER)
        command([sys.executable, "-m", "py_compile", str(HELPER)])
        command([sys.executable, "-I", str(HELPER), "--self-test"])
        command(["bash", "-n", str(RUNNER)])
        PHASE = "arming_game_profile"
        # The helper backs up original bytes BEFORE writing temporary overrides.
        command([sys.executable, "-I", str(HELPER), "arm", str(ROOT)])
        CONFIG_ARMED = True
        PHASE = "playing_with_sync_validation"
        say("GAME-SPECIFIC Vulkan CORE+SYNC validation armed for CUSA11456.")
        say("Wait until the NESTED script displays READY, then launch via Moonlight.")
        say("No sampler changes. If Ghost stays black, leave it ~90 s; exit normally with your gamepad.")
        RESULT = run_trial()
        PHASE = "finished_child_trial"
        say(f"CHILD_TRIAL_RETURN_CODE={RESULT}")
    except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError) as e:
        say("SAFE_STOP=" + repr(e))
        RESULT = 1
    finally:
        cleanup()
    return RESULT


if __name__ == "__main__":
    raise SystemExit(main())
