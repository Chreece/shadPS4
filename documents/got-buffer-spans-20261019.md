# GoT large buffer-table repair, 2026-10-19

The latest native capture completes without the previous crash, but the menu remains white and the world is missing. It exposes a remaining live shader defect: large GPU-selected buffer tables exceed the bounded-expansion binding budget, dropping vector loads and a formatted byte store. The candidate preserves those operations through shared buffer spans. Local compilation, address checks and shader replay pass; visual recovery is not established.

Source: `a75e0223dd17feb655e08e7356940c2e9c6d187d`. Paired host/runtime scripts: `f2b2ef3555cc822021ba7ffec737f2637dd02ed5`. Branch: `diagnostic/got-indexed-sample-feedback-20261019`.

## Latest native evidence

Input: `got-table-guard-evidence-20261019-203255.tar.gz`, built from `71faef4a8d76282a1931445198571082f0e3c70e`. The capture reaches its 300-second timeout with no reported GPU fault or view-pool exhaustion. Twenty-one logged table-bound rejections confirm that the previous cached-layout guard is exercised.

| Check | Result |
|---|---|
| Latest shader records | 951 distinct programs; final image/sampler resources resolve |
| Empty-table image/sampler cases | All four previously affected programs resolve |
| Remaining live buffer failure | `0x5065ed7b`, at larger table counts |
| Table sizes for that shader | 153 permutations, 1–1,773 records; 144 exceed 13 records |
| Latest captured IR for that shader | No buffer store survives |
| Executed texture feedback | 217,086 samples; no out-of-range or nonfinite samples |
| High-volume indirect scene queries | Four complete with `index_count=12`, `instance_count=0`, zero GPU vertices/fragments |
| Direct controls | Three complete with nonzero GPU execution |
| Visible result | White menu; late image has no restored world |

Three other `bad_buffers=1` records are unused static placeholders for scalar reads already lowered through the GPU memory path. They are distinct from the lost live vector accesses in `0x5065ed7b`. A final descriptor count alone is not sufficient evidence that a live access exists or executes correctly.

Texture feedback includes 216,450 nonzero samples from a 797-record compute table. Nonzero RGBA includes alpha and does not prove correct RGB. The missing compute store is a confirmed defect, but its responsibility for the white menu and zero scene instances is not yet proven.

## Repair

Small tables retain the existing bounded expansion. For larger unresolved compute buffer operations, the compiler recognizes affine descriptor reads and builds one shared buffer span per descriptor field. The recorded shader has four such fields inside 504-byte records: offsets 364, 380, 400 and 428. Offset 400 includes the signed 8-bit formatted load/store.

The CPU validates the descriptor table, finds the shared address span, and refreshes metadata before specialization/binding. The GPU keeps the original selected descriptor and index. Generated checks cover selector bounds, layout, individual buffer extent and the bound span. Raw vector operations check each component; formatted operations retain the existing format conversion and predicated byte store. The captured shader needs ten buffer resources with this path, rather than a separate descriptor for every table record.

The binder uses persistent buffer-cache storage for shared spans. Only merged child ranges receive shader-write tracking and texture invalidation; gaps between buffers are not declared written. Metadata survives shader-cache serialization. Binary/meta versions are 53/27.

This is generic emulation behavior, with no game or shader-hash condition. The initial implementation accepts host-visible affine tables whose live children share a descriptor layout, within the device storage-buffer range and 32,768-record limit. It requires 64-bit shader arithmetic and zero scalar offset, and excludes swizzling/add-thread-ID. Mixed layouts, invalid mappings and oversized spans fail closed. These limits remain explicit rather than substituting arbitrary resources.

## Validation and limits

- Full Release compilation and shadPS4 link pass.
- The actual captured 13-record shader/table retains 18 raw scalarized loads and one byte store, with ten buffers and zero unresolved buffer/image/sampler resources. A reduced binding budget deliberately exercises the new path for that small capture.
- Synthetic 797- and 1,773-record fixtures repeat those 13 captured records. Both retain the store and pass Vulkan 1.3 SPIR-V validation. **They are scale checks, not the complete native large tables.**
- All three replays pass real shader-metadata serialization/refresh round trips. The external harness replaces only native virtual-memory mapping validation with access to captured memory; native mapping and Vulkan execution are not validated here.
- Sixteen evaluations of the actual generated address IR pass, including valid accesses, component bounds, invalid selectors, zero records, altered layout and addresses outside the span. Eleven span-builder boundary/refusal cases pass.
- All seven fresh and 82 earlier shader replays still compile and validate. The four previously documented missing-input fixtures in the earlier set remain fixtures.
- The earlier count/stride/cache guard replay still passes, including inaccessible retired memory. Existing host preflight checks, including LDS/Phi and synchronization checks, pass.
- Bash syntax, all 16 embedded Python blocks and all 87 overlay paths are checked.

The [OpenOrbis probe](homebrew/bounded-buffers/README.md) now includes 1,773 records, first/middle/last/out-of-range selectors, stride variation and sentinel checks over every record. Its GCN assembly is unchanged from the assembled Bonaire version. The OpenOrbis application has not been linked or run on PS4 here. No native Vulkan device/gameplay session is available in this workspace.

## Next native run

The isolated launcher builds this pinned candidate, restores the previous source/executable, and captures 300 seconds with a copied profile. Conditional Ollama unloading below 8 GiB free VRAM and restoration are retained. Production is unchanged; GDS, DMA, depth and raster experiments remain disabled.

`BufferTableSpan` reports lowering; bounded `BufferTableSpanBound` reports refreshed record counts, bytes and child ranges at binding checkpoints. Zero records in those reports can indicate that the new path refused the live table. The opt-in descriptor-table dump limit increases from 64 KiB to 8 MiB so the complete 797/1,773-record tables can be captured. The archive is named `got-buffer-span-evidence-<timestamp>.tar.gz`.

Enter gameplay during the capture. Verify that live large-table spans bind, the formatted store survives, scene instance counts and GPU geometry return, and the menu/world visibly improve. If spans are refused or graphics remain missing, the complete large tables and retained-write evidence identify the next concrete failure. Build success and valid SPIR-V do not establish that GoT is fixed.
