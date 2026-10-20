.text
.p2align 8
.long 0xbeeb03ff, 0
buffer_load_dword v1, v0, s[0:3], 0 idxen
s_waitcnt vmcnt(0)
v_rcp_legacy_f32 v2, v1
v_rcp_f32 v3, v1
buffer_store_dwordx2 v[2:3], v0, s[4:7], 0 idxen glc
s_waitcnt vmcnt(0)
s_endpgm
