#!/usr/bin/env python3
"""Apply opt-in, bounded forward dependency retries to exactly audited GoW SRT.

Only patches the checksum-identical source captured 2026-10-18 15:13.
The original C++ loop remains byte-for-byte in the disabled branch.
"""
import argparse
import difflib
import hashlib
from pathlib import Path

PROTECTED_SRT_SHA256 = '14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161'
LEGACY_LOOP = '''    for (auto [src_off_dw, use] : *use_list) {
        if (src_off_dw.IsImmediate()) {
            c.mov(r10d, ptr[rdi + (src_off_dw.U32() << 2)]);
        } else {
            if (!CanLowerSrtOffsetStructurally(src_off_dw, pass_info.offset_support_cache)) {
                ++pass_info.dynamic_offsets_deferred;
                continue;
            }
            const size_t code_begin = c.getSize();
            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {
                c.setSize(code_begin);
                LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");
                continue;
            }
            c.shl(r10d, 2);
            c.mov(r10d, r10d);
            c.mov(r10d, dword[rdi + r10]);
        }
        c.mov(ptr[rsi + (pass_info.dst_off_dw << 2)], r10d);

        SetFlatbufOffset(use, pass_info.dst_off_dw);
        pass_info.dst_off_dw++;
    }'''
RETRY_LOOP = r'''    // Default is bit-for-bit the established (proven graphics) emission order.
    // Enable bounded forward reference resolution only in isolated experiments.
    static const bool srt_retry_enabled = [] {
        const char* value = std::getenv("SHADPS4_SRT_FORWARD_RETRY");
        return value && value[0] == '1' && value[1] == '\0';
    }();
    if (!srt_retry_enabled) {
''' + '\n'.join('    '+line for line in LEGACY_LOOP.split('\n')) + r'''
    } else {
        std::vector<std::pair<IR::Value, IR::Inst*>> entries;
        entries.reserve(use_list->size());
        for (const auto& [src_off_dw, use] : *use_list) {
            entries.emplace_back(src_off_dw, use);
        }
        const auto retry = ResolveSrtForwardDependencies(
            std::move(entries), [&](const auto& entry) {
                const auto& [src_off_dw, use] = entry;
                if (src_off_dw.IsImmediate()) {
                    c.mov(r10d, ptr[rdi + (src_off_dw.U32() << 2)]);
                } else {
                    if (!CanLowerSrtOffsetStructurally(src_off_dw,
                                                       pass_info.offset_support_cache)) {
                        ++pass_info.dynamic_offsets_deferred;
                        return SrtAttemptResult::Deferred;
                    }
                    const size_t code_begin = c.getSize();
                    if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {
                        c.setSize(code_begin);
                        return SrtAttemptResult::Retry;
                    }
                    c.shl(r10d, 2);
                    c.mov(r10d, r10d);
                    c.mov(r10d, dword[rdi + r10]);
                }
                c.mov(ptr[rsi + (pass_info.dst_off_dw << 2)], r10d);
                SetFlatbufOffset(use, pass_info.dst_off_dw);
                ++pass_info.dst_off_dw;
                return SrtAttemptResult::Resolved;
            });
        // The old error stays observable for entries that cannot be lowered.
        for (size_t i = 0; i < retry.unresolved; ++i) {
            LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");
        }
        if (retry.recovered || retry.unresolved) {
            LOG_WARNING(Render_Recompiler,
                        "SRT_FORWARD_RETRY recovered={} unresolved={} deferred={} passes={}",
                        retry.recovered, retry.unresolved, retry.deferred, retry.passes);
        }
    }'''
HEADER_ANCHOR = '#include "shader_recompiler/ir/passes/srt.h"'
NEW_HEADER = '#include "shader_recompiler/ir/passes/srt_dependency_retry.h"'
CSTDLIB_ANCHOR = '#include <unordered_set>'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def transform(src: str):
    if src.count(LEGACY_LOOP) != 1:
        raise ValueError(f'LEGACY_VISIT_POINTER_LOOP_MATCHES_{src.count(LEGACY_LOOP)}')
    if src.count(HEADER_ANCHOR) != 1 or src.count(CSTDLIB_ANCHOR) != 1:
        raise ValueError('SOURCE_INCLUDE_CONTRACT_CHANGED')
    if 'SHADPS4_SRT_FORWARD_RETRY' in src or NEW_HEADER in src:
        raise ValueError('EXPERIMENT_ALREADY_STAGED')
    s = src.replace(CSTDLIB_ANCHOR, '#include <cstdlib>\n'+CSTDLIB_ANCHOR,1)
    s = s.replace(HEADER_ANCHOR, HEADER_ANCHOR+'\n'+NEW_HEADER,1)
    s = s.replace(LEGACY_LOOP,RETRY_LOOP,1)
    if s.count('SHADPS4_SRT_FORWARD_RETRY') != 1:
        raise ValueError('SRT_RETRY_FLAG_NOT_UNIQUE')
    if s.count('SRT_FORWARD_RETRY recovered=') != 1:
        raise ValueError('SRT_COUNTER_NOT_UNIQUE')
    if s.count('LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");') != src.count('LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");') + 1:
        raise ValueError('SRT_LEGACY_ERRORS_CHANGED')
    # Invert only the exact three permitted changes and demand original bytes.
    inv = s.replace(RETRY_LOOP,LEGACY_LOOP,1)
    inv = inv.replace(HEADER_ANCHOR+'\n'+NEW_HEADER,HEADER_ANCHOR,1)
    inv = inv.replace('#include <cstdlib>\n'+CSTDLIB_ANCHOR,CSTDLIB_ANCHOR,1)
    if inv != src:
        raise ValueError('UNRELATED_SHADER_SOURCE_WAS_MODIFIED')
    return s

def main():
    p=argparse.ArgumentParser()
    p.add_argument('source',type=Path)
    p.add_argument('report',type=Path)
    p.add_argument('--selftest',action='store_true')
    a=p.parse_args()
    if not a.source.is_file() or a.source.is_symlink():
        raise SystemExit('SRT_SOURCE_UNSAFE_OR_MISSING')
    before=a.source.read_bytes()
    digest=sha(before)
    if digest != PROTECTED_SRT_SHA256:
        raise SystemExit('PROVEN_SRT_SOURCE_SHA_MISMATCH_NO_MODIFICATION='+digest)
    original=before.decode('utf8')
    try:
        changed=transform(original)
    except (AssertionError,ValueError) as exc:
        raise SystemExit('SRT_FORWARD_PATCH_CONTRACT_FAILED='+str(exc))
    diff=''.join(difflib.unified_diff(original.splitlines(True),changed.splitlines(True),fromfile='audited-original-srt.cpp',tofile='optin-srt-forward-retry.cpp'))
    a.report.write_text(f'PROTECTED_PREIMAGE_SHA256={digest}\nNEW_SHA256={sha(changed.encode())}\nDEFAULT_OLD_SRT_VISIT_LOOP_IDENTICAL=YES\n\n'+diff)
    if not a.selftest:
        a.source.write_text(changed,encoding='utf8')
    print('SRT_FORWARD_RETRY_SOURCE_CONTRACT=PASS')
    print('NEW_SRT_CODE_COMMITTED=' + str(not a.selftest))
    print('SRT_RETRY_OPT_IN_ONLY=YES')

if __name__=='__main__':main()
