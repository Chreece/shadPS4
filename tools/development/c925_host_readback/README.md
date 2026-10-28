# C925 explicit GPU-to-host readback handoff

## Native evidence and limits

The uploaded `got-c925-frame-boundary-20261028T184819836147Z.tar.gz` (SHA256 `ced0f6fcc7f08c6da685ab84188fe4bbfc22e571ce89bc69ca8cc498dd561122`) reuses the validated PR102 ELF `da4a37e5f60069afbdcc7ab5f24a1e3495f92deae8b21c851dd1157a5d1dcbb3` without source/configuration changes. Four frames change; complete materials remain missing. The intermittent identical-frame freeze did **not** reproduce. Both native snapshots put the GPU command thread in `GetProgram -> generated SRT read -> SignalHandler -> ReadMemory -> DownloadMemory -> Scheduler::Finish -> Semaphore::Wait -> drmSyncobjTimelineWait`. Per-process GPU time and guest/command CPU counters increase. This establishes a readback wait path, **not a proven deadlock, target timeline value, failing shader, or exact stale-data read**. No wait may be removed on that evidence.

The exact uploaded buffer and image download call sites copy/retile into host-cached staging allocations, then retain completion/deferred callbacks and `Invalidate()` before CPU reads. Neither call site contains an explicit host-read memory dependency. The upstream runtime implementation reviewed records device copy access and defaults its dependency destination to `AllCommands/MemoryRead|MemoryWrite`, not a host read. **The native lower runtime/staging/tiler files are missing from the upload**, so the runner must inspect and capture them before applying this candidate. If it finds an existing host dependency, a missing helper, or an unfamiliar copy implementation, it returns a source-only archive without a patch, build or game launch. Absence of a token is a conservative source screen, not a complete synchronization proof.

Khronos separates execution completion, device-to-host memory availability/visibility, and noncoherent host cache invalidation. See the GPU-to-CPU example at https://docs.vulkan.org/guide/latest/synchronization_examples.html and host access/memory dependency rules at https://docs.vulkan.org/spec/latest/chapters/synchronization.html . A completion wait plus invalidate is not a substitute for the required memory dependency. This proposed correction does not establish a causal link to GoT's intermittent freeze or missing scene.

## Exact source change

The two-file adapter adds `BufferMemoryBarrier2` at each staging handoff, before the existing completion/deferred scheduling:

- Buffer download: COPY / TRANSFER_WRITE -> HOST / HOST_READ.
- Image readback: ALL_COMMANDS / MEMORY_WRITE -> HOST / HOST_READ, since the tiling path can produce staging bytes through compute as well as transfer.
- Buffer handle, offset and length are those of the actual staging allocation; both queue-family indices are ignored, with no ownership transfer.
- End dynamic rendering before recording the dependency.
- Keep all original copy sizes, copied byte contents, dirty-range decisions, `Finish`, deferred completion, `Invalidate`, and backing-memory writes unchanged.
- Bounded first-four-per-site messages show whether the handoff runs; they do not count all readbacks or prove stale reads were observed.

No new transfer, eager tiled copy, settings mutation, fake descriptor, shader hash/game condition, forced timeline value, skipped draw, disabled wait or removal of cache invalidation. No shader IR/translation changes or shader-cache deletion. The accepted PR68 constructor expression and PR69 stencil logic remain intact, as do PR95/96/99/100/102 source hashes.

Preimages are fixed by the supplied native archive. After-images:

- `buffer_cache.cpp`: `9e3f5d8a54ae8dd7496beaf4a4c9a9ba7362349daf18a9d46b89205ee4caaa5e`.
- `texture_cache.cpp`: `4daf189a752d09f035a7ac4012db2fd8c2306c13faea9871cbe9a3957f733f58`.

This branch is an exact native-source adapter, **not a completed C++ forward-port into fork main or a latest-upstream rebase**. Official upstream main checked `b6f0c58dba9ee75b93477703032ae4f1e0de8f1c`. Native evidence and selective integration are required before renderer promotion.

## Tests actually completed

- Actual generated barrier statements compiled and ran in a representative Vulkan-type recording context: **32 cases PASS** under GCC C++20 Werror, Clang C++20 Werror and Clang address/undefined sanitizers. Checks stage/access masks, exact offsets including >4GiB, sizes, ignored ownership indices, and end-rendering order. These are model API tests, **not actual Vulkan header compilation or GPU execution**.
- **21 Python tests PASS**, including mechanical Git forward/reverse byte restoration on the actual two uploaded files, changed-source/repeated-apply rejection, existing/missing/unknown helper guards, comment versus implementation matching, symlink refusal, a real child-process success/failure test, and mocked full-run success, fixture failure before writes, build-failure source/ELF rollback, preserved-source refusal, external-save refusal, model restore and final headroom checks.
- The mocked host tests are not native emulator results. No full native compilation or GPU run of this new change has occurred yet.
- Remote script Git blobs match the locally tested bytes.

Reproduce the Python tests with a directory containing the exact `src/...` preimages:

```bash
python3 -W error::ResourceWarning test_host_readback.py /path/to/native-source-snapshot
```

## Host runner

The pinned runtime helpers are the existing `resume_c925_revalidation.py` at commit `87d3eeacf828b0f990b6aeb11b3b5da46f1b0792` (SHA256 `df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250`) and `resume_finite_raw.py` at `9504f10af235f28842cbb103044edb362f550d1a` (SHA256 `c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b`). They are imported as helpers; their old main entry points are not called.

The runner checks the actual ELF, accumulates recorded source-after hashes in order, and verifies the current captured renderer/presentation sources. It audits the lower readback helpers and returns their exact bytes if assumptions need review. **Before modifying emulator source**, it compiles the barrier fixture against the host's actual checked-out Vulkan-Hpp headers, catching fragile API/initializer mistakes early. If those gates pass, it checkpoints PR102, changes only two C++ files, rebuilds the existing Ninja tree incrementally with full logs and a15-second heartbeat, and preserves all other source hashes. A failed build restores those two source files and the checkpointed ELF; no failed build launches a game.

One successful changed-source test uses the existing isolated profile, the unchanged8GiB VRAM requirement, same-server conditional model unload/context+expiry restoration, no input, absolute screenshot timestamps and118s TERM +2s owned-process KILL allowance. It does not attach a debugger, restart Sunshine/X11/network/services, alter original saves/configuration, or replace the protected emulator or launcher. Pixel signatures are recorded separately from process exit status. All outcomes are archived under `~/projects/shadps4/logs/got-c925-host-readback-*`; source-only and failed gates also produce evidence.

The canonical common journal already contains the latest frame/stack review at `docs/emulator/reviews/20261028T184819836147Z-frame-boundary.json` on main. This candidate's local results and native-pending limitations must remain distinct from that actual hardware run and from earlier kept shader fixes.
