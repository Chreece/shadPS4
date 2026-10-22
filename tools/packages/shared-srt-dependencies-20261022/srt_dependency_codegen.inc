// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Included only by flatten_extended_userdata_pass.cpp, inside its anonymous namespace.

static void VisitPointer(const IR::Value& off_dw, IR::Inst* subtree, PassInfo& pass_info,
                         Xbyak::CodeGenerator& c) {
    // A value-number-equivalent pointer may already have been emitted at another
    // root. Propagate only a real allocated slot, never slot zero or a guessed value.
    if (subtree->GetOpcode() == IR::Opcode::ReadConst ||
        subtree->GetOpcode() == IR::Opcode::ReadConstBuffer) {
        if (GetFlatbufOffset(subtree) == 0) {
            const auto* canonical = pass_info.DeduplicateInstruction(subtree);
            const u16 offset = GetFlatbufOffset(canonical);
            if (offset == 0) {
                ++pass_info.pending_srt_reads;
                return;
            }
            SetFlatbufOffset(subtree, offset);
        }
    }
    if (!IsHostUniformSrtOffset(off_dw)) {
        ++pass_info.dynamic_srt_reads;
        return;
    }
    // A cyclic pointer graph must not recurse indefinitely or fabricate a read.
    if (!pass_info.active_srt_pointers.insert(subtree).second) {
        ++pass_info.pending_srt_reads;
        return;
    }
    const size_t pointer_checkpoint = c.getSize();
    if (!PushPtr(c, pass_info, off_dw)) {
        c.setSize(pointer_checkpoint);
        pass_info.active_srt_pointers.erase(subtree);
        ++pass_info.pending_srt_reads;
        return;
    }
    PassInfo::PtrUserList* use_list = pass_info.GetUsesAsPointer(subtree);
    ASSERT(use_list);
    std::vector<std::pair<IR::Value, IR::Inst*>> reads(use_list->begin(), use_list->end());
    const auto result = ScheduleSrtReads(reads.size(), [&](size_t index) {
        const auto& [src_off_dw, use] = reads[index];
        if (GetFlatbufOffset(use) != 0) {
            return SrtReadAttempt::Complete;
        }
        IR::Inst* canonical = pass_info.DeduplicateInstruction(use);
        if (const u16 offset = GetFlatbufOffset(canonical); offset != 0) {
            SetFlatbufOffset(use, offset);
            return SrtReadAttempt::Complete;
        }
        if (!IsHostUniformSrtOffset(src_off_dw)) {
            return SrtReadAttempt::Unsupported;
        }
        if (pass_info.dst_off_dw == std::numeric_limits<u16>::max()) {
            return SrtReadAttempt::Deferred;
        }
        const size_t checkpoint = c.getSize();
        if (src_off_dw.IsImmediate()) {
            c.mov(r10d, ptr[rdi + (src_off_dw.U32() << 2)]);
        } else {
            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {
                c.setSize(checkpoint);
                return SrtReadAttempt::Deferred;
            }
            c.shl(r10d, 2);
            c.mov(r10d, r10d);
            c.mov(r10d, dword[rdi + r10]);
        }
        const u16 slot = pass_info.dst_off_dw;
        c.mov(ptr[rsi + (slot << 2)], r10d);
        SetFlatbufOffset(use, slot);
        if (canonical != use) {
            SetFlatbufOffset(canonical, slot);
        }
        ++pass_info.dst_off_dw;
        return SrtReadAttempt::Complete;
    });
    pass_info.pending_srt_reads += result.pending;
    pass_info.dynamic_srt_reads += result.unsupported;

    for (const auto& [src_off_dw, use] : *use_list) {
        if (pass_info.GetUsesAsPointer(use)) {
            VisitPointer(src_off_dw, use, pass_info, c);
        }
    }
    PopPtr(c);
    pass_info.active_srt_pointers.erase(subtree);
}
