# Universal shadPS4 experiment diary

This index is **emulator-wide**, shared by Ragnarök, Ghost of Tsushima and ES-DE tests. Record every trial, including compilation failures and reverted attempts, by exact source SHA and evidence. No shader hash, game ID or per-game workaround is a fix. The canonical per-run machine-readable trial stream is `~/projects/shadps4/diary/trials.jsonl` on the test host; this versioned index preserves reviewed conclusions and links to technical evidence.

## Evidence-backed trials: 26 October 2026

| Trial / source | Test phase | Measured evidence and visual result | Decision |
| --- | --- | --- | --- |
| `shadps4-srt-bda-20261026-130836-3231483` / `11610d67064433179d7943d684b0632d470a3c7b` | Compiled, 12/12 tests | Runtime 36.867 s, exited -11, RADV context/device loss; no proven gameplay frame. | **BLOCKED**. Keep bounded BDA concepts only as candidate, no promotion. |
| `shadps4-srt-warnfix-20261026-140602-4067061` / `44cae5008d29e2f2d7af214744dc2905d18e7607` | Compiled, 18/18 tests | Runtime 29.191 s, exit -11. Console ends with X11 fatal IO and thread deadlock, host display restarted; no rendered gameplay. Full native warning count 116→104; first-party 24→14. | **BLOCKED**. Retain proven first-party warning corrections; further Vulkan stability undetermined. |
| `shadps4-srt-findilsb-20261026-160302-916073` / `60384c87bd83a90a8b41141d60a64a8dc579436e` | 25/25 tests but native link not reached | Build stopped at `fs.cpp:719-721`: Logger privately inherited BaseDevice so polymorphic shared_ptr conversion failed. No game launched. | **BUILD FAILURE**, retain FindILsb tests/fix; repair Logger inheritance, do not describe graphics. |
| `shadps4-srt-findilsb-20261026-170628-1424127` / `14975c44f2ab4840a2d73f15e60ef72314f6cef5` | 25/25 tests; linked, 120.265 s native observation | Full console has **0 unsupported FindILsb32** versus 2,357 in prior bounded excerpt; **3,906 dynamic Phi**, **5,591 failed SRT offsets**, 19 invalid sharp sources. Screenshots at 11/58/104s all show waiting-for-game-video loading overlay; 58s and 104s are byte-identical. No real game video. `DeviceLost` counter 0 but RADV reports soft-recovery context loss at shutdown. | **KEEP** implemented FindILsb32 and Logger fix; retain SHA256 `8316d6cb2243f0d4ffd8f838a02046738d67bff426e6f344698b25b6f421cf28` as comparison, **NOT** gameplay-validated. |
| `shadps4-srt-gpu-phi-20261026-173859-1718732` / `6551cd3476bd606d44f1d6d3fd49af6abc413bc7` | 30/30 tests; native compiled; runtime **32.856 s** early exit -11 | GPU finite-Phi sharp split **0**; dynamic Phi **3,906**, failed SRT offsets **5,592**, invalid sharp **20**, demand-paged shaders **36**, unsupported FindILsb **0**. 11/21s captures are byte-identical waiting-for-video splash; RADV context lost and Vulkan ErrorDeviceLost. Incremental compile warning count 0 is NOT comparable to full native builds. | **REJECT / REVERT** finite indexed-Phi source change because no discovered pattern was exercised; restore previous graphics code. Preserve all test evidence and keep 120s validated FindILsb candidate. |

The known stable preexperiment native binary SHA256 is `7604bae00c78b72e163138327f61a2f92bf549ac66446487d1ab7370a2f9b4d1`; production ES-DE / GoW executable SHA256 is `8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e`. Do not overwrite either. Other game trials remain in their original detailed technical threads/files and host JSONL; do not retroactively manufacture incomplete missing results in this index.

## Technical evidence

- [GoW SRT and FindILsb trial report](gow-srt-findilsb-progress-20261026.md)
- [GoW warning and X11 postmortem](gow-x11-fault-warning-audit-20261026.md)
- [GoW on-demand BDA evidence](gow-srt-bda-20261026-evidence.md)

For each new trial: record exact source and executable SHA, upstream base, build/test passes **and failures**, warning counts and scope, game/title, visual and audio observations, runtime and error evidence, retained/reverted decisions, and next generic fix. Never silently discard trials or claim a black/loading screenshot as rendering success.
