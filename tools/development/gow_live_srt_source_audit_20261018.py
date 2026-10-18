#!/usr/bin/env python3
"""Read-only source audit for protected GoW Ragnarök shader and audio paths.

No build, no git checkout/reset, no game launch, no Ollama API calls, no service
changes, and no interaction with SSH/Sunshine. Capture only a fixed allowlist
of source files necessary to make the next code change against the real tree.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

DEFAULT_PROJECT = Path.home() / "projects/shadps4"
PROTECTED_REL = Path(
    "GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
)
EXPECTED_PROVEN_SRT_SHA256 = (
    "14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161"
)
SOURCES = (
    "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp",
    "src/shader_recompiler/ir/passes/resource_discover_pass.cpp",
    "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp",
    "src/shader_recompiler/ir/passes/srt.h",
    "src/shader_recompiler/ir/srt_gvn_table.h",
    "src/shader_recompiler/recompiler.cpp",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
    "src/video_core/buffer_cache/buffer_cache.cpp",
    "src/video_core/buffer_cache/memory_tracker.h",
    "src/video_core/page_manager.cpp",
    "src/core/libraries/audio/audioout.cpp",
    "src/core/libraries/videoout/driver.cpp",
)

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def summarize_srt(code: str) -> dict:
    """Summarize resource walker coverage without guessing local code layout."""
    start = code.find("static bool ComputeOffset(")
    # There may be a forward declaration before its definition.
    matches = list(re.finditer(r"static bool ComputeOffset\s*\(", code))
    definitions = [
        m.start() for m in matches if (j := code.find("{", m.end())) >= 0 and
        (";" not in code[m.end():j])
    ]
    if definitions:
        start = definitions[-1]
    end = code.find("static void ", start + 1)
    if end < 0:
        end = min(len(code), start + 12000)
    body = code[start:end] if start >= 0 else ""
    cases = sorted(set(re.findall(r"case\s+IR::Opcode::([A-Za-z0-9_]+)\s*:", body)))
    features = {
        "local_srt_sha256_matched_prior_archive": None,
        "compute_offset_switch_cases": cases,
        "compute_offset_function_identified": start >= 0,
        "findilsb_codegen_present": "EmitComputeOffsetFindILsb32" in code,
        "readfirstlane_codegen_present": "ReadFirstLane" in code,
        "dynamic_shift_count_fixed": (
            "c.mov(ecx, reg);" in code and
            "c.shl(reg, inst->Arg(0).U32());\n        c.mov(ecx, reg);" not in code
        ),
        "bfe32_safe_mask_present": (
            "SrtCodegen::EmitLowBitMask" in code or
            "c.jae(full_width)" in code
        ),
        "failure_reports_opcode": (
            "Unexpected instruction for offset computation" in body
        ),
        "visit_pointer_failure_marker": (
            "Failed to compute offset for SRT walker" in code
        ),
    }
    return features

def git_info(source: Path) -> dict:
    def cmd(*args):
        try:
            p = subprocess.run(
                ["git", "-C", str(source), *args],
                stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=6, check=False,
            )
            return p.stdout.strip() if p.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    return {
        "head": cmd("rev-parse", "HEAD"),
        "branch": cmd("branch", "--show-current"),
        "tracked_source_modifications": cmd(
            "diff", "--name-only", "--", "src"
        ),
        "staged_source_modifications": cmd(
            "diff", "--cached", "--name-only", "--", "src"
        ),
    }

def execute(project: Path) -> Path:
    source = project / PROTECTED_REL
    if not (source / ".git").exists():
        raise RuntimeError(f"PROVEN_SOURCE_GIT_WORKTREE_NOT_FOUND={source}")
    logs = project / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    tag = "gow-live-source-audit-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    out = logs / tag
    out.mkdir(exist_ok=False)
    copied = []
    errors = []
    for rel in SOURCES:
        path = source / rel
        if not path.is_file() or path.is_symlink():
            errors.append({"path": rel, "reason": "MISSING_OR_SYMLINK"})
            continue
        if path.stat().st_size > 2_000_000:
            errors.append({"path": rel, "reason": "ABOVE_PER_FILE_2MB_LIMIT"})
            continue
        data = path.read_bytes()
        dest = out / "source" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        copied.append({"path": rel, "bytes": len(data), "sha256": sha256(data)})
    local_shader = source / SOURCES[0]
    srt_info = {}
    if local_shader.is_file() and not local_shader.is_symlink():
        source_data = local_shader.read_bytes()
        srt_info = summarize_srt(source_data.decode("utf-8", errors="replace"))
        srt_info["local_srt_sha256_matched_prior_archive"] = (
            sha256(source_data) == EXPECTED_PROVEN_SRT_SHA256
        )
    record = {
        "task": "GoW Ragnarök exact live source verification",
        "captured_at": datetime.now().astimezone().isoformat(),
        "protected_source": str(source),
        "source_files": copied,
        "missing_files": errors,
        "shader_analysis": srt_info,
        "git": git_info(source),
        "no_compile": True,
        "no_game_launch": True,
        "ollama_untouched": True,
        "x11_sunshine_ssh_unchanged": True,
    }
    (out / "manifest.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    )
    archive = logs / (tag + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(out, arcname=out.name)
    print("LOCAL_SRT_SOURCE_SHA256_MATCHES_PRIOR=" +
          str(srt_info.get("local_srt_sha256_matched_prior_archive")))
    print("CAPTURED_SOURCE_FILES=" + str(len(copied)))
    print("UNAVAILABLE_SOURCE_FILES=" + str(len(errors)))
    print("SRT_SUPPORTED_COMPUTEOFFSET_OPCODES=" +
          ",".join(srt_info.get("compute_offset_switch_cases", [])))
    print("NO_BUILD=YES OLLAMA_UNTOUCHED=YES SSH_OPEN=YES")
    print("ARCHIVE=" + str(archive))
    return archive

def main():
    args = argparse.ArgumentParser()
    args.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    args.add_argument("--selftest", action="store_true")
    opt = args.parse_args()
    if opt.selftest:
        fixture = (
            "static bool ComputeOffset(const IR::Value& x) {\n"
            "  switch(0) {case IR::Opcode::FindILsb32: return true; "
            "case IR::Opcode::IAdd32: return true;}\n}\n"
            "Failed to compute offset for SRT walker\n"
        )
        f = summarize_srt(fixture)
        assert f["compute_offset_switch_cases"] == ["FindILsb32", "IAdd32"]
        assert f["compute_offset_function_identified"]
        assert f["visit_pointer_failure_marker"]
        print("LIVE_SRT_AUDIT_SELFTEST=PASS")
        return
    execute(opt.project_root)

if __name__ == "__main__":
    main()
