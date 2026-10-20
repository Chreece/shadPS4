.text
.p2align 8
.long 0xbeeb03ff, 0
s_lshl_b32 s9, s8, 6
v_add_i32 v0, vcc, s9, v0
buffer_load_dword v1, v0, s[0:3], 0 idxen
s_waitcnt vmcnt(0)
buffer_store_dword v1, v0, s[4:7], 0 idxen glc
s_waitcnt vmcnt(0)
s_endpgm
