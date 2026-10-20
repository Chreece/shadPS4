.text
.p2align 8
.long 0xbeeb03ff, 0
s_mov_b32 m0, s4
v_interp_mov_f32 v4, p0, attr0.y
v_interp_p1_f32 v5, v0, attr0.x
v_interp_p2_f32 v5, v1, attr0.x
v_cvt_u32_f32 v2, v2
v_cvt_u32_f32 v3, v3
v_lshlrev_b32 v3, 6, v3
v_add_i32 v2, vcc, v2, v3
buffer_store_dwordx2 v[4:5], v2, s[0:3], 0 idxen glc
s_waitcnt vmcnt(0)
exp null off, off, off, off done vm
s_endpgm
