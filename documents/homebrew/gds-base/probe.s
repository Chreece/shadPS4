.text
.p2align 8
.long 0xbeeb03ff, 0
s_mov_b32 m0, 0x07000020
v_mov_b32 v0, 0
v_mov_b32 v1, 17
v_mov_b32 v2, 29
ds_write2_b32 v0, v1, v2 offset0:1 offset1:2 gds
s_waitcnt lgkmcnt(0)
s_mov_b32 m0, 0x08000020
v_mov_b32 v1, 41
ds_write_b32 v0, v1 offset:4 gds
s_waitcnt lgkmcnt(0)
s_mov_b32 m0, 0x07000020
ds_read2_b32 v[4:5], v0 offset0:1 offset1:2 gds
s_waitcnt lgkmcnt(0)
v_mov_b32 v1, 3
ds_add_rtn_u32 v6, v0, v1 offset:4 gds
s_waitcnt lgkmcnt(0)
v_mov_b32 v1, 20
v_mov_b32 v2, 23
ds_cmpst_rtn_b32 v7, v0, v1, v2 offset:4 gds
s_waitcnt lgkmcnt(0)
ds_read_b32 v8, v0 offset:4 gds
s_waitcnt lgkmcnt(0)
s_mov_b32 m0, 0x08000020
ds_read_b32 v9, v0 offset:4 gds
s_waitcnt lgkmcnt(0)
s_mov_b32 m0, 0x00001000
v_mov_b32 v0, 0x704
ds_read_b32 v10, v0 gds
s_waitcnt lgkmcnt(0)
v_mov_b32 v0, 0x804
ds_read_b32 v11, v0 gds
s_waitcnt lgkmcnt(0)
v_mov_b32 v0, 0
buffer_store_dwordx4 v[4:7], v0, s[0:3], 0 idxen offset:0 glc
buffer_store_dwordx4 v[8:11], v0, s[0:3], 0 idxen offset:16 glc
s_waitcnt vmcnt(0)
s_endpgm
