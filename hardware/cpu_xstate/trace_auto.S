/* SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
 * SPDX-License-Identifier: GPL-2.0-or-later */

.text
.macro AUTO_FLAGS name, width
.global \name
.type \name,@function
\name:
    pushfq
    pop %r8
    mov %r8, 0(%rdi)
    .if \width == 16
    pushw $0xcd7
    popfw
    .else
    pushq $0xcd7
    popfq
    .endif
    mov $0, %ecx
    xgetbv
    mov %rax, 8(%rdi)
    mov %rdx, 16(%rdi)
    pushfq
    pop %rax
    mov %rax, 24(%rdi)
    push %r8
    popfq
    ret
.size \name, .-\name
.endm
AUTO_FLAGS probe_auto_flags16, 16
AUTO_FLAGS probe_auto_flags64, 64

.global probe_auto_redirect, probe_auto_redirect_fault, probe_auto_redirect_query
.type probe_auto_redirect,@function
probe_auto_redirect:
    mov %rdi, %r8
    mov $1, %ecx
probe_auto_redirect_fault:
    ud2
probe_auto_redirect_query:
    xgetbv
    mov %rax, 0(%r8)
    mov %rdx, 8(%r8)
    ret
.size probe_auto_redirect, .-probe_auto_redirect
.section .note.GNU-stack,"",@progbits
