# Universal SRT / GPU BDA evidence — 2026-10-26

## Baseline and failed candidate

- Game context: God of War Ragnarök CUSA34384; generic fixes apply to the emulator, never title identifiers or skip flags.
- Preserved successful native baseline executable SHA-256: `7604bae00c78b72e163138327f61a2f92bf549ac66446487d1ab7370a2f9b4d1`.
- Baseline result: 120.314 s test-limit termination, not a demonstrated playable graphics scene. At 58 and 104 s the screenshots were black.
- First invariant-Phi-only candidate executable SHA-256: `23468f9c3f8fd8d2555c487eb22d1d6514d7db7e623106a137849b688bd4d357`.
- Uploaded Phi runtime archive: `shadps4-gow-srt-phi-20261026-120214-2284610.tar.gz`, SHA-256 `e56db165d72990354421cdd2eccb3e2ac41cbeb83464240b99dcb8496493df95`.
- Phi runtime: 32.508 s; exit `-11`; Vulkan/RADV reported context lost and `ErrorDeviceLost`. The code -11 does not on its own identify a CPU fault; no original fault RIP was captured.
- Two 11 s / 21 s screenshots are byte-identical and show the startup loading overlay, not gameplay.
- Kernel journal was readable but contained no matching reset line in the selected interval.
- Console summary: 11,272 errors, many `Failed to compute offset for SRT walker` messages. GPU-dependent `Phi` expressions remain unresolved. Warnings about huge clamped buffer bindings and missing SRT sharp locations also remain. It is not established that a particular Phi caused device loss.
- Six invariant Phi regression tests passed, but were not a gameplay correctness proof.

## Source-level generic correctness defects

1. The default optional `direct_memory_access_enabled=false` disabled dynamic ReadConst entirely. When an SRT load retained flatbuf offset zero, shader emission instead accessed flatbuffer word zero, unrelated to the guest address. With host Vulkan BDA capability, unflattened ReadConst must use the guest-address GPU path automatically; flattened loads stay on the fast path.
2. The device fault bitmap used load/OR/store on a shared word. Concurrent shader invocations marking different pages could lose bits. Replace it with a device-scope atomic OR.
3. A 64-bit guest address was truncated to a u32 page-table index without testing whether the dword belonged to the arena's 40-bit guest GPU range. Share the CPU and shader bounds and reject invalid addresses before indexing.

New isolated generic source candidate: `11610d67064433179d7943d684b0632d470a3c7b` from branch `fix/universal-dynamic-srt-bda-20261026`. Six new policy/address-boundary regression tests were added (twelve in total). They and the new SPIR-V emitter have not yet been tested on the user's machine.

## Limits and promotion criteria

- This is not a completed implementation of GPU-indexed texture/sampler sharp resolution. Dynamic SRT reads used only to synthesize descriptor identities may still require a GPU-side descriptor/indexing representation.
- On Vulkan devices without BDA support, unresolved dynamic reads are diagnosed but still lack a complete implementation. Do not claim cross-device compatibility.
- Run native-only isolated build; all twelve regressions must pass; verify no DynamoRIO, no title skips or compatibility overrides; retain original executable and production saves.
- Compare GoW against the preserved 120.314 s baseline under comparable VRAM headroom, including loading, frames, SRT warnings, Vulkan fault behavior, audio and gameplay (only mark what is actually observed).
- Check GoT with the same executable before promoting any universal renderer changes to main. Never silently merge unverified candidates.
- Append every build and gameplay result, positive or negative, into the immutable `~/projects/shadps4/diary/trials.jsonl` ledger.

The new source addresses specific emulator defects. It does not establish that Ragnarök's missing world graphics or GPU reset is fixed until runtime verification succeeds.
