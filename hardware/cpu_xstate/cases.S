/* SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
 * SPDX-License-Identifier: GPL-2.0-or-later */

.text
.macro STATE_PROBE name, opcode
.p2align 4
.global \name, \name\()_end, \name\()_fault, \name\()_resume
.type \name,@function
\name:
    push %rbx
    sub $512, %rsp
    mov %rdx, %r8
    fxsave64 (%rsp)
    fninit
    vzeroall
    cmpl $0, 32(%r8)
    je 1f
    fld1
    fldpi
    mov 40(%r8), %r10
    .irp n,0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
    vmovdqu (32*\n)(%r10), %ymm\n
    .endr
1:
    ldmxcsr 36(%r8)
    mov %rsi, %rax
    mov %rsi, %rdx
    shr $32, %rdx
    pushfq
    pop %rbx
    mov %rbx, 0(%r8)
\name\()_fault:
    \opcode (%rdi)
\name\()_resume:
    pushfq
    pop %rbx
    mov %rbx, 8(%r8)
    mov %rax, 16(%r8)
    mov %rdx, 24(%r8)
    fxsave64 64(%r8)
    .irp n,0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
    vextractf128 $1, %ymm\n, (640+16*\n)(%r8)
    .endr
    fxrstor64 (%rsp)
    vzeroupper
    add $512, %rsp
    pop %rbx
    ret
\name\()_end:
.size \name, .-\name
.endm

STATE_PROBE probe_xsave, xsave
STATE_PROBE probe_xsave64, xsave64
STATE_PROBE probe_xsaveopt, xsaveopt
STATE_PROBE probe_xsaveopt64, xsaveopt64
STATE_PROBE probe_xrstor, xrstor
STATE_PROBE probe_xrstor64, xrstor64

.p2align 4
.global probe_xgetbv, probe_xgetbv_end, probe_xgetbv_fault, probe_xgetbv_resume
.type probe_xgetbv,@function
probe_xgetbv:
    mov %rdx, %r8
    mov %esi, %ecx
    mov $0x11223344, %eax
    mov $0x55667788, %edx
    pushfq
    pop %r9
    mov %r9, 0(%r8)
probe_xgetbv_fault:
    xgetbv
probe_xgetbv_resume:
    pushfq
    pop %r9
    mov %r9, 8(%r8)
    mov %rax, 16(%r8)
    mov %rdx, 24(%r8)
    ret
probe_xgetbv_end:
.size probe_xgetbv, .-probe_xgetbv

.section .note.GNU-stack,"",@progbits
