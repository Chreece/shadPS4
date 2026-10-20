.text
.p2align 8
.long 0xbeeb03ff, 0
s_dcache_inv
s_lshl_b32 s7, s6, 3
s_load_dwordx2 s[8:9], s[4:5], s7
s_waitcnt lgkmcnt(0)
s_load_dword s10, s[8:9], 0
s_waitcnt lgkmcnt(0)
v_mov_b32 v1, s10
v_mov_b32 v2, s6
buffer_store_dword v1, v2, s[0:3], 0 idxen glc
s_waitcnt vmcnt(0)
s_endpgm
