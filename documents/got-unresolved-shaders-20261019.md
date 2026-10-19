# Recovered shader-resource repairs, 2026-10-19

Follow-up: [bounded GPU-selected buffer loads and stores](got-bounded-buffer-tables-20261019.md). The next gameplay capture confirms zero unresolved final image/sampler fetches across 949 distinct shaders, but graphics remain broken. The report below records the earlier image/sampler candidate and its validation; its source and runner pins are historical.

All 82 shader binaries flagged with unresolved images or samplers in the recovered capture now compile with zero unresolved image/sampler fetches in local replay. All 82 emitted SPIR-V modules validate for Vulkan 1.3. This is compiler/resource-path progress; the white menu, scene geometry, terrain and horses have not been visually verified with this candidate.

Source: `84117bbd1e356f6440c4c7dcfc239d950f144d1d`. Host/runtime scripts: `b88c112b123c68e9db57bc67b352dba6300b962e`.

## Evidence and changes

Input: `got-unresolved-shaders-recovery-20261019-183558.tar.gz`, including original GCN, flattened user data, SRT walker programs, and pre-resource-patch IR for all 82 shaders (59 compute, 22 fragment, one vertex). The original capture did not launch a new game or rebuild the emulator.

| Shared defect | Captured shaders | General correction |
|---|---:|---|
| Buffer units differ from material-record stride | 54 compute | Derive complete table entries from total bytes. A captured 216-by-16-byte V# describes nine 384-byte material records. Keep exact divisibility, full-record bounds, profile limits and the GPU null sentinel. |
| Repeated samples consume the same expansion budget repeatedly | 22 fragment + 1 compute | Reserve expanded sampler sources once per identical table, count and depth mode. Preserve each sample's coordinates and offsets. |
| Small pointer-table loops and conservative sampler budgeting | 3 compute + 1 vertex | Recognize shifted/zero-offset addressing, proven zero-based signed counters, constant latch bounds, uniform samplers and image reads. Count known integer/floating sampler uses without unnecessarily doubling them. Keep bounds in shader specialization. |
| GPU-selected record contains a pointer to the descriptors | 1 compute | Retain the GPU record ID. Resolve each full table record's pointer into paired image/sampler arrays at binding time, with separate pointee offsets and readable-span checks. |

The indirect case has 13 captured 504-byte records. Its T# is 780 bytes into the selected pointee; its S# is 820 bytes into the same pointee. The shader selects the same array index for both; the extra null image slot handles out-of-range IDs. CPU code never substitutes an evaluated fragment/lane index.

No shader hash or title check controls these repairs. The existing opt-in strided-table switch remains. Existing LDS/Phi, synchronization, table-count specialization, and image-view capacity changes remain. Shader binary version is 50; persistent metadata version is 24 because image/sampler resource layouts now contain pointer-table metadata.

## Validation and limits

- Full shadPS4 compilation and link pass.
- All existing host preflight checks pass, including LDS/Phi, descriptor bounds and table-count specialization.
- All 82 recovered GCN programs replay without unresolved image/sampler fetches; all 82 generated modules pass `spirv-val --target-env vulkan1.3`.
- Seven bounded-pointer regression scenarios and six counter-proof scenarios pass. They cover count changes, zero/excessive bounds, bypassed guards, mismatched masks, arithmetic overflow, unsupported implicit sampling, signed counter initialization, non-unit steps and invalid loop edges.
- The production indirect reader passes checks for distinct contents, readable spans crossing mapping boundaries, tagged pointers, null/unmapped pointees, unreadable regions and truncated pointer reads.
- Script syntax, embedded Python, eight-entry hash-filter limits and all 82 overlay paths are checked. An abnormal-exit cleanup fixture preserves exit status, complete unresolved recovery, and indirect table/pointee files.

