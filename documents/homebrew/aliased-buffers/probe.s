.text
.p2align 8
.long 0xbeeb03ff, 0
v_mov_b32 v2, s10
s_cmp_eq_u32 s8, 0
s_cbranch_scc0 copy
v_mov_b32 v1, s9
buffer_store_dword v1, v2, s[0:3], 0 idxen glc
s_waitcnt vmcnt(0)
s_branch done
copy:
buffer_load_dword v1, v2, s[0:3], 0 idxen glc
s_waitcnt vmcnt(0)
v_mov_b32 v3, s11
buffer_store_dword v1, v3, s[4:7], 0 idxen glc
s_waitcnt vmcnt(0)
done:
s_endpgm
