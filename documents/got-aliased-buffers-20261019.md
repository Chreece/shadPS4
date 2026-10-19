# GoT sparse-buffer coherence repair, 2026-10-19

The `got-buffer-span-evidence-20261019-212746.tar.gz` capture built and launched source `a75e0223dd17feb655e08e7356940c2e9c6d187d`. The world remains missing. It ended at image-pool exhaustion, not a reported GPU VM fault. This candidate fixes two reproducible resource-coherence defects and enables bounded image-slot-pressure collection in the isolated runner. No visual recovery is claimed.

## Native evidence

- All 222 captured permutations of `0x5065ed7b` retain a byte store. The large writable span binds with 1,773 records, 1,390,953,848 bytes and 1,269 merged child ranges. Final image/sampler resources resolve.
- Three direct control queries execute geometry. Four selected high-volume indirect queries execute none: two have zero indices and two have zero instances. These selected draws are not a census of every scene draw.
- The native feedback checkpoints for the compute table sampled only 13/10/10-record versions. They do not establish whether the later 1,773-record version sampled correctly.
- Image slots rise from 31,049 at submission tick 1916 to 60,929 at tick 1929. The fixed pool asserts at 66,891 occupied slots. Device memory at the last reported checkpoint is about 8.98 GB. The capture does not distinguish retained live images from pending deferred destruction.
- Late screenshots still lack the world. Build success, resolved resources and surviving stores are not gameplay success.

## Changes

`Runtime` previously indexed hazards by Vulkan buffer handle and buffer-relative offsets. BufferCache can create overlapping sparse arenas backed by the same guest-memory allocations. A shader write through one arena was invisible to a subsequent hazard check through another. Sparse-buffer hazards now use one guest-address interval namespace; ordinary buffers keep separate handle-based namespaces. Existing global memory barriers clear both namespaces.

`Info::RefreshBufferTableSpans` previously merged adjacent/overlapping children. Texel synchronization and GPU texture invalidation use child base addresses. Coalescing erased those addresses, so later children could miss the existing synchronization/invalidation path. The candidate preserves every distinct address/size pair and removes only exact duplicates. Gaps remain excluded from shader-write tracking. This does not add a new partial-image alias implementation or change the existing writable-texel copy policy.

The isolated runner enables the existing opt-in texture-slot-pressure path. Its virtual image reservation is enlarged to 1,048,576 slots, and collection budgets scale with excess occupied slots. Low VRAM usage no longer cancels an active slot-pressure budget. Age checks, GPU-valid-image download requirements and scheduler-deferred destruction remain in place. New counts distinguish creation, reclamation and pending destruction. This addresses the observed fixed-capacity failure; a long native run is still required to assess churn and stability. Production/default slot-pressure behavior remains unchanged.

Table dumps and GPU feedback take another bounded checkpoint when table size grows substantially. Contiguous image tables are now eligible for dumps as well. Dump storage is capped at 64 MiB of table payload, 128 captures and 64 resource keys; pointee sidecars may add bounded payload. This should capture the actual large table rather than repeatedly collecting only the early small table.

## Validation

- Complete Release shadPS4 compilation and link passed.
- Source-extracted hazard regression: old code fails cross-arena read-after-write; new code passes 13 checks covering RAW, WAR, WAW, indirect reads, overlap endpoints, flush clearing, unrelated ranges, ordinary buffers and interval growth. Vulkan command submission is stubbed in that host regression; it is not a GPU run.
- Actual child-range normalization preserves adjacent/overlapping children and different sizes at one base, while removing exact duplicates.
- An [OpenOrbis overlapping-buffer probe](homebrew/aliased-buffers/README.md) is supplied. Its GCN assembled for Bonaire with LLVM 19. The application has not been linked or executed on PS4 here. The small homebrew allocation checks guest overlap semantics; it does not force separate Vulkan sparse arenas.

The Vulkan specification requires memory dependencies between accesses to aliased sparse resources: https://docs.vulkan.org/spec/latest/chapters/sparsemem.html . Image destruction remains deferred until submitted uses complete: https://docs.vulkan.org/refpages/latest/refpages/source/vkDestroyImage.html . The upstream image expansion discussion at https://github.com/shadps4-emu/shadPS4/pull/5282 was reviewed, but its proposed change is not included: this capture does not establish that particular mip-loss case.

The remaining white menu and absent world require native verification. No GDS, clipping, depth or raster-state experiment is enabled by this candidate.
