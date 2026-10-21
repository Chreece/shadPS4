.text
.p2align 8
.long 0xbeeb03ff, 0
s_cmp_eq_u32 s8, 0
s_cbranch_scc0 read_value
v_mov_b32 v1, s10
v_add_i32 v1, vcc, s9, v1
v_mov_b32 v2, s10
v_lshlrev_b32 v2, 12, v2
buffer_store_dword v1, v2, s[0:3], 0 idxen glc
s_waitcnt vmcnt(0)
s_branch done
read_value:
s_dcache_inv
s_lshl_b32 s11, s10, 3
s_buffer_load_dwordx2 s[12:13], s[4:7], s11
s_waitcnt lgkmcnt(0)
s_load_dword s14, s[12:13], 0
s_waitcnt lgkmcnt(0)
v_mov_b32 v1, s14
v_mov_b32 v2, s10
buffer_store_dword v1, v2, s[0:3], 0 idxen glc
s_waitcnt vmcnt(0)
done:
s_endpgm
