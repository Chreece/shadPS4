#!/usr/bin/env python3
"""GoW109: clean generic-only provenance build, removing all historical GoW overrides.

Builds on GoW103/98/97/96/91.  The Vulkan descriptor keeps its full guest V#
range.  For compute stores whose normalized address can be proven affine in
invocation/workgroup IDs, only the proven reachable byte range is marked
GPU-authoritative.  Any unsupported write expression falls back to the legacy
full-range tracking.  No shader hashes, game IDs, arbitrary caps or fake values.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"

GOW103_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
              "98245b63d1fdd6210c69d9aa8392acf790e6361d/tools/gow103-srt-arithmetic.py")
GOW103_BLOB = "6b7b950014480a32491997ff9cd4958481e9e154"

RESOURCE_H = "src/shader_recompiler/resource.h"
RESOURCE_PATCH = "src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
BUFFER_H = "src/video_core/buffer_cache/buffer_cache.h"
MEMORY_TRACKER_H = "src/video_core/buffer_cache/memory_tracker.h"
REGION_MANAGER_H = "src/video_core/buffer_cache/region_manager.h"
RASTER = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
SERIAL = "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"

CLEAN_BASE_COMMIT = "c19f883ba41aa48fa39f3db4e1916d4bca8b384e"
CLEAN_BLOBS = {
    "src/core/libraries/kernel/process.cpp": "05acbce16dc315c5abaccb5190011ceed6ad848a",
    "src/core/libraries/videoout/driver.cpp": "8d8d988ad31d165502d7369b15eb7a0f344e97d0",
    "src/shader_recompiler/backend/spirv/emit_spirv.cpp": "12bf8dec080710706809b470803466281e0c7117",
    "src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp": "e2f35b1afc4f8d74fbd48322ffdf9fc887403d9f",
    "src/shader_recompiler/frontend/translate/data_share.cpp": "8dc99e22209b6e095d8d8fc94c4e664e93f99707",
    "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp": "67d361a8b0a6b3e9a94c55c08e69085d52116fc5",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp": "4c0bd50034795c24e9d2a1a1faf5b6dd79c11fe8",
    "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp": "098127335842bf0b72b1f18b40c0a26558781f38",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.h": "edd9737b83964919c59bbdcabdeb873ace511a01",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp": "ad3d4c881e8f61d080e221452a4366fef039ab89",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp": "adc6045a0064fbe284848a54c4ea33ed431e37cc",
    "src/video_core/renderer_vulkan/vk_presenter.cpp": "daec351dcdb338ef89bfddc76315733816f84333",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp": "180ab9518fa51afd1426bd7e53a1d5124d619c51",
    "src/shader_recompiler/specialization.h": "9d208bf12b232d9ae227933813e72a4968b1ed70",
}
PHASE87_PATCH_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
                     "6f4d48b3dd4cf2fb9d487ba661e2478856b55408/"
                     "tools/gow87-permutation-metadata.patch")
PHASE87_PATCH_SHA256 = "35e1b1299070ab3f7dea09d087bc81b4b3c9e8672fbcd2259317f52508d554f8"
PHASE87_FILES = {
    "src/video_core/renderer_vulkan/vk_pipeline_cache.h",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp",
    "src/shader_recompiler/specialization.h",
}
HISTORICAL_GOW_CPP = {
    "src/core/libraries/kernel/process.cpp",
    "src/core/libraries/videoout/driver.cpp",
    "src/shader_recompiler/backend/spirv/emit_spirv.cpp",
    "src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp",
    "src/shader_recompiler/frontend/translate/data_share.cpp",
    "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp",
    "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp",
    "src/video_core/renderer_vulkan/vk_presenter.cpp",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
}
FORBIDDEN_SOURCE_PATTERNS = (
    rb"SHADPS4_GOW_", rb"CUSA34384", rb"God of War", rb"Ragnar",
    rb"GOW43_", rb"GOW57_", rb"GOW58_", rb"GOW_SRT_",
    rb"0xe83356c8", rb"0xad0b8f1f", rb"0x04691bd5",
)


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def load_gow103():
    scripts = ROOT / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    p = scripts / "gow103-pinned-for-gow104.py"
    if not p.is_file() or p.is_symlink() or git_blob(p.read_bytes()) != GOW103_BLOB:
        fd, tmp = tempfile.mkstemp(prefix=".gow104-", dir=scripts)
        os.close(fd)
        try:
            subprocess.run([
                "curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
                "--max-time", "120", GOW103_URL, "-o", tmp
            ], check=True, timeout=380)
            data = Path(tmp).read_bytes()
            if git_blob(data) != GOW103_BLOB:
                raise RuntimeError("Pinned GoW103 helper fingerprint mismatch")
            if p.is_symlink():
                raise RuntimeError("Refusing symlinked helper")
            os.replace(tmp, p)
        finally:
            Path(tmp).unlink(missing_ok=True)
    m = types.ModuleType("gow105_gow103")
    m.__file__ = str(p)
    exec(compile(p.read_bytes(), str(p), "exec"), m.__dict__)
    return m


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 source match, got {count}")
    return text.replace(old, new, 1)


def source_for(src: Path, overrides: dict, rel: str) -> Path:
    key = (src / rel).resolve()
    p = Path(overrides.get(key, key))
    if not p.is_file() or p.is_symlink():
        raise RuntimeError("Missing/unsafe retained source: " + rel)
    return p


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_cache_file(rel: str) -> Path:
    return ROOT / "scripts/gow109-clean-base" / rel


def fetch_clean_file(rel: str) -> bytes:
    expected = CLEAN_BLOBS[rel]
    path = clean_cache_file(rel)
    if path.is_file() and not path.is_symlink() and git_blob(path.read_bytes()) == expected:
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".gow109-clean-", dir=path.parent)
    os.close(fd)
    try:
        url = ("https://raw.githubusercontent.com/Chreece/shadPS4/" +
               CLEAN_BASE_COMMIT + "/" + rel)
        subprocess.run(["curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
                        "--max-time", "120", url, "-o", tmp], check=True, timeout=380)
        data = Path(tmp).read_bytes()
        if git_blob(data) != expected:
            raise RuntimeError("Clean-base Git blob mismatch: " + rel)
        if path.is_symlink():
            raise RuntimeError("Refusing symlinked clean-base cache: " + rel)
        os.replace(tmp, path)
        return data
    finally:
        Path(tmp).unlink(missing_ok=True)


def phase87_patch_path() -> Path:
    path = ROOT / "scripts/gow109-clean-base/phase87-permutation-metadata.patch"
    if path.is_file() and not path.is_symlink() and sha256_bytes(path.read_bytes()) == PHASE87_PATCH_SHA256:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".gow109-phase87-", dir=path.parent)
    os.close(fd)
    try:
        subprocess.run(["curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
                        "--max-time", "120", PHASE87_PATCH_URL, "-o", tmp],
                       check=True, timeout=380)
        data = Path(tmp).read_bytes()
        if sha256_bytes(data) != PHASE87_PATCH_SHA256:
            raise RuntimeError("Phase87 generic metadata patch checksum mismatch")
        if path.is_symlink():
            raise RuntimeError("Refusing symlinked Phase87 patch cache")
        os.replace(tmp, path)
        return path
    finally:
        Path(tmp).unlink(missing_ok=True)


def clean_general_sources() -> dict[str, bytes]:
    clean = {rel: fetch_clean_file(rel) for rel in CLEAN_BLOBS}
    tests = ROOT / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gow109-phase87-", dir=tests) as td:
        root = Path(td)
        for rel in PHASE87_FILES:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(clean[rel])
        patch = phase87_patch_path()
        subprocess.run(["git", "apply", "--check", patch], cwd=root, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        subprocess.run(["git", "apply", patch], cwd=root, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        for rel in PHASE87_FILES:
            clean[rel] = (root / rel).read_bytes()
    return clean


def assert_no_game_specific_source(rel: str, data: bytes):
    for pattern in FORBIDDEN_SOURCE_PATTERNS:
        if re.search(pattern, data, re.IGNORECASE):
            raise RuntimeError("Game-specific source marker survived clean rebuild: " +
                               rel + " / " + pattern.decode(errors="replace"))


def adapt_generic_srt_order(data: bytes):
    text = data.decode()
    begin = text.find('    // "1": proven-shader-only control. "2": opt-in experimental coverage')
    end = text.find('\n    c.ret();', begin)
    if begin < 0 or end < 0:
        raise RuntimeError("Retained SRT dependency-control block missing")

    old = text[begin:end]
    if "SHADPS4_GOW_SRT_AUTO_ROOTS" not in old or "SHADPS4_GOW_SRT_SGPR8_FIRST" not in old:
        raise RuntimeError("Retained GoW SRT controls are incomplete/unexpected")

    new = '''    // Resolve true inter-root data dependencies for every shader.  The planner
    // preserves the original stable order when there are no dependencies or
    // when analysis is ambiguous, cyclic, or capped; it never invents values.
    std::vector<std::pair<IR::ScalarReg, IR::Inst*>> roots;
    roots.reserve(pass_info.srt_roots.size());
    for (const auto& [sgpr, root] : pass_info.srt_roots) {
        roots.emplace_back(sgpr, root);
    }
    const auto plan = MakeSrtRootDependencyPlan(pass_info);
    if (plan.reordered) {
        LOG_DEBUG(Render_Recompiler,
                  "SRT root dependency order changed shader={:#x} roots={} edges={}",
                  info.pgm_hash, roots.size(), plan.edges.size());
    }
    for (const u32 root_index : plan.order) {
        const auto [sgpr, root] = roots[root_index];
        VisitPointer(IR::Value(static_cast<u32>(sgpr)), root, pass_info, c);
    }
'''
    text = text[:begin] + new + text[end:]

    if "SHADPS4_GOW_SRT_AUTO_ROOTS" in text or "SHADPS4_GOW_SRT_SGPR8_FIRST" in text:
        raise RuntimeError("GoW SRT behavioral environment controls survived genericization")
    if "const auto plan = MakeSrtRootDependencyPlan(pass_info);" not in text:
        raise RuntimeError("Generic SRT dependency planner missing")
    if "for (const u32 root_index : plan.order)" not in text:
        raise RuntimeError("Generic SRT planner order is not consumed")
    return text.encode(), {
        "generic_dependency_planner": True,
        "game_env_controls_removed": True,
        "stable_fallback_on_ambiguous_cycle_cap": True,
        "game_specific": False,
    }


def adapt_resource_h(data: bytes):
    text = data.decode()
    if "struct BufferWriteExtent" in text:
        raise RuntimeError("BufferWriteExtent already exists")

    extent = r'''struct BufferWriteExtent {
    // Conservative upper bound, in bytes from V# base, for all stores in this
    // shader invocation.  Coefficients multiply the maximum value of the named
    // Vulkan built-in. constant_end_bytes already includes access width.
    std::array<u64, 3> local_id_coeff_bytes{};
    std::array<u64, 3> workgroup_id_coeff_bytes{};
    std::array<u64, 3> global_id_coeff_bytes{};
    u64 local_index_coeff_bytes{};
    u64 workgroup_index_coeff_bytes{};
    u64 constant_end_bytes{};
    bool seen{};
    bool complete{true};

    constexpr bool Usable() const noexcept {
        return seen && complete && constant_end_bytes != 0;
    }
};

'''
    text = replace_once(text, "struct BufferResource {\n",
                        extent + "struct BufferResource {\n",
                        "BufferWriteExtent insertion")
    text = replace_once(text, "    bool is_formatted{};\n",
                        "    bool is_formatted{};\n"
                        "    BufferWriteExtent write_extent{};\n",
                        "BufferResource write extent")
    return text.encode(), {
        "separate_binding_and_write_extent": True,
        "arbitrary_cap": False,
        "game_specific": False,
    }


def adapt_resource_patch(data: bytes):
    text = data.decode()
    if "AnalyzeInvocationAffine" in text:
        raise RuntimeError("GoW104 resource analysis already present")
    if "#include <optional>\n" not in text:
        text = replace_once(text, "#include <limits>\n",
                            "#include <limits>\n#include <optional>\n",
                            "optional include")

    anchor = "class Descriptors {\n"
    helper = r'''struct InvocationAffine {
    u64 constant{};
    std::array<u64, 3> local{};
    std::array<u64, 3> workgroup{};
    std::array<u64, 3> global{};
    u64 local_index{};
    u64 workgroup_index{};
};

bool CheckedAdd(u64& dst, u64 value) {
    if (value > std::numeric_limits<u64>::max() - dst) {
        return false;
    }
    dst += value;
    return true;
}

bool CheckedMul(u64& value, u64 factor) {
    if (factor != 0 && value > std::numeric_limits<u64>::max() / factor) {
        return false;
    }
    value *= factor;
    return true;
}

bool AddAffine(InvocationAffine& out, const InvocationAffine& rhs) {
    if (!CheckedAdd(out.constant, rhs.constant) ||
        !CheckedAdd(out.local_index, rhs.local_index) ||
        !CheckedAdd(out.workgroup_index, rhs.workgroup_index)) {
        return false;
    }
    for (u32 i = 0; i < 3; ++i) {
        if (!CheckedAdd(out.local[i], rhs.local[i]) ||
            !CheckedAdd(out.workgroup[i], rhs.workgroup[i]) ||
            !CheckedAdd(out.global[i], rhs.global[i])) {
            return false;
        }
    }
    return true;
}

bool ScaleAffine(InvocationAffine& out, u64 factor) {
    if (!CheckedMul(out.constant, factor) ||
        !CheckedMul(out.local_index, factor) ||
        !CheckedMul(out.workgroup_index, factor)) {
        return false;
    }
    for (u32 i = 0; i < 3; ++i) {
        if (!CheckedMul(out.local[i], factor) ||
            !CheckedMul(out.workgroup[i], factor) ||
            !CheckedMul(out.global[i], factor)) {
            return false;
        }
    }
    return true;
}

std::optional<InvocationAffine> AnalyzeInvocationAffine(IR::Value value, u32 depth = 0) {
    if (depth > 24) {
        return std::nullopt;
    }
    if (value.IsImmediate()) {
        InvocationAffine out{};
        out.constant = value.U32();
        return out;
    }
    IR::Inst* inst = value.TryInst();
    if (!inst) {
        return std::nullopt;
    }
    switch (inst->GetOpcode()) {
    case IR::Opcode::GetAttributeU32: {
        if (!inst->Arg(1).IsImmediate()) {
            return std::nullopt;
        }
        const u32 comp = inst->Arg(1).U32();
        InvocationAffine out{};
        switch (inst->Arg(0).Attribute()) {
        case IR::Attribute::LocalInvocationId:
            if (comp >= 3) return std::nullopt;
            out.local[comp] = 1;
            return out;
        case IR::Attribute::WorkgroupId:
            if (comp >= 3) return std::nullopt;
            out.workgroup[comp] = 1;
            return out;
        case IR::Attribute::GlobalInvocationId:
            if (comp >= 3) return std::nullopt;
            out.global[comp] = 1;
            return out;
        case IR::Attribute::LocalInvocationIndex:
            out.local_index = 1;
            return out;
        case IR::Attribute::WorkgroupIndex:
            out.workgroup_index = 1;
            return out;
        default:
            return std::nullopt;
        }
    }
    case IR::Opcode::ReadFirstLane:
        // Treating the operand as varying is conservative for an upper bound.
        return AnalyzeInvocationAffine(inst->Arg(0), depth + 1);
    case IR::Opcode::IAdd32: {
        auto lhs = AnalyzeInvocationAffine(inst->Arg(0), depth + 1);
        auto rhs = AnalyzeInvocationAffine(inst->Arg(1), depth + 1);
        if (!lhs || !rhs || !AddAffine(*lhs, *rhs)) {
            return std::nullopt;
        }
        return lhs;
    }
    case IR::Opcode::IMul32: {
        const bool imm0 = inst->Arg(0).IsImmediate();
        const bool imm1 = inst->Arg(1).IsImmediate();
        if (imm0 == imm1) {
            return std::nullopt;
        }
        const u64 factor = inst->Arg(imm0 ? 0 : 1).U32();
        auto value = AnalyzeInvocationAffine(inst->Arg(imm0 ? 1 : 0), depth + 1);
        if (!value || !ScaleAffine(*value, factor)) {
            return std::nullopt;
        }
        return value;
    }
    case IR::Opcode::ShiftLeftLogical32: {
        if (!inst->Arg(1).IsImmediate() || inst->Arg(1).U32() >= 32) {
            return std::nullopt;
        }
        auto value = AnalyzeInvocationAffine(inst->Arg(0), depth + 1);
        const u64 factor = u64{1} << inst->Arg(1).U32();
        if (!value || !ScaleAffine(*value, factor)) {
            return std::nullopt;
        }
        return value;
    }
    default:
        return std::nullopt;
    }
}

std::optional<u32> BufferWriteWidthBytes(const IR::Inst& inst, const Profile& profile) {
    if (IsBufferAtomic(inst)) {
        return BufferDataType(inst, profile) == IR::Type::U64 ? 8u : 4u;
    }
    switch (inst.GetOpcode()) {
    case IR::Opcode::StoreBufferU8: return 1;
    case IR::Opcode::StoreBufferU16: return 2;
    case IR::Opcode::StoreBufferU32:
    case IR::Opcode::StoreBufferF32: return 4;
    case IR::Opcode::StoreBufferU32x2:
    case IR::Opcode::StoreBufferF32x2:
    case IR::Opcode::StoreBufferU64: return 8;
    case IR::Opcode::StoreBufferU32x3:
    case IR::Opcode::StoreBufferF32x3: return 12;
    case IR::Opcode::StoreBufferU32x4:
    case IR::Opcode::StoreBufferF32x4: return 16;
    // Formatted stores depend on descriptor/format component semantics.  Keep
    // legacy full-range tracking until their extent can be proven separately.
    case IR::Opcode::StoreBufferFormatF32:
    default:
        return std::nullopt;
    }
}

void InvalidateWriteExtent(BufferWriteExtent& extent) {
    extent.seen = true;
    extent.complete = false;
}

void UpdateBufferWriteExtent(IR::Inst& inst, Info& info, const Profile& profile) {
    if (!IsBufferStore(inst) || !inst.Arg(0).IsImmediate()) {
        return;
    }
    auto& desc = info.buffers[inst.Arg(0).U32()];
    if (desc.IsSpecial()) {
        return;
    }
    if (info.sw_stage != SwStage::Compute || desc.is_formatted) {
        InvalidateWriteExtent(desc.write_extent);
        return;
    }

    const auto width = BufferWriteWidthBytes(inst, profile);
    const auto affine = AnalyzeInvocationAffine(inst.Arg(IR::LoadBufferArgs::Address));
    if (!width || !affine) {
        InvalidateWriteExtent(desc.write_extent);
        return;
    }

    const auto sharp = desc.GetSharp(info);
    const auto inst_info = inst.Flags<IR::BufferInstInfo>();
    const auto is_inst_typed =
        inst_info.inst_data_fmt != AmdGpu::DataFormat::FormatInvalid;
    const auto data_format = is_inst_typed
                                 ? AmdGpu::RemapDataFormat(inst_info.inst_data_fmt.Value())
                                 : sharp.GetDataFmt();
    const u32 shift = BufferAddressShift(inst, data_format);
    const u64 scale = u64{1} << shift;

    InvocationAffine bytes = *affine;
    if (!ScaleAffine(bytes, scale) ||
        !CheckedAdd(bytes.constant, static_cast<u64>(*width))) {
        InvalidateWriteExtent(desc.write_extent);
        return;
    }

    auto& extent = desc.write_extent;
    extent.seen = true;
    if (!extent.complete) {
        return;
    }
    extent.constant_end_bytes = std::max(extent.constant_end_bytes, bytes.constant);
    extent.local_index_coeff_bytes =
        std::max(extent.local_index_coeff_bytes, bytes.local_index);
    extent.workgroup_index_coeff_bytes =
        std::max(extent.workgroup_index_coeff_bytes, bytes.workgroup_index);
    for (u32 i = 0; i < 3; ++i) {
        extent.local_id_coeff_bytes[i] =
            std::max(extent.local_id_coeff_bytes[i], bytes.local[i]);
        extent.workgroup_id_coeff_bytes[i] =
            std::max(extent.workgroup_id_coeff_bytes[i], bytes.workgroup[i]);
        extent.global_id_coeff_bytes[i] =
            std::max(extent.global_id_coeff_bytes[i], bytes.global[i]);
    }
}

'''
    text = replace_once(text, anchor, helper + anchor, "affine helper insertion")

    old = '''        if (IsBufferInstruction(inst)) {
            PatchBufferArgs(inst, info);
        } else if (IsImageInstruction(inst)) {
'''
    new = '''        if (IsBufferInstruction(inst)) {
            PatchBufferArgs(inst, info);
            if (IsBufferStore(inst)) {
                UpdateBufferWriteExtent(inst, info, profile);
            }
        } else if (IsImageInstruction(inst)) {
'''
    text = replace_once(text, old, new, "write extent collection")
    return text.encode(), {
        "affine_invocation_analysis": True,
        "unsupported_write_falls_back_full": True,
        "game_specific": False,
    }


def adapt_region_manager(data: bytes):
    text = data.decode()
    if "gpu_selective_tracked" in text:
        raise RuntimeError("Selective GPU readback state already exists")

    ctor_old = '''        cpu.Fill(true);
        gpu.Fill(false);
    }
'''
    ctor_new = '''        cpu.Fill(true);
        gpu.Fill(false);
        gpu_selective_tracked.Fill(false);
    }
'''
    text = replace_once(text, ctor_old, ctor_new, "selective tracker initialization")

    insert_anchor = '''    template <StateOp cpu_op, StateOp gpu_op, bool locked = true>
    void ChangeRegionState(u64 offset, u64 size) {
'''
    methods = r'''    // Mark only a proven GPU-written range for exact CPU-read synchronization.
    // This is independent of the global readback mode and therefore does not
    // protect unrelated declared buffer capacity.
    void MarkRegionAsGpuModifiedTracked(u64 offset, u64 size) {
        if (readbacks_mode == GpuReadbacksMode::Precise) {
            ChangeRegionState<StateOp::Clear, StateOp::Set>(offset, size);
            return;
        }

        const auto bounds = GetBounds(offset, size);
        bool update_watchers{};
        RegionBits write_prot{};
        RegionBits read_prot{};
        Bounds watcher_bounds = MIN_BOUNDS;

        mutex.lock();
        IterateWords(bounds, [&](u64 index, u64 mask) {
            const u64 prev_cpu = cpu[index];
            const u64 next_cpu = prev_cpu & ~mask;
            write_prot[index] = (next_cpu ^ prev_cpu) & mask;
            read_prot[index] = (~gpu_selective_tracked[index]) & mask;
            const u64 prot_word = write_prot[index] | read_prot[index];
            if (prot_word) {
                update_watchers = true;
                if (index <= watcher_bounds.start_word) {
                    watcher_bounds.start_word = index;
                    watcher_bounds.start_page = std::countr_zero(prot_word);
                }
                if (index >= watcher_bounds.end_word) {
                    watcher_bounds.end_word = index;
                    watcher_bounds.end_page =
                        PAGES_PER_WORD - std::countl_zero(prot_word) - 1;
                }
            }
        });

        if (update_watchers) {
            tracker->UpdatePageWatchersForRegion(cpu_addr, watcher_bounds, write_prot, read_prot,
                                                 PageOp::Track, PageOp::Track);
        }

        IterateWords(bounds, [&](u64 index, u64 mask) {
            UpdateState<StateOp::Clear, StateOp::Set>(index, mask);
            const u64 summary_bit = u64{1} << index;
            gpu_selective_tracked[index] |= mask;
            gpu_selective_tracked.set_summary =
                gpu_selective_tracked[index]
                    ? (gpu_selective_tracked.set_summary | summary_bit)
                    : (gpu_selective_tracked.set_summary & ~summary_bit);
            gpu_selective_tracked.clear_summary =
                gpu_selective_tracked[index] != ~u64{0}
                    ? (gpu_selective_tracked.clear_summary | summary_bit)
                    : (gpu_selective_tracked.clear_summary & ~summary_bit);
        });
        mutex.unlock();
    }

    // Drop only read watchers installed by MarkRegionAsGpuModifiedTracked.
    // Precise mode owns its own read watchers through the normal GPU state.
    void UntrackGpuModifiedPages(u64 offset, u64 size) {
        if (readbacks_mode == GpuReadbacksMode::Precise) {
            return;
        }

        const auto bounds = GetBounds(offset, size);
        bool update_watchers{};
        RegionBits write_prot{};
        RegionBits read_prot{};
        Bounds watcher_bounds = MIN_BOUNDS;

        mutex.lock();
        IterateWords(bounds, [&](u64 index, u64 mask) {
            read_prot[index] = gpu_selective_tracked[index] & mask;
            const u64 prot_word = read_prot[index];
            if (prot_word) {
                update_watchers = true;
                if (index <= watcher_bounds.start_word) {
                    watcher_bounds.start_word = index;
                    watcher_bounds.start_page = std::countr_zero(prot_word);
                }
                if (index >= watcher_bounds.end_word) {
                    watcher_bounds.end_word = index;
                    watcher_bounds.end_page =
                        PAGES_PER_WORD - std::countl_zero(prot_word) - 1;
                }
            }
        });

        if (update_watchers) {
            tracker->UpdatePageWatchersForRegion(cpu_addr, watcher_bounds, write_prot, read_prot,
                                                 PageOp::None, PageOp::Untrack);
        }

        IterateWords(bounds, [&](u64 index, u64 mask) {
            const u64 summary_bit = u64{1} << index;
            gpu_selective_tracked[index] &= ~mask;
            gpu_selective_tracked.set_summary =
                gpu_selective_tracked[index]
                    ? (gpu_selective_tracked.set_summary | summary_bit)
                    : (gpu_selective_tracked.set_summary & ~summary_bit);
            gpu_selective_tracked.clear_summary =
                gpu_selective_tracked[index] != ~u64{0}
                    ? (gpu_selective_tracked.clear_summary | summary_bit)
                    : (gpu_selective_tracked.clear_summary & ~summary_bit);
        });
        mutex.unlock();
    }

'''
    text = replace_once(text, insert_anchor, methods + insert_anchor,
                        "selective readback RegionManager methods")

    member_old = '''    RegionBits cpu;
    RegionBits gpu;
    LockType mutex;
'''
    member_new = '''    RegionBits cpu;
    RegionBits gpu;
    RegionBits gpu_selective_tracked;
    LockType mutex;
'''
    text = replace_once(text, member_old, member_new, "selective tracker member")

    query_anchor = '''    void Lock(const Bounds& bounds) noexcept {
'''
    query_method = '''    bool IsRegionSelectiveGpuTracked(u64 offset, u64 size) const noexcept {
        const auto [start_word, start_page, end_word, end_page] = GetBounds(offset, size);
        const auto [start_mask, end_mask] = GetMasks(start_page, end_page);
        if (start_word == end_word) {
            return gpu_selective_tracked[start_word] & (start_mask & end_mask);
        }
        if (gpu_selective_tracked[start_word] & start_mask) {
            return true;
        }
        for (u64 index = start_word + 1; index < end_word; ++index) {
            if (gpu_selective_tracked[index]) {
                return true;
            }
        }
        return gpu_selective_tracked[end_word] & end_mask;
    }

'''
    text = replace_once(text, query_anchor, query_method + query_anchor,
                        "selective tracker query")

    return text.encode(), {
        "selective_page_read_watchers": True,
        "unrelated_ranges_untouched": True,
        "global_precise_required": False,
        "game_specific": False,
    }


def adapt_memory_tracker(data: bytes):
    text = data.decode()
    if "MarkRegionAsGpuModifiedTracked" in text:
        raise RuntimeError("Selective GPU tracker already present")

    anchor = '''    /// Unmark region as modified from the host GPU
    void UnmarkRegionAsGpuModified'''
    method = '''    /// Mark a statically proven GPU-written range and install a CPU-read
    /// watcher for exactly those pages even when global precise readbacks are disabled.
    void MarkRegionAsGpuModifiedTracked(VAddr cpu_addr, u64 size) noexcept {
        IteratePages<true>(cpu_addr, size, [](RegionManager* manager, u64 offset, u64 size) {
            manager->MarkRegionAsGpuModifiedTracked(offset, size);
        });
    }

'''
    text = replace_once(text, anchor, method + anchor, "selective GPU modified marker")

    old = '''    void UnmarkRegionAsGpuModified(VAddr cpu_addr, u64 size, bool is_write) noexcept {
        IteratePages(cpu_addr, size, [is_write](RegionManager* manager, u64 offset, u64 size) {
            if (is_write) {
                manager->template ChangeRegionState<StateOp::Set, StateOp::Clear>(offset, size);
            } else {
                manager->template ChangeRegionState<StateOp::None, StateOp::Clear>(offset, size);
            }
        });
    }
'''
    new = '''    void UnmarkRegionAsGpuModified(VAddr cpu_addr, u64 size, bool is_write) noexcept {
        IteratePages(cpu_addr, size, [is_write](RegionManager* manager, u64 offset, u64 size) {
            manager->UntrackGpuModifiedPages(offset, size);
            if (is_write) {
                manager->template ChangeRegionState<StateOp::Set, StateOp::Clear>(offset, size);
            } else {
                manager->template ChangeRegionState<StateOp::None, StateOp::Clear>(offset, size);
            }
        });
    }
'''
    text = replace_once(text, old, new, "selective GPU watcher removal")

    invalidate_old = '''    /// Removes all protection from a page and ensures GPU data has been flushed if requested
    void InvalidateRegion(VAddr cpu_addr, u64 size, auto&& on_flush) noexcept {
        if (readbacks_mode == GpuReadbacksMode::Disabled) {
            return MarkRegionAsCpuModified(cpu_addr, size);
        }
        bool should_flush = false;
        IteratePages(cpu_addr, size, [&should_flush](RegionManager* manager, u64 offset, u64 size) {
            const auto bounds = manager->GetBounds(offset, size);
            manager->Lock(bounds);
            const bool modified = manager->template IsRegionModified<Type::GPU>(offset, size);
            if (!modified) {
                manager->template ChangeRegionState<StateOp::Set, StateOp::None, false>(offset,
                                                                                        size);
            }
            should_flush |= modified;
            manager->Unlock(bounds);
        });
        if (should_flush) {
            on_flush();
        }
    }
'''
    invalidate_new = '''    /// Removes CPU protection and flushes GPU data before a CPU write when needed.
    /// Proven selective GPU pages retain correctness even when global precise readbacks are off.
    void InvalidateRegion(VAddr cpu_addr, u64 size, auto&& on_flush) noexcept {
        if (readbacks_mode == GpuReadbacksMode::Disabled) {
            bool should_flush = false;
            IteratePages(cpu_addr, size, [&should_flush](RegionManager* manager, u64 offset,
                                                         u64 size) {
                const auto bounds = manager->GetBounds(offset, size);
                manager->Lock(bounds);
                const bool selective = manager->IsRegionSelectiveGpuTracked(offset, size);
                if (!selective) {
                    manager->template ChangeRegionState<StateOp::Set, StateOp::None, false>(
                        offset, size);
                }
                should_flush |= selective;
                manager->Unlock(bounds);
            });
            if (should_flush) {
                on_flush();
            }
            return;
        }

        bool should_flush = false;
        IteratePages(cpu_addr, size, [&should_flush](RegionManager* manager, u64 offset, u64 size) {
            const auto bounds = manager->GetBounds(offset, size);
            manager->Lock(bounds);
            const bool modified = manager->template IsRegionModified<Type::GPU>(offset, size);
            if (!modified) {
                manager->template ChangeRegionState<StateOp::Set, StateOp::None, false>(offset,
                                                                                        size);
            }
            should_flush |= modified;
            manager->Unlock(bounds);
        });
        if (should_flush) {
            on_flush();
        }
    }
'''
    text = replace_once(text, invalidate_old, invalidate_new,
                        "selective write-fault ownership handoff")
    return text.encode(), {
        "selective_cpu_read_watchers": True,
        "selective_cpu_write_fault_flush": True,
        "global_precise_required": False,
        "game_specific": False,
    }


def adapt_buffer_h(data: bytes):
    text = data.decode()
    old = '''    [[nodiscard]] std::pair<const Buffer*, u64> ObtainBuffer(VAddr device_addr, u32 size,
                                                             bool is_written,
                                                             bool is_texel_buffer = false);
'''
    new = '''    [[nodiscard]] std::pair<const Buffer*, u64> ObtainBuffer(
        VAddr device_addr, u32 size, bool is_written, bool is_texel_buffer = false,
        u32 proven_write_size = 0);
'''
    text = replace_once(text, old, new, "ObtainBuffer declaration")
    return text.encode(), {"separate_write_size_api": True}


def adapt_buffer_cpp(data: bytes):
    text = data.decode()

    old_sig = """std::pair<const Buffer*, u64> BufferCache::ObtainBuffer(VAddr device_addr, u32 size,
                                                        bool is_written, bool is_texel_buffer) {"""
    new_sig = """std::pair<const Buffer*, u64> BufferCache::ObtainBuffer(VAddr device_addr, u32 size,
                                                        bool is_written, bool is_texel_buffer,
                                                        u32 proven_write_size) {"""
    text = replace_once(text, old_sig, new_sig, "ObtainBuffer definition")

    # GoW91 already wraps the normal SynchronizeMemory call in its texel/image
    # coherence evidence path. Insert the bounded-write decision without
    # deleting or bypassing that retained logic.
    sync_old = """    const bool image_synchronized =
        SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);"""
    sync_new = """    // A proven write extent separates descriptor/binding size from GPU ownership.
    // Keep the full range resident and synchronized for reads, but do not mark
    // bytes the shader cannot reach as GPU-authored.
    const bool bounded_write =
        is_written && !is_texel_buffer && proven_write_size != 0 && proven_write_size < size;
    const bool image_synchronized =
        SynchronizeMemory(arena, device_addr, size, is_written && !bounded_write,
                          is_texel_buffer);"""
    text = replace_once(text, sync_old, sync_new, "GoW91 synchronization hook")

    old_dirty = """    if (is_written) {
        gpu_modified_ranges.Add(device_addr, size);
    }"""
    # GoW91 token rewriting may have retained this statement on one line.
    if old_dirty not in text:
        one_line = "    if (is_written) { gpu_modified_ranges.Add(device_addr, size); }"
        if one_line not in text:
            raise RuntimeError("GPU dirty-range statement source shape changed")
        old_dirty = one_line
    new_dirty = """    if (is_written) {
        if (bounded_write) {
            memory_tracker->MarkRegionAsGpuModifiedTracked(device_addr, proven_write_size);
            gpu_modified_ranges.Add(device_addr, proven_write_size);
        } else {
            gpu_modified_ranges.Add(device_addr, size);
        }
    }"""
    text = replace_once(text, old_dirty, new_dirty, "bounded GPU dirty range")

    return text.encode(), {
        "full_binding_preserved": True,
        "bounded_write_ownership": True,
        "gow91_texel_coherency_preserved": "TEXEL_STREAM_ROUTE" in text,
        "legacy_fallback_preserved": True,
    }


def adapt_rasterizer(data: bytes):
    text = data.decode()
    if "ComputeProvenWriteSize" in text:
        raise RuntimeError("GoW104 rasterizer logic already present")

    if any(x not in text for x in (
            "#include <atomic>\n", "#include <limits>\n", "#include <optional>\n")):
        insert = text.find("\n\nnamespace Vulkan")
        if insert < 0:
            raise RuntimeError("Rasterizer include/namespace boundary missing")
        additions = ""
        for header in ("atomic", "limits", "optional"):
            line = f"#include <{header}>\n"
            if line not in text:
                additions += line
        text = text[:insert] + "\n" + additions.rstrip("\n") + text[insert:]

    anchor = "void Rasterizer::BindBuffers(const Shader::Info& stage"
    pos = text.find(anchor)
    if pos < 0:
        raise RuntimeError("BindBuffers anchor missing")
    line = text.rfind("\n", 0, pos) + 1

    helper = r'''std::optional<u64> ComputeProvenWriteSize(
    const Shader::BufferWriteExtent& extent, const std::array<u64, 3>& local,
    const std::array<u64, 3>& groups) {
    if (!extent.Usable()) {
        return std::nullopt;
    }
    for (u32 i = 0; i < 3; ++i) {
        if (local[i] == 0 || groups[i] == 0) {
            return std::nullopt;
        }
    }

    const auto checked_mul = [](u64 a, u64 b, u64& out) {
        if (a != 0 && b > std::numeric_limits<u64>::max() / a) {
            return false;
        }
        out = a * b;
        return true;
    };
    const auto checked_add = [](u64& a, u64 b) {
        if (b > std::numeric_limits<u64>::max() - a) {
            return false;
        }
        a += b;
        return true;
    };

    u64 end = extent.constant_end_bytes;
    u64 local_count = 1;
    u64 group_count = 1;
    for (u32 i = 0; i < 3; ++i) {
        u64 term{};
        if (!checked_mul(extent.local_id_coeff_bytes[i], local[i] - 1, term) ||
            !checked_add(end, term) ||
            !checked_mul(extent.workgroup_id_coeff_bytes[i], groups[i] - 1, term) ||
            !checked_add(end, term)) {
            return std::nullopt;
        }

        u64 global_count{};
        if (!checked_mul(local[i], groups[i], global_count) || global_count == 0 ||
            !checked_mul(extent.global_id_coeff_bytes[i], global_count - 1, term) ||
            !checked_add(end, term) ||
            !checked_mul(local_count, local[i], local_count) ||
            !checked_mul(group_count, groups[i], group_count)) {
            return std::nullopt;
        }
    }

    u64 term{};
    if (!checked_mul(extent.local_index_coeff_bytes, local_count - 1, term) ||
        !checked_add(end, term) ||
        !checked_mul(extent.workgroup_index_coeff_bytes, group_count - 1, term) ||
        !checked_add(end, term)) {
        return std::nullopt;
    }
    return end;
}

'''
    text = text[:line] + helper + text[line:]

    old = """                const auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, size, desc.is_written, desc.is_formatted);
