/* SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
 * SPDX-License-Identifier: GPL-2.0-or-later */

.text
.macro BLOCK_COPY name, backwards
.global \name, \name\()_fault
.type \name,@function
\name:
    mov %rcx, %r8
    mov %rdx, %rcx
    .if \backwards
    std
    .else
    cld
    .endif
\name\()_fault:
    rep movsb
    pushfq
    pop %rax
    cld
    mov %rcx, 0(%r8)
    mov %rsi, 8(%r8)
    mov %rdi, 16(%r8)
    mov %rax, 24(%r8)
    ret
.size \name, .-\name
.endm
BLOCK_COPY probe_block_forward, 0
BLOCK_COPY probe_block_backward, 1

.global probe_block_load, probe_block_load_fault
.type probe_block_load,@function
probe_block_load:
    mov $0x1234, %r8d
    mov $0x5678, %eax
    add $3, %r8d
probe_block_load_fault:
    mov (%rdi), %rax
    xor %rcx, %rcx
    add %r8, %rax
    ret
.size probe_block_load, .-probe_block_load

.global probe_block_divide, probe_block_divide_fault, probe_block_divide_resume
.type probe_block_divide,@function
probe_block_divide:
    mov $99, %eax
    xor %edx, %edx
    xor %ecx, %ecx
probe_block_divide_fault:
    div %rcx
probe_block_divide_resume:
    add $7, %rax
    ret
.size probe_block_divide, .-probe_block_divide

.global probe_block_async, probe_block_async_begin, probe_block_async_end
.type probe_block_async,@function
probe_block_async:
    mov $0x13579, %edx
    mov %rdx, -128(%rsp)
    vpcmpeqd %ymm0, %ymm0, %ymm0
    vpcmpeqd %ymm1, %ymm1, %ymm1
    movl $1, 0(%rdi)
probe_block_async_begin:
    .rept 32
    nop
    .endr
    cmpl $8, 4(%rdi)
    jb probe_block_async_begin
probe_block_async_end:
    mov %rdx, 8(%rdi)
    mov -128(%rsp), %rax
    mov %rax, 16(%rdi)
    vmovdqu %ymm0, 32(%rdi)
    vmovdqu %ymm1, 64(%rdi)
    xor %eax, %eax
    ret
.size probe_block_async, .-probe_block_async
.section .note.GNU-stack,"",@progbits
