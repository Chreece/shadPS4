.text
.p2align 8
.long 0xbeeb03ff, 0
v_and_b32 v1, 1, v0
v_lshlrev_b32 v1, 2, v1
v_and_b32 v0, 2, v0
v_lshlrev_b32 v0, 1, v0
v_add_i32 v1, vcc, -1, v1
v_add_i32 v0, vcc, -1, v0
v_cvt_f32_i32 v1, v1
v_cvt_f32_i32 v0, v0
v_mov_b32 v2, 0
v_mov_b32 v3, 1.0
v_mov_b32 v4, 2
exp pos0 v1, v0, v2, v3 done
exp param0 v1, v4, v2, v3
s_endpgm
