#!/usr/bin/env python3
"""Bounded SRT parent-dependency diagnostics on exactly archived compiled GoT sources.

Logs only unresolved flattened table locations. Does not change IR/guest
resource indexing or host SRT scheduling, and refuses unknown local revisions.
"""
from pathlib import Path
import subprocess,sys
root=Path(sys.argv[1]); srt=root/'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp'; serial=root/'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
def sha(p):return subprocess.check_output(['git','hash-object',str(p)],text=True).strip()
assert sha(srt)=='1447a450ca062fa98595ceda766604758a155fe9', f'SRT source revision changed: {sha(srt)}'
assert sha(serial)=='c7a35e3eac405d8ba8880c7fc41aee0367087b08', f'Shader serializer changed: {sha(serial)}'
s=srt.read_text();v=serial.read_text()
def replace(old,new,reason):
 global s
 assert s.count(old)==1,(reason,s.count(old))
 s=s.replace(old,new,1)
replace('#include <unordered_map>','#include <atomic>\n#include <unordered_map>','bounded diagnostic atomic')
old='''    if (schedule.pending != 0) {
        LOG_ERROR(Render_Recompiler,
                  "SRT dependency resolution stalled after {} rounds: {} unresolved reads",
                  schedule.rounds, schedule.pending);
    }'''
new='''    if (schedule.pending != 0) {
        LOG_ERROR(Render_Recompiler,
                  "SRT dependency resolution stalled after {} rounds: {} unresolved reads",
                  schedule.rounds, schedule.pending);
        // Which exact parent reads prevent the *real* host walker from
        // advancing? Never evaluate or substitute a GPU-varying offset.
        static std::atomic<u32> reports{0};
        for (const auto& [ptr_source, reads] : pass_info.pointer_uses) {
            for (const auto& [index, read] : reads) {
                if (GetFlatbufOffset(read) != 0u) {
                    continue;
                }
                if (reports.fetch_add(1u, std::memory_order_relaxed) >= 80u) {
                    break;
                }
                const IR::Inst* op = index.TryInst();
                const IR::Inst* child0 = op && op->NumArgs() ? op->Arg(0).TryInst() : nullptr;
                const IR::Inst* child1 = op && op->NumArgs() > 1 ? op->Arg(1).TryInst() : nullptr;
                const u16 parent_slot = GetFlatbufOffset(pass_info.DeduplicateInstruction(read));
                LOG_WARNING(Render_Recompiler,
                            "SrtPendingParent: shader={:#x} read_op={} ptr_op={} "
                            "index_op={} child0_op={} child1_op={} host_uniform={} "
                            "canonical_slot={} pending={} dynamic={}",
                            info.pgm_hash, static_cast<u32>(read->GetOpcode()),
                            static_cast<u32>(ptr_source->GetOpcode()),
                            op ? static_cast<u32>(op->GetOpcode()) : 0u,
                            child0 ? static_cast<u32>(child0->GetOpcode()) : 0u,
                            child1 ? static_cast<u32>(child1->GetOpcode()) : 0u,
                            IsHostUniformSrtOffset(index), parent_slot,
                            schedule.pending, pass_info.dynamic_srt_reads);
            }
        }
    }'''
replace(old,new,'rejected SRT parent diagnostic')
needle='static constexpr u32 ShaderBinaryVersion = 0x17u;'
assert v.count(needle)==1,'unexpected shader binary version'
v=v.replace(needle,'static constexpr u32 ShaderBinaryVersion = 0x18u;',1)
srt.write_text(s);serial.write_text(v)
print('DIAGNOSTIC_SRT_SOURCE_SHA='+sha(srt));print('DIAGNOSTIC_SERIAL_SHA='+sha(serial));print('SRT_WALKER_SEMANTICS_CHANGED=NO')
