# v4 — 14 October 2026, 15:36 build-log repair

Source evidence: `esde-20261014-153630.log` shows a single merge conflict in `src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp`. Upstream at `f47bbd5d6e9f49d3fd11cb4fd50533a835976a08` has shader versions `9/6` (blob `08108e16888984bb8b06cb87ddedc6c094b995d3`), whereas the exact proven GoW Phase91 ancestor `bde2b3c1fdc8add6b73b24894d4d47837b1f2db3` has `11/8` (blob `adc6045a0064fbe284848a54c4ea33ed431e37cc`).

The full 15,063/15,064-byte GitHub files were compared; **all bytes other than the two version declarations are identical**. The verified local conflict-replay fixture produced `MERGE_CONFLICT_FLOW=PASS`: detect one conflicted path, verify stages, select GoW values, commit merged result. v4 handles **only this exact conflict and exact pair of stage blobs**, then verifies the committed blob and GoW ancestry. Any other conflict, changed upstream code, changed blob, unexpected cache version, or nontrivial serialization difference fails without installing anything.

Earlier v3 fallback for missing `src/core/debug_state.cpp` was also verified: upstream and the GoW pin both contain blob `3d6ec64f2394c155ae4e0b4e0b69ca62c4c1d2a5`.

**One-paste on the Debian host (v4 pinned source):**

```bash
curl -4fsSL https://raw.githubusercontent.com/Chreece/shadPS4/3cc8f3b6c7d43f1fa748fc371c76af3c018d0125/esde-v4.py | python3 -
```

This is an upstream-main merge and release-build candidate, **not a tested game binary**. The C++ compilation, preserved overlay reconciliation, loading GUI and controller exit, and gameplay still require verification on the user's machine. Existing ES-DE is protected by the installer's fail-closed checks.

---

**Short pinned mobile-SSH command (same v2 code, matching blob `b9f077681fabae0c991c2fc3ba04f433a47b4e1b`):**

```bash
curl -4fsSL https://raw.githubusercontent.com/Chreece/shadPS4/e9e354c0ecd37e34e05d9b8ebc48843e882739e2/esde.py | python3 -
```

# Update: v2 repair after 14:36 CEST log

The first user run on 2026-10-14 at 14:36:54 stopped before build:
`GoW provenance branch changed; refusing unreviewed commits`.
The GoW branch moved from the pinned implementation `bde2b3c1...` to `2dca9c6d...` solely through a 55-line documentation commit. The GoT branch also added later diagnostics. These changes do **not** invalidate the pinned implementation.

The v2 bootstrap uses immutable original installer source from GitHub commit `153775e23f3e7f2c77fced12a37e9a69b26ddfd1`, verifies plaintext SHA-256 `4351dd9a97ca439215d543cf70307cd6653cb8e39d356d19633ded6691da0b85`, then patches only audited exact matches and compiles it before execution.

**Recommended one-paste command (v2):**

```bash
curl -4fsSL --http1.1 https://raw.githubusercontent.com/Chreece/shadPS4/37a93129ba9381e6aaed4e513b35c2388ed4d948/scripts/build-esde-proven-20261014-v2.py | python3 -
```

New safeguards: ancestry validation rather than mutable branch tip equality; never merge later diagnostics; exact GoW SHA as merge target; pinned GoW/GoT file reads; preflight local Phase91/v46 evidence before re-cloning; reuse previously downloaded Git objects without depending on old worktrees; report upstream conflicts under `evidence/upstream-merge-conflicts.*` and abort the incomplete merge. ES-DE is not changed on a source/build failure.

Offline syntax checks and a mock advanced-branch ancestry test passed. Compilation and gameplay on the user's Debian/RADV server remain **unverified** until a new run finishes. The `--print-source` flag displays the entire patched source for audit, and `--check-source` checks the signature/patch/syntax without building.

---

# ES-DE verified integration candidate — 14 October 2026

This is a **source integration/build-and-stage installer**, not a precompiled release or a claim of gameplay compatibility.

## Goal

- Clone **official `shadps4-emu/shadPS4` `main`** in a new workspace.
- Retain the pinned, custom Linux CPU identity/GPU path from `Chreece/shadPS4` GoW Phase91.
- Reconcile exact on-disk **GoW Phase91** source/object fingerprints; do not invent new shader values.
- Reconcile exact on-disk **GoT v40/v45/v46** source corrections; private RADV remains game-specific.
- Carry `Chreece/shadPS4` **startup/loading GUI** (`0b7ff78483e831db2d424b2351f5d931a56a1555`) and **controller quit dialog** (`075bd16f79dd7ea4342f5a3d1790e1c52cf77fcd`).
- Compile on Linux x86-64 using Clang 19 or GCC 14, with the CPU-ID translator bundled.
- Retain the established `~/Applications/shadps4/shadps4` ES-DE entry, using a backed-up launcher. Detect GoW (`CUSA34384`) and set `SHADPS4_GOW_SRT_AUTO_ROOTS=2` just for that game. Detect GoT (`CUSA11456`) and select a user-provided private RADV manifest, if configured.
- Abort on mismatches/conflicts; never replace a running game's binary or delete the old builds.

## Install candidate

```bash
curl -4fsSL https://raw.githubusercontent.com/Chreece/shadPS4/153775e23f3e7f2c77fced12a37e9a69b26ddfd1/scripts/build-esde-proven-20261014.py | python3 -
```

The checked-in launcher contains an integrity-verified compressed copy of the 425-line readable Python source, preserved byte-for-byte (`SHA-256 4351dd9a97ca439215d543cf70307cd6653cb8e39d356d19633ded6691da0b85`). It can print the **entire readable source** using `python3 scripts/build-esde-proven-20261014.py --print-source`.

Work: `~/projects/shadps4/ESDE/release-<timestamp>`

Logs: `~/projects/shadps4/logs/esde-<timestamp>.log`

A successful compile and CLI smoke test produces `RESULT=INSTALLED_PENDING_GAMEPLAY`. The GoW notice and GoT brightness screen are the last witnessed visual milestones; later gameplay has **not** been demonstrated here.

## Guardrails

- No `sudo`, driver replacement, emulator process kill, save/config reset, or SSH logout.
- Existing GoW/GoT test directories are **read only** for source provenance.
- The installation is left unchanged if a conflict, missing input, build/test failure or active game is detected.
- Original ES-DE executable/symlink is saved to `shadps4.before-proven-<timestamp>`. In a failed post-install smoke test the original launcher is restored.
- **Limitations:** this builder has been Python-syntax and generated Bash-syntax checked off host. The actual binary still needs to be built and tested on the Debian machine. User-specific patches may conflict with current upstream, in which case the installer safely stops and writes a conflict report rather than guessing.

## Historical inputs

GoW branch `fix/gow91-texel-stream-coherency-20261014` head `bde2b3c1fdc8add6b73b24894d4d47837b1f2db3`; known visible output requires the Phase91 binary's runtime flag. GoT branch `fix/got-v33-gow-srt-port-20261013` head `3c067d02124a242f1b13b7245a7c8b558dd87152`; v46 corrected Vulkan-invalid bitfield lowering using isolated overlay.

**Do not merge this preparation branch into fork main as a replacement for an upstream rebase.** The script itself clones the official source fresh.
