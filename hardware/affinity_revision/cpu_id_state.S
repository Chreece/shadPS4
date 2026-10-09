# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

.text
.macro SNAPSHOT name, operation
.globl \name
.type \name, @function
\name:
.cfi_startproc
    push %rbx
    push %rbp
    push %r12
    push %r13
    push %r14
    push %r15
    sub $1024, %rsp
.cfi_def_cfa_offset 1080
    mov %rdi, 960(%rsp)
    mov %rsi, 968(%rsp)
    fnstcw 976(%rsp)
    stmxcsr 980(%rsp)
    movw $0x077f, 984(%rsp)
    fldcw 984(%rsp)
    movl $0x3f80, 984(%rsp)
    ldmxcsr 984(%rsp)
    mov %rsp, 656(%rsp)
    vmovdqu vector_pattern(%rip), %ymm0
    vmovdqu vector_pattern(%rip), %ymm1
    vmovdqu vector_pattern(%rip), %ymm2
    vmovdqu vector_pattern(%rip), %ymm3
    vmovdqu vector_pattern(%rip), %ymm4
    vmovdqu vector_pattern(%rip), %ymm5
    vmovdqu vector_pattern(%rip), %ymm6
    vmovdqu vector_pattern(%rip), %ymm7
    vmovdqu vector_pattern(%rip), %ymm8
    vmovdqu vector_pattern(%rip), %ymm9
    vmovdqu vector_pattern(%rip), %ymm10
    vmovdqu vector_pattern(%rip), %ymm11
    vmovdqu vector_pattern(%rip), %ymm12
    vmovdqu vector_pattern(%rip), %ymm13
    vmovdqu vector_pattern(%rip), %ymm14
    vmovdqu vector_pattern(%rip), %ymm15
    movq $0x11223344, -16(%rsp)
    movq $0x11223344, -24(%rsp)
    movq $0x11223344, -32(%rsp)
    movq $0x11223344, -40(%rsp)
    movq $0x11223344, -48(%rsp)
    movq $0x11223344, -56(%rsp)
    movq $0x11223344, -64(%rsp)
    movq $0x11223344, -72(%rsp)
    movq $0x11223344, -80(%rsp)
    movq $0x11223344, -88(%rsp)
    movq $0x11223344, -96(%rsp)
    movq $0x11223344, -104(%rsp)
    movq $0x11223344, -112(%rsp)
    movq $0x11223344, -120(%rsp)
    movq $0x11223344, -128(%rsp)
    movabs $1, %rax
    movabs $0, %rcx
    movabs $0x1234567800000002, %rdx
    movabs $0x1234567800000003, %rbx
    movabs $0x1234567800000005, %rbp
    movabs $0x1234567800000006, %rsi
    movabs $0x1234567800000007, %rdi
    movabs $0x1234567800000008, %r8
    movabs $0x1234567800000009, %r9
    movabs $0x123456780000000a, %r10
    movabs $0x123456780000000b, %r11
    movabs $0x123456780000000c, %r12
    movabs $0x123456780000000d, %r13
    movabs $0x123456780000000e, %r14
    movabs $0x123456780000000f, %r15
    pushq $0xed7
    popfq
.if \operation == 0
    cpuid
.elseif \operation == 1
    call *968(%rsp)
.elseif \operation == 2
    rdtscp
.else
    rdpid %r15
.endif
    mov %rax, 0(%rsp)
    mov %rcx, 8(%rsp)
    mov %rdx, 16(%rsp)
    mov %rbx, 24(%rsp)
    mov %rsp, 32(%rsp)
    mov %rbp, 40(%rsp)
    mov %rsi, 48(%rsp)
    mov %rdi, 56(%rsp)
    mov %r8, 64(%rsp)
    mov %r9, 72(%rsp)
    mov %r10, 80(%rsp)
    mov %r11, 88(%rsp)
    mov %r12, 96(%rsp)
    mov %r13, 104(%rsp)
    mov %r14, 112(%rsp)
    mov %r15, 120(%rsp)
    pushfq
    pop %rax
    mov %rax, 128(%rsp)
    vmovdqu %ymm0, 136(%rsp)
    vmovdqu %ymm1, 168(%rsp)
    vmovdqu %ymm2, 200(%rsp)
    vmovdqu %ymm3, 232(%rsp)
    vmovdqu %ymm4, 264(%rsp)
    vmovdqu %ymm5, 296(%rsp)
    vmovdqu %ymm6, 328(%rsp)
    vmovdqu %ymm7, 360(%rsp)
    vmovdqu %ymm8, 392(%rsp)
    vmovdqu %ymm9, 424(%rsp)
    vmovdqu %ymm10, 456(%rsp)
    vmovdqu %ymm11, 488(%rsp)
    vmovdqu %ymm12, 520(%rsp)
    vmovdqu %ymm13, 552(%rsp)
    vmovdqu %ymm14, 584(%rsp)
    vmovdqu %ymm15, 616(%rsp)
    stmxcsr 648(%rsp)
    fnstcw 652(%rsp)
    mov -16(%rsp), %rax
    mov %rax, 664(%rsp)
    mov -24(%rsp), %rax
    mov %rax, 672(%rsp)
    mov -32(%rsp), %rax
    mov %rax, 680(%rsp)
    mov -40(%rsp), %rax
    mov %rax, 688(%rsp)
    mov -48(%rsp), %rax
    mov %rax, 696(%rsp)
    mov -56(%rsp), %rax
    mov %rax, 704(%rsp)
    mov -64(%rsp), %rax
    mov %rax, 712(%rsp)
    mov -72(%rsp), %rax
    mov %rax, 720(%rsp)
    mov -80(%rsp), %rax
    mov %rax, 728(%rsp)
    mov -88(%rsp), %rax
    mov %rax, 736(%rsp)
    mov -96(%rsp), %rax
    mov %rax, 744(%rsp)
    mov -104(%rsp), %rax
    mov %rax, 752(%rsp)
    mov -112(%rsp), %rax
    mov %rax, 760(%rsp)
    mov -120(%rsp), %rax
    mov %rax, 768(%rsp)
    mov -128(%rsp), %rax
    mov %rax, 776(%rsp)
    cld
    vzeroupper
    mov 960(%rsp), %rdi
    mov %rsp, %rsi
    mov $98, %ecx
    rep movsq
    fldcw 976(%rsp)
    ldmxcsr 980(%rsp)
    add $1024, %rsp
    pop %r15
    pop %r14
    pop %r13
    pop %r12
    pop %rbp
    pop %rbx
.cfi_def_cfa_offset 8
    ret
.cfi_endproc
.size \name, .-\name
.endm

SNAPSHOT SnapshotCpuid, 0
SNAPSHOT SnapshotFaultCpuid, 1
SNAPSHOT SnapshotRdtscp, 2
SNAPSHOT SnapshotRdpid, 3

.section .rodata
.balign 32
vector_pattern:
    .quad 0x0123456789abcdef, 0x0123456789abcdef
    .quad 0x0123456789abcdef, 0x0123456789abcdef
.section .note.GNU-stack,"",@progbits

