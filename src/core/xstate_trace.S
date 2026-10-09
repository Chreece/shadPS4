/* SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
 * SPDX-License-Identifier: GPL-2.0-or-later */

.text
.global XstateTraceCopy, XstateTraceCopyFault, XstateTraceCopyResume
.hidden XstateTraceCopy, XstateTraceCopyFault, XstateTraceCopyResume
.type XstateTraceCopy,@function
XstateTraceCopy:
    mov %rdx, %rcx
XstateTraceCopyFault:
    rep movsb
XstateTraceCopyResume:
    mov %rcx, %rax
    ret
.size XstateTraceCopy, .-XstateTraceCopy
.section .note.GNU-stack,"",@progbits
