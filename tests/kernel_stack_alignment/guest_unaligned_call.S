// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
.text
.globl CallGuestMisaligned
.type CallGuestMisaligned, @function
CallGuestMisaligned:
    // Entry RSP%16=8 after ordinary C++ caller. By *not* compensating
    // with an 8-byte stack adjustment, the target sees RSP%16=0.
    // This simulates guest machine-code calling a host HLE entry.
    movq %rdi, %r11
    movq %rsi, %rdi
    call *%r11
    ret
.size CallGuestMisaligned, .-CallGuestMisaligned
.section .note.GNU-stack, "", @progbits
