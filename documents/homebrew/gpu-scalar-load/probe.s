.text
.p2align 8
.long 0xbeeb03ff, 0
s_cmp_eq_u32 s8, 0
s_cbranch_scc0 read_value
v_mov_b32 v1, s9
v_mov_b32 v2, 0
buffer_store_dword v1, v2, s[0:3], 0 idxen glc
s_waitcnt vmcnt(0)
s_branch done
read_value:
s_dcache_inv
s_load_dword s12, s[4:5], 0
s_waitcnt lgkmcnt(0)
v_mov_b32 v1, s12
v_mov_b32 v2, s10
buffer_store_dword v1, v2, s[0:3], 0 idxen glc
s_waitcnt vmcnt(0)
done:
s_endpgm
