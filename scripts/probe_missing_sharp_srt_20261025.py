#!/usr/bin/env python3
"""Exact-source, instrumentation-only SRT/SHARP producer audit.

No descriptors or shader values are modified. Abort unless each original
source site matches the previously successful native GoT archive.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
res = root / "src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
flat = root / "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
inc = root / "src/shader_recompiler/ir/passes/srt_dependency_codegen.inc"

def once(src, old, new, name):
    count = src.count(old)
    if count != 1:
        raise RuntimeError(f"{name}: expected one original source pattern, found {count}")
    return src.replace(old, new, 1)

s = res.read_text()
s = once(s, "#include <limits>\n", "#include <limits>\n#include <unordered_map>\n", "SHARP includes")
s = once(s, "SharpLocation SharpLocationFromSource(const IR::Inst* inst) {",
'''SharpLocation SharpLocationFromSource(const IR::Inst* inst, u64 shader_hash,
                                      const char* resource_kind, u32 word) {
    // Report which shader and which exact descriptor producer failed; never
    // invent CPU-side values for GPU-dependent SHARP expressions.
    const auto report_unresolved = [&](u32 slot, const char* reason) {
        static thread_local std::unordered_map<u64, u32> per_shader;
        auto it = per_shader.find(shader_hash);
        if (it == per_shader.end()) {
            if (per_shader.size() >= 256) return;
            it = per_shader.emplace(shader_hash, 0).first;
        }
        if (it->second++ >= 4) return;
        const auto* base = inst->NumArgs() > 0 ? inst->Arg(0).TryInst() : nullptr;
        const bool has_index = inst->NumArgs() > 1;
        const auto* index = has_index ? inst->Arg(1).TryInst() : nullptr;
        const bool index_imm = has_index && inst->Arg(1).IsImmediate();
        const u32 immediate = index_imm && inst->Arg(1).Type() == IR::Type::U32
                                  ? inst->Arg(1).U32() : 0;
        LOG_WARNING(Render_Recompiler,
                    "SharpMissing: shader={:#x} kind={} word={} source_op={} "
                    "base_op={} index_op={} index_immediate={} offset={} slot={} reason={}",
                    shader_hash, resource_kind, word, static_cast<u32>(inst->GetOpcode()),
                    base ? static_cast<s32>(base->GetOpcode()) : -1,
                    index ? static_cast<s32>(index->GetOpcode()) : -1,
                    index_imm, immediate, slot, reason);
    };''', "SHARP producer context")
s = once(s,
'''        // Unrecognised descriptor producers have no valid CPU SHARP offset.
        // Never interpret arbitrary IR instruction metadata as an address.
        return UNKNOWN_LOCATION;''',
'''        // Unrecognised descriptor producers have no valid CPU SHARP offset.
        // Never interpret arbitrary IR instruction metadata as an address.
        report_unresolved(0u, "unsupported_opcode");
        return UNKNOWN_LOCATION;''', "unsupported SHARP producer")
s = once(s,
'''    if (location == 0) {
        LOG_WARNING(Render_Recompiler, "Sharp source was not flatenned");
        return UNKNOWN_LOCATION;
    }''',
'''    if (location == 0) {
        report_unresolved(location, "unflattened_read");
        return UNKNOWN_LOCATION;
    }''', "unflattened SHARP")
s = once(s, "SharpFetch<T> ConstructSharpFetch(const SharpReference& sharp) {",
'''SharpFetch<T> ConstructSharpFetch(const SharpReference& sharp, u64 shader_hash,
                                  const char* resource_kind) {''', "pass SHARP producer context")
s = once(s, "SharpLocationFromSource(dword.Inst());",
         "SharpLocationFromSource(dword.Inst(), shader_hash, resource_kind, i);",
         "log SHARP word")
replacements = [
    ("ConstructSharpFetch<AmdGpu::Buffer>(resource.sharps[0])",
     'ConstructSharpFetch<AmdGpu::Buffer>(resource.sharps[0], info.pgm_hash, "buffer")'),
    ("ConstructSharpFetch<AmdGpu::Image>(resource.sharps[0])",
     'ConstructSharpFetch<AmdGpu::Image>(resource.sharps[0], info.pgm_hash, "image")'),
    ("ConstructSharpFetch<AmdGpu::Buffer>(resource.indexed_table_sharp)",
     'ConstructSharpFetch<AmdGpu::Buffer>(resource.indexed_table_sharp, info.pgm_hash, "image_table")'),
    ("ConstructSharpFetch<AmdGpu::Sampler>(resource.sharps[1])",
     'ConstructSharpFetch<AmdGpu::Sampler>(resource.sharps[1], info.pgm_hash, "sampler")'),
    ("SharpLocationFromSource(resource.sharps[0].dwords[0].Inst())",
     'SharpLocationFromSource(resource.sharps[0].dwords[0].Inst(), info.pgm_hash, "fmask", 0u)'),
    ("SharpLocationFromSource(lod_prod.Inst())",
     'SharpLocationFromSource(lod_prod.Inst(), info.pgm_hash, "sampler_lod", 0u)'),
]
for i, (before, after) in enumerate(replacements):
    s = once(s, before, after, f"SHARP call {i}")

t = flat.read_text()
t = once(t, "    size_t dynamic_srt_reads{};",
'''    size_t dynamic_srt_reads{};
    // Why the current shader's last SRT scheduler round is blocked.
    size_t blocked_parent_slot{};
    size_t blocked_dynamic_pointer{};
    size_t blocked_pointer_cycle{};
    size_t blocked_pointer_setup{};
    size_t blocked_dynamic_child_index{};
    size_t deferred_child_offset{};''', "SRT reason counters")
t = once(t, "            pass_info.dynamic_srt_reads = 0;",
'''            pass_info.dynamic_srt_reads = 0;
            pass_info.blocked_parent_slot = 0;
            pass_info.blocked_dynamic_pointer = 0;
            pass_info.blocked_pointer_cycle = 0;
            pass_info.blocked_pointer_setup = 0;
            pass_info.blocked_dynamic_child_index = 0;
            pass_info.deferred_child_offset = 0;''', "SRT reason reset")
t = once(t,
'''    if (schedule.pending != 0) {
        LOG_ERROR(Render_Recompiler,
                  "SRT dependency resolution stalled after {} rounds: {} unresolved reads",
                  schedule.rounds, schedule.pending);
    }''',
'''    if (schedule.pending != 0) {
        LOG_ERROR(Render_Recompiler,
                  "SRT dependency resolution stalled shader={:#x} after {} rounds: {} "
                  "unresolved reads ({} GPU-dynamic)",
                  info.pgm_hash, schedule.rounds, schedule.pending,
                  pass_info.dynamic_srt_reads);
        static thread_local std::unordered_map<u64, u32> reported;
        auto it = reported.find(info.pgm_hash);
        if (it == reported.end() && reported.size() < 128) {
            it = reported.emplace(info.pgm_hash, 0).first;
        }
        if (it != reported.end() && it->second++ < 2) {
            LOG_ERROR(Render_Recompiler,
                      "SRTBlockReasons: shader={:#x} no_parent_slot={} "
                      "dynamic_pointer={} pointer_cycle={} push_failure={} "
                      "dynamic_child_index={} deferred_child_index={} pending={} roots={}",
                      info.pgm_hash, pass_info.blocked_parent_slot,
                      pass_info.blocked_dynamic_pointer, pass_info.blocked_pointer_cycle,
                      pass_info.blocked_pointer_setup, pass_info.blocked_dynamic_child_index,
                      pass_info.deferred_child_offset, schedule.pending, pass_info.srt_roots.size());
            u32 shown = 0;
            for (const auto& [parent, reads] : pass_info.pointer_uses) {
                const auto op = parent->GetOpcode();
                const bool scalar_parent = op == IR::Opcode::ReadConst ||
                                           op == IR::Opcode::ReadConstBuffer;
                const u32 parent_slot = scalar_parent ? GetFlatbufOffset(parent) : 0u;
                for (const auto& [offset, read] : reads) {
                    if (GetFlatbufOffset(read) != 0 || shown >= 18) continue;
                    const auto* canonical = pass_info.DeduplicateInstruction(read);
                    const auto* inst_offset = offset.TryInst();
                    LOG_WARNING(Render_Recompiler,
                                "SRTBlockedRead: shader={:#x} parent_op={} parent_slot={} "
                                "read_op={} read_slot={} canonical_slot={} offset_op={} "
                                "offset_immediate={} offset_dw={} uniform_offset={}",
                                info.pgm_hash, static_cast<u32>(op), parent_slot,
                                static_cast<u32>(read->GetOpcode()), GetFlatbufOffset(read),
                                GetFlatbufOffset(canonical),
                                inst_offset ? static_cast<s32>(inst_offset->GetOpcode()) : -1,
                                offset.IsImmediate(), offset.IsImmediate() &&
                                offset.Type() == IR::Type::U32 ? offset.U32() : 0u,
                                IsHostUniformSrtOffset(offset));
                    ++shown;
                }
                if (shown >= 18) break;
            }
        }
    }''', "exact SRT stall diagnostics")

u = inc.read_text()
u = once(u, '''                ++pass_info.pending_srt_reads;
                return;''',
'''                ++pass_info.pending_srt_reads;
                ++pass_info.blocked_parent_slot;
                return;''', "pending parent")
u = once(u,
'''    if (!IsHostUniformSrtOffset(off_dw)) {
        ++pass_info.dynamic_srt_reads;
        return;
    }''',
'''    if (!IsHostUniformSrtOffset(off_dw)) {
        ++pass_info.dynamic_srt_reads;
        ++pass_info.blocked_dynamic_pointer;
        return;
    }''', "nonuniform parent")
u = once(u,
'''    if (!pass_info.active_srt_pointers.insert(subtree).second) {
        ++pass_info.pending_srt_reads;
        return;
    }''',
'''    if (!pass_info.active_srt_pointers.insert(subtree).second) {
        ++pass_info.pending_srt_reads;
        ++pass_info.blocked_pointer_cycle;
        return;
    }''', "SRT cyclic dependency")
u = once(u,
'''        ++pass_info.pending_srt_reads;
        return;
    }
    PassInfo::PtrUserList* use_list''',
'''        ++pass_info.pending_srt_reads;
        ++pass_info.blocked_pointer_setup;
        return;
    }
    PassInfo::PtrUserList* use_list''', "SRT pointer setup failure")
u = once(u,
'''        if (!IsHostUniformSrtOffset(src_off_dw)) {
            return SrtReadAttempt::Unsupported;
        }''',
'''        if (!IsHostUniformSrtOffset(src_off_dw)) {
            ++pass_info.blocked_dynamic_child_index;
            return SrtReadAttempt::Unsupported;
        }''', "SRT GPU-dynamic child")
u = once(u,
'''            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {
                c.setSize(checkpoint);
                return SrtReadAttempt::Deferred;
            }''',
'''            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {
                c.setSize(checkpoint);
                ++pass_info.deferred_child_offset;
                return SrtReadAttempt::Deferred;
            }''', "SRT unresolved child offset")

# Never mutate sources if even one anchor fails.
for path, content in ((res, s), (flat, t), (inc, u)):
    path.write_text(content)
    print(f"INSTRUMENTED {path.relative_to(root)}", flush=True)
