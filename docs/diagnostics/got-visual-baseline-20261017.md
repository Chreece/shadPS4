# Ghost of Tsushima: visual correctness baseline (2026-10-17)

This branch pins the **source that retained the horseback world** in the
`got-gow-shreg-table-20261017-205408.tar.gz` 92-second test.

Source parent/reference: `e9f470b359845a456139a865aa1d7b3001f0dbfd`.
Game executable was tested via temporary pinned overlays; the runner restores
its original executable and source after a test. This branch is **not** a
production deployment and does not claim full rendering correctness.

## Observed evidence

| Test | Candidate | GPU-feedback flag | Pipelines | Final visual |
| --- | --- | --- | ---: | --- |
| `got-gow-shreg-table-20261017-205408` | `e9f470b` | not present | 790 | horse/rider, partial terrain/world |
| `got-gpu-tsharp-depthfixed-20261017-213442` | `bee2a087` | 1 | 579 | 75 s near-black; many world objects missing |
| `got-feedback-off-ab-20261017-215148` | `bee2a087` | 0 | 808 | horse/rider, partial terrain/world; screenshot overlay about 8 FPS |

All three runs reached the game, with **zero reported GPU hangs**. Pipeline
counts are workload/compile observations, *not* a standalone graphics-quality
score. Same-time screenshots can show different game frames if timing differs.
Enabling per-fragment ID-feedback atomics substantially altered
rendering/performance; the A/B comparison makes this a regression suspect.
Do **not** use that feedback experiment as a rendering baseline or interpret
its increased FPS as proof of an optimization.

## Missing implementation, not just performance

- FS `0x7eaf40ad`: 24 SRT dependencies unresolved; a real guest T# table
  occupies 884,832 bytes (27,651 x 32-byte records), whereas the experimental
  Vulkan image-array implementation accepts at most 15 guest entries.
- CS `0x55d17c16`: 44 unresolved SRT dependencies.
- CS `0x5c4c2b6f`: 76 unresolved SRT dependencies.
- Unresolved offsets involve runtime `Phi`, `GetAttributeU32`,
  and `BitCastU32F32(GetAttribute)`. Do not host-evaluate
  per-invocation values or replace them with arbitrary descriptor zero.
- Shader compilation passing does not mean images are correctly bound.
  The captured per-shader runtime image audits still report null images.

An indexed-image renderer must preserve GPU-selected guest indices,
store a correct dynamically updated index-to-Vulkan-resource mapping,
validate table/data formats and address ranges, and synchronize
guest-memory/texture-cache lifetimes. Never bind a truncated 15-entry
prefix as though it represented the complete guest table.

## Promotion gate for new candidates

1. Compare actual menu and world screenshots at corresponding scene/game
   positions, not just wall-clock seconds or FPS; do not promote on higher FPS
   when geometry/textures are missing.
2. Confirm fewer **valid** null/unresolved T# resources and demonstrate
   actual Vulkan image bindings for the targeted shader(s).
3. Retain GoW PM4 SET_SH_REG, proven resource, CPU and memory fixes.
4. No extra per-fragment tracing/atomic operations in the visual candidate.
5. Build and host regressions must pass; game must run without a GPU hang.
6. Preserve the tested baseline executable, game profile/saves,
   Sunshine/X11/ES-DE and the SSH session.

Next implementation target: correct GPU-run indexed resource resolution and
residency first (start with FS `0x7eaf40ad` white menu); follow with compute
image reads and writes. Do not start another FPS-only A/B cycle.