Four shaders require 168 additional host-read descriptor words that were absent from the original capture. Their entries below explicitly identify fixture-assisted replay. Fixtures populate only absent words, retaining all captured values, and are not claimed to be game textures. The other 78 replays need no invented host inputs. Indexed table contents are still runtime resources: successful compilation does not prove valid or correctly sampled textures on the GPU. The indirect pointee data also remains unverified on the user's GPU.

Workgroup dimensions and shader-input setup were reconstructed from captured IR/SPIR-V; local replay is not execution of the game or a hardware comparison. No PS4/OpenOrbis hardware run or new gameplay run was performed. The final acceptance remains visible world geometry and correct rendering.

## Next run

Use `scripts/got-indexed-sample-feedback-build-and-run-20261019.sh` from the latest diagnostic branch. It pins the source and paired scripts above, builds the isolated candidate, and runs the existing 300-second capture. It preserves the active emulator, source bytes and original profile. Ollama handling remains conditional on the existing 8 GiB VRAM threshold and restores prior state.

The eight detailed targets cover the original menu shader plus the stride-unit, indirect-pointer, repeated-fragment, vertex and three small-loop cases. The runtime also recovers any shaders still flagged unresolved. Indirect image targets are retained as `.table.bin.pointees.bin`: one 36-byte record per table index, consisting of a little-endian 32-bit readable flag followed by the raw 32-byte T#. Unreadable records carry a zero flag. Sampler binding counts and ordinary shader dumps remain available separately.

## Complete replay inventory

“Pass” means zero unresolved image/sampler fetches and valid Vulkan 1.3 SPIR-V. It does not mean the shader has been shown rendering correctly.

