/* SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
 * SPDX-License-Identifier: GPL-2.0-or-later */

.text
.global probe_branch_call
.type probe_branch_call,@function
probe_branch_call:
    push %rbx
    push %r12
    push %r13
    pushfq
    pop %r13
    mov %rcx, %r12
    mov %rdi, %rbx
    mov %rdx, %rcx
    push %rsi
    popfq
    call *%rbx
    pushfq
    pop %rsi
    mov %rax, 0(%r12)
    mov %rcx, 8(%r12)
    mov %rsi, 16(%r12)
    push %r13
    popfq
    pop %r13
    pop %r12
    pop %rbx
    ret
.size probe_branch_call, .-probe_branch_call

.global probe_branch_spin, probe_branch_spin_begin, probe_branch_spin_middle, probe_branch_spin_end
.type probe_branch_spin,@function
probe_branch_spin:
    mov $0x3579, %edx
    mov %rdx, -128(%rsp)
    movl $1, (%rdi)
probe_branch_spin_begin:
    jmp probe_branch_spin_middle
probe_branch_spin_middle:
    jmp probe_branch_spin_begin
probe_branch_spin_end:
    mov %rdx, 8(%rdi)
    mov -128(%rsp), %rax
    mov %rax, 16(%rdi)
    xor %eax, %eax
    ret
.size probe_branch_spin, .-probe_branch_spin
.section .note.GNU-stack,"",@progbits
