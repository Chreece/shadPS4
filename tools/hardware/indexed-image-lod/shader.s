// SPDX-License-Identifier: GPL-2.0-or-later
// Original GCN, matching shader.h; OrbShdr header offset occupies the first two words.
.text
.long 0xbeeb03ff, 17
buffer_load_dword v3, off, s[0:3], 0
s_waitcnt vmcnt(0)
v_readfirstlane_b32 s8, v3
s_mul_i32 s8, s8, 64
s_add_i32 s9, s8, 32
s_buffer_load_dwordx8 s[12:19], s[4:7], s8
s_buffer_load_dwordx4 s[20:23], s[4:7], s9
s_waitcnt lgkmcnt(0)
v_mov_b32 v0, 0.5
v_mov_b32 v1, 0.5
v_mov_b32 v2, 0
image_sample_lz v[4:7], v[0:1], s[12:19], s[20:23] dmask:0xf
s_waitcnt vmcnt(0)
buffer_store_dwordx4 v[4:7], off, s[0:3], 0 offset:16
image_sample_l v[4:7], v[0:2], s[12:19], s[20:23] dmask:0xf
s_waitcnt vmcnt(0)
buffer_store_dwordx4 v[4:7], off, s[0:3], 0 offset:32
v_mov_b32 v2, 0.5
v_mov_b32 v0, 0x3f01
image_sample_lz_o v[4:7], v[0:2], s[12:19], s[20:23] dmask:0xf
s_waitcnt vmcnt(0)
buffer_store_dwordx4 v[4:7], off, s[0:3], 0 offset:48
s_waitcnt vmcnt(0)
s_endpgm
s_nop 0
