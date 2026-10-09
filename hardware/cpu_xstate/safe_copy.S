/* SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
 * SPDX-License-Identifier: GPL-2.0-or-later */

.text
.global SafeCopy, safe_copy_fault, safe_copy_resume
.type SafeCopy,@function
SafeCopy:
    mov %rdx, %rcx
safe_copy_fault:
    rep movsb
safe_copy_resume:
    mov %rcx, %rax
    ret
.size SafeCopy, .-SafeCopy
.section .note.GNU-stack,"",@progbits