| Shader | Stage | Validation input | Result |
|---|---|---|---|
| `0x1d85313` | cs | Captured program and static inputs | Pass |
| `0x111263ee` | cs | Captured program and static inputs | Pass |
| `0x12808db5` | cs | Captured program and static inputs | Pass |
| `0x135bdfc8` | cs | Captured program and static inputs | Pass |
| `0x139aa00d` | cs | Captured program and static inputs | Pass |
| `0x155f46b2` | cs | Captured program and static inputs | Pass |
| `0x17973b3c` | cs | Captured program and static inputs | Pass |
| `0x17df04e9` | cs | Captured program and static inputs | Pass |
| `0x21b6953d` | cs | Captured program and static inputs | Pass |
| `0x22365e1f` | cs | Captured program and static inputs | Pass |
| `0x29c94ef4` | cs | Captured program and static inputs | Pass |
| `0x2ba92c19` | cs | Captured program and static inputs | Pass |
| `0x32ec6d10` | cs | Captured program and static inputs | Pass |
| `0x333e59b5` | cs | Captured program and static inputs | Pass |
| `0x33a001dc` | cs | Captured program and static inputs | Pass |
| `0x38ab3c08` | cs | Captured program and static inputs | Pass |
| `0x38edbd81` | cs | Captured program and static inputs | Pass |
| `0x3ca39324` | cs | Captured program and static inputs | Pass |
| `0x462299e6` | cs | Captured program and static inputs | Pass |
| `0x4783d0fe` | cs | Captured program and static inputs | Pass |
| `0x486c5b88` | cs | Captured program and static inputs | Pass |
| `0x489aa7f4` | cs | Captured program and static inputs | Pass |
| `0x5065ed7b` | cs | Captured program and static inputs | Pass |
| `0x5665d0a0` | cs | Captured program and static inputs | Pass |
| `0x56fb6bd4` | cs | Captured program and static inputs | Pass |
| `0x58a64dd6` | cs | Captured program and static inputs | Pass |
| `0x58d0fe7d` | cs | Captured program and static inputs | Pass |
| `0x58f22286` | cs | Captured program + explicit missing-descriptor fixtures | Pass |
| `0x5a56df57` | cs | Captured program and static inputs | Pass |
| `0x601993a3` | cs | Captured program and static inputs | Pass |
| `0x693b73c3` | cs | Captured program and static inputs | Pass |
| `0x6b6e3bc5` | cs | Captured program and static inputs | Pass |
| `0x7219c893` | cs | Captured program and static inputs | Pass |
| `0x76ea3515` | cs | Captured program and static inputs | Pass |
| `0x78fe685a` | cs | Captured program and static inputs | Pass |
| `0x7b469f0b` | cs | Captured program and static inputs | Pass |
| `0x7ed8fcac` | cs | Captured program and static inputs | Pass |
| `0x7f1ac0a4` | cs | Captured program and static inputs | Pass |
| `0x7fbbf7cc` | cs | Captured program and static inputs | Pass |
| `0x83c06d36` | cs | Captured program and static inputs | Pass |
| `0x92e1d1c6` | cs | Captured program and static inputs | Pass |
| `0x9cb3ffed` | cs | Captured program and static inputs | Pass |
| `0x9db8b2ff` | cs | Captured program and static inputs | Pass |
| `0x9ef15078` | cs | Captured program and static inputs | Pass |
| `0xa0194fef` | cs | Captured program and static inputs | Pass |
| `0xa40cb508` | cs | Captured program and static inputs | Pass |
| `0xa69d425f` | cs | Captured program and static inputs | Pass |
| `0xc3d87aea` | cs | Captured program and static inputs | Pass |
| `0xc7542465` | cs | Captured program + explicit missing-descriptor fixtures | Pass |
| `0xcabe3c02` | cs | Captured program and static inputs | Pass |
| `0xd196a0b4` | cs | Captured program and static inputs | Pass |
| `0xd62a4905` | cs | Captured program and static inputs | Pass |
| `0xde03cca1` | cs | Captured program and static inputs | Pass |
| `0xdf07c25f` | cs | Captured program and static inputs | Pass |
| `0xe476f075` | cs | Captured program and static inputs | Pass |
| `0xe4829727` | cs | Captured program and static inputs | Pass |
| `0xe7a4748e` | cs | Captured program and static inputs | Pass |
| `0xee963aae` | cs | Captured program + explicit missing-descriptor fixtures | Pass |
| `0xffa5ac47` | cs | Captured program and static inputs | Pass |
| `0x12759a5e` | fs | Captured program and static inputs | Pass |
| `0x2c68e189` | fs | Captured program and static inputs | Pass |
| `0x390183d0` | fs | Captured program and static inputs | Pass |
| `0x3bc41a98` | fs | Captured program and static inputs | Pass |
| `0x42d69219` | fs | Captured program and static inputs | Pass |
| `0x492c9042` | fs | Captured program and static inputs | Pass |
| `0x57dc9914` | fs | Captured program and static inputs | Pass |
| `0x841a87b9` | fs | Captured program and static inputs | Pass |
| `0x84e273e2` | fs | Captured program and static inputs | Pass |
| `0x8ace2eca` | fs | Captured program and static inputs | Pass |
| `0x9b058e50` | fs | Captured program and static inputs | Pass |
| `0x9e04f722` | fs | Captured program and static inputs | Pass |
| `0xae5db78e` | fs | Captured program and static inputs | Pass |
| `0xb1e037c6` | fs | Captured program and static inputs | Pass |
| `0xb491d9b7` | fs | Captured program and static inputs | Pass |
| `0xb98b960c` | fs | Captured program and static inputs | Pass |
| `0xc7fe0866` | fs | Captured program and static inputs | Pass |
| `0xdfc36ed1` | fs | Captured program and static inputs | Pass |
| `0xe0c69350` | fs | Captured program and static inputs | Pass |
| `0xe27e4a59` | fs | Captured program and static inputs | Pass |
| `0xe3f4c49a` | fs | Captured program and static inputs | Pass |
| `0xe7ec2c29` | fs | Captured program and static inputs | Pass |
| `0x142dead8` | vs | Captured program + explicit missing-descriptor fixtures | Pass |
