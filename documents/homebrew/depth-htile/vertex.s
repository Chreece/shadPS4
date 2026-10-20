.text
.p2align 8
.long 0xbeeb03ff, 0
v_mov_b32 v5, 0x40400000
v_cmp_eq_u32 vcc, 1, v0
v_cndmask_b32 v1, -1.0, v5, vcc
v_cmp_eq_u32 vcc, 2, v0
v_cndmask_b32 v2, -1.0, v5, vcc
v_mov_b32 v3, s0
v_mov_b32 v4, 1.0
exp pos0 v1, v2, v3, v4 done
s_endpgm
