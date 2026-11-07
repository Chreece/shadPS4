// SPDX-License-Identifier: GPL-2.0-or-later
// Original GCN probe; first two dwords hold the OrbShdr offset.
.text
.long 0xbeeb03ff, 20
s_mov_b64 vcc, s[4:5]
s_mov_b32 s10, 0
s_cbranch_vccz zero
s_branch after_zero
zero:
s_mov_b32 s10, 1
after_zero:
s_mov_b32 s11, 0
s_cbranch_vccnz nonzero
s_branch after_nonzero
nonzero:
s_mov_b32 s11, 1
after_nonzero:
s_mov_b64 s[14:15], exec
s_mov_b64 exec, s[8:9]
s_mov_b32 s12, 0
loop:
s_ff1_i32_b32 s16, s6
s_ff1_i32_b32 s17, s7
s_cmp_lg_u32 -1, s16
s_cselect_b64 s[18:19], exec, 0
s_cmp_eq_u32 -1, s17
s_cselect_b64 vcc, exec, 0
s_or_b64 vcc, vcc, s[18:19]
s_cbranch_vccz high
s_bitset0_b32 s6, s16
s_branch join
high:
s_bitset0_b32 s7, s17
s_add_i32 s16, s17, 32
join:
s_cmp_eq_u32 -1, s16
s_cbranch_scc1 done
s_add_u32 s12, s12, 1
s_cmp_ge_u32 s12, 65
s_cbranch_scc1 done
s_branch loop
done:
s_mov_b64 exec, s[14:15]
v_mov_b32 v1, s10
v_mov_b32 v2, s11
v_mov_b32 v3, s12
v_mov_b32 v4, s6
buffer_store_dwordx4 v[1:4], v0, s[0:3], 0 idxen
s_waitcnt vmcnt(0)
s_endpgm
