# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

.text
.macro probe name, op, scalar, vex, wide, memory
.global \name
.type \name, @function
\name:
.cfi_startproc
    vmovdqu (%rsi), %ymm0
    vmovdqu (%rdi), %ymm1
    pushfq
.cfi_adjust_cfa_offset 8
    popq %r8
.cfi_adjust_cfa_offset -8
    movq %r8, 40(%rdx)
.if \memory
  .if \vex
    \op (%rdi), %xmm0, %xmm0
  .else
    \op (%rdi), %xmm0
  .endif
.elseif \wide
    \op %ymm1, %ymm0
.elseif \vex && \scalar
    \op %xmm1, %xmm0, %xmm0
.else
    \op %xmm1, %xmm0
.endif
    pushfq
.cfi_adjust_cfa_offset 8
    popq %r8
.cfi_adjust_cfa_offset -8
    movq %r8, 48(%rdx)
    vmovdqu %ymm0, (%rdx)
    stmxcsr 32(%rdx)
    vzeroupper
    ret
.cfi_endproc
.size \name, .-\name
.endm

probe rcp_ss, rcpss, 1, 0, 0, 0
probe rcp_ss_mem, rcpss, 1, 0, 0, 1
probe rcp_vss, vrcpss, 1, 1, 0, 0
probe rcp_vss_mem, vrcpss, 1, 1, 0, 1
probe rcp_ps, rcpps, 0, 0, 0, 0
probe rcp_vps, vrcpps, 0, 1, 0, 0
probe rcp_vps256, vrcpps, 0, 1, 1, 0
probe rsqrt_ss, rsqrtss, 1, 0, 0, 0
probe rsqrt_ss_mem, rsqrtss, 1, 0, 0, 1
probe rsqrt_vss, vrsqrtss, 1, 1, 0, 0
probe rsqrt_vss_mem, vrsqrtss, 1, 1, 0, 1
probe rsqrt_ps, rsqrtps, 0, 0, 0, 0
probe rsqrt_vps, vrsqrtps, 0, 1, 0, 0
probe rsqrt_vps256, vrsqrtps, 0, 1, 1, 0

.section .note.GNU-stack,"",@progbits
