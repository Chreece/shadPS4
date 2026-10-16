// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Exercise the exact app2app replacement callback from the production client.
// The mock DynamoRIO API models an executable basic block with translation PCs.
#include <assert.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>

#include "../../src/core/cpu_id_translation/client.c"

static void set_range(uintptr_t begin, uintptr_t end) {
    atomic_store_explicit(&guest_begin,begin,memory_order_relaxed);
    atomic_store_explicit(&guest_end,end,memory_order_release);
    mock_opcode_reads=0;
    mock_traps_allocated=0;
    mock_replacements=0;
}

static void empty_guest_range_skips_decode(void) {
    set_range(0,0);
    instr_t cpuid={.pc=(app_pc)(uintptr_t)0x1100,.opcode=OP_cpuid};
    instrlist_t block={.first=&cpuid};
    assert(TranslateBlock(NULL,NULL,&block,false,false)==DR_EMIT_DEFAULT);
    assert(mock_opcode_reads==0 && mock_replacements==0 && block.first==&cpuid);
}

static void host_block_never_has_identity_opcode_intercepted(void) {
    set_range(0x1000,0x2000);
    instr_t cpuid={.pc=(app_pc)(uintptr_t)0x5000,.opcode=OP_cpuid};
    instrlist_t block={.first=&cpuid};
    assert(TranslateBlock(NULL,NULL,&block,false,false)==DR_EMIT_DEFAULT);
    assert(mock_opcode_reads==0 && mock_replacements==0 && block.first==&cpuid);
}

static void guest_without_identity_instructions_uses_default(void) {
    set_range(0x1000,0x2000);
    instr_t first={.pc=(app_pc)(uintptr_t)0x1100,.opcode=OP_nop};
    instr_t second={.pc=(app_pc)(uintptr_t)0x1120,.opcode=OP_nop};
    first.next=&second;
    instrlist_t block={.first=&first};
    assert(TranslateBlock(NULL,NULL,&block,false,false)==DR_EMIT_DEFAULT);
    assert(mock_opcode_reads==2 && mock_replacements==0 && block.first==&first);
}

static void replacement_has_stored_translation(int opcode,bool translating) {
    set_range(0x1000,0x2000);
    instr_t original={.pc=(app_pc)(uintptr_t)0x1500,.opcode=opcode};
    instrlist_t block={.first=&original};
    assert(TranslateBlock(NULL,NULL,&block,false,translating)==
           DR_EMIT_STORE_TRANSLATIONS);
    assert(mock_replacements==1 && mock_traps_allocated==1);
    assert(block.first!=&original && block.first->is_trap &&
           block.first->opcode==OP_ud2);
    assert(block.first->pc==original.pc);
    free(block.first);
}

static void mixed_native_and_guest_blocks_keep_precise_identity_pc(void) {
    set_range(0x1000,0x2000);
    instr_t first={.pc=(app_pc)(uintptr_t)0x800,.opcode=OP_cpuid};
    instr_t second={.pc=(app_pc)(uintptr_t)0x1800,.opcode=OP_nop};
    instr_t third={.pc=(app_pc)(uintptr_t)0x1810,.opcode=OP_rdpid};
    first.next=&second;
    second.next=&third;
    instrlist_t block={.first=&first};
    assert(TranslateBlock(NULL,NULL,&block,false,false)==
           DR_EMIT_STORE_TRANSLATIONS);
    assert(block.first==&first);
    assert(first.next==&second && second.next!=&third);
    assert(second.next->opcode==OP_ud2 && second.next->pc==third.pc);
    assert(mock_replacements==1 && mock_traps_allocated==1);
    free(second.next);
}

int main(void) {
    empty_guest_range_skips_decode();
    host_block_never_has_identity_opcode_intercepted();
    guest_without_identity_instructions_uses_default();
    replacement_has_stored_translation(OP_cpuid,false);
    replacement_has_stored_translation(OP_rdtscp,false);
    replacement_has_stored_translation(OP_rdpid,false);
    replacement_has_stored_translation(OP_cpuid,true);
    mixed_native_and_guest_blocks_keep_precise_identity_pc();
    puts("CPU_ID_TRANSLATION_SELECTIVE=PASS cases=8");
    return 0;
}
