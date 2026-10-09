/* SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
 * SPDX-License-Identifier: GPL-2.0-or-later */

.text
.p2align 4
.global LiveStateProbe, live_state_fault, live_state_resume
.type LiveStateProbe,@function
LiveStateProbe:
    push %rbx
    push %rbp
    push %r12
    push %r13
    push %r14
    push %r15
    mov %rdi, %r15
    mov %rsi, %rdi
    mov $0x13579bdf2468ace0, %rax
    .irp offset,8,16,24,32,40,48,56,64,72,80,88,96,104,112,120,128
    mov %rax, -\offset(%rsp)
    .endr
    test %edx, %edx
    jz 1f
    vmovdqu64 live_vector(%rip), %zmm31
    vmovdqu64 live_vector(%rip), %zmm0
    kmovq %rax, %k6
1:
    mov $7, %eax
    mov $0x12340001, %ecx
    xor %edx, %edx
    mov $0x12340003, %ebx
    mov $0x12340005, %ebp
    mov $0x12340006, %esi
    mov $0x12340008, %r8d
    mov $0x12340009, %r9d
    mov $0x1234000a, %r10d
    mov $0x1234000b, %r11d
    mov $0x1234000c, %r12d
    mov $0x1234000d, %r13d
    mov $0x1234000e, %r14d
    std
    stc
live_state_fault:
    xrstor64 (%rdi)
live_state_resume:
    mov %rax, 0(%r15)
    mov %rcx, 8(%r15)
    mov %rdx, 16(%r15)
    mov %rbx, 24(%r15)
    mov %rsp, 32(%r15)
    mov %rbp, 40(%r15)
    mov %rsi, 48(%r15)
    mov %rdi, 56(%r15)
    mov %r8, 64(%r15)
    mov %r9, 72(%r15)
    mov %r10, 80(%r15)
    mov %r11, 88(%r15)
    mov %r12, 96(%r15)
    mov %r13, 104(%r15)
    mov %r14, 112(%r15)
    mov %r15, 120(%r15)
    // Capture the red zone before PUSHF overwrites its top slot.
    .irp offset,8,16,24,32,40,48,56,64,72,80,88,96,104,112,120,128
    mov -\offset(%rsp), %rax
    mov %rax, (128+\offset)(%r15)
    .endr
    pushfq
    pop %rax
    mov %rax, 128(%r15)
    cld
    mov $0, %eax
    cmpl $0, 264(%r15)
    je 2f
    vmovdqu64 %zmm31, 272(%r15)
    vmovdqu64 %zmm0, 336(%r15)
    kmovq %k6, 400(%r15)
2:
    vzeroupper
    pop %r15
    pop %r14
    pop %r13
    pop %r12
    pop %rbp
    pop %rbx
    ret
.size LiveStateProbe, .-LiveStateProbe

.section .rodata
.p2align 6
live_vector:
    .quad 0x13579bdf2468ace0, 0x13579bdf2468ace0
    .quad 0x13579bdf2468ace0, 0x13579bdf2468ace0
    .quad 0x13579bdf2468ace0, 0x13579bdf2468ace0
    .quad 0x13579bdf2468ace0, 0x13579bdf2468ace0
.section .note.GNU-stack,"",@progbits
