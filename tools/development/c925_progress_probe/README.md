# Temporary live-progress probe for the retained C925 build

This is **diagnostics, not a rendering fix or a main-merge candidate**. PR103's latest native run compiled and exercised both host-readback barrier paths, but the85/112-second images are identical and the materials remain absent. The previous GDB snapshots caught synchronous descriptor readback in a *progressing* run. They do not identify the frozen run's blocking operation.

The shared journal review is `docs/emulator/reviews/20261028T191841526857Z-host-readback-native.json`. Prior rejected Linear8/readback-toggle/unchanged-PR96 experiments and GoW's conflated scheduler-counter warning remain applicable. This probe records individual semaphore identities rather than combining unrelated timelines.

## What changes temporarily

The exact-source adapter adds scopes to eight native C++ files: scheduler, semaphore, rasterizer, pipeline cache, presenter, video-output driver, buffer cache, texture cache. A new header writes scalar progress state through lock-free atomic references into a private, pre-created64KiB mapped file. Normal tracing paths do not allocate or perform file I/O on the render thread; the Python runner samples that file once per second. Fixed32-thread/12-level budgets report dropped scopes, and sequence checks avoid presenting partially updated stacks as coherent samples. No guest-memory contents or shader binaries are read by this probe.

Scopes distinguish shader lookup, buffer/image downloads, submission, waits, graphics/compute preparation and flip preparation/presentation. Counters represent **function entry/completion, not GPU-executed draw counts**. Durations are inclusive wall time, not CPU/GPU execution duration. Nested durations must not be summed as exclusive cost. Counter fields can be sampled between increments; stable per-thread stacks have their own sequence checks.

An active timeline wait is polled with250ms `vkWaitSemaphores` timeouts only while diagnostics are enabled. The original target is unchanged; timeout never counts as success, no semaphore is signalled, and no work is skipped. After each timeout the existing `Refresh()` obtains an actual Vulkan counter value. The initial recorded `KnownGpuTick` may be cached; only the subsequent timeout/Refresh observation is a current query. The pre-existing non-success retry policy remains unchanged; this tool is not an error-recovery fix. No debugger attaches or pauses are used, but instrumentation/polling can still affect timing, so a non-reproduced freeze is not a resolution.

## Native protections and cleanup

The runner requires exact PR103 ELF `b572e6f78facd0175b4a18ca3074909c91e6c2923c2cfe6398d40912a021d641` plus the latest accumulated source manifests. It checkpoints that ELF, runs the actual mapped-file fixture on available host compilers, applies the eight-file diagnostic overlay, and builds incrementally with visible output and15-second quiet-build heartbeats. It never launches after build failure.

After one menu-only118s TERM +2s KILL test it **restores all eight original source files and the PR103 ELF, even after a successful diagnostic run**. It removes the added probe header. Restored source modification times advance so Ninja cannot silently reuse diagnostic objects in a later baseline build. A separately named diagnostic executable checkpoint is retained for provenance, not installed in ES-DE or a launcher. Original saves/configuration/protected executables remain untouched. The existing isolated profile,8GiB VRAM requirement, known-context same-server Ollama restoration and no-input contract remain. The experiment does not restart Sunshine, X11, SSH, containers or network services.

Only the test child receives `SHADPS4_PROGRESS_FILE`. The trace file must be a regular nonsymlink owned by the child user, exact size and private permissions. The environment selector enables diagnostic recording only, never title-specific renderer semantics.

## Actual local validation

* Actual header/file mapping, nesting, updates and eight-thread workload:39 assertions PASS plus8002 scopes under GCC and Clang C++20 Werror, and Clang ASan/UBSan. Disabled mode also PASS. This is not a native emulator build.
* Actual transformed `Semaphore::Wait` body extracted into a representative Vulkan interface fixture: four cases PASS in disabled and enabled modes under both GCC/Clang. Covers cached-complete, real-counter-complete, repeated timeout then success, and inherited error retry. Original target and requested timeout are asserted. The fixture is not a Vulkan-device test.
* Eleven Python tests PASS in0.258s with ResourceWarning treated as error: exact eight-source transform, changed-source prewrite rejection, repeat refusal, mechanical Git reverse/forward checks, actual source+ELF rollback fixture, parser invalid/unready/version/unstable states, explicit semaphore identity/target, isolated-profile rejection/no writes, and real mapped-file collector shutdown.
* Initial extracted-wait fixture compilation failed under `-Werror=misleading-indentation` in its mock-device scaffolding. Braces were added to that fixture; all four enabled/disabled cases were rerun successfully. Production overlay was unchanged by that fixture correction.
* Full native emulator compilation and actual GPU/probe capture **not yet run**. The whole host runner has not been end-to-end executed here. No frozen-frame cause or graphics improvement is claimed.

The host command downloads `execution_progress_probe.h`, `apply_progress_probe.py`, `progress_reader.py`, `probe_contract.cpp` and `run_progress_probe.py` from one pinned commit, plus the previously pinned runtime and model-headroom helpers. All outcomes go to `~/projects/shadps4/logs/got-c925-progress-probe-*.tar.gz`, including complete native build errors or rollback problems.

Official upstream main was checked at `b6f0c58dba9ee75b93477703032ae4f1e0de8f1c`; this diagnostic is explicitly not a rebase or production source integration.
