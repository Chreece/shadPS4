#!/usr/bin/env python3
"""Preimage-pinned SRT failure-only provenance tracing, test worktree only."""
import argparse
import hashlib
from pathlib import Path

PROTECTED_SHA = "14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161"
CHANGES = (
  ('#include <unordered_map>', '#include <atomic>\n#include <unordered_map>'),
  ('    u32 dynamic_offsets_deferred{};',
   '    u32 dynamic_offsets_deferred{};\n    u64 diagnostic_shader_hash{};'),
  ('''    case IR::Opcode::ReadConst:
    case IR::Opcode::ReadConstBuffer:
        if (u16 offset = GetFlatbufOffset(pass_info.DeduplicateInstruction(inst)); offset != 0) {
            c.mov(reg, ptr[rsi + (offset << 2)]);
            return true;
        }
        return false;''',
   '''    case IR::Opcode::ReadConst:
    case IR::Opcode::ReadConstBuffer: {
        IR::Inst* canonical = pass_info.DeduplicateInstruction(inst);
        const u16 offset = GetFlatbufOffset(canonical);
        if (offset != 0) {
            c.mov(reg, ptr[rsi + (offset << 2)]);
            return true;
        }
        static std::atomic<u32> reports{0};
        if (reports.fetch_add(1, std::memory_order_relaxed) < 128) {
            LOG_ERROR(Render_Recompiler,
                      "GOW_SRT_UNASSIGNED shader={:#x} source_opcode={} "
                      "original_flatbuf={} canonical_flatbuf={} same_inst={} dest_offset={}",
                      pass_info.diagnostic_shader_hash,
                      static_cast<u32>(inst->GetOpcode()),
                      GetFlatbufOffset(inst), offset, inst == canonical, pass_info.dst_off_dw);
        }
        return false;
    }'''),
  ('''    PassInfo pass_info;

    // traverse at end and assign offsets to duplicate readconsts''',
   '''    PassInfo pass_info;
    pass_info.diagnostic_shader_hash = program.info.pgm_hash;

    // traverse at end and assign offsets to duplicate readconsts''')
)
def patched(src):
    if hashlib.sha256(src).hexdigest() != PROTECTED_SHA:
        raise ValueError("PROTECTED_SRT_PREIMAGE_SHA256_MISMATCH")
    original=src.decode()
    out=original
    for before,after in CHANGES:
        if out.count(before) != 1: raise ValueError("SOURCE_ANCHOR_NOT_UNIQUE")
        out=out.replace(before,after,1)
    recovered=out
    for before,after in reversed(CHANGES):
        recovered=recovered.replace(after,before,1)
    if recovered != original: raise ValueError("PATCH_NOT_REVERSIBLE")
    return out.encode()
def main():
    p=argparse.ArgumentParser()
    p.add_argument("source",type=Path)
    p.add_argument("--apply",action="store_true")
    a=p.parse_args()
    if not a.source.is_file() or a.source.is_symlink():
        raise SystemExit("SRT_SOURCE_NOT_REGULAR_FILE")
    try:
        original=a.source.read_bytes()
        changed=patched(original)
    except ValueError as error:
        raise SystemExit("SRT_PROBE_PRECHECK_FAILED="+str(error))
    if a.apply:
        a.source.write_bytes(changed)
    print("SRT_PROVENANCE_TRACE="+("APPLIED" if a.apply else "PREFLIGHT_PASS"))
    print("SRT_SOURCE_REVERSE_EQUAL=YES; SRT_PROGRAM_BEHAVIOR_UNCHANGED=YES")
if __name__=="__main__":main()
