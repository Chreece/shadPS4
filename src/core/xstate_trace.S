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

.global XstateTraceEnter, XstateTraceEnterResume, XstateTraceLeave, XstateTraceLeaveResume
.hidden XstateTraceEnter, XstateTraceEnterResume, XstateTraceLeave, XstateTraceLeaveResume
.type XstateTraceEnter,@function
XstateTraceEnter:
    int3
XstateTraceEnterResume:
    ret
.size XstateTraceEnter, .-XstateTraceEnter
.type XstateTraceLeave,@function
XstateTraceLeave:
    int3
XstateTraceLeaveResume:
    ret
.size XstateTraceLeave, .-XstateTraceLeave

.global XstateTracePause, XstateTracePauseResume, XstateTraceRestart, XstateTraceRestartResume
.hidden XstateTracePause, XstateTracePauseResume, XstateTraceRestart, XstateTraceRestartResume
.type XstateTracePause,@function
XstateTracePause:
    int3
XstateTracePauseResume:
    ret
.size XstateTracePause, .-XstateTracePause
.type XstateTraceRestart,@function
XstateTraceRestart:
    int3
XstateTraceRestartResume:
    ret
.size XstateTraceRestart, .-XstateTraceRestart
.section .note.GNU-stack,"",@progbits
