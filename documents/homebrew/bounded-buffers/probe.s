.text
.p2align 8
.long 0xbeeb03ff, 0
buffer_load_dword v1, v0, s[4:7], 0 idxen
s_waitcnt vmcnt(0)
v_readfirstlane_b32 s12, v1
s_cmp_lt_u32 s12, s8
s_cbranch_scc0 done
s_mul_i32 s13, s12, 32
s_buffer_load_dwordx4 s[16:19], s[0:3], s13
s_waitcnt lgkmcnt(0)
v_mov_b32 v2, 1
buffer_load_dword v3, v2, s[16:19], 0 idxen
s_waitcnt vmcnt(0)
v_add_i32 v3, vcc, 256, v3
buffer_store_dword v3, v2, s[16:19], 0 idxen glc
s_waitcnt vmcnt(0)
buffer_load_format_x v4, v2, s[16:19], 0 idxen glc
s_waitcnt vmcnt(0)
v_add_f32 v4, 1.0, v4
buffer_store_format_x v4, v2, s[16:19], 0 idxen glc
s_waitcnt vmcnt(0)
done:
s_endpgm