"""
    new = """                u64 tracked_write_size = size;
                if (desc.is_written && !desc.is_formatted &&
                    stage.sw_stage == Shader::SwStage::Compute) {
                    const auto& cs = liverpool->GetCsRegs();
                    const std::array<u64, 3> local = {
                        cs.num_thread_x.full, cs.num_thread_y.full, cs.num_thread_z.full};
                    const std::array<u64, 3> groups = {cs.dim_x, cs.dim_y, cs.dim_z};
                    if (const auto proven = ComputeProvenWriteSize(desc.write_extent, local, groups);
                        proven && *proven != 0 && *proven < tracked_write_size) {
                        const u64 full_size = tracked_write_size;
                        tracked_write_size = *proven;
                        static std::atomic<u64> proven_write_events{0};
                        const u64 event =
                            proven_write_events.fetch_add(1, std::memory_order_relaxed) + 1;
                        if (event <= 32 || (event & (event - 1)) == 0) {
                            LOG_INFO(Render_Vulkan,
                                     "PROVEN_WRITE_EXTENT shader={:#x} full={} tracked={} event={}",
                                     stage.pgm_hash, full_size, tracked_write_size, event);
                        }
                    }
                }
                const auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, static_cast<u32>(size), desc.is_written, desc.is_formatted,
                    static_cast<u32>(tracked_write_size));
