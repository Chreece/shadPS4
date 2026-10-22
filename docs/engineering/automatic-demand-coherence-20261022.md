# Automatic demand coherence — first shared-policy stage

This package creates a three-file source delta on the exact clean candidate
27e3a68a6a96f2aa6bab42df50a32f2cbf0f2d77 (tree 40ad46ae118d3d6c2112eed0d821d0b4cd0d146e).
It does NOT re-import the legacy experimental renderer, replace the progressing
94ed29b7 executable, alter saves, or claim that all games now work.

## Implemented

The existing Precise readback path becomes mandatory independent of old global,
per-title, or clean-mode readbacks_mode values. That path arms CPU-read watchers
for GPU-dirty pages and downloads their real contents when the CPU access demands
it. GPU-only work does not require a CPU download merely because time passed.
The existing copy batching (including widened dirty-region windows), waits and
cache invalidation stay unchanged. This is activation of the existing demand
coherence machinery, not a newly optimized coherence engine.

The current Linux userfaultfd backend registers write-protect faults only. It
cannot fulfill CPU-read watching. Backend selection therefore chooses the existing
read-capable signal implementation when precise demand coherence is required,
regardless of a saved userfaultfd preference. This is a backend capability rule,
not a title, shader, driver-vendor, or hardware-model exception.

The legacy JSON field remains parseable for existing frontends/configuration
files, but cannot disable coherence. Per-title overrides for this field are no
longer exported. The old setter is retained as a no-op compatibility API; the
getter reports the effective Precise policy. Fresh defaults and launch logging
are consistent with that policy. No config file is rewritten by this package.
UI removal of legacy controls in external launchers is not included.

## Evidence and boundaries

The uploaded 19:57 run uses the old reference executable with readbacks_mode=2
and three functional indexed-resource switches. It ends on user/session stop,
not a recorded context-loss event. Earlier 9d70b53b testing still lost context
with readbacks_mode=2. Consequently this policy change is NOT claimed to repair
that hang or to substitute for porting dynamic texture/sampler-table support.
The indexed-resource implementation and all shader instructions are unchanged.

Images and buffer aliases still depend on the existing image-cache implementation;
this package does not claim complete PS4 image/CPU coherency or complete GPU memory
emulation. There are no new blanket synchronization waits or frame-wide readbacks.
Performance and broader compatibility need native testing. No game was run here.

## Verification

verify_coherence.py compiles exact policy/backend fragments and unchanged tracker
functions with explicit page-cache/backend stubs. It covers 90 combinations of
old global/per-title/clean-mode values and UFFD preferences; clean/empty demand,
dirty demand, re-arming, repeated clean access, callback-unlocked ordering and
backend selection. The original policy and original write-only backend selection
fail separate executable negative controls. The stubs do NOT perform real Vulkan
readbacks, OS signal handling or GPU work. GCC 14 and Clang 17 with Clang ASan/UBSan
passed locally. The host verifies the original tracker blobs and the complete
three-file production delta before the same tests and real production compilation.

Seventeen source/workflow/real-Git checks and twelve installer checks passed here.
They include unchanged active checkout, preservation of previous shader/dependency
objects, refusal of wrong source trees and package corruption, repeat installation,
complete manifests and cleanup after an injected test-log collection failure.
They are not tests of actual OS faults, driver synchronization or all-game behavior.

## Host action

Install the complete pinned package and run run_coherence.py --build-only.
It uses the already-installed checksum-verified native/Sirit build and restoration
workflow, starts from the full latest candidate (not a reconstructed old stack),
keeps the exact Sirit gitlink and all prior changes, compiles the actual affected
translation units and completes the link. It never launches the game. Original
checkouts and build executable are restored, and the new binary is preserved
separately. UPLOAD_THIS prints the build report on completion or failure.

No full new emulator build, GPU run, recovered loading or all-games compatibility
is asserted until tested. Do not replay the known hanging candidate as a supposed
new graphics fix solely because readbacks no longer need a manual setting.

Payload: 9296 bytes; SHA256 b38446e621dcb7fadf9a78ec02a793fec9531500917aa4040e50fa6c281ac0a6.
Installer SHA256 d882d87e0c3f734baa8fe1225ef6cdec6dd549e7ba624e206d9221017fda0839.
