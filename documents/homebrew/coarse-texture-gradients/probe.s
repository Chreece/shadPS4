.text
.p2align 8
.long 0xbeeb03ff, 0
v_mov_b32 v7, v0
v_and_b32 v4, 7, v0
v_lshrrev_b32 v5, 3, v0
v_cvt_f32_u32 v4, v4
v_cvt_f32_u32 v5, v5
v_add_f32 v4, 0.5, v4
v_add_f32 v5, 0.5, v5
v_mul_f32 v4, 0x3e000000, v4
v_mul_f32 v5, 0x3e000000, v5
v_mov_b32 v0, 0x3e000000
v_mov_b32 v1, 0
v_mov_b32 v2, 0
v_mov_b32 v3, 0x3e000000
image_sample_d v[8:11], v[0:5], s[0:7], s[8:11] dmask:0xf
image_sample_cd v[12:15], v[0:5], s[0:7], s[8:11] dmask:0xf
s_waitcnt vmcnt(0)
buffer_store_dwordx4 v[8:11], v7, s[12:15], 0 idxen glc
buffer_store_dwordx4 v[12:15], v7, s[12:15], 16 idxen glc
image_sample_lz v[0:3], v[4:5], s[0:7], s[8:11] dmask:0xf
s_waitcnt vmcnt(0)
buffer_store_dwordx4 v[0:3], v7, s[12:15], 32 idxen glc
s_waitcnt vmcnt(0)
s_endpgm