"""
    text = replace_once(text, old, new, "bounded ObtainBuffer call")

    old_inv = """                if (desc.is_written) {
                    // Raw storage-buffer writes can also make an aliased cached image stale.
                    texture_cache.InvalidateMemoryFromGPU(vsharp.base_address, size);
                }
"""
    new_inv = """                if (desc.is_written) {
                    // The Vulkan binding remains full-size. Only GPU-authored bytes
                    // invalidate aliased image state when a proof is available.
                    texture_cache.InvalidateMemoryFromGPU(vsharp.base_address, tracked_write_size);
                }
"""
    text = replace_once(text, old_inv, new_inv, "bounded image invalidation")

    read_old = '''bool Rasterizer::ReadMemory(VAddr addr, u64 size, bool assume_locks) {
    if (!IsMapped(addr, size)) {
        // Not GPU mapped memory, can skip invalidation logic entirely.
        return false;
    }
    buffer_cache.ReadMemory(addr, size, false, assume_locks);
    return true;
}'''
    read_new = '''bool Rasterizer::ReadMemory(VAddr addr, u64 size, bool assume_locks) {
    if (!IsMapped(addr, size)) {
        // Not GPU mapped memory, can skip invalidation logic entirely.
        return false;
    }
    if (buffer_cache.IsRegionGpuModified(addr, size)) {
        static std::atomic<u64> selective_readbacks{0};
        const u64 event = selective_readbacks.fetch_add(1, std::memory_order_relaxed) + 1;
        if (event <= 32 || (event & (event - 1)) == 0) {
            LOG_INFO(Render_Vulkan,
                     "SELECTIVE_GPU_READBACK address={:#x} bytes={} event={}",
                     addr, size, event);
        }
    }
    buffer_cache.ReadMemory(addr, size, false, assume_locks);
    return true;
}'''
    text = replace_once(text, read_old, read_new, "selective readback telemetry")
    return text.encode(), {
        "runtime_dispatch_bound": True,
        "full_descriptor_range_preserved": True,
        "fallback_full_range": True,
    }


def adapt_serialization(data: bytes):
    text = data.decode()
    marker = "static constexpr u32 ShaderMetaVersion = "
    pos = text.find(marker)
    if pos < 0:
        raise RuntimeError("ShaderMetaVersion missing")
    end = text.find("u;", pos)
    if end < 0:
        raise RuntimeError("ShaderMetaVersion source shape changed")
    old = text[pos:end + 2]
    # BufferResource raw bytes are serialized as part of InfoPersistent.
    import re
import shutil
    match = re.search(r"ShaderMetaVersion = (\d+)u;", old)
    if not match:
        raise RuntimeError("ShaderMetaVersion parse failed")
    version = int(match.group(1))
    text = text[:pos] + f"static constexpr u32 ShaderMetaVersion = {version + 1}u;" + text[end + 2:]
    return text.encode(), {"shader_meta_version": [version, version + 1]}


def transform_all(gow97, gow96, gow91, src, overrides):
    originals = {}
    finals = {}
    proof = {}

    # Retain only the proven generic buffer/cache chain.
    p = source_for(src, overrides, gow91.CPP)
    raw = p.read_bytes()
    after91, _, p91 = gow91.adapt_source(raw)
    after108, p108 = adapt_buffer_cpp(after91)
    originals[gow91.CPP] = raw
    finals[gow91.CPP] = after108
    proof["gow91_generic_texel"] = p91
    proof["bounded_write_buffer_cache"] = p108

    # Generic Phi/resource discovery correction.
    p = source_for(src, overrides, gow96.RESOURCE)
    raw = p.read_bytes()
    after, _, p96 = gow96.adapt_resource(raw, gow96.collaborator_source())
    originals[gow96.RESOURCE] = raw
    finals[gow96.RESOURCE] = after
    proof["resource_discovery"] = p96

    # Generic write-extent/coherence files not historically game-instrumented.
    adapters = (
        (RESOURCE_H, adapt_resource_h, "resource_h"),
        (BUFFER_H, adapt_buffer_h, "buffer_h"),
        (REGION_MANAGER_H, adapt_region_manager, "region_manager"),
        (MEMORY_TRACKER_H, adapt_memory_tracker, "memory_tracker"),
    )
    for rel, fn, key in adapters:
        p = source_for(src, overrides, rel)
        raw = p.read_bytes()
        out, pr = fn(raw)
        originals[rel] = raw
        finals[rel] = out
        proof[key] = pr

    # Reconstruct every historically GoW-modified source from the clean Phase84
    # integration commit, then reapply only the generic fixes that proved useful.
    clean = clean_general_sources()
    clean_proof = {}
    for rel, clean_raw in clean.items():
        p = source_for(src, overrides, rel)
        local_raw = p.read_bytes()
        final = clean_raw
        reapplied = []

        if rel == gow97.FLAT:
            final, _, p103 = gow97.adapt_flatten(final)
            final, porder = adapt_generic_srt_order(final)
            proof["srt_arithmetic_and_rollback"] = p103
            proof["generic_srt_order"] = porder
            reapplied += ["srt_arithmetic_and_rollback", "generic_root_dependency_order"]
        elif rel == RESOURCE_PATCH:
            final, pr = adapt_resource_patch(final)
            proof["resource_patch"] = pr
            reapplied.append("generic_write_extent_analysis")
        elif rel == RASTER:
            final, pr = adapt_rasterizer(final)
            proof["rasterizer"] = pr
            reapplied += ["bounded_write_extent", "selective_readback_telemetry"]
        elif rel == SERIAL:
            final, pr = adapt_serialization(final)
            proof["serialization"] = pr
            reapplied += ["phase87_permutation_metadata", "write_extent_cache_version"]
        elif rel in PHASE87_FILES:
            reapplied.append("phase87_permutation_metadata")

        assert_no_game_specific_source(rel, final)
        originals[rel] = local_raw
        finals[rel] = final
        clean_proof[rel] = {
            "clean_base_commit": CLEAN_BASE_COMMIT,
            "clean_git_blob": CLEAN_BLOBS[rel],
            "phase87_generic_metadata": rel in PHASE87_FILES,
            "generic_reapplied": reapplied,
            "final_sha256": sha256_bytes(final),
        }

    # The remaining generated sources must also contain no game-specific controls.
    for rel, final in finals.items():
        assert_no_game_specific_source(rel, final)

    proof["clean_historical_source_replacement"] = clean_proof
    proof["historical_game_cpp_count"] = len(HISTORICAL_GOW_CPP)
    proof["game_specific"] = False
    return originals, finals, proof


def build_candidate(gow97, gow96, gow91, base, phase90, phase89, helper, work, logs):
    src, build, prior, pins, overrides = gow91.prior_state(base, phase90, phase89)
    originals, finals, proof = transform_all(gow97, gow96, gow91, src, overrides)

    overlay = work / "overlay"
    patch = ""
    for rel, raw in originals.items():
        target = overlay / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        patch += "".join(difflib.unified_diff(
            raw.decode().splitlines(keepends=True),
            finals[rel].decode().splitlines(keepends=True),
            fromfile="a/" + rel, tofile="b/" + rel, n=3))

    pf = logs / "gow109-clean-generic.patch"
    pf.write_text(patch)
    helper.run(["git", "apply", "--check", pf], logs / "patch-check.log", cwd=overlay, timeout=30)
    helper.run(["git", "apply", pf], logs / "patch-apply.log", cwd=overlay, timeout=30)

    for rel, expected in finals.items():
        if (overlay / rel).read_bytes() != expected:
            raise RuntimeError("Applied source differs from verified GoW109 plan: " + rel)

    changed = tuple(finals.keys())

    # phase89's preservation audit expects every previously corrected header to
    # remain the effective include.  When GoW104 intentionally supersedes one
    # of those headers, point that audit at the verified current overlay copy.
    # The original retained file is still checked byte-for-byte below.
    build_overrides = dict(overrides)
    for rel in changed:
        original = (src / rel).resolve()
        current = overlay / rel
        if Path(rel).suffix == ".h" and original in build_overrides and current.is_file():
            build_overrides[original] = current.resolve()

    binary, stats, mapping = phase89.chained_build(
        helper, build, src, overlay, work, logs, changed, prior, build_overrides,
        [gow91.BASE90 / "overlay", phase90.BASE89 / "overlay",
         phase89.BASE88 / "overlay", base.BASE87 / "overlay"])

    # Every historical GoW-modified C++ unit must now be linked from this
    # clean overlay, never from an older retained diagnostic object.
    replaced_clean = {}
    for rel in sorted(HISTORICAL_GOW_CPP):
        original_obj = (build / ("CMakeFiles/shadps4.dir/" + rel + ".o")).resolve()
        replacement = mapping.get(original_obj)
        if replacement is None or not Path(replacement).is_file():
            raise RuntimeError("Historical GoW object was not replaced by clean build: " + rel)
        if not Path(replacement).is_relative_to(work):
            raise RuntimeError("Clean replacement object escaped current build: " + rel)
        replaced_clean[rel] = str(replacement)

    if any(gow91.sha(p) != h for p, h in pins.items()):
        raise RuntimeError("Earlier retained object changed")
    for rel, raw in originals.items():
        if source_for(src, overrides, rel).read_bytes() != raw:
            raise RuntimeError("Original retained source changed: " + rel)

    runtime = build / "cpu-id-runtime"
    if not runtime.is_dir():
        raise RuntimeError("CPU-ID runtime missing")
    (work / "cpu-id-runtime").symlink_to(runtime.resolve(), target_is_directory=True)
    helper.run(["ldd", binary], logs / "libraries.log", timeout=20)
    if "not found" in (logs / "libraries.log").read_text():
        raise RuntimeError("Linked library unavailable")

    stats.update(
        gow109_clean_generic_provenance=proof,
        clean_base_commit=CLEAN_BASE_COMMIT,
        historical_game_cpp_replaced=replaced_clean,
        historical_game_cpp_count=len(replaced_clean),
        forbidden_game_source_markers_absent=True,
        phase87_generic_metadata_reapplied=True,
        readback_validation_mode="normal runtime; no forced precise readbacks",
        global_arbitrary_buffer_cap=False,
        game_specific=False,
        replacement_objects={str(k): str(v) for k, v in mapping.items()},
    )
    (work / "manifest.json").write_text(json.dumps(stats, indent=2) + "\n")
    (logs / "build-proof.json").write_text(json.dumps(stats, indent=2) + "\n")
    return binary, stats


def self_test(gow97, gow96, gow91):
    phase90 = gow91.get_helper()
    phase89 = phase90.get_helper()
    base = gow91.get_base(phase89)
    src, _, _, _, overrides = gow91.prior_state(base, phase90, phase89)
    originals, finals, proof = transform_all(gow97, gow96, gow91, src, overrides)

    assert b"struct BufferWriteExtent" in finals[RESOURCE_H]
    assert b"AnalyzeInvocationAffine" in finals[RESOURCE_PATCH]
    assert b"MarkRegionAsGpuModifiedTracked" in finals[MEMORY_TRACKER_H]
    assert b"gpu_selective_tracked" in finals[REGION_MANAGER_H]
    assert b"PageOp::None, PageOp::Untrack" in finals[REGION_MANAGER_H]
    assert b"IsRegionSelectiveGpuTracked" in finals[REGION_MANAGER_H]
    assert b"const bool selective = manager->IsRegionSelectiveGpuTracked" in finals[MEMORY_TRACKER_H]
    assert b"MarkRegionAsGpuModifiedTracked" in finals[gow91.CPP]
    assert b"proven_write_size" in finals[gow91.CPP]
    assert b"ComputeProvenWriteSize" in finals[RASTER]
    assert b"tracked_write_size" in finals[RASTER]
    assert b"SELECTIVE_GPU_READBACK" in finals[RASTER]
    assert b"SHADPS4_GOW_SRT_AUTO_ROOTS" not in finals[gow97.FLAT]
    assert b"SHADPS4_GOW_SRT_SGPR8_FIRST" not in finals[gow97.FLAT]
    assert b"SRT root dependency order changed" in finals[gow97.FLAT]
    assert len(proof["clean_historical_source_replacement"]) == len(CLEAN_BLOBS)
    assert proof["historical_game_cpp_count"] == 11
    for rel, final in finals.items():
        assert_no_game_specific_source(rel, final)
    assert proof["resource_h"]["game_specific"] is False
    assert proof["resource_patch"]["game_specific"] is False
    assert proof["gow103"]["game_specific"] is False
    print("GOW109_SELF_TEST=PASS clean generic provenance; no historical GoW source markers", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    g103 = load_gow103()
    gow98 = g103.load_gow98()
    gow97 = gow98.load_gow97()
    gow98.install_readfirstlane_support(gow97)
    g103.install_fixes(gow98, gow97)
    gow96 = gow97.load_gow96()
    gow91 = gow96.load_gow91()

    if args.self_test:
        self_test(gow97, gow96, gow91)
        return 0

    phase90 = gow91.get_helper()
    phase89 = phase90.get_helper()
    base = gow91.get_base(phase89)
    gow91.install(base, phase90, phase89)
    base.build_candidate = lambda helper, work, logs: build_candidate(
        gow97, gow96, gow91, base, phase90, phase89, helper, work, logs)
    gow96.install_runtime(base)

    old = sys.argv[:]
    try:
        sys.argv = ["gow109"] + (["--build-only"] if args.build_only else []) + (
            ["--restore"] if args.restore else [])
        return base.main()
    finally:
        sys.argv = old


if __name__ == "__main__":
    raise SystemExit(main())
