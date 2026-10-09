#!/usr/bin/env python3
"""Launch a pinned, locally transformed one-run GoW SRT guard diagnostic.

Compiles a separate test binary with FindILsb32 correction and a temporary
per-compute-shader guard. It skips only shaders with unresolved SRT offsets.
The base collector restores all temporarily touched source files before
launching the game. No changes to the installed ES-DE binary, SSH or saves.

No general Phi workaround or permanent bypass is implied by this diagnostic.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
       "3d42bca656e0b5ba3ecd4f97ff0b43d1a305b53b/scripts/"
       "diagnose-gow-srt-findilsb32-20261009.py")
EXPECTED_BASE_BLOB = "685e4f1ae6d464f21ff69673315bf7374d59f6c2"
INFO_SHA = "75dcac52a353f68169bcd931d85e1301c5bda8e4"

def replace_once(source: str, old: str, new: str, reason: str) -> str:
    n = source.count(old)
    if n != 1:
        raise RuntimeError(f"SAFE_STOP: {reason} source anchor count is {n}, expected 1")
    return source.replace(old, new, 1)

def transform(src: str) -> str:
    patch = lambda old, new, label: replace_once(src, old, new, label)
    # Don't use lambda for modifications; source is reassigned every time.
    src = replace_once(src, 'Applications/shadps4-gow-srt-findilsb-trial-',
                       'Applications/shadps4-gow-srt-guard-trial-', 'trial path')
    src = replace_once(src, '("shadps4-gow-srt-findilsb-" + STAMP + ".tar.gz")',
                       '("shadps4-gow-srt-guard-" + STAMP + ".tar.gz")', 'archive name')
    src = replace_once(src, 'prefix=".gow-srt-findilsb-"',
                       'prefix=".gow-srt-guard-"', 'working folder')
    src = replace_once(
        src, 'SRT = SRC / "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"',
        'SRT = SRC / "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"\n'
        'INFO = SRC / "src/shader_recompiler/info.h"\n'
        'EXPECTED_INFO_SHA = "' + INFO_SHA + '"',
        'info path')
    src = replace_once(src, '    srt_text = SRT.read_text()',
                       '    srt_text = SRT.read_text()\n    info_text = INFO.read_text()',
                       'capture original info')
    src = replace_once(
        src, '    static std::atomic<u64> suppressed{0};',
        '    if (cs.gow_srt_unresolved_offsets == 0) {\n'
        '        return false; // Valid compute shaders still submit normally.\n'
        '    }\n'
        '    static std::atomic<u64> suppressed{0};',
        'selective guard')
    src = replace_once(
        src, '"GOW_GPU_COMPUTE_SUPPRESSED count={} pgm_hash={:#x} indirect={} grid={}x{}x{}",\n'
             '                    count, cs.pgm_hash, is_indirect,',
        '"GOW_GPU_COMPUTE_SUPPRESSED count={} pgm_hash={:#x} unresolved={} indirect={} grid={}x{}x{}",\n'
        '                    count, cs.pgm_hash, cs.gow_srt_unresolved_offsets, is_indirect,',
        'guard logging')

    info_patch_and_srt = r'''
    # Trial-only data flow: carry the actual SRT failure count from the shader
    # compile into Rasterizer::DispatchDirect/Indirect, rather than hard-coding
    # another shader list or defaulting every Phi to an incorrect constant.
    if info_text.count("    bool translation_failed{};") != 1:
        fail("Shader::Info declaration was changed")
    BACKUPS[str(INFO)] = INFO.read_bytes()
    (WORK / "shader-info.h.original").write_bytes(BACKUPS[str(INFO)])
    INFO.write_text(info_text.replace(
        "    bool translation_failed{};",
        "    bool translation_failed{};\n    u32 gow_srt_unresolved_offsets{};"))
    patched_srt = SRT.read_text()
    missing_src = """            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {
                LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");
                continue;
            }"""
    missing_ptr = """    if (!PushPtr(c, pass_info, off_dw)) {
        LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");
        return;
    }"""
    gen = "    GenerateSrtProgram(program.info, pass_info);"
    for key, anchor in [
        ("offset loop", missing_src), ("pointer", missing_ptr),
        ("compile end", gen), ("counter", "    u32 phi_failed_offset_count{};"),
    ]:
        if patched_srt.count(anchor) != 1:
            fail("Reviewed SRT walker anchor changed: " + key)
    SRT.write_text(
        patched_srt
        .replace("    u32 phi_failed_offset_count{};",
                 "    u32 phi_failed_offset_count{};\n    u32 gow_srt_failure_count{};")
        .replace(missing_src, missing_src.replace(
            '                LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");',
            '                ++pass_info.gow_srt_failure_count;\n'
            '                LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");'))
        .replace(missing_ptr, missing_ptr.replace(
            '        LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");',
            '        ++pass_info.gow_srt_failure_count;\n'
            '        LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");'))
        .replace(gen, gen +
            '\n    program.info.gow_srt_unresolved_offsets = pass_info.gow_srt_failure_count;\n'
            '    if (program.info.hw_stage == HwStage::Compute && pass_info.gow_srt_failure_count) {\n'
            '        LOG_WARNING(Render_Recompiler,\n'
            '                    "GOW_SRT_FLAGGED_COMPUTE hash={:#x} missing_offsets={} phi={}",\n'
            '                    program.info.pgm_hash, pass_info.gow_srt_failure_count,\n'
            '                    pass_info.phi_failed_offset_count);\n'
            '    }'))
'''
    original_diff = '''    diff = git("diff", "--", "src/core/libraries/kernel/process.cpp",
               "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
               "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp")'''
    src = replace_once(
        src, original_diff,
        info_patch_and_srt + '''    diff = git("diff", "--", "src/core/libraries/kernel/process.cpp",
               "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
               "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
               "src/shader_recompiler/info.h")''', "extra source patch")
    original_guard = '''    if blob(SRT) != EXPECTED_SRT_SHA:
        fail("SRT walker source no longer matches the reviewed shift-code bug")'''
    src = replace_once(
        src, original_guard,
        original_guard +
        '\n    if blob(INFO) != EXPECTED_INFO_SHA:\n'
        '        fail("Shader::Info source differs from verified revision")',
        "preflight source checksum")
    src = replace_once(
        src, '    env["SHADPS4_GOW_SUPPRESS_GPU_COMPUTE"] = "1"',
        '    env["SHADPS4_GOW_SUPPRESS_GPU_COMPUTE"] = "1"  # guarded shaders only',
        'runtime switch')
    src = replace_once(
        src, '"experiment": "suppress ALL Vulkan GPU compute dispatches; preserve graphics/HLE",',
        '"experiment": "skip ONLY GPU compute shaders with unresolved SRT; run valid compute and graphics",',
        'report description')
    src = replace_once(
        src, '    counts = Counter()\n    all_shader_markers = []',
        '    counts = Counter()\n    flagged_srt = []\n    all_shader_markers = []',
        'shader markers')
    src = replace_once(
        src, '''            if "GOW_GPU_COMPUTE_SUPPRESSED" in line:
                counts["compute_suppression_markers"] += 1''',
        '''            if "GOW_GPU_COMPUTE_SUPPRESSED" in line:
                counts["compute_suppression_markers"] += 1
            if "GOW_SRT_FLAGGED_COMPUTE" in line:
                counts["srt_incomplete_compute_shaders"] += 1
                if len(flagged_srt) < 1000:
                    flagged_srt.append(line.strip()[:500])''',
        'source attribution')
    src = replace_once(
        src, '    (WORK / "complete-log-error-counts.json").write_text(json.dumps(counts, indent=2))',
        '    (WORK / "complete-log-error-counts.json").write_text(json.dumps(counts, indent=2))\n'
        '    (WORK / "srt-incomplete-compute-shaders.txt").write_text("\\n".join(flagged_srt))',
        "persist attribution")
    src = replace_once(
        src, '        "suppression_log_occurrences": n_skips,',
        '        "suppression_log_occurrences": n_skips,\n'
        '        "incomplete_srt_compute_shaders": counts["srt_incomplete_compute_shaders"],',
        'structured report')
    src = replace_once(
        src, '    note("=== GOD OF WAR: CORRECT FINDILSB32 SRT OFFSET EMISSION ===")',
        '    note("=== GOD OF WAR: SKIP ONLY COMPUTE WITH INCOMPLETE SRT ===")',
        'trial title')
    src = replace_once(
        src, '    if not SRC.is_dir() or not BUILD.is_dir() or not PROCESS.is_file() or not RASTERIZER.is_file() or not SRT.is_file():',
        '    if not SRC.is_dir() or not BUILD.is_dir() or not PROCESS.is_file() or not RASTERIZER.is_file() or not SRT.is_file() or not INFO.is_file():',
        'required file')
    src = replace_once(
        src, '''    RESULT = ("FINDILSB32_USED_IN_HUNG_SHADER" if outcome["hung_shader_path_used"]
              else "FINDILSB32_UNUSED_IN_HUNG_SHADER" if outcome["hung_shader_trace_present"]
              else "SRT_SHADER_TRACE_MISSING")''',
        '''    RESULT = ("INVALID_SRT_GPU_COMPUTE_GUARDED" if outcome["gpu_compute_suppressed"]
              else "NO_INVALID_SRT_COMPUTE_DISPATCH")''',
        'result classification')
    src = replace_once(
        src, '''sys.exit(0 if RESULT in ("FINDILSB32_USED_IN_HUNG_SHADER",
                          "FINDILSB32_UNUSED_IN_HUNG_SHADER") else 1)''',
        '''sys.exit(0 if RESULT in ("INVALID_SRT_GPU_COMPUTE_GUARDED",
                          "NO_INVALID_SRT_COMPUTE_DISPATCH") else 1)''',
        'return code')
    return src

def main() -> int:
    with tempfile.TemporaryDirectory(prefix="gow-srt-guard-script-") as temp:
        baseline = Path(temp) / "pinned-original.py"
        trial = Path(temp) / "srt-guard-trial.py"
        dl = subprocess.run(["curl", "-fLsS", "--retry", "3", URL, "-o", str(baseline)],
                            check=False)
        if dl.returncode:
            raise RuntimeError("SAFE_STOP: unable to download pinned collector")
        content = baseline.read_bytes()
        blob_hash = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if blob_hash != EXPECTED_BASE_BLOB:
            raise RuntimeError("SAFE_STOP: pinned collector checksum mismatch: " + blob_hash)
        source = transform(content.decode())
        compile(source, str(trial), "exec")
        trial.write_text(source)
        if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
            print(f"WRAPPER_SELF_TEST=PASS transformed_bytes={len(source)}")
            print("VALID_COMPUTE_PRESERVED=YES; INSTALLED_ESDE_UNCHANGED=YES")
            return 0
        print("DIAGNOSTIC=INCOMPLETE_SRT_COMPUTE_ONLY", flush=True)
        print("VALID_COMPUTE_AND_GRAPHICS_REMAIN_ENABLED=YES", flush=True)
        print("MOONLIGHT_NOT_NEEDED=YES", flush=True)
        return subprocess.run([sys.executable, str(trial)], check=False).returncode

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("SAFE_STOP_WRAPPER=" + str(error), file=sys.stderr)
        sys.exit(1)
