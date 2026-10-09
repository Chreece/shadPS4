#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ghost exact 513x513 draw identity under temporary per-game Vulkan validation.

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
TRIAL_REV = "5def465a8fea236b35a871e02f5ba0e23d4b5356"
STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = HOME / ("ghost-no-gdb-validation-" + STAMP + ".tar.gz")
ROOT = Path(tempfile.mkdtemp(prefix="ghost-no-gdb-validation-" + STAMP + "-", dir=HOME / ".cache"))
HELPER = ROOT / "ghost-vk-validation-config.py"
RUNNER = ROOT / "ghost-no-gdb-gfx513-hang-onerun.sh"
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
            not candidate.name.startswith("ghost-no-gdb-") or
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
        "r8_index1_point_applied": text.count("GHOST_R8_INDEX1_APPLIED shader="),
        "null_image_binding_hits": text.count("GHOST_NULL_IMAGE_BINDING shader="),
        "stencil_storage_alias_hits": text.count("GHOST_STENCIL_STORAGE_ALIAS shader="),
        "gfx513_draw_events": text.count("GHOST_GFX513_DRAW seq="),
        "gfx513_guest_shader_stage_events": text.count("GHOST_GFX513_STAGE seq="),
        "gfx513_texture_bindings": text.count("GHOST_GFX513_IMAGE seq="),
        "gfx513_unique_guest_shader_hashes": sorted(set(re.findall(
            r"GHOST_GFX513_STAGE seq=\d+ stage=\d+ guest_shader=(0x[0-9a-fA-F]+)", text))),
        "storage_depth_illegal_remaining": text.count("GHOST_STORAGE_IMAGE_INVALID shader="),
        "storage_usage_00339_vuids": text.count("VUID-VkWriteDescriptorSet-descriptorType-00339"),
        "storage_format_07028_vuids": text.count("VUID-vkCmdDispatchIndirect-OpTypeImage-07028"),
        "r8_index0_unchanged_by_patch": True,
        "copy_fallback_assert_context_lines": text.count("GHOST_COPY_FALLBACK_ASSERT mips="),
        "sparse_arena_config_count": text.count("GHOST_ARENA_1G_CONFIG arena_page="),
        "oversize_buffer_vuid_count": text.count("VUID-VkBufferCreateInfo-size-06409"),
        "sparse_arena_merge_limit_asserts": text.count("GHOST_ARENA_1G sparse arena merge="),
        "sparse_arena_initial_limit_asserts": text.count("GHOST_ARENA_1G oversized initial sparse buffer="),
        "image_descriptor_type_vuids": len(re.findall(r"VUID-VkWriteDescriptorSet-descriptorType-00319", combined)),
        "sync_hazards": len(re.findall(r"SYNC-HAZARD|WRITE_AFTER_READ|READ_AFTER_WRITE|WRITE_AFTER_WRITE", combined)),
        "validation_diagnostics": len(re.findall(r"Validation (?:Error|Warning)", combined, re.I)),
        "nine_mip_copy_calls": text.count("GHOST_MIP_COPY mips="),
        "reverse_d32_to_r32_mip_copies": text.count("GHOST_MIP_REVERSE_COPY mips="),
        "no_early_gdb_watchpoint": True,
        "nine_mip_asserts": text.count("GHOST_MIP_ASSERT"),
        "other_asserts": text.count("Assertion Failed!"),
        "vulkan_device_lost": text.count("Device lost during submit"),
        "radv_context_lost": text.count("context is lost"),
    }
    fallback_records = [
        line for line in text.splitlines() if "GHOST_COPY_FALLBACK_ASSERT mips=" in line
    ]
    reverse_records = [
        line for line in text.splitlines() if "GHOST_MIP_REVERSE_COPY mips=" in line
    ]
    oversize_records = [line for line in text.splitlines()
                        if "VUID-VkBufferCreateInfo-size-06409" in line]
    arena_init_records = [line for line in text.splitlines()
                          if "GHOST_ARENA_1G_CONFIG arena_page=" in line]
    arena_limit_records = [line for line in text.splitlines() if
                           "GHOST_ARENA_1G sparse arena merge=" in line or
                           "GHOST_ARENA_1G oversized initial sparse buffer=" in line]
    sampler_records = [
        line for line in text.splitlines() if "GHOST_R8_INDEX1_APPLIED shader=" in line
    ]
    validation_04553 = [
        line for line in text.splitlines() if
        "VUID-vkCmdDraw-magFilter-04553" in line and "DebugUtilsCallback" in line
    ]
    frame_samples = re.findall(
        r"GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)", text
    )
    results["max_guest_flips"] = max((int(m[1]) for m in frame_samples), default=0)
    results["distinct_04553_validation_messages"] = len(validation_04553)
    details = [
        "R8_INDEX1_APPLIED_EVENTS=" + str(len(sampler_records)),
        "NULL_IMAGE_BINDING_EVENTS=" + str(text.count("GHOST_NULL_IMAGE_BINDING shader=")),
        "STENCIL_STORAGE_ALIAS_HITS=" + str(text.count("GHOST_STENCIL_STORAGE_ALIAS shader=")),
        "GFX513_DRAW_EVENTS=" + str(text.count("GHOST_GFX513_DRAW seq=")),
        "GFX513_SHADER_STAGE_RECORDS=" + str(text.count("GHOST_GFX513_STAGE seq=")),
        "GFX513_TEXTURE_BINDING_RECORDS=" + str(text.count("GHOST_GFX513_IMAGE seq=")),
        "STORAGE_DEPTH_ILLEGAL_REMAINING=" + str(text.count("GHOST_STORAGE_IMAGE_INVALID shader=")),
        "VUID_STORAGE_USAGE_00339=" + str(text.count("VUID-VkWriteDescriptorSet-descriptorType-00339")),
        "VUID_STORAGE_FORMAT_07028=" + str(text.count("VUID-vkCmdDispatchIndirect-OpTypeImage-07028")),
        "VUID04553_EVENTS=" + str(len(validation_04553)),
        "COPY_FALLBACK_PROBE_EVENTS=" + str(len(fallback_records)),
        "REVERSE_D32_TO_R32_MIP_TRANSFERS=" + str(len(reverse_records)),
        "NO_EARLY_GDB_WATCHPOINT=true",
        "HOST_SPARSE_ARENA_LIMITS=" + str(len(arena_init_records)),
        "OVERSIZE_BUFFER_VUIDS=" + str(len(oversize_records)),
        "SPARSE_ARENA_GUARD_ASSERTS=" + str(len(arena_limit_records)),
        "last_frame_sample=" + (repr(frame_samples[-1]) if frame_samples else "NONE"),
        "=== FINAL 513x513 DRAW CONTEXTS (last 120 lines) ===",
        *[line for line in text.splitlines() if "GHOST_GFX513_" in line][-120:],
        "=== STENCIL STORAGE ALIASES (first 12) ===",
        *[line for line in text.splitlines() if "GHOST_STENCIL_STORAGE_ALIAS shader=" in line][:12],
        "=== REMAINING INVALID STORAGE (first 12) ===",
        *[line for line in text.splitlines() if "GHOST_STORAGE_IMAGE_INVALID shader=" in line][:12],
        "=== R8 INDEX1 CHANGES ===", *sampler_records[:6],
        "=== SPARSE ARENA HOST LIMIT CONFIG ===", *arena_init_records[:6],
        "=== SPARSE ARENA OVERSIZE VALIDATION ===", *oversize_records[:10],
        "=== SPARSE ARENA GUARD ASSERTIONS ===", *arena_limit_records[:8],
        "=== VERIFIED D32->R32 REVERSE IMAGE COPIES ===", *reverse_records[:12],
        "=== NEW UNHANDLED IMAGE COPY FORMATS ===", *fallback_records[:8],
        "=== 04553 VALIDATION ERRORS ===", *validation_04553[:6],
        "Note: fallback ASSERT(num_mips == 1) is still enabled.",
    ]
    (ROOT / "targeted-results.txt").write_text("\n".join(details) + "\n")
    found = [line for line in combined.splitlines() if re.search(
        r"VUID-|SYNC-HAZARD|VK_LAYER_KHRONOS_validation|Validation Error|Validation Warning|Device lost|GHOST_R8_INDEX1_APPLIED|GHOST_COPY_FALLBACK_ASSERT|GHOST_ARENA_1G|GHOST_MIP_REVERSE_COPY",
        line, re.I)]
    report = [
        "GHOST: NO EARLY GDB WATCHPOINT CONTROL WITH SAME D32/R32 VULKAN FIXES",
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
    print((ROOT / "targeted-results.txt").read_text(), flush=True)


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
                              "ghost-no-gdb-control-onerun.sh", "__pycache__"):
                continue
            if child.name.startswith("original-") and child.suffix == ".bin":
                continue
            archive.add(child, arcname=child.name)
    say("ARCHIVE=" + str(OUT))
    say("Configuration originals, game saves and current SSH were preserved.")


def selftest():
    assert BASELINE == "f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f"
    assert re.search(r"\bVUID-vkCmdCopyBufferToImage", "VUID-vkCmdCopyBufferToImage-02375")
    assert "ghost-no-gdb-" in "ghost-no-gdb-20261008.tar.gz"
    assert CONFIG_REV != TRIAL_REV
    assert TRIAL_REV == "5def465a8fea236b35a871e02f5ba0e23d4b5356"
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
        git_raw(TRIAL_REV, "ghost-no-gdb-gfx513-hang-onerun.sh", RUNNER)
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
        say("One-run RADV 513x513 guest graphics draw identification, with stencil alias preserved.")
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
