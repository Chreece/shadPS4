# Relaxed fence readback candidate

Status: EXPERIMENTAL. Not a verified The Last Guardian fix. No upstream pull request is implied.

This candidate adds read watches for outstanding GPU buffer writes at the command processor's existing `Rasterizer::OnFence()` calls. A later CPU read fault still uses the existing synchronous `ReadMemory`/`DownloadMemory` implementation. Arming a fence does not itself copy buffers or wait for the GPU.

GPU-dirty pages and installed read watches are tracked separately. This avoids duplicate watches at repeated fences and avoids removing a watch that was never installed when a write fault downloads data before the next fence.

`Disabled` remains disabled. `Precise` retains immediate read protection. The new behavior is exercised with `Relaxed`; no title ID or call to `SetReadbacksMode` is added. The Linux write-protect-only userfaultfd backend falls back to signals for either enabled readback mode.

## Tests and limits

`python3 tests/standalone/readback_fence_test.py` compiles the real tracker headers with a recording PageManager/settings boundary. Eleven cases include 30,000 randomized transitions, repeat fences, partial downloads, pre-fence write faults, read-only buffers, zero sizes, holes and word/region boundaries. Add `--sanitize` for ASan and UBSan.

These tests are not Vulkan synchronization tests, not multithreaded stress tests, not a full emulator build and not a gameplay test. The fake fence boundary in the baseline is a no-op; the failing baseline case demonstrates the new policy's observable behavior, not proof that the old Relaxed mode violated its intended contract.

The installed emulator, launch wrappers, saves, configuration, shader caches, and existing playtest stack are not modified by preparing this branch. Any runtime trial must retain the existing upstream + validated PR stack and loading screen, add only this candidate, record the effective readback mode, and compare the same scene. No performance improvement is claimed yet.
