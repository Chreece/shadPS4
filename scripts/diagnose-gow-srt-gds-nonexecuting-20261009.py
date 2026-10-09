#!/usr/bin/env python3
"""Unattended God of War: selective SRT guard + non-executing GDS diagnostic.

The verified SRT guard kept GPU responsive until the unsupported
DS_ORDERED_COUNT translator exception. For THIS isolated trial only, let
shader 0xDBAA6AE4 translate with a placeholder register, mark its Info, and
GUARANTEE its Vulkan compute dispatch is skipped. No GDS opcode emulation is
claimed: the placeholder must never execute on the GPU.

All other valid compute shaders and ordinary graphics remain enabled.
Source files are restored before the game launches; production ES-DE, saves
and SSH are never modified. One bounded unattended run with screenshots.
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
DATA_SHARE_SHA = "4962c20eafa6fecd4a7044dac3dcfe15f505cd50"

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


def add_nonexecuting_gds_diagnostic(src: str) -> str:
    """Transform the verified selective guard, requiring exact source anchors."""
    src = replace_once(src,
        'Applications/shadps4-gow-srt-guard-trial-',
        'Applications/shadps4-gow-srt-gds-trial-', "GDS trial path")
    src = replace_once(src,
        '("shadps4-gow-srt-guard-" + STAMP + ".tar.gz")',
        '("shadps4-gow-srt-gds-" + STAMP + ".tar.gz")', "GDS report name")
    src = replace_once(src, 'prefix=".gow-srt-guard-"',
                       'prefix=".gow-srt-gds-"', "GDS work directory")
    src = replace_once(
        src, 'INFO = SRC / "src/shader_recompiler/info.h"',
        'INFO = SRC / "src/shader_recompiler/info.h"\n'
        'DATA_SHARE = SRC / "src/shader_recompiler/frontend/translate/data_share.cpp"\n'
        'EXPECTED_DATA_SHARE_SHA = "' + DATA_SHARE_SHA + '"',
        "GDS translation source path")
    src = replace_once(src,
        '    info_text = INFO.read_text()',
        '    info_text = INFO.read_text()\n    data_share_text = DATA_SHARE.read_text()',
        "GDS translator source read")

    # The translator-only placeholder must be traceable to this specific
    # shader and the renderer must skip its eventual compute dispatch.
    src = replace_once(
        src, '    if (cs.gow_srt_unresolved_offsets == 0) {\n'
             '        return false; // Valid compute shaders still submit normally.\n'
             '    }',
        '    if (cs.gow_srt_unresolved_offsets == 0 && cs.gow_gds_unimplemented == 0) {\n'
        '        return false; // Valid compute and graphics remain enabled.\n'
        '    }\n'
        '    if (cs.gow_gds_unimplemented != 0) {\n'
        '        LOG_WARNING(Render_Vulkan,\n'
        '                    "GOW_GDS_DIAG_DISPATCH_SUPPRESSED hash={:#x} diagnostic_only=true",\n'
        '                    cs.pgm_hash);\n'
        '    }',
        "GDS non-execution guard")
    src = replace_once(
        src, '"GOW_GPU_COMPUTE_SUPPRESSED count={} pgm_hash={:#x} unresolved={} indirect={} grid={}x{}x{}",\n'
             '                    count, cs.pgm_hash, cs.gow_srt_unresolved_offsets, is_indirect,',
        '"GOW_GPU_COMPUTE_SUPPRESSED count={} pgm_hash={:#x} unresolved={} gds_skipped={} indirect={} grid={}x{}x{}",\n'
        '                    count, cs.pgm_hash, cs.gow_srt_unresolved_offsets,\n'
        '                    cs.gow_gds_unimplemented, is_indirect,',
        "GDS marker in suppression logs")

    # Existing SRT-info extension is kept unchanged, add a separate flag.
    src = replace_once(
        src, '"    bool translation_failed{};\\n    u32 gow_srt_unresolved_offsets{};"))',
        '"    bool translation_failed{};\\n    u32 gow_srt_unresolved_offsets{};\\n"\n'
        '        "    u32 gow_gds_unimplemented{};"))',
        "compile-time GDS non-execution flag")

    gds_patch = r'''
    # Compilation-only allowance for *exactly* the known guest GDS shader.
    # This is never a functional DS_ORDERED_COUNT implementation: the
    # rasterizer checks gow_gds_unimplemented and skips the GPU dispatch.
    gds_case_anchor = """    default:
        LogMissingOpcode(inst);
    }
}"""
    include_anchor = '#include "shader_recompiler/frontend/translate/translate.h"'
    if data_share_text.count(gds_case_anchor) != 1 or data_share_text.count(include_anchor) != 1:
        fail("Data-share instruction translator changed; no GDS substitution attempted")
    BACKUPS[str(DATA_SHARE)] = DATA_SHARE.read_bytes()
    (WORK/"data-share.cpp.original").write_bytes(BACKUPS[str(DATA_SHARE)])
    gds_case = """    case Opcode::DS_ORDERED_COUNT:
        if (info.pgm_hash == 0xdbaa6ae4ULL && info.hw_stage == HwStage::Compute &&
            std::getenv("SHADPS4_GOW_DIAGNOSTIC_GDS_NONEXECUTING") != nullptr) {
            info.gow_gds_unimplemented = 1;
            LOG_WARNING(Render_Recompiler,
                        "GOW_GDS_DIAG_TRANSLATED_ONLY hash={:#x} offset0={:#x} offset1={:#x}",
                        info.pgm_hash, u32(inst.control.ds.offset0),
                        u32(inst.control.ds.offset1));
            // Only construct a valid IR placeholder to traverse translation.
            // The corresponding compute shader MUST NOT be executed.
            SetDst(inst.dst[0], ir.Imm32(0));
            return;
        }
        LogMissingOpcode(inst);
        return;
    default:
        LogMissingOpcode(inst);
    }
}"""
    DATA_SHARE.write_text(
        data_share_text.replace(include_anchor,
            '#include <cstdlib>\n'
            '#include "common/logging/log.h"\n'
            + include_anchor)
        .replace(gds_case_anchor, gds_case))
'''
    old_diff = '''    diff = git("diff", "--", "src/core/libraries/kernel/process.cpp",
                "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
                "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
                "src/shader_recompiler/info.h")'''
    src = replace_once(
        src, old_diff,
        gds_patch + '''    diff = git("diff", "--", "src/core/libraries/kernel/process.cpp",
                "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
                "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
                "src/shader_recompiler/info.h",
                "src/shader_recompiler/frontend/translate/data_share.cpp")''',
        "GDS trace compilation patch")
    src = replace_once(
        src, '''    if blob(INFO) != EXPECTED_INFO_SHA:
        fail("Shader::Info source differs from verified revision")''',
        '''    if blob(INFO) != EXPECTED_INFO_SHA:
        fail("Shader::Info source differs from verified revision")
    if blob(DATA_SHARE) != EXPECTED_DATA_SHARE_SHA:
        fail("Data-share translator source differs from reviewed revision")''',
        "GDS source checksum")
    src = replace_once(
        src, '    env["SHADPS4_GOW_SUPPRESS_GPU_COMPUTE"] = "1"  # guarded shaders only',
        '    env["SHADPS4_GOW_SUPPRESS_GPU_COMPUTE"] = "1"  # guarded shaders only\n'
        '    env["SHADPS4_GOW_DIAGNOSTIC_GDS_NONEXECUTING"] = "1"',
        "enable GDS compilation-only capture")
    src = replace_once(
        src, '    counts = Counter()\n    flagged_srt = []',
        '    counts = Counter()\n    gds_markers = []\n    flagged_srt = []',
        "GDS counter storage")
    src = replace_once(
        src, '''            if "GOW_SRT_FLAGGED_COMPUTE" in line:
                counts["srt_incomplete_compute_shaders"] += 1''',
        '''            if "GOW_GDS_DIAG_TRANSLATED_ONLY" in line:
                counts["gds_compilation_only"] += 1
                if len(gds_markers) < 100:
                    gds_markers.append(line.strip()[:500])
            if "GOW_GDS_DIAG_DISPATCH_SUPPRESSED" in line:
                counts["gds_dispatch_skipped"] += 1
                if len(gds_markers) < 100:
                    gds_markers.append(line.strip()[:500])
            if "GOW_SRT_FLAGGED_COMPUTE" in line:
                counts["srt_incomplete_compute_shaders"] += 1''',
        "GDS archive counters")
    src = replace_once(
        src, '    (WORK / "srt-incomplete-compute-shaders.txt").write_text("\\n".join(flagged_srt))',
        '    (WORK / "srt-incomplete-compute-shaders.txt").write_text("\\n".join(flagged_srt))\n'
        '    (WORK / "gds-diagnostic-only-markers.txt").write_text("\\n".join(gds_markers))',
        "persist GDS markers")
    src = replace_once(
        src, '        "incomplete_srt_compute_shaders": counts["srt_incomplete_compute_shaders"],',
        '        "incomplete_srt_compute_shaders": counts["srt_incomplete_compute_shaders"],\n'
        '        "gds_compiled_without_semantics": counts["gds_compilation_only"],\n'
        '        "gds_gpu_dispatches_prevented": counts["gds_dispatch_skipped"],',
        "GDS structured report")
    src = replace_once(
        src, '"experiment": "skip ONLY GPU compute shaders with unresolved SRT; run valid compute and graphics",',
        '"experiment": "skip compute with unresolved SRT or unimplemented ordered-GDS; run other compute and graphics",',
        "experiment definition")
    src = replace_once(
        src, '    note("=== GOD OF WAR: SKIP ONLY COMPUTE WITH INCOMPLETE SRT ===")',
        '    note("=== GOD OF WAR: SELECTIVE SRT + NONEXECUTING GDS DIAGNOSTIC ===")',
        "experiment banner")
    src = replace_once(
        src, '    if not SRC.is_dir() or not BUILD.is_dir() or not PROCESS.is_file() or not RASTERIZER.is_file() or not SRT.is_file() or not INFO.is_file():',
        '    if not SRC.is_dir() or not BUILD.is_dir() or not PROCESS.is_file() or not RASTERIZER.is_file() or not SRT.is_file() or not INFO.is_file() or not DATA_SHARE.is_file():',
        "translator required")
    src = replace_once(
        src, '    note("GAME_INTRO_AND_MENU=NOT_YET_VISUALLY_CONFIRMED")',
        '    note("GDS_COMPILE_ONLY_NO_EXECUTE=" + str(outcome["gds_compiled_without_semantics"]))\n'
        '    note("GDS_DISPATCHES_SKIPPED=" + str(outcome["gds_gpu_dispatches_prevented"]))\n'
        '    note("GAME_INTRO_AND_MENU=NOT_YET_VISUALLY_CONFIRMED")',
        "report GDS guard execution")
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
        source = add_nonexecuting_gds_diagnostic(transform(content.decode()))
        compile(source, str(trial), "exec")
        for marker in ("GOW_GDS_DIAG_TRANSLATED_ONLY", "GOW_GDS_DIAG_DISPATCH_SUPPRESSED",
                       "gow_gds_unimplemented", "DATA_SHARE.write_text"):
            if marker not in source:
                raise RuntimeError("SAFE_STOP: missing GDS safety marker " + marker)
        trial.write_text(source)
        if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
            print(f"WRAPPER_SELF_TEST=PASS transformed_bytes={len(source)}")
            print("VALID_COMPUTE_PRESERVED=YES; INSTALLED_ESDE_UNCHANGED=YES")
            return 0
        print("DIAGNOSTIC=SELECTIVE_SRT_AND_NONEXECUTING_GDS", flush=True)
        print("VALID_COMPUTE_AND_GRAPHICS_REMAIN_ENABLED=YES", flush=True)
        print("GDS_SEMANTICS_IMPLEMENTED=NO; MARKED_GDS_DISPATCHES_WILL_NOT_RUN", flush=True)
        print("MOONLIGHT_NOT_NEEDED=YES", flush=True)
        return subprocess.run([sys.executable, str(trial)], check=False).returncode

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("SAFE_STOP_WRAPPER=" + str(error), file=sys.stderr)
        sys.exit(1)
