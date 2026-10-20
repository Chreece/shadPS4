.text
.p2align 8
.long 0xbeeb03ff, 0
v_mov_b32 v1, 0x13579bdf
buffer_store_dword v1, v0, s[0:3], 0 idxen glc
s_waitcnt vmcnt(0)
s_endpgm
