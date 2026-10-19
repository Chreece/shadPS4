#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Audited 2026-10-19 general-engine C++ conflict reconciliation.

Never invoke a generic ours/theirs strategy. Every reviewed conflict
preview must match the SHA-256 of the actual user's uploaded 22:21 archive.
These are source-level changes, NOT runtime title/shader workarounds.
Any changed preimage, unknown C++ conflict, or broken postimage stops.
"""
from __future__ import annotations
import hashlib
import re

# Exact Git merge-file -p previews produced by the user's combined upstream
# Oct19 + generic Oct17 GPU/SRT source. Keyed by FULL relative source path.
REVIEWED = {
    "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp":
        ("1f28bdbdfca211ede82c10ada9229e24f1bd26619c6143936cc9d4585512c7ce",
         "50d6e4273ef2ce9431ae8023606d3f2cf55ba35bf2fac93e69e7ff8cffb62a7a",17),
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp":
        ("67e660d98a7e296726d27197bdadf38763f09a3b84fea5fa8e2b40e2de05de67",
         "7d764e40c2c0a373ff27c7a0981de4e58ef7f0df624440192e2253064ad5348a",7),
    "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp":
        ("131097fcdfea67e7f0fc77f063c500df4507e7088d1c7fcca16c9f22a3150778",
         "ce79b9cd2b9732d0376ccddb8c208d9d0095fe56b9bcde4523fdcfd6b81d65e5",1),
    "src/video_core/amdgpu/liverpool.cpp":
        ("0a397993cf3fb26f10ad0e49895c9ecdb3b79ba5c5c40ab1ace034dc33989f32",
         "4bde5923583ba72e61b2ed3913ad276631126fd4a5c12ec141896b85853ddbb4",1),
    "src/video_core/page_manager.cpp":
        ("96d97d60261f0f339ed8e3805527dbf19b129bc886734f92a477a326e5857982",
         "7e37797990e2a31731c89ebe7c22e99495a81219599f2b66047cbddaefad5a07",2),
    "src/video_core/renderer_vulkan/vk_instance.cpp":
        ("9109236d023e0fe9949a6e01d2de2a719fc15097c1f5e2d5eeb6132381370e93",
         "6feafc252ca444fee7a43b22f4ada213c12f084ba1a18b4f1d834367e46225ec",3),
    "src/video_core/renderer_vulkan/vk_instance.h":
        ("312d6d2b0210135d02f99a90669edfb63ae17d662e78d6250a25ba41adb8e2b2",
         "1e520689199a8a92d07dfb7fc0fb36be411218a971752b3b71592750eebc5471",2),
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":
        ("0c15cb2732e352f021e42c15b57f591df0c816699eb5bfeab9aecb11eb94b7af",
         "0c59f49623304f6bc7e9e56ff463c256dda659989532643221c3b932d8bcafa9",1),
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp":
        ("643c7c576542bfdc22a17196d5ad39a69414b62d594e02362bdd7b322fbc903f",
         "8a1ae91eeaf3b82af06461608affe4587010f5852b0fab2e80b1b3f3754b9714",5),
}
CONFLICT = re.compile(
    r"<<<<<<< current-GoW-upstream\n(.*?)=======\n(.*?)>>>>>>> generic-GoT\n",
    re.S,
)

def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()

def _pick(file: str, index: int, ours: str, theirs: str) -> str:
    if file.endswith("/flatten_extended_userdata_pass.cpp"):
        if index in (1, 3, 4, 7, 8, 13, 17):
            return theirs
        if index == 2:
            assert "srt_offset_codegen.h" in ours
            assert "srt_offset_codegen.h" in theirs
            return theirs
        if index in (5, 6):
            return ours
        if index == 9:
            # The ReadConst scope must close before other switch cases.
            # The earlier GoW branch accidentally placed ReadFirstLane inside
            # that scope; the correct case exists in the Ghost implementation.
            assert ours.count("case IR::Opcode::ReadFirstLane:") == 1
            assert theirs.strip() == "}"
            return theirs
        if index == 10:
            assert theirs.count("case IR::Opcode::ReadFirstLane:") == 1
            assert "IsHostUniformSrtOffset" in theirs
            # Keep the GoW validated FindILsb32 implementation below;
            # no duplicate switch label or divergent helper implementation.
            assert "case IR::Opcode::FindILsb32:" in theirs
            return theirs[:theirs.index("    case IR::Opcode::FindILsb32:")]
        if index in (11, 12):
            # GoW PushPtr only pushes rdi AFTER successfully evaluating the
            # dynamic offset and rewinds precisely the emitted scratch code.
            # The other branch adds an unconditional rdi push and would
            # double-push on the immediate path. Never combine both.
            return ours
        if index == 14:
            # Callback must return false to retry unresolved ReadConst uses.
            # GoW's 'continue' is invalid inside ScheduleSrtReadyReads lambda.
            assert "continue;" in ours and "return false" in theirs
            return theirs
        if index == 15:
            # Keep the graph planner, then use Ghost's fixed-point API,
            # which receives the actual ReadConst instructions.
            boundary = ours.find("static void GenerateSrtProgram(")
            assert boundary > 0 and theirs.startswith("static void GenerateSrtProgram(")
            return ours[:boundary] + theirs
        if index == 16:
            # Root order (GoW) AND fixed-point real guest descriptor recovery
            # (Ghost) are complementary, not mutually exclusive.
            assert "ScheduleSrtAcrossRoots(" in theirs
            assert "MakeSrtRootDependencyPlan" in ours
            assert theirs.count("roots[i]") == 1
            return (
                "    // Preserve GoW root dependency ordering while retaining the\n"
                "    // generic fixed-point recovery of guest descriptor reads.\n"
                "    const auto root_plan = MakeSrtRootDependencyPlan(pass_info);\n"
                + theirs.replace("roots[i]", "roots[root_plan.order[i]]")
            )
    if file.endswith("/resource_patching_pass.cpp"):
        # The revised Info-aware descriptor lookup, missing-word accounting,
        # and the constructed fetch reused for binding are all needed.
        return theirs
    if file.endswith("/shader_info_collection_pass.cpp"):
        # Both versions implement real guest DMA for unflattened ReadConst;
        # retain the prior precise explanation, behavior is unchanged.
        return ours
    if file.endswith("/liverpool.cpp"):
        assert "graphics_diagnostics.h" in ours
        return ours
    if file.endswith("/page_manager.cpp"):
        # Identical memory transition logic; only commentary conflicts.
        return ours
    if file.endswith("/vk_instance.cpp"):
        if index == 1:
            # GoW already declares sparse_queue_found above. Importing
            # Ghost's declaration here would duplicate the local variable.
            assert "bool sparse_queue_found = false;" in theirs
            return ours
        return theirs
    if file.endswith("/vk_instance.h"):
        return theirs
    if file.endswith("/vk_pipeline_serialization.cpp"):
        # New indexed descriptor formats require distinct cache versions.
        assert "ShaderBinaryVersion" in theirs and "ShaderMetaVersion" in theirs
        return theirs
    if file.endswith("/vk_rasterizer.cpp"):
        if index in (1, 4):
            return theirs
        if index == 2:
            # Preserve real Vulkan occlusion query completion AND the
            # generic draw counter. No graphics command is discarded.
            assert "occlusion->EndDraw(" in ours
            assert "CountSubmittedDraw(" in theirs
            return ours + theirs
        if index == 3:
            # Protect the proven rate-limited GPU clamp log, but keep real
            # descriptor/overflow audit, which is always opt-in.
            assert "clamp_counts" in ours and "if ((seen &" in ours
            at = theirs.index("                    if (audit) {")
            assert "clamped_buffers" in theirs and "overflow_buffers" in theirs
            return ours + "                    }\n" + theirs[at:]
        if index == 5:
            return ours
    raise ValueError("Unknown reviewed source conflict: " + file + "#" + str(index))

def _validate_semantics(file: str, merged: str):
    if file.endswith("/flatten_extended_userdata_pass.cpp"):
        for marker in (
            "MakeSrtRootDependencyPlan(pass_info)",
            "ScheduleSrtAcrossRoots(",
            "roots[root_plan.order[i]]",
            "ScheduleSrtReadyReads(",
            "IsHostUniformSrtOffset(inst->Arg(0))",
            "CanLowerSrtOffsetStructurally(",
            "EmitConstantLeftShiftByDynamicCount(",
            "EmitInlineReadConst(",
            "TraceSrtHostBarriers(",
        ):
            if marker not in merged:
                raise ValueError("SRT preservation check missing: " + marker)
        # one ReadFirstLane switch implementation with wave-uniform guard;
        # do not mistake other policy helper switch instances for duplicates.
        end = merged.index("// Evaluate genuine wave-uniform scalar")
        start = merged.rindex("static bool ComputeOffset(", 0, end)
        compute = merged[start:end]
        if compute.count("case IR::Opcode::ReadFirstLane:") != 1 or (
            compute.count("case IR::Opcode::FindILsb32:") != 1
        ):
            raise ValueError("Duplicate/missing SRT opcode switch labels")
    elif file.endswith("/vk_rasterizer.cpp"):
        for marker in (
            "occlusion->EndDraw(cmdbuf, query)",
            "CountSubmittedDraw(liverpool->regs, state, is_indexed, true)",
            "clamp_counts",
            "ResourceAuditClamp:",
            "page_manager.DrainPendingInvalidations();",
            "Rasterizer::InvalidateMemoryOnGpuThread(",
        ):
            if marker not in merged:
                raise ValueError("GPU/occlusion contract lost: " + marker)
    elif file.endswith("/vk_instance.cpp"):
        if merged.count("bool sparse_queue_found = false;") != 1:
            raise ValueError("Sparse queue variable duplicated")
    elif file.endswith("/liverpool.cpp"):
        if '#include "video_core/graphics_diagnostics.h"' not in merged:
            raise ValueError("PM4 generic diagnostics header lost")

def resolve_reviewed(file: str, preview: bytes) -> bytes | None:
    if file not in REVIEWED:
        return None
    preview_sha, expected_sha, count = REVIEWED[file]
    if digest(preview) != preview_sha:
        # Host source/branch changed; never select either side by guessing.
        return None
    text = preview.decode("utf-8")
    hits = [0]
    def sub(match):
        hits[0] += 1
        return _pick(file, hits[0], match.group(1), match.group(2))
    merged = CONFLICT.sub(sub, text)
    if hits[0] != count or any(m in merged for m in ("<<<<<<<", "=======", ">>>>>>>")):
        raise ValueError("Unresolved or unexpected C++ conflict marker")
    if digest(merged.encode()) != expected_sha:
        raise ValueError("Audited general-engine postimage differs")
    _validate_semantics(file, merged)
    return merged.encode()

def preserve_verified_srt_header(file: str, current: bytes, incoming: bytes) -> bytes | None:
    """Resolve additions only if compiled GoW source and GoT header are proven."""
    pin = {
        "src/shader_recompiler/ir/passes/srt_offset_preflight.h":
            "667744445a209b0a74197cc6ee8f28e2a1486b63",
        "src/shader_recompiler/ir/passes/srt_offset_codegen.h":
            "46ecf54cb408600570c4f84869c20b3fe7faac3d",
    }.get(file)
    if pin is None:
        return None
    blob = hashlib.sha1(
        ("blob " + str(len(incoming)) + "\0").encode() + incoming
    ).hexdigest()
    if blob != pin:
        return None
    if file.endswith("srt_offset_preflight.h"):
        if digest(current) != (
            "4b7b8a6e4422d1620c3c2910e94554bb3fa99a4e05d4ee82bb7ee9a43b88ae1b"
        ):
            return None
        if b"CanLowerSrtOffsetStructurally" not in current:
            return None
    else:
        if b"EmitConstantLeftShiftByDynamicCount" not in current:
            return None
        if b"#pragma once" not in current:
            return None
    # Keep the previously successfully compiled GoW implementation rather
    # than overwrite the header with a different historical snapshot.
    # Subsequent source/ABI changes still need their native regression tests.
    return current
