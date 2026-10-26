# Ghost of Tsushima - shadPS4 engineering checkpoint

This directory is the **GoT game-specific evidence appendix**, not a second emulator-wide diary. The single authoritative combined GoW/GoT/all-games history is [the universal emulator trial diary](../emulator/EXPERIMENT_DIARY.md), with an append-only [structured trial ledger](../emulator/TRIALS.jsonl) and [generic fix registry](../emulator/FIXES.json). The goal is generic correctness across games - never title-ID cases, shader-hash branches, fake descriptors, skipped draws, synthetic texels, forced input, or unsafe CPU substitutions for live GPU values.

- [EXPERIMENT_DIARY.md](EXPERIMENT_DIARY.md): successful and failed experiments with emulator effect, native proof, game appearance, performance, retained code and exact evidence.
- [BASELINES.json](BASELINES.json): machine-readable hashes and promotion status.
- [PENDING_MAIN_FORWARDPORTS.md](PENDING_MAIN_FORWARDPORTS.md): proven resource changes still awaiting isolated C++ forward-port and main build regression, plus exact accepted and rejected provenance.
- [PROMOTION_POLICY.md](PROMOTION_POLICY.md): rules for going from local exact-source validation to the GoT tree, a main-targeted PR, and safe general-engine promotion.

**Development branches**: the last experimentally validated PR #52 + PR #57 baseline is `test/generic-precise-linear-image-coherence-20261025`; the separate proven-resource-capability candidate is PR #64; the consolidated GoT development branch is `got/proven-progress-20261026`. These differ from GitHub `main` by hundreds of commits; **do not merge the full experimental ancestry into main**. Main promotion must be a small, reviewed and build-validated forward-port of the specific proven generic change.

**Required for EVERY game build/test:** real game loading splash path (reads `/app0/sce_sys/pic1.png`, presents `IsSplashVisible()`), gamepad host quit dialog (GUIDE opens, A/Cross confirms, B/Circle cancels, consumes inputs), manual gameplay, `timeout -s TERM -k 2s 118s`, SSH stays open, frozen binary SHA, four screenshots, bounded error logs and full `UPLOAD_THIS` diagnostics. Do not modify saved configuration, ES-DE, game saves, or the last accepted candidate for experiments. Gameplay ends within 120 seconds; build time is separate. A forced timeout may report exit 124 **or 137**; inspect GPU/device error evidence separately.

**Candidate locations on user host:** `~/projects/shadps4/GhostOfTsushima/candidates/`. Keep both SHA-pinned accepted and latest demonstrated candidates. Logs under `~/projects/shadps4/logs/`. Scripts under `~/projects/shadps4/scripts/`. Never delete a binary until an exact SHA, corresponding archive and disposition are recorded.

**Status labels**: `KEEP - tested generic capability` (correct behavior observed even if visible geometry unchanged), `KEEP - visual`, `DIAGNOSTIC`, `REJECT`, `BLOCKED`, `NOT TESTED`. KEEP in the dedicated GoT tree does **not** imply safe fast-forward of its entire ancestry to `main`. Main promotion requires isolated diff plus native regression checks.

The remaining important unresolved systems are dynamically indexed image/buffer resources outside already bound tables; SRT walker parent dependencies; SHARP flatbuf sources; stencil-reference and unsigned/saturated buffer-size warnings; dark/untextured terrain; missing character geometry; and GPU/CPU sync performance. Separate each experimentally.
