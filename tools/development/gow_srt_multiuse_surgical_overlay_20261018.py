#!/usr/bin/env python3
"""Surgical opt-in SRT duplicate-use retention for the ACTUAL protected GoW source.

The only permitted source changes: include policy, flat_map to flat_multimap,
count collision, store all consumers when enabled, log per-shader stats.
Never rewrite graphics, GPU memory, shader readback or guest audio handling.
"""
from __future__ import annotations
import argparse
import hashlib
import difflib
from pathlib import Path

SOURCE_SHA256 = "14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161"
INCLUDE_POLICY = '#include "shader_recompiler/ir/passes/srt_multiuse_policy.h"'
MAP_OLD = "boost::container::flat_map<IR::Value, Shader::IR::Inst*, PointerListCompare>"
MAP_NEW = "boost::container::flat_multimap<IR::Value, Shader::IR::Inst*, PointerListCompare>"
ASSIGN_OLD = "        user_list[inst->Arg(1)] = inst;"
ASSIGN_NEW = """        // Preserve distinct same-offset SSA ReadConst consumers only in
        // the opt-in mode; legacy keeps the exact last-wins result.
        static const bool preserve_multiuse = [] {
            const char* flag = std::getenv("SHADPS4_SRT_PRESERVE_MULTIUSE");
            return flag && flag[0] == '1' && flag[1] == '\\0';
        }();
        Optimization::InsertSrtPointerUse(user_list, inst->Arg(1), inst,
                                         preserve_multiuse, pass_info.multiuse_stats);"""
# In this function, source is already inside Shader::Optimization. The
# fully-qualified spelling is unambiguous, whereas 'Optimization::' would
# refer to Shader::Optimization::Optimization and fail compilation.
ASSIGN_NEW = ASSIGN_NEW.replace(
    "Optimization::InsertSrtPointerUse(", "InsertSrtPointerUse("
)
STATE_OLD = "    u16 dst_off_dw;"
STATE_NEW = STATE_OLD + "\n    Optimization::SrtMultiUseStats multiuse_stats{};"
REPORT_OLD = "    GenerateSrtProgram(program.info, pass_info);\n"
REPORT_NEW = REPORT_OLD + """    if (pass_info.multiuse_stats.collisions != 0) {
        LOG_WARNING(Render_Recompiler,
                    "SRT_MULTIUSE_RESULT shader={:#x} collisions={} retained={} mode={}",
                    program.info.pgm_hash, pass_info.multiuse_stats.collisions,
                    pass_info.multiuse_stats.retained,
                    pass_info.multiuse_stats.retained ? "preserve" : "legacy");
    }
"""

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def once(src: str, before: str, after: str, label: str) -> str:
    n = src.count(before)
    if n != 1:
        raise ValueError(f"SRT_MULTIUSE_{label}_ANCHOR_COUNT={n}")
    return src.replace(before, after, 1)

def change(src: str) -> str:
    if INCLUDE_POLICY in src or "SHADPS4_SRT_PRESERVE_MULTIUSE" in src:
        raise ValueError("SRT_MULTIUSE_ALREADY_PATCHED")
    result = once(src, "#include <unordered_map>",
                  "#include <cstdlib>\n#include <unordered_map>", "CSTDLIB")
    result = once(result, '#include "shader_recompiler/ir/passes/srt.h"',
                  '#include "shader_recompiler/ir/passes/srt.h"\n'+INCLUDE_POLICY,
                  "POLICY_HEADER")
    result = once(result, MAP_OLD, MAP_NEW, "MAP_MUST_BE_OLD_FLAT_MAP")
    result = once(result, STATE_OLD, STATE_NEW, "STATS_STATE")
    result = once(result, ASSIGN_OLD, ASSIGN_NEW, "SINGLE_CONSUMER_ASSIGNMENT")
    result = once(result, REPORT_OLD, REPORT_NEW, "ONE_REPORT_PER_SHADER")
    # Roundtrip checks protect *every unrelated C++ source byte*.
    restored = once(result, REPORT_NEW, REPORT_OLD, "ROUNDTRIP_REPORT")
    restored = once(restored, ASSIGN_NEW, ASSIGN_OLD, "ROUNDTRIP_ASSIGNMENT")
    restored = once(restored, STATE_NEW, STATE_OLD, "ROUNDTRIP_STATE")
    restored = once(restored, MAP_NEW, MAP_OLD, "ROUNDTRIP_MAP")
    restored = once(restored,
                    '#include "shader_recompiler/ir/passes/srt.h"\n'+INCLUDE_POLICY,
                    '#include "shader_recompiler/ir/passes/srt.h"', "ROUNDTRIP_HEADER")
    restored = once(restored, "#include <cstdlib>\n#include <unordered_map>",
                    "#include <unordered_map>", "ROUNDTRIP_CSTDLIB")
    if restored != src:
        raise ValueError("UNRELATED_PROVEN_SHADER_SOURCE_CHANGED")
    return result

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("source", type=Path)
    p.add_argument("report", type=Path)
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()
    if not a.source.is_file() or a.source.is_symlink():
        raise SystemExit("UNSAFE_SHADER_SOURCE")
    before = a.source.read_bytes()
    digest = sha(before)
    if digest != SOURCE_SHA256:
        raise SystemExit(f"PROTECTED_SRT_MULTIUSE_PREIMAGE_SHA_MISMATCH={digest}")
    try:
        result = change(before.decode("utf8")).encode("utf8")
    except Exception as ex:
        raise SystemExit(f"SRT_MULTIUSE_SOURCE_CONTRACT_FAILED={ex}")
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(
        f"PROTECTED_PREIMAGE_SHA256={digest}\n"
        f"CANDIDATE_SHA256={sha(result)}\n"
        "DEFAULT_LAST_WINS=UNCHANGED\n"
        "ONLY_SHARED_OFFSET_SRT_READS_AFFECTED_WHEN_OPTED_IN=YES\n"
        "AUDIO_GPU_COHERENCE_GRAPHICS_SOURCE=UNMODIFIED\n\n" +
        "".join(difflib.unified_diff(
            before.decode().splitlines(True),
            result.decode().splitlines(True),
            fromfile="protected-original-srt.cpp",
            tofile="srt-multiuse-opt-in.cpp"))
    )
    if not a.selftest:
        a.source.write_bytes(result)
    print("SRT_MULTIUSE_PROVEN_SOURCE_CONTRACT=PASS")
    print("SRT_MULTIUSE_SOURCE_APPLIED="+str(not a.selftest))
    print("SRT_MULTIUSE_DEFAULT_OFF=YES")

if __name__ == "__main__":
    main()
