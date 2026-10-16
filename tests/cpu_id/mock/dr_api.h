// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// Deterministic C test doubles for the narrow DynamoRIO app2app client API.
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

#define DR_EXPORT
#define DR_ASSERT(x) do { if (!(x)) abort(); } while (0)
#define DR_ASSERT_MSG(x, message) DR_ASSERT(x)
#define DR_EMIT_DEFAULT 0
#define DR_EMIT_STORE_TRANSLATIONS 1

typedef uint8_t* app_pc;
typedef unsigned dr_emit_flags_t;
typedef unsigned client_id_t;
typedef struct instr_t {
    struct instr_t* next;
    app_pc pc;
    int opcode;
    bool is_trap;
} instr_t;
typedef struct instrlist_t {
    instr_t* first;
} instrlist_t;
typedef struct module_data_t {
    app_pc start;
    void* handle;
} module_data_t;

enum { OP_cpuid = 1, OP_rdtscp = 2, OP_rdpid = 3, OP_nop = 4, OP_ud2 = 5 };
static size_t mock_opcode_reads;
static size_t mock_traps_allocated;
static size_t mock_replacements;

static inline instr_t* instrlist_first_app(instrlist_t* list) {return list->first;}
static inline instr_t* instr_get_next_app(instr_t* inst) {return inst->next;}
static inline app_pc instr_get_app_pc(instr_t* inst) {return inst->pc;}
static inline int instr_get_opcode(instr_t* inst) {
    ++mock_opcode_reads;
    return inst->opcode;
}
static inline instr_t* INSTR_CREATE_ud2(void* context) {
    (void)context;
    instr_t* trap=(instr_t*)calloc(1,sizeof(instr_t));
    if (!trap)abort();
    trap->opcode=OP_ud2;
    trap->is_trap=true;
    ++mock_traps_allocated;
    return trap;
}
static inline void instr_set_translation(instr_t* inst, app_pc pc) {inst->pc=pc;}
static inline void instrlist_replace(instrlist_t* list, instr_t* old, instr_t* next) {
    instr_t** slot=&list->first;
    while (*slot && *slot!=old) slot=&(*slot)->next;
    if (!*slot)abort();
    next->next=old->next;
    *slot=next;
    ++mock_replacements;
}
static inline void instr_destroy(void* context, instr_t* inst) {(void)context;(void)inst;}
static inline module_data_t* dr_get_main_module(void) {
    static module_data_t main={{0},NULL};
    return &main;
}
static inline void dr_free_module_data(module_data_t* data) {(void)data;}
static inline void* dr_get_proc_address(void* h,const char* n) {
    (void)h;(void)n;return (void*)(uintptr_t)1;
}
static inline void dr_set_client_name(const char* name,const char* url) {
    (void)name;(void)url;
}
