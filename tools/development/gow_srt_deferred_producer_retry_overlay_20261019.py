#!/usr/bin/env python3
"""Add bounded dependency-first SRT retry ONLY to proven GoW source.

If a dynamic index depends on a ReadConst/ReadConstBuffer whose flatbuf offset
was not assigned yet, retry after child producers have executed. Preserve the
original emission order/offsets for successful reads, and never fabricate a
missing flattened buffer value. Unknown source versions fail closed.
"""
from pathlib import Path
import argparse
import difflib
import hashlib

PROTECTED_SHA = "14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161"
CHANGES = (
    ("shader identity",
     "    u32 dynamic_offsets_deferred{};\n",
     "    u32 dynamic_offsets_deferred{};\n    u64 srt_shader_hash{};\n"),
    ("shader hash initialization",
     "static void GenerateSrtProgram(Info& info, PassInfo& pass_info) {\n    Xbyak::CodeGenerator& c = g_srt_codegen;",
     "static void GenerateSrtProgram(Info& info, PassInfo& pass_info) {\n    Xbyak::CodeGenerator& c = g_srt_codegen;\n    pass_info.srt_shader_hash = info.pgm_hash;"),
    ("deferred entry list",
     "    // First copy all the src data from this tree level\n",
     "    // Defer only missing dynamic-index dependencies; valid entries\n"
     "    // retain their original guest/flattened-buffer output order.\n"
     "    std::vector<std::pair<IR::Value, IR::Inst*>> deferred_reads;\n"
     "    // First copy all the src data from this tree level\n"),
    ("defer unassigned offset",
     """            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {
                c.setSize(code_begin);
                LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");
                continue;
            }""",
     """            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {
                c.setSize(code_begin);
                deferred_reads.emplace_back(src_off_dw, use);
                continue;
            }"""),
    ("bounded producer-first retry",
     """    // Then visit any children used as pointers
    for (const auto [src_off_dw, use] : *use_list) {
        if (pass_info.GetUsesAsPointer(use)) {
            VisitPointer(src_off_dw, use, pass_info, c);
        }
    }

    PopPtr(c);""",
     """    // Then visit any children used as pointers. A previously missing
    // flattened-buffer producer may be materialized in one of these children.
    for (const auto [src_off_dw, use] : *use_list) {
        if (GetFlatbufOffset(use) != 0 && pass_info.GetUsesAsPointer(use)) {
            VisitPointer(src_off_dw, use, pass_info, c);
        }
    }

    // Retry ONLY previously unassigned dynamic-index loads. Do this while
    // RDI still addresses the current SRT table (nested visits restore RDI).
    // A resolved source may unlock another deferred consumer, so use bounded
    // passes. A remaining unresolved dependency retains its preexisting
    // unflattened guest-memory fallback and is never assigned a fake slot.
    if (!deferred_reads.empty()) {
        size_t recovered = 0;
        const size_t max_rounds = deferred_reads.size();
        for (size_t round = 0; round < max_rounds && !deferred_reads.empty(); ++round) {
            bool progress = false;
            for (auto it = deferred_reads.begin(); it != deferred_reads.end();) {
                const auto& [src_off_dw, use] = *it;
                if (GetFlatbufOffset(pass_info.DeduplicateInstruction(use)) != 0) {
                    it = deferred_reads.erase(it);
                    progress = true;
                    continue;
                }
                const size_t code_begin = c.getSize();
                if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {
                    c.setSize(code_begin);
                    ++it;
                    continue;
                }
                c.shl(r10d, 2);
                c.mov(r10d, r10d);
                c.mov(r10d, dword[rdi + r10]);
                c.mov(ptr[rsi + (pass_info.dst_off_dw << 2)], r10d);
                SetFlatbufOffset(use, pass_info.dst_off_dw++);
                if (pass_info.GetUsesAsPointer(use)) {
                    VisitPointer(src_off_dw, use, pass_info, c);
                }
                ++recovered;
                progress = true;
                it = deferred_reads.erase(it);
            }
            if (!progress) {
                break;
            }
        }
        if (recovered != 0) {
            static thread_local u32 recovery_reports = 0;
            if (recovery_reports++ < 32) {
                LOG_WARNING(Render_Recompiler,
                            "GOW_SRT_DEPENDENCY_RECOVERED shader={:#x} count={} remaining={}",
                            pass_info.srt_shader_hash, recovered, deferred_reads.size());
            }
        }
        for (const auto& entry : deferred_reads) {
            LOG_ERROR(Render_Recompiler,
                      "Failed to compute offset for SRT walker shader={:#x} "
                      "unresolved_opcode={} dst_off={}",
                      pass_info.srt_shader_hash,
                      static_cast<u32>(entry.first.Inst()->GetOpcode()),
                      pass_info.dst_off_dw);
        }
    }

    PopPtr(c);"""),
)

def transform(source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != PROTECTED_SHA:
        raise ValueError("UNRECOGNIZED_PROTECTED_SRT_PREIMAGE")
    before = source.decode("utf8")
    after = before
    for name, old, new in CHANGES:
        if after.count(old) != 1:
            raise ValueError("SRT_"+name.replace(" ","_")+"_ANCHOR_COUNT="+str(after.count(old)))
        after = after.replace(old, new, 1)
    recovered = after
    for name, old, new in reversed(CHANGES):
        if recovered.count(new) != 1:
            raise ValueError("SRT_REVERSE_ANCHOR_MISSING_"+name.replace(" ","_"))
        recovered = recovered.replace(new, old, 1)
    if recovered != before:
        raise ValueError("SRT_EXACT_REVERSE_RECONSTRUCTION_FAILED")
    return after.encode("utf8")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("source",type=Path)
    parser.add_argument("diff",type=Path)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    if not args.source.is_file() or args.source.is_symlink():
        raise SystemExit("SRT_INPUT_MISSING_OR_UNSAFE")
    original=args.source.read_bytes()
    try:
        changed=transform(original)
    except (ValueError,UnicodeDecodeError) as e:
        raise SystemExit("SRT_DEFERRED_PRODUCER_FIX_REFUSED="+str(e))
    args.diff.parent.mkdir(parents=True,exist_ok=True)
    args.diff.write_text("".join(difflib.unified_diff(
        original.decode().splitlines(True),changed.decode().splitlines(True),
        fromfile="protected/SRT.cpp",tofile="candidate/SRT.cpp")))
    if args.apply:
        args.source.write_bytes(changed)
    print("SRT_DEFERRED_PRODUCER_RETRY="+("APPLIED" if args.apply else "PREFLIGHT_PASS"))
    print("SOURCE_SHA256_VERIFIED_EXACT_REVERSIBILITY_PASS=YES")
    print("NO_GUESSED_OFFSETS_OR_VALID_SHADER_REORDERING=YES")
    print("UNRESOLVED_LOADS_RETAIN_UNFLATTENED_GUEST_MEMORY_FALLBACK=YES")

if __name__=="__main__":
    main()
