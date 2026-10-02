# Reviewed upstream integration — 2026-10-02

Prepared with OpenAI Codex. This is a candidate for the user's Linux Docker build and game testing, not a claim that every open PR is safe or that the remaining visual symptoms are fixed.

Baseline: `ac9122931a1d19467e1288e29350a8fbd9bf7a14` (fork main).
Upstream snapshot: `62653e388ccd2f5d062b6bcf4df06e4081a0aca0`.
Reviewed all 65 open upstream PRs, their discussion/review comments and inline thread status, plus 12 recently merged PRs. Historical comments count as resolved only when subsequent changes/acknowledgments address them. Empty discussions are not proof of correctness.

## New changes

Fourteen new PR implementations are included. Six are still open upstream and eight have been merged upstream. Existing implementations of #5199, #5200, #5201 and #5202 were inspected and retained; their application produced no source changes.

| PR | Change | Upstream state |
|---|---|---|
| [#5220](https://github.com/shadps4-emu/shadPS4/pull/5220) | shader_recompiler: Preserve EXEC scope after empty branch | Open, experimental integration |
| [#5155](https://github.com/shadps4-emu/shadPS4/pull/5155) | video_core: Use each preloaded permutation's own flattened user data | Open, experimental integration |
| [#5207](https://github.com/shadps4-emu/shadPS4/pull/5207) | file_sys: Normalize guest paths and fix mount matching | Open, experimental integration |
| [#5213](https://github.com/shadps4-emu/shadPS4/pull/5213) | texture_cache: Raise max image views | Open, experimental integration |
| [#4929](https://github.com/shadps4-emu/shadPS4/pull/4929) | video_core: delete an incompatible pipeline cache instead of ignoring it | Open, experimental integration |
| [#4726](https://github.com/shadps4-emu/shadPS4/pull/4726) | amdgpu: Reassemble fragmented ASC PM4 packets | Open, experimental integration |
| [#5212](https://github.com/shadps4-emu/shadPS4/pull/5212) | common: Rework slot vector | Merged |
| [#5118](https://github.com/shadps4-emu/shadPS4/pull/5118) | texture_cache: Avoid associating color images with stencil | Merged |
| [#5217](https://github.com/shadps4-emu/shadPS4/pull/5217) | kernel/time: Implement monotonic real-time network clock | Merged |
| [#5208](https://github.com/shadps4-emu/shadPS4/pull/5208) | kernel: Fix sceKernelReserveVirtualRange alignment and implement ClearName | Merged |
| [#5186](https://github.com/shadps4-emu/shadPS4/pull/5186) | shader_recompiler: Fix V_ALIGNBIT_B32 / V_ALIGNBYTE_B32 shift by 32 at shift 0 | Merged |
| [#5182](https://github.com/shadps4-emu/shadPS4/pull/5182) | kernel: Implement sceKernelReleaseFlexibleMemory and POSIX mlock/munlock aliases | Merged |
| [#5194](https://github.com/shadps4-emu/shadPS4/pull/5194) | shader_recompiler: Fix V_CVT_PK_U8_F32 rounding, saturation and byte select | Merged |
| [#5203](https://github.com/shadps4-emu/shadPS4/pull/5203) | shader_recompiler: Fix OpImageTexelPointer coords for 1D | Merged |

Each topic has its own branch: `integrate/shader-prs-20261002`, `integrate/kernel-prs-20261002`, `integrate/texture-prs-20261002`, `integrate/cache-prs-20261002`, `integrate/pm4-prs-20261002`, and `integrate/paths-prs-20261002`. These are merged into `integrate/reviewed-prs-20261002` before publication to main. Origin PRs and commits are attributed in each topic commit.

## Integration adaptations

- Preserve NGS2 in the top-level test target list when importing the PM4 test target.
- Update the depth-growth regression fixture for upstream SlotVector's `Insert` API. The depth initialization implementation and evidence markers remain in place.
- Advance the shader binary cache version from 6 to 7 so old shader binaries cannot bypass the EXEC/ALU/atomic fixes. The first game launch can require shader recompilation.
- Add the standalone `pr_integration` suite to the existing local Docker installer gate. No all-platform CI is requested; integration commits use `[skip ci]`.

## Evidence and limits

- Local GCC 13 Release: 13 production-code regression cases pass (EXEC divergence 3, stable storage 2, guest paths 2, upstream PM4 6).
- Negative control: compiling the same suite against the old CFG source fails exactly the two empty-then/else cases; the scalar-only case passes. The combined source passes all three.
- Existing NGS2, user-service, occlusion, image-transfer, graphics-diagnostics and six quit-dialog checks pass on the combined source.
- Installer and evidence collector: 23 Python checks pass.
- The 13 new regression cases also pass in Debug with undefined-behavior sanitization and no sanitizer report.
- Full combined emulator build is **pending on the user's system**. This workspace has no Docker daemon. Upstream PR builds do not substitute for the exact combined revision. The provided local installer runs focused checks, a full Linux build, dependency/startup smoke checks, and switches the default only after they pass.
- No game session from this combined revision has been observed. Acceptance requires exact-revision logs AND user feedback, including a repeat launch. Passing CPU tests does not establish GPU correctness, visual quality, performance, or universal hardware/platform compatibility.

## Preserved local work

Keep the working NGS2/ATRAC9 streaming, direct filters, interpolation and 7.1 mapping; sparse/BDA fixes; missing-user startup guard; measured occlusion/count handling; validated transfer/barrier and presentation changes; and depth-growth initialization. The latest gamepad-exit implementation remains present, but its runtime confirmation is still pending.

Do not reintroduce the locally rejected raw-buffer synchronization (#5120), containment (#4818), old tiled-mip experiment, or late audio tracing. The unrelated upstream mip/detiling change #5196 is not imported in this candidate. No Sunshine, service, display, save, or audio configuration changes are part of this integration.

## Deferred PRs

These are deferred, not a claim that every PR is defective. Reasons distinguish concrete objections/failures from absent evidence or features outside this local test's scope. The exact reviewed heads and discussion URLs are in `REVIEWED_PRS_20261002.json`.

| PR | Reason |
|---|---|
| [#5219](https://github.com/shadps4-emu/shadPS4/pull/5219) | Reviewer reports current Linux build broken. |
| [#5216](https://github.com/shadps4-emu/shadPS4/pull/5216) | Unresolved patch-order review; macOS-only change. |
| [#5168](https://github.com/shadps4-emu/shadPS4/pull/5168) | Changes-requested review remains; compute dispatch skipping needs GPU review. |
| [#5149](https://github.com/shadps4-emu/shadPS4/pull/5149) | Reviewer asked to shelve until testable; guessed ABI/codec behavior and unconfirmed hang fix. |
| [#5134](https://github.com/shadps4-emu/shadPS4/pull/5134) | Depends on multiple unaccepted graphics/kernel changes; no isolated acceptance for this combination. |
| [#5218](https://github.com/shadps4-emu/shadPS4/pull/5218) | Large default-on experimental resource-guard change; author requests architectural review, no independent acceptance. |
| [#5120](https://github.com/shadps4-emu/shadPS4/pull/5120) | Previously tested locally and removed as ineffective; do not reintroduce. |
| [#5091](https://github.com/shadps4-emu/shadPS4/pull/5091) | Maintainer requests normal coherency correctness before copy fast path; unresolved normal-path behavior. |
| [#5121](https://github.com/shadps4-emu/shadPS4/pull/5121) | Raw-copy fast path overlaps rejected raw-buffer experiment and unresolved normal-path coherence review in #5091. |
| [#5215](https://github.com/shadps4-emu/shadPS4/pull/5215) | Requires unmerged sirit #20; AMD path not tested. |
| [#5179](https://github.com/shadps4-emu/shadPS4/pull/5179) | MipStats returns an empty placeholder; reviewer asks for real reporting and program register behavior. |
| [#5188](https://github.com/shadps4-emu/shadPS4/pull/5188) | Reviewer requests VFS abstraction; latest proposed host-mutation approach lacks final review/acceptance. |
| [#5190](https://github.com/shadps4-emu/shadPS4/pull/5190) | Broad predication/streamout change overlaps our measured occlusion implementation; no combined acceptance. |
| [#5189](https://github.com/shadps4-emu/shadPS4/pull/5189) | Unmap semantics changed repeatedly after correctness objections; no final confirmation for latest behavior. |
| [#5192](https://github.com/shadps4-emu/shadPS4/pull/5192) | Code review: Vnode readiness is triggered unconditionally, without detecting a file change; socket/low-water semantics incomplete. |
| [#5211](https://github.com/shadps4-emu/shadPS4/pull/5211) | Camera feature/ABI expansion is outside the current Linux game-test scope; no camera fixture available. |
| [#5140](https://github.com/shadps4-emu/shadPS4/pull/5140) | Discussion includes FMV crash report; attribution unresolved. Keep existing tested presentation synchronization. |
| [#5197](https://github.com/shadps4-emu/shadPS4/pull/5197) | Changes requested over tests not proving hardware semantics; unresolved. |
| [#5046](https://github.com/shadps4-emu/shadPS4/pull/5046) | Unresolved sample-count CMask correctness review. |
| [#5183](https://github.com/shadps4-emu/shadPS4/pull/5183) | Draft dependency reorganization; not eligible. |
| [#5159](https://github.com/shadps4-emu/shadPS4/pull/5159) | Maintainer suggests a different page-table approach; unresolved. |
| [#4953](https://github.com/shadps4-emu/shadPS4/pull/4953) | Maintainer requires rebase. |
| [#5143](https://github.com/shadps4-emu/shadPS4/pull/5143) | Maintainer identifies masked race/root cause; author calls it a demonstration. |
| [#5073](https://github.com/shadps4-emu/shadPS4/pull/5073) | Unresolved packaging review; no local emulator fix. |
| [#4863](https://github.com/shadps4-emu/shadPS4/pull/4863) | Umbrella patch has regression/rebase/split requests. Prefer individually reviewed fixes. |
| [#3396](https://github.com/shadps4-emu/shadPS4/pull/3396) | Draft. |
| [#4610](https://github.com/shadps4-emu/shadPS4/pull/4610) | Draft; overlaps existing measured occlusion support. |
| [#5051](https://github.com/shadps4-emu/shadPS4/pull/5051) | Unanswered extent/same-address image correctness questions. |
| [#4341](https://github.com/shadps4-emu/shadPS4/pull/4341) | Unresolved test architecture review; not an emulator fix. |
| [#5014](https://github.com/shadps4-emu/shadPS4/pull/5014) | Code review: signed arithmetic/unchecked shifts can be undefined; unresolved constants can retain stale values. Defer parser expansion. |
| [#4891](https://github.com/shadps4-emu/shadPS4/pull/4891) | Unresolved DLC entitlement/semantics review. |
| [#5002](https://github.com/shadps4-emu/shadPS4/pull/5002) | Maintainer requires rebase. |
| [#4808](https://github.com/shadps4-emu/shadPS4/pull/4808) | Author confirms game output has not been verified and agrees to wait for game testing. |
| [#5041](https://github.com/shadps4-emu/shadPS4/pull/5041) | Unresolved non-Neo compatibility review. |
| [#4830](https://github.com/shadps4-emu/shadPS4/pull/4830) | Draft. |
| [#4372](https://github.com/shadps4-emu/shadPS4/pull/4372) | Unresolved interpolation correctness review. |
| [#4887](https://github.com/shadps4-emu/shadPS4/pull/4887) | Unresolved configuration-migration review. |
| [#4794](https://github.com/shadps4-emu/shadPS4/pull/4794) | Bundle has requested changes and unresolved review; do not import for log cleanup. |
| [#4971](https://github.com/shadps4-emu/shadPS4/pull/4971) | No runtime evidence; test encoder changes remove zero initialization and lack execution cases. Defer SDWA expansion. |
| [#4885](https://github.com/shadps4-emu/shadPS4/pull/4885) | Unresolved filesystem error/correctness review. |
| [#4939](https://github.com/shadps4-emu/shadPS4/pull/4939) | Unresolved inline reviews. |
| [#4185](https://github.com/shadps4-emu/shadPS4/pull/4185) | Draft. |
| [#4682](https://github.com/shadps4-emu/shadPS4/pull/4682) | Unresolved networking override review. |
| [#4738](https://github.com/shadps4-emu/shadPS4/pull/4738) | Linux/Windows test jobs failed; build jobs passed. Logs return HTTP 410, so cause cannot be verified; excluded. |
| [#4699](https://github.com/shadps4-emu/shadPS4/pull/4699) | Draft timing experiment. |
| [#4818](https://github.com/shadps4-emu/shadPS4/pull/4818) | Previously tested locally and removed as ineffective; do not reintroduce. |
| [#4796](https://github.com/shadps4-emu/shadPS4/pull/4796) | Linux/Windows test jobs failed; build jobs passed. Logs return HTTP 410, so cause cannot be verified; excluded. |
| [#4721](https://github.com/shadps4-emu/shadPS4/pull/4721) | Draft. |
| [#4720](https://github.com/shadps4-emu/shadPS4/pull/4720) | Large replacement cache architecture without review or supplied runtime evidence; defer until isolated validation. |
| [#4714](https://github.com/shadps4-emu/shadPS4/pull/4714) | Unanswered maintainer question about observed attachment configuration. |
| [#4616](https://github.com/shadps4-emu/shadPS4/pull/4616) | Maintainer asks for more testing after past related regressions; no confirmed target-game regression test. |
| [#4773](https://github.com/shadps4-emu/shadPS4/pull/4773) | Broad alias-coherency rewrite overlaps previously rejected texture experiments; no verified combined acceptance. |
| [#4770](https://github.com/shadps4-emu/shadPS4/pull/4770) | Unaddressed maintainer objection to placement and approach. |
| [#4771](https://github.com/shadps4-emu/shadPS4/pull/4771) | Draft. |
| [#4712](https://github.com/shadps4-emu/shadPS4/pull/4712) | Maintainer reports clang still fails. |
| [#4320](https://github.com/shadps4-emu/shadPS4/pull/4320) | Draft. |
| [#4220](https://github.com/shadps4-emu/shadPS4/pull/4220) | Draft. |
| [#4160](https://github.com/shadps4-emu/shadPS4/pull/4160) | Unresolved renderer architecture review. |
| [#3771](https://github.com/shadps4-emu/shadPS4/pull/3771) | Draft. |

## Local acceptance

1. Close the game and ES-DE normally. Run the pinned one-paste Docker installer as the desktop user. Keep the printed restore command.
2. Wait for `DEFAULT_MAIN_RESULT=PASS`, then `CAPTURE_ARMED` and `NOW launch`. Launch the ordinary game entry. The collector waits indefinitely for this launch; its session limit starts after the game starts.
3. Visit the same problem area and a new area, watch a cutscene, check dialogue and 7.1 output, and test Home then Cross to exit. Report any input leaking to the game.
4. Exit normally. Upload the report printed after exit and describe what you saw/heard. Run the same pinned command again for a second launch; a verified installed binary is reused.
5. Keep saves/settings/7.1 intact. Runtime errors, missing geometry, stalls, regressions or incomplete captures block a claim of success. The working rollback core remains available.
