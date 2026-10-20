.text
.p2align 8
.long 0xbeeb03ff, 0
s_and_b64 s[10:11], s[4:5], s[6:7]
s_cselect_b32 s14, 1, 0
v_mov_b32 v1, s14
buffer_store_dword v1, v0, s[0:3], 0 idxen offset:0 glc
s_andn2_b64 s[10:11], s[4:5], s[6:7]
s_cselect_b32 s14, 1, 0
v_mov_b32 v1, s14
buffer_store_dword v1, v0, s[0:3], 0 idxen offset:256 glc
s_nand_b64 s[10:11], s[4:5], s[6:7]
s_cselect_b32 s14, 1, 0
v_mov_b32 v1, s14
buffer_store_dword v1, v0, s[0:3], 0 idxen offset:512 glc
s_or_b64 s[10:11], s[4:5], s[6:7]
s_cselect_b32 s14, 1, 0
v_mov_b32 v1, s14
buffer_store_dword v1, v0, s[0:3], 0 idxen offset:768 glc
s_orn2_b64 s[10:11], s[4:5], s[6:7]
s_cselect_b32 s14, 1, 0
v_mov_b32 v1, s14
buffer_store_dword v1, v0, s[0:3], 0 idxen offset:1024 glc
s_nor_b64 s[10:11], s[4:5], s[6:7]
s_cselect_b32 s14, 1, 0
v_mov_b32 v1, s14
buffer_store_dword v1, v0, s[0:3], 0 idxen offset:1280 glc
s_xor_b64 s[10:11], s[4:5], s[6:7]
s_cselect_b32 s14, 1, 0
v_mov_b32 v1, s14
buffer_store_dword v1, v0, s[0:3], 0 idxen offset:1536 glc
s_xnor_b64 s[10:11], s[4:5], s[6:7]
s_cselect_b32 s14, 1, 0
v_mov_b32 v1, s14
buffer_store_dword v1, v0, s[0:3], 0 idxen offset:1792 glc
s_not_b64 s[10:11], s[4:5]
s_cselect_b32 s14, 1, 0
v_mov_b32 v1, s14
buffer_store_dword v1, v0, s[0:3], 0 idxen offset:2048 glc
s_and_saveexec_b64 s[12:13], s[4:5]
s_cselect_b32 s14, 1, 0
s_mov_b64 exec, s[12:13]
v_mov_b32 v1, s14
buffer_store_dword v1, v0, s[0:3], 0 idxen offset:2304 glc
s_orn2_saveexec_b64 s[12:13], s[4:5]
s_cselect_b32 s14, 1, 0
s_mov_b64 exec, s[12:13]
v_mov_b32 v1, s14
buffer_store_dword v1, v0, s[0:3], 0 idxen offset:2560 glc
s_waitcnt vmcnt(0)
s_endpgm
