#!/usr/bin/env python3
"""Apply the proven generic Ragnarök-era emulator fixes to a fresh upstream-main tree.

This module contains source transformations only. It has no game ID, shader hash,
per-game setting, SHADPS4_GOW_* switch, draw skip, fake value, or readbacks override.
"""
from __future__ import annotations

import difflib
import hashlib
import re
from pathlib import Path

RESOURCE_H = "src/shader_recompiler/resource.h"
RESOURCE_PATCH = "src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
RESOURCE_DISCOVER = "src/shader_recompiler/ir/passes/resource_discover_pass.cpp"
FLAT = "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
BUFFER_H = "src/video_core/buffer_cache/buffer_cache.h"
BUFFER_CPP = "src/video_core/buffer_cache/buffer_cache.cpp"
FAULT_CPP = "src/video_core/buffer_cache/fault_manager.cpp"
REGION_H = "src/video_core/buffer_cache/region_manager.h"
MEMORY_TRACKER_H = "src/video_core/buffer_cache/memory_tracker.h"
RASTER = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
SERIAL = "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected one source match, got {n}")
    return text.replace(old, new, 1)


def mask_cpp(text: str) -> str:
    token = re.compile(
        r'//[^\n]*|/\*[\s\S]*?\*/|R"(?P<d>[^ ()\\\t\n]{0,16})\([\s\S]*?\)(?P=d)"|'
        r'"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'')
    return token.sub(lambda m: "".join("\n" if ch == "\n" else " " for ch in m.group(0)), text)


def function_span(text: str, name: str) -> tuple[int, int]:
    masked = mask_cpp(text)
    starts = [m.start() for m in re.finditer(re.escape(name + "("), masked)]
    found = []
    for start in starts:
        paren = masked.find("(", start)
        depth = 0
        close = None
        for i in range(paren, len(masked)):
            if masked[i] == "(":
                depth += 1
            elif masked[i] == ")":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close is None:
            continue
        brace = close + 1
        while brace < len(masked) and masked[brace].isspace():
            brace += 1
        if brace >= len(masked) or masked[brace] != "{":
            continue
        depth = 0
        end = None
        for i in range(brace, len(masked)):
            if masked[i] == "{":
                depth += 1
            elif masked[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is not None:
            found.append((start, end))
    if len(found) != 1:
        raise RuntimeError(f"{name}: definition missing or ambiguous")
    return found[0]


def adapt_fault_manager(data: bytes) -> tuple[bytes, dict]:
    text = data.decode()
    old = "(void)buffer_cache.ObtainBuffer(start, end - start);"
    new = (
        "(void)buffer_cache.ObtainBuffer(\n"
        "                start, end - start, ObtainBufferFlags::IgnoreStreamBuffer);"
    )
    text = one(text, old, new, "DMA fault resident-buffer route")
    return text.encode(), {
        "dma_fault_uses_resident_buffer": True,
        "stream_fast_path_unchanged": True,
        "game_specific": False,
    }


def adapt_texel_image_ranges(text: str) -> tuple[str, dict]:
    if "TEXEL_IMAGE_SUBRANGE_SYNC" in text:
        raise RuntimeError("texel subrange synchronization already present")
    if '#include "common/scope_exit.h"\n' not in text:
        text = one(text, '#include "common/alignment.h"\n',
                   '#include "common/alignment.h"\n#include "common/scope_exit.h"\n',
                   "scope_exit include")

    old_range = '''    ASSERT_MSG(device_addr == image.info.guest_address,
               "Texel buffer aliases image subresources {:x} : {:x}", device_addr,
               image.info.guest_address);
    const u64 arena_offset = arena->Offset(device_addr);'''
    new_range = '''    const u64 alias_image_size = image.info.guest_size;
    const u64 alias_request_skip = image.info.guest_address > device_addr
                                       ? image.info.guest_address - device_addr : 0;
    const u64 alias_image_offset = device_addr > image.info.guest_address
                                       ? device_addr - image.info.guest_address : 0;
    if (alias_request_skip >= size || alias_image_offset >= alias_image_size) {
        return false;
    }
    const u64 alias_copy_size = std::min<u64>(u64(size) - alias_request_skip,
                                              alias_image_size - alias_image_offset);
    if (device_addr < arena->cpu_addr) {
        return false;
    }
    const u64 alias_request_offset = arena->Offset(device_addr);
    if (alias_request_offset > arena->size_bytes ||
        alias_request_skip > arena->size_bytes - alias_request_offset ||
        alias_copy_size > arena->size_bytes - alias_request_offset - alias_request_skip) {
        return false;
    }
    const u64 arena_offset = alias_request_offset + alias_request_skip;
    if (image.info.resources.levels == 0 ||
        image.info.resources.levels > image.info.mips_layout.size() ||
        image.info.resources.layers == 0) {
        return false;
    }'''
    text = one(text, old_range, new_range, "texel image alias range")

    old_mip = '''        if (arena_offset + mip_info.offset + mip_info.size > arena->size_bytes) {
            break;
        }'''
    new_mip = '''        if (mip_info.size == 0 || mip_info.offset > alias_image_size ||
            mip_info.size > alias_image_size - mip_info.offset) {
            return false;
        }'''
    text = one(text, old_mip, new_mip, "texel mip validation")

    old_copy = '''    tile_manager.TileImage(image, buffer_copies, arena, arena_offset);
    return true;'''
    new_copy = '''    if (alias_image_offset == 0 && alias_copy_size == alias_image_size) {
        tile_manager.TileImage(image, buffer_copies, arena, arena_offset);
        return true;
    }

    const auto alias_scratch = staging_pool.Request(
        alias_image_size, MemoryType::DeviceLocal, instance.StorageMinAlignment(), true, true);
    SCOPE_EXIT { staging_pool.FreeDeferred(alias_scratch); };
    if (!image.info.props.is_tiled) {
        const vk::BufferCopy preserve = {
            .srcOffset = arena_offset,
            .dstOffset = alias_scratch.offset + alias_image_offset,
            .size = alias_copy_size,
        };
        runtime.CopyBuffer(arena, alias_scratch.buffer, std::span{&preserve, 1});
    }
    if (runtime.IsBufferAccessed(alias_scratch.buffer, alias_scratch.offset,
                                 alias_image_size, true)) {
        runtime.FlushBarriers();
    }
    tile_manager.TileImage(image, buffer_copies, alias_scratch.buffer, alias_scratch.offset);
    const vk::BufferCopy alias_copy = {
        .srcOffset = alias_scratch.offset + alias_image_offset,
        .dstOffset = arena_offset,
        .size = alias_copy_size,
    };
    runtime.CopyBuffer(alias_scratch.buffer, arena, std::span{&alias_copy, 1});
    LOG_DEBUG(Render_Vulkan,
              "Texel/image subrange synchronized request={:#x} bytes={} image={:#x} copied={}",
              device_addr, size, image.info.guest_address, alias_copy_size);
    return true;'''
    text = one(text, old_copy, new_copy, "texel subrange copy")
    return text, {
        "image_alias_intersection_only": True,
        "full_image_scratch_for_partial_alias": True,
        "game_specific": False,
    }


def adapt_buffer_header(data: bytes) -> tuple[bytes, dict]:
    text = data.decode()
    old = '''    [[nodiscard]] std::pair<const Buffer*, u64> ObtainBuffer(
        VAddr device_addr, u32 size, ObtainBufferFlags flags = ObtainBufferFlags::None);'''
    new = '''    [[nodiscard]] std::pair<const Buffer*, u64> ObtainBuffer(
        VAddr device_addr, u32 size, ObtainBufferFlags flags = ObtainBufferFlags::None,
        u32 proven_write_size = 0);'''
    text = one(text, old, new, "ObtainBuffer proven write size")
    return text.encode(), {"binding_size_separate_from_write_extent": True, "game_specific": False}


def adapt_region_manager(data: bytes) -> tuple[bytes, dict]:
    text = data.decode()
    if "gpu_selective_tracked" in text:
        raise RuntimeError("selective GPU tracking already present")

    text = one(text, '''        cpu.Fill(true);
        gpu.Fill(false);
    }
''', '''        cpu.Fill(true);
        gpu.Fill(false);
        gpu_selective_tracked.Fill(false);
    }
''', "selective tracking initialization")

    anchor = '''    template <StateOp cpu_op, StateOp gpu_op, bool locked = true>
    void ChangeRegionState(u64 offset, u64 size) {
'''
    methods = r'''    void MarkRegionAsGpuModifiedTracked(u64 offset, u64 size) {
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

    bool IsRegionSelectiveGpuTracked(u64 offset, u64 size) const noexcept {
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
    text = one(text, anchor, methods + anchor, "selective RegionManager API")
    text = one(text, '''    RegionBits cpu;
    RegionBits gpu;
    LockType mutex;
''', '''    RegionBits cpu;
    RegionBits gpu;
    RegionBits gpu_selective_tracked;
    LockType mutex;
''', "selective RegionBits member")
    return text.encode(), {
        "selective_read_watchers": True,
        "global_precise_required": False,
        "game_specific": False,
    }


def adapt_memory_tracker(data: bytes) -> tuple[bytes, dict]:
    text = data.decode()
    anchor = '''    /// Unmark region as modified from the host GPU
    void UnmarkRegionAsGpuModified'''
    method = '''    /// Mark only a proven GPU-written range and install CPU read tracking.
    void MarkRegionAsGpuModifiedTracked(VAddr cpu_addr, u64 size) noexcept {
        IteratePages<true>(cpu_addr, size, [](RegionManager* manager, u64 offset, u64 size) {
            manager->MarkRegionAsGpuModifiedTracked(offset, size);
        });
    }

'''
    text = one(text, anchor, method + anchor, "selective memory tracker marker")

    old_unmark = '''    void UnmarkRegionAsGpuModified(VAddr cpu_addr, u64 size, bool is_write) noexcept {
        IteratePages(cpu_addr, size, [is_write](RegionManager* manager, u64 offset, u64 size) {
            if (is_write) {
                manager->template ChangeRegionState<StateOp::Set, StateOp::Clear>(offset, size);
            } else {
                manager->template ChangeRegionState<StateOp::None, StateOp::Clear>(offset, size);
            }
        });
    }
'''
    new_unmark = '''    void UnmarkRegionAsGpuModified(VAddr cpu_addr, u64 size, bool is_write) noexcept {
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
    text = one(text, old_unmark, new_unmark, "selective watcher removal")

    old_invalidate = '''    /// Removes all protection from a page and ensures GPU data has been flushed if requested
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
    new_invalidate = '''    /// Remove CPU protection and flush selectively tracked GPU data before a CPU write.
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
    text = one(text, old_invalidate, new_invalidate, "selective write-fault handoff")
    return text.encode(), {
        "selective_cpu_write_fault_flush": True,
        "readbacks_mode_zero_supported": True,
        "game_specific": False,
    }


def adapt_resource_h(data: bytes) -> tuple[bytes, dict]:
    text = data.decode()
    if "struct BufferWriteExtent" in text:
        raise RuntimeError("BufferWriteExtent already present")
    extent = '''struct BufferWriteExtent {
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
    text = one(text, "struct BufferResource {\n", extent + "struct BufferResource {\n",
               "BufferWriteExtent insertion")
    text = one(text, "    bool is_formatted{};\n",
               "    bool is_formatted{};\n    BufferWriteExtent write_extent{};\n",
               "BufferResource extent member")
    return text.encode(), {"write_extent_metadata": True, "game_specific": False}


def adapt_resource_patch(data: bytes) -> tuple[bytes, dict]:
    text = data.decode()
    if "#include <optional>\n" not in text:
        if "#include <limits>\n" in text:
            text = one(text, "#include <limits>\n", "#include <limits>\n#include <optional>\n",
                       "optional include")
        else:
            text = one(text, "#include <algorithm>\n",
                       "#include <algorithm>\n#include <optional>\n", "optional include")

    anchor = "class Descriptors {\n"
    helper = r'''struct InvocationAffine {
    u64 constant{};
    std::array<u64, 3> local{};
    std::array<u64, 3> workgroup{};
    std::array<u64, 3> global{};
    u64 local_index{};
    u64 workgroup_index{};
};

static bool CheckedAdd(u64& dst, u64 value) {
    if (value > std::numeric_limits<u64>::max() - dst) return false;
    dst += value;
    return true;
}
static bool CheckedMul(u64& value, u64 factor) {
    if (factor && value > std::numeric_limits<u64>::max() / factor) return false;
    value *= factor;
    return true;
}
static bool AddAffine(InvocationAffine& out, const InvocationAffine& rhs) {
    if (!CheckedAdd(out.constant, rhs.constant) ||
        !CheckedAdd(out.local_index, rhs.local_index) ||
        !CheckedAdd(out.workgroup_index, rhs.workgroup_index)) return false;
    for (u32 i = 0; i < 3; ++i) {
        if (!CheckedAdd(out.local[i], rhs.local[i]) ||
            !CheckedAdd(out.workgroup[i], rhs.workgroup[i]) ||
            !CheckedAdd(out.global[i], rhs.global[i])) return false;
    }
    return true;
}
static bool ScaleAffine(InvocationAffine& out, u64 factor) {
    if (!CheckedMul(out.constant, factor) ||
        !CheckedMul(out.local_index, factor) ||
        !CheckedMul(out.workgroup_index, factor)) return false;
    for (u32 i = 0; i < 3; ++i) {
        if (!CheckedMul(out.local[i], factor) ||
            !CheckedMul(out.workgroup[i], factor) ||
            !CheckedMul(out.global[i], factor)) return false;
    }
    return true;
}

static std::optional<InvocationAffine> AnalyzeInvocationAffine(IR::Value value, u32 depth = 0) {
    if (depth > 24) return std::nullopt;
    if (value.IsImmediate()) {
        InvocationAffine out{};
        out.constant = value.U32();
        return out;
    }
    IR::Inst* inst = value.TryInst();
    if (!inst) return std::nullopt;
    switch (inst->GetOpcode()) {
    case IR::Opcode::GetAttributeU32: {
        if (!inst->Arg(1).IsImmediate()) return std::nullopt;
        const u32 comp = inst->Arg(1).U32();
        InvocationAffine out{};
        switch (inst->Arg(0).Attribute()) {
        case IR::Attribute::LocalInvocationId:
            if (comp >= 3) return std::nullopt; out.local[comp] = 1; return out;
        case IR::Attribute::WorkgroupId:
            if (comp >= 3) return std::nullopt; out.workgroup[comp] = 1; return out;
        case IR::Attribute::GlobalInvocationId:
            if (comp >= 3) return std::nullopt; out.global[comp] = 1; return out;
        case IR::Attribute::LocalInvocationIndex:
            out.local_index = 1; return out;
        case IR::Attribute::WorkgroupIndex:
            out.workgroup_index = 1; return out;
        default:
            return std::nullopt;
        }
    }
    case IR::Opcode::ReadFirstLane:
        return AnalyzeInvocationAffine(inst->Arg(0), depth + 1);
    case IR::Opcode::IAdd32: {
        auto lhs = AnalyzeInvocationAffine(inst->Arg(0), depth + 1);
        auto rhs = AnalyzeInvocationAffine(inst->Arg(1), depth + 1);
        if (!lhs || !rhs || !AddAffine(*lhs, *rhs)) return std::nullopt;
        return lhs;
    }
    case IR::Opcode::IMul32: {
        const bool imm0 = inst->Arg(0).IsImmediate();
        const bool imm1 = inst->Arg(1).IsImmediate();
        if (imm0 == imm1) return std::nullopt;
        const u64 factor = inst->Arg(imm0 ? 0 : 1).U32();
        auto out = AnalyzeInvocationAffine(inst->Arg(imm0 ? 1 : 0), depth + 1);
        if (!out || !ScaleAffine(*out, factor)) return std::nullopt;
        return out;
    }
    case IR::Opcode::ShiftLeftLogical32: {
        if (!inst->Arg(1).IsImmediate() || inst->Arg(1).U32() >= 32) return std::nullopt;
        auto out = AnalyzeInvocationAffine(inst->Arg(0), depth + 1);
        if (!out || !ScaleAffine(*out, u64{1} << inst->Arg(1).U32())) return std::nullopt;
        return out;
    }
    default:
        return std::nullopt;
    }
}

static std::optional<u32> BufferWriteWidthBytes(const IR::Inst& inst, const Profile& profile) {
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
    default: return std::nullopt;
    }
}

static void InvalidateWriteExtent(BufferWriteExtent& extent) {
    extent.seen = true;
    extent.complete = false;
}

static void UpdateBufferWriteExtent(IR::Inst& inst, Info& info, const Profile& profile) {
    if (!IsBufferStore(inst) || !inst.Arg(0).IsImmediate()) return;
    auto& desc = info.buffers[inst.Arg(0).U32()];
    if (desc.IsSpecial()) return;
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
    InvocationAffine bytes = *affine;
    if (!ScaleAffine(bytes, u64{1} << shift) ||
        !CheckedAdd(bytes.constant, static_cast<u64>(*width))) {
        InvalidateWriteExtent(desc.write_extent);
        return;
    }
    auto& extent = desc.write_extent;
    extent.seen = true;
    if (!extent.complete) return;
    extent.constant_end_bytes = std::max(extent.constant_end_bytes, bytes.constant);
    extent.local_index_coeff_bytes = std::max(extent.local_index_coeff_bytes, bytes.local_index);
    extent.workgroup_index_coeff_bytes =
        std::max(extent.workgroup_index_coeff_bytes, bytes.workgroup_index);
    for (u32 i = 0; i < 3; ++i) {
        extent.local_id_coeff_bytes[i] = std::max(extent.local_id_coeff_bytes[i], bytes.local[i]);
        extent.workgroup_id_coeff_bytes[i] =
            std::max(extent.workgroup_id_coeff_bytes[i], bytes.workgroup[i]);
        extent.global_id_coeff_bytes[i] =
            std::max(extent.global_id_coeff_bytes[i], bytes.global[i]);
    }
}

'''
    text = one(text, anchor, helper + anchor, "write-extent helpers")
    old = '''        if (IsBufferInstruction(inst)) {
            PatchBufferArgs(inst, info);
        } else if (IsImageInstruction(inst)) {'''
    new = '''        if (IsBufferInstruction(inst)) {
            PatchBufferArgs(inst, info);
            if (IsBufferStore(inst)) {
                UpdateBufferWriteExtent(inst, info, profile);
            }
        } else if (IsImageInstruction(inst)) {'''
    text = one(text, old, new, "write-extent collection")
    return text.encode(), {"affine_write_extent": True, "game_specific": False}


def adapt_srt(data: bytes) -> tuple[bytes, dict]:
    text = data.decode()

    # Stack balance.
    a, b = function_span(text, "PushPtr")
    old_fn = text[a:b]
    if old_fn.count("c.push(rdi);") == 1:
        new_fn = '''PushPtr(Xbyak::CodeGenerator& c, PassInfo& pass_info, const IR::Value& off_dw) {
    if (off_dw.IsImmediate()) {
        c.push(rdi);
        c.mov(rdi, ptr[rdi + (off_dw.U32() << 2)]);
    } else {
        const size_t code_begin = c.getSize();
        if (!ComputeOffset(c, r10d, pass_info, off_dw)) {
            c.setSize(code_begin);
            return false;
        }
        c.shl(r10d, 2);
        c.mov(r10d, r10d);
        c.push(rdi);
        c.mov(rdi, ptr[rdi + r10]);
    }
    c.mov(r10, 0xFFFFFFFFFFFFULL);
    c.and_(rdi, r10);
    return true;
}'''
        prefix_start = text.rfind("\n", 0, a) + 1
        prefix = text[prefix_start:a]
        if prefix.strip() != "static inline bool":
            raise RuntimeError("PushPtr declaration prefix changed")
        text = text[:prefix_start] + "static inline bool " + new_fn + text[b:]

    # Dynamic-left shift bug.
    a, b = function_span(text, "EmitComputeOffsetShiftLeftLogical32")
    fn = text[a:b]
    buggy = "        c.shl(reg, inst->Arg(0).U32());\n        c.mov(ecx, reg);\n"
    if buggy in fn:
        fn = fn.replace(buggy, "        c.mov(ecx, reg);\n", 1)
        text = text[:a] + fn + text[b:]

    # BFE width 32.
    a, b = function_span(text, "EmitComputeOffsetBitFieldUExtract")
    fn = text[a:b]
    buggy_bfe = "        c.and_(reg, (1U << inst->Arg(2).U32()) - 1);\n"
    if buggy_bfe in fn:
        fn = fn.replace(
            buggy_bfe,
            "        const u32 width = inst->Arg(2).U32();\n"
            "        if (width > 32) return false;\n"
            "        if (width < 32) c.and_(reg, (1U << width) - 1);\n",
            1)
        text = text[:a] + fn + text[b:]

    if "EmitComputeOffsetShiftRightArithmetic32" not in text:
        _, b = function_span(text, "EmitComputeOffsetShiftRightLogical32")
        helper = r'''

static bool EmitComputeOffsetShiftRightArithmetic32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                                     PassInfo& pass_info, IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.mov(ecx, reg);
        c.mov(reg, inst->Arg(0).U32());
        c.sar(reg, cl);
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.sar(reg, inst->Arg(1).U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.pop(rcx);
        c.sar(reg, cl);
    }
    return true;
}
'''
        text = text[:b] + helper + text[b:]

    if "EmitComputeOffsetFindILsb32" not in text:
        a, _ = function_span(text, "IsAllowedOffsetInstruction")
        line = text.rfind("\n", 0, a) + 1
        helper = r'''static bool EmitComputeOffsetFindILsb32(Xbyak::CodeGenerator& c,
                                        Xbyak::Reg32 reg, PassInfo& pass_info, IR::Inst* inst) {
    const IR::Value arg = inst->Arg(0);
    if (arg.IsImmediate()) {
        c.mov(reg, arg.U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, arg));
    }
    Xbyak::Label nonzero;
    c.bsf(reg, reg);
    c.jnz(nonzero);
    c.mov(reg, 0xffffffffu);
    c.L(nonzero);
    return true;
}

'''
        text = text[:line] + helper + text[line:]

    # Allowed opcodes.
    a, b = function_span(text, "IsAllowedOffsetInstruction")
    fn = text[a:b]
    for op in ("ReadFirstLane", "ShiftRightArithmetic32", "FindILsb32"):
        case = f"    case IR::Opcode::{op}:\n"
        if case not in fn:
            fn = fn.replace("    case IR::Opcode::IAdd32:\n", case + "    case IR::Opcode::IAdd32:\n", 1)
    text = text[:a] + fn + text[b:]

    # Evaluator cases.
    a, b = function_span(text, "ComputeOffset")
    fn = text[a:b]
    if "case IR::Opcode::ReadFirstLane:" not in fn:
        fn = fn.replace(
            "    case IR::Opcode::IAdd32:\n",
            "    case IR::Opcode::ReadFirstLane:\n"
            "        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));\n"
            "        return true;\n"
            "    case IR::Opcode::IAdd32:\n", 1)
    if "case IR::Opcode::ShiftRightArithmetic32:" not in fn:
        fn = fn.replace(
            "    case IR::Opcode::ShiftRightLogical32:\n"
            "        ABORT_ON_FAILURE(EmitComputeOffsetShiftRightLogical32(c, reg, pass_info, inst));\n"
            "        return true;\n",
            "    case IR::Opcode::ShiftRightLogical32:\n"
            "        ABORT_ON_FAILURE(EmitComputeOffsetShiftRightLogical32(c, reg, pass_info, inst));\n"
            "        return true;\n"
            "    case IR::Opcode::ShiftRightArithmetic32:\n"
            "        ABORT_ON_FAILURE(EmitComputeOffsetShiftRightArithmetic32(c, reg, pass_info, inst));\n"
            "        return true;\n", 1)
    if "case IR::Opcode::FindILsb32:" not in fn:
        fn = fn.replace(
            "    case IR::Opcode::BitFieldUExtract:\n"
            "        ABORT_ON_FAILURE(EmitComputeOffsetBitFieldUExtract(c, reg, pass_info, inst));\n"
            "        return true;\n",
            "    case IR::Opcode::BitFieldUExtract:\n"
            "        ABORT_ON_FAILURE(EmitComputeOffsetBitFieldUExtract(c, reg, pass_info, inst));\n"
            "        return true;\n"
            "    case IR::Opcode::FindILsb32:\n"
            "        ABORT_ON_FAILURE(EmitComputeOffsetFindILsb32(c, reg, pass_info, inst));\n"
            "        return true;\n", 1)
    text = text[:a] + fn + text[b:]

    # Roll back partial dynamic child offset code.
    a, b = function_span(text, "VisitPointer")
    fn = text[a:b]
    needle = "            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {\n"
    if needle in fn and "const size_t code_begin = c.getSize();" not in fn:
        fn = fn.replace(
            needle,
            "            const size_t code_begin = c.getSize();\n" + needle +
            "                c.setSize(code_begin);\n", 1)
    text = text[:a] + fn + text[b:]

    # Generic root dependency order.
    if "MakeSrtRootDependencyPlan" not in text:
        if "#include <unordered_set>\n" not in text:
            text = one(text, "#include <unordered_map>\n",
                       "#include <unordered_map>\n#include <unordered_set>\n",
                       "unordered_set include")
        marker = "static void GenerateSrtProgram(Info& info, PassInfo& pass_info) {\n"
        planner = r'''struct SrtRootDependencyPlan {
    std::vector<u32> order;
    std::vector<std::pair<u32, u32>> edges;
    bool reordered{};
    bool cyclic{};
    bool ambiguous{};
    bool capped{};
};

static SrtRootDependencyPlan MakeSrtRootDependencyPlan(PassInfo& pass_info) {
    SrtRootDependencyPlan plan;
    using Root = std::pair<IR::ScalarReg, IR::Inst*>;
    std::vector<Root> roots;
    for (const auto& [sgpr, root] : pass_info.srt_roots) roots.emplace_back(sgpr, root);
    for (u32 i = 0; i < roots.size(); ++i) plan.order.push_back(i);
    constexpr size_t MAX_ROOTS = 32, MAX_WALK_NODES = 4096, MAX_INDEX_NODES = 4096;
    if (roots.size() > MAX_ROOTS) { plan.capped = true; return plan; }

    std::unordered_map<IR::Inst*, u32> produced_by_root;
    std::vector<std::vector<IR::Value>> index_expressions(roots.size());
    size_t walked_nodes = 0;
    for (u32 root_id = 0; root_id < roots.size(); ++root_id) {
        std::vector<IR::Inst*> todo{roots[root_id].second};
        std::unordered_set<IR::Inst*> visited;
        for (size_t pos = 0; pos < todo.size(); ++pos) {
            if (++walked_nodes > MAX_WALK_NODES) { plan.capped = true; return plan; }
            auto* parent = todo[pos];
            if (!visited.insert(parent).second) continue;
            auto* children = pass_info.GetUsesAsPointer(parent);
            if (!children) continue;
            for (const auto& [src_offset, child] : *children) {
                index_expressions[root_id].push_back(src_offset);
                IR::Inst* canonical = pass_info.DeduplicateInstruction(child);
                for (IR::Inst* source : {child, canonical}) {
                    const auto [it, inserted] = produced_by_root.try_emplace(source, root_id);
                    if (!inserted && it->second != root_id) plan.ambiguous = true;
                }
                if (pass_info.GetUsesAsPointer(child)) todo.push_back(child);
            }
        }
    }
    if (plan.ambiguous) return plan;

    std::vector<std::vector<bool>> deps(roots.size(), std::vector<bool>(roots.size(), false));
    size_t index_nodes = 0;
    for (u32 consumer = 0; consumer < roots.size(); ++consumer) {
        std::vector<IR::Value> pending = index_expressions[consumer];
        std::unordered_set<IR::Inst*> visited;
        while (!pending.empty()) {
            if (++index_nodes > MAX_INDEX_NODES) { plan.capped = true; return plan; }
            const IR::Value value = pending.back(); pending.pop_back();
            if (value.IsImmediate()) continue;
            IR::Inst* inst = value.TryInst();
            if (!inst || !visited.insert(inst).second) continue;
            const auto op = inst->GetOpcode();
            if (op == IR::Opcode::ReadConst || op == IR::Opcode::ReadConstBuffer) {
                auto it = produced_by_root.find(inst);
                if (it == produced_by_root.end())
                    it = produced_by_root.find(pass_info.DeduplicateInstruction(inst));
                if (it != produced_by_root.end() && it->second != consumer)
                    deps[consumer][it->second] = true;
                continue;
            }
            if (op == IR::Opcode::GetUserData || !IsAllowedOffsetInstruction(inst)) continue;
            for (u32 arg = 0; arg < inst->NumArgs(); ++arg) {
                IR::Value child = inst->Arg(arg);
                if (!child.IsImmediate()) pending.push_back(child);
            }
        }
    }
    for (u32 c = 0; c < roots.size(); ++c)
        for (u32 p = 0; p < roots.size(); ++p)
            if (deps[c][p]) plan.edges.emplace_back(c, p);

    std::vector<u32> sorted;
    std::vector<bool> done(roots.size(), false);
    while (sorted.size() < roots.size()) {
        bool advanced = false;
        for (u32 candidate = 0; candidate < roots.size(); ++candidate) {
            if (done[candidate]) continue;
            bool ready = true;
            for (u32 d = 0; d < roots.size(); ++d)
                if (deps[candidate][d] && !done[d]) { ready = false; break; }
            if (ready) {
                done[candidate] = true;
                sorted.push_back(candidate);
                advanced = true;
                break;
            }
        }
        if (!advanced) { plan.cyclic = true; return plan; }
    }
    plan.reordered = sorted != plan.order;
    plan.order = std::move(sorted);
    return plan;
}

'''
        text = one(text, marker, planner + marker, "generic root planner")

        old_loop = '''    for (const auto& [sgpr_base, root] : pass_info.srt_roots) {
        VisitPointer(IR::Value(static_cast<u32>(sgpr_base)), root, pass_info, c);
    }
'''
        new_loop = '''    std::vector<std::pair<IR::ScalarReg, IR::Inst*>> roots;
    roots.reserve(pass_info.srt_roots.size());
    for (const auto& [sgpr, root] : pass_info.srt_roots) roots.emplace_back(sgpr, root);
    const auto plan = MakeSrtRootDependencyPlan(pass_info);
    for (const u32 index : plan.order) {
        const auto [sgpr, root] = roots[index];
        VisitPointer(IR::Value(static_cast<u32>(sgpr)), root, pass_info, c);
    }
'''
        text = one(text, old_loop, new_loop, "generic root traversal")

    return text.encode(), {
        "stack_balance": True,
        "read_first_lane": True,
        "arithmetic_right_shift": True,
        "find_lsb": True,
        "bfe_width_32": True,
        "partial_codegen_rollback": True,
        "generic_dependency_order": True,
        "game_specific": False,
    }


def adapt_buffer_cpp(data: bytes) -> tuple[bytes, dict]:
    text = data.decode()
    text, range_proof = adapt_texel_image_ranges(text)

    if "#include <atomic>\n" not in text:
        text = one(text, "#include <algorithm>\n", "#include <algorithm>\n#include <atomic>\n",
                   "atomic include")

    old_sig = '''std::pair<const Buffer*, u64> BufferCache::ObtainBuffer(VAddr device_addr, u32 size,
                                                        ObtainBufferFlags flags) {'''
    new_sig = '''std::pair<const Buffer*, u64> BufferCache::ObtainBuffer(VAddr device_addr, u32 size,
                                                        ObtainBufferFlags flags,
                                                        u32 proven_write_size) {'''
    text = one(text, old_sig, new_sig, "ObtainBuffer signature")

    old_gate = '''    // For read-only buffers use device local stream buffer to reduce renderpass breaks.
    if (!is_written && !skip_stream_buffer && size <= STREAM_THRESHOLD &&
        !IsRegionGpuModified(device_addr, size)) {'''
    new_gate = '''    const bool stream_eligible =
        !is_written && !skip_stream_buffer && size <= STREAM_THRESHOLD &&
        !IsRegionGpuModified(device_addr, size);
    if (stream_eligible && (!is_texel_buffer || size == 0)) {'''
    text = one(text, old_gate, new_gate, "formatted stream bypass")

    old_sync = '''    if (is_written) {
        texture_cache.MarkAsMaybeGpuDirty(device_addr, size);
    }
    SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);
    if (is_written) {
        gpu_modified_ranges.Add(device_addr, size);
    }'''
    new_sync = '''    const bool bounded_write =
        is_written && !is_texel_buffer && proven_write_size != 0 && proven_write_size < size;
    const u64 gpu_write_size = bounded_write ? proven_write_size : size;
    if (is_written) {
        texture_cache.MarkAsMaybeGpuDirty(device_addr, gpu_write_size);
    }
    const bool image_synchronized =
        SynchronizeMemory(arena, device_addr, size, is_written && !bounded_write, is_texel_buffer);
    if (stream_eligible && is_texel_buffer) {
        static std::atomic<u64> texel_sync_count{0};
        if (image_synchronized) texel_sync_count.fetch_add(1, std::memory_order_relaxed);
    }
    if (is_written) {
        if (bounded_write) {
            memory_tracker->MarkRegionAsGpuModifiedTracked(device_addr, proven_write_size);
            gpu_modified_ranges.Add(device_addr, proven_write_size);
        } else {
            gpu_modified_ranges.Add(device_addr, size);
        }
    }'''
    text = one(text, old_sync, new_sync, "bounded write ownership")
    return text.encode(), {
        **range_proof,
        "formatted_reads_bypass_cpu_stream": True,
        "bounded_write_ownership": True,
        "game_specific": False,
    }


def adapt_rasterizer(data: bytes) -> tuple[bytes, dict]:
    text = data.decode()
    for header in ("atomic", "limits", "optional"):
        line = f"#include <{header}>\n"
        if line not in text:
            pos = text.find("\n\nnamespace Vulkan")
            if pos < 0:
                raise RuntimeError("rasterizer include insertion point missing")
            text = text[:pos] + "\n" + line.rstrip("\n") + text[pos:]

    marker = "void Rasterizer::BindBuffers(const Shader::Info& stage"
    pos = text.find(marker)
    if pos < 0:
        raise RuntimeError("BindBuffers missing")
    line = text.rfind("\n", 0, pos) + 1
    helper = r'''static std::optional<u64> ComputeProvenWriteSize(
    const Shader::BufferWriteExtent& extent, const std::array<u64, 3>& local,
    const std::array<u64, 3>& groups) {
    if (!extent.Usable()) return std::nullopt;
    for (u32 i = 0; i < 3; ++i) if (!local[i] || !groups[i]) return std::nullopt;
    const auto mul = [](u64 a, u64 b, u64& out) {
        if (a && b > std::numeric_limits<u64>::max() / a) return false;
        out = a * b; return true;
    };
    const auto add = [](u64& a, u64 b) {
        if (b > std::numeric_limits<u64>::max() - a) return false;
        a += b; return true;
    };
    u64 end = extent.constant_end_bytes, local_count = 1, group_count = 1;
    for (u32 i = 0; i < 3; ++i) {
        u64 term{}, global_count{};
        if (!mul(extent.local_id_coeff_bytes[i], local[i]-1, term) || !add(end, term) ||
            !mul(extent.workgroup_id_coeff_bytes[i], groups[i]-1, term) || !add(end, term) ||
            !mul(local[i], groups[i], global_count) || !global_count ||
            !mul(extent.global_id_coeff_bytes[i], global_count-1, term) || !add(end, term) ||
            !mul(local_count, local[i], local_count) ||
            !mul(group_count, groups[i], group_count)) return std::nullopt;
    }
    u64 term{};
    if (!mul(extent.local_index_coeff_bytes, local_count-1, term) || !add(end, term) ||
        !mul(extent.workgroup_index_coeff_bytes, group_count-1, term) || !add(end, term))
        return std::nullopt;
    return end;
}

'''
    text = text[:line] + helper + text[line:]

    old = '''                const auto [buffer, offset] =
                    buffer_cache.ObtainBuffer(vsharp.base_address, size, flags);'''
    new = '''                u64 tracked_write_size = size;
                if (desc.is_written && !desc.is_formatted &&
                    stage.sw_stage == Shader::SwStage::Compute) {
                    const auto& cs = liverpool->GetCsRegs();
                    const std::array<u64, 3> local = {
                        cs.num_thread_x.full, cs.num_thread_y.full, cs.num_thread_z.full};
                    const std::array<u64, 3> groups = {cs.dim_x, cs.dim_y, cs.dim_z};
                    if (const auto proven = ComputeProvenWriteSize(desc.write_extent, local, groups);
                        proven && *proven != 0 && *proven < tracked_write_size) {
                        tracked_write_size = *proven;
                    }
                }
                const auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, size, flags, static_cast<u32>(tracked_write_size));'''
    text = one(text, old, new, "bounded buffer acquisition")

    text = one(text,
'''                if (desc.is_written) {
                    // Raw storage-buffer writes can also make an aliased cached image stale.
                    texture_cache.InvalidateMemoryFromGPU(vsharp.base_address, size);
                }''',
'''                if (desc.is_written) {
                    texture_cache.InvalidateMemoryFromGPU(vsharp.base_address, tracked_write_size);
                }''', "bounded image invalidation")

    old_read = '''bool Rasterizer::ReadMemory(VAddr addr, u64 size, bool assume_locks) {
    if (!IsMapped(addr, size)) {
        // Not GPU mapped memory, can skip invalidation logic entirely.
        return false;
    }
    buffer_cache.ReadMemory(addr, size, false, false, assume_locks);
    return true;
}'''
    new_read = '''bool Rasterizer::ReadMemory(VAddr addr, u64 size, bool assume_locks) {
    if (!IsMapped(addr, size)) {
        return false;
    }
    buffer_cache.ReadMemory(addr, size, false, false, assume_locks);
    return true;
}'''
    text = one(text, old_read, new_read, "readback path normalization")
    return text.encode(), {
        "bounded_runtime_write_extent": True,
        "normal_readback_path": True,
        "game_specific": False,
    }


def adapt_serialization(data: bytes) -> tuple[bytes, dict]:
    text = data.decode()
    m = re.search(r"static constexpr u32 ShaderMetaVersion = (\d+)u;", text)
    if not m:
        raise RuntimeError("ShaderMetaVersion missing")
    old = int(m.group(1))
    text = text[:m.start(1)] + str(old + 1) + text[m.end(1):]
    return text.encode(), {"shader_meta_version": [old, old + 1], "game_specific": False}


def apply_to_tree(root: Path) -> dict:
    root = root.resolve()
    plan = [
        (FAULT_CPP, adapt_fault_manager),
        (BUFFER_H, adapt_buffer_header),
        (BUFFER_CPP, adapt_buffer_cpp),
        (REGION_H, adapt_region_manager),
        (MEMORY_TRACKER_H, adapt_memory_tracker),
        (RESOURCE_H, adapt_resource_h),
        (RESOURCE_PATCH, adapt_resource_patch),
        (FLAT, adapt_srt),
        (RASTER, adapt_rasterizer),
        (SERIAL, adapt_serialization),
    ]
    proof = {}
    for rel, fn in plan:
        p = root / rel
        if not p.is_file() or p.is_symlink():
            raise RuntimeError("missing/unsafe source: " + rel)
        before = p.read_bytes()
        after, item = fn(before)
        if after == before:
            raise RuntimeError("transformation made no change: " + rel)
        p.write_bytes(after)
        proof[rel] = {
            **item,
            "before_sha256": hashlib.sha256(before).hexdigest(),
            "after_sha256": hashlib.sha256(after).hexdigest(),
        }

    forbidden = (
        "SHADPS4_GOW_", "CUSA34384", "GOW43_", "GOW57_", "GOW58_",
        "GOW_SRT_", "0xe83356c8", "0xad0b8f1f", "0x04691bd5",
    )
    bad = {}
    for p in (root / "src").rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        try:
            t = p.read_text(errors="ignore")
        except Exception:
            continue
        hits = [x for x in forbidden if x in t]
        if hits:
            bad[str(p.relative_to(root))] = hits
    if bad:
        raise RuntimeError("game-specific source markers remain: " + repr(bad))

    return proof
