// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "dr_api.h"
static inline bool drwrap_init(void) {return true;}
static inline void drwrap_exit(void) {}
static inline void* drwrap_get_arg(void* context,int index) {
    (void)context;(void)index;return NULL;
}
static inline bool drwrap_skip_call(void* context,void* value,unsigned flags) {
    (void)context;(void)value;(void)flags;return true;
}
static inline bool drwrap_wrap(app_pc pc,void(*fn)(void*,void**),void* user) {
    (void)pc;(void)fn;(void)user;return true;
}
