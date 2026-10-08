# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
.macro BODY label, unwind, dst, src, instruction:vararg
.global \label, \label\()_end
.if \unwind
.type \label, @function
.else
.type \label, @object
.endif
\label:
.if \unwind
.cfi_startproc
.endif
push %rbx
.if \unwind
.cfi_adjust_cfa_offset 8
.endif
.if \unwind
.cfi_offset %rbx, -16
.endif
vmovdqu (%rdi), %ymm\dst
vmovdqu 32(%rdi), %ymm\src
movabs $0x123456789abcdef0, %rax
movabs $0x23456789abcdef01, %rcx
movabs $0x3456789abcdef012, %rbx
mov %rax, -128(%rsp)
mov %rax, -120(%rsp)
mov %rax, -112(%rsp)
mov %rax, -104(%rsp)
mov %rax, -96(%rsp)
mov %rax, -88(%rsp)
mov %rax, -80(%rsp)
mov %rax, -72(%rsp)
mov %rax, -64(%rsp)
mov %rax, -56(%rsp)
mov %rax, -48(%rsp)
mov %rax, -40(%rsp)
mov %rax, -32(%rsp)
mov %rax, -24(%rsp)
mov %rax, -16(%rsp)
mov %rax, -8(%rsp)
push $0xad7
popfq
\instruction
sfence
vmovdqu %ymm\dst, (%rsi)
vmovdqu %ymm\src, 32(%rsi)
mov %rax, 72(%rsi)
mov %rcx, 80(%rsi)
mov %rbx, 88(%rsi)
mov %rdx, 96(%rsi)
pushfq
popq 64(%rsi)
xor %eax, %eax
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -128(%rsp)
je .Lcheck\@_0
or $1, %eax
.Lcheck\@_0:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -120(%rsp)
je .Lcheck\@_1
or $2, %eax
.Lcheck\@_1:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -112(%rsp)
je .Lcheck\@_2
or $4, %eax
.Lcheck\@_2:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -104(%rsp)
je .Lcheck\@_3
or $8, %eax
.Lcheck\@_3:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -96(%rsp)
je .Lcheck\@_4
or $16, %eax
.Lcheck\@_4:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -88(%rsp)
je .Lcheck\@_5
or $32, %eax
.Lcheck\@_5:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -80(%rsp)
je .Lcheck\@_6
or $64, %eax
.Lcheck\@_6:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -72(%rsp)
je .Lcheck\@_7
or $128, %eax
.Lcheck\@_7:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -64(%rsp)
je .Lcheck\@_8
or $256, %eax
.Lcheck\@_8:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -56(%rsp)
je .Lcheck\@_9
or $512, %eax
.Lcheck\@_9:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -48(%rsp)
je .Lcheck\@_10
or $1024, %eax
.Lcheck\@_10:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -40(%rsp)
je .Lcheck\@_11
or $2048, %eax
.Lcheck\@_11:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -32(%rsp)
je .Lcheck\@_12
or $4096, %eax
.Lcheck\@_12:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -24(%rsp)
je .Lcheck\@_13
or $8192, %eax
.Lcheck\@_13:
movabs $0x123456789abcdef0, %rcx
cmp %rcx, -16(%rsp)
je .Lcheck\@_14
or $16384, %eax
.Lcheck\@_14:
mov %rax, 104(%rsi)
vzeroupper
pop %rbx
.if \unwind
.cfi_adjust_cfa_offset -8
.endif
ret
.if \unwind
.cfi_endproc
.endif
\label\()_end:
.size \label, .-\label
.endm
.text
BODY extrq_reg, 1, 0, 1, extrq %xmm1, %xmm0
BODY extrq_imm, 1, 0, 1, extrq $8, $16, %xmm0
BODY insertq_reg, 1, 0, 1, insertq %xmm1, %xmm0
BODY insertq_imm, 1, 0, 1, insertq $8, $16, %xmm1, %xmm0
BODY extrq_high, 1, 8, 15, extrq %xmm15, %xmm8
BODY extrq_imm_high, 1, 8, 15, extrq $8, $16, %xmm8
BODY insertq_high, 1, 8, 15, insertq %xmm15, %xmm8
BODY insertq_imm_high, 1, 8, 15, insertq $8, $16, %xmm15, %xmm8
BODY movntss, 1, 0, 1, movntss %xmm0, (%rdx)
BODY movntsd, 1, 0, 1, movntsd %xmm0, (%rdx)
BODY movntss_high, 1, 8, 15, movntss %xmm8, 8(%rdx)
BODY movntsd_high, 1, 8, 15, movntsd %xmm8, 8(%rdx)
.section .rodata
BODY raw_extrq_reg, 0, 0, 1, extrq %xmm1, %xmm0
BODY raw_extrq_imm, 0, 0, 1, extrq $8, $16, %xmm0
BODY raw_insertq_reg, 0, 0, 1, insertq %xmm1, %xmm0
BODY raw_insertq_imm, 0, 0, 1, insertq $8, $16, %xmm1, %xmm0
BODY raw_extrq_high, 0, 8, 15, extrq %xmm15, %xmm8
BODY raw_extrq_imm_high, 0, 8, 15, extrq $8, $16, %xmm8
BODY raw_insertq_high, 0, 8, 15, insertq %xmm15, %xmm8
BODY raw_insertq_imm_high, 0, 8, 15, insertq $8, $16, %xmm15, %xmm8
BODY raw_movntss, 0, 0, 1, movntss %xmm0, (%rdx)
BODY raw_movntsd, 0, 0, 1, movntsd %xmm0, (%rdx)
BODY raw_movntss_high, 0, 8, 15, movntss %xmm8, 8(%rdx)
BODY raw_movntsd_high, 0, 8, 15, movntsd %xmm8, 8(%rdx)
.section .note.GNU-stack,"",@progbits
