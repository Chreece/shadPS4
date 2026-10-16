// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "dr_api.h"
static inline bool drmgr_init(void) {return true;}
static inline void drmgr_exit(void) {}
static inline bool drmgr_register_exit_event(void(*fn)(void)) {(void)fn;return true;}
static inline bool drmgr_register_module_load_event(
    void(*fn)(void*,const module_data_t*,bool)) {(void)fn;return true;}
static inline bool drmgr_register_bb_app2app_event(
    dr_emit_flags_t(*fn)(void*,void*,instrlist_t*,bool,bool),
    void* user) {(void)fn;(void)user;return true;}
