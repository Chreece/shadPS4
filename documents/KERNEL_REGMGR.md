# Registry manager: request evidence before implementation

Date: 2026-10-03. Base: `cf59bf1f6ccac530b8105e7e9ace2d4656f31bcd`.
Topic branch: `diag/kernel-regmgr-request`.

## Observed call and blocker

The completed game capture `shadps4-graphics-evidence-jlbgkmnb.tar.gz` on
the base revision contains two startup calls to `__sys_regmgr_call`, both with
`op: 0x19, key: 0, len: 16`. It exits normally. Those lines do not identify the
registry entry, establish a gameplay failure caused by it, or justify assigning
a default registry value.

The implementation returns `ORBIS_OK` without writing either output. This is
still an incomplete stub. The current change **does not implement that syscall
or claim a game fix**. It supplies the missing request evidence needed to decide
which behaviour can be implemented responsibly.

## Public research

Checked upstream code and PR/issue searches for `regmgr`. No ready upstream fix
was found; the upstream function remains the same stub at
[8e23388a](https://github.com/shadps4-emu/shadPS4/blob/8e23388af83fe31ec6372a29e3940e2b459dfc6d/src/core/libraries/kernel/kernel.cpp).

Primary public implementations establish a useful request layout, but do not
constitute a complete native PS4 specification:

- [Obliteration, 2a53d5c4](https://github.com/obhq/obliteration/blob/2a53d5c4dec2ffc5c61b077a426240292fc45c08/legacy/src/regmgr/mod.rs)
  reads the encoded key at offset 0 and another input word at offset 8 for
  operation `0x19`. Its operation `0x18` integer setter reads the value at offset
  12. It separates the syscall return from registry status written through the
  third argument. Its matched-key `0x19` path remains a TODO.
- [RPCSX, e8ae1481](https://github.com/RPCSX/rpcsx/blob/e8ae1481ab7ba04d5c6bef89dd852aabba2c88ff/kernel/orbis/src/sys/sys_sce.cpp)
  defines a 16-byte operation-25 request: 64-bit encoded ID, 32-bit unknown word,
  then 32-bit value. Its integer values include explicitly marked hardcoded
  language/button defaults; its final fallback writes zero.
- [Prosperity, 9b2ca10b](https://github.com/Force67/prosperity/blob/9b2ca10b38b01342fc3ec86f817129b7973d3181/delta/kern/lv2/sys_regmgr.cc)
  also treats `0x19` as an encoded integer query. Its handling of unknown entries
  differs from the other projects. It is not sufficient authority for returning
  an error or zero for every request.

These sources agree on the fields needed for a diagnostic. They do not establish
which key this game requests. In particular, `key: 0` in the old log is the
second syscall argument, not the encoded key inside the request. No guessed
language, controller, debug, or memory setting has been added.

## Diagnostic scope

For `op == 0x19` and exactly 16 bytes, the syscall checks the complete buffer with
the existing guest memory manager's CPU-read range/permission check. If readable,
it copies only the 8-byte encoded key and 4-byte word at offset 8 into local
integers and includes them in the existing error message:

```text
(STUBBED) called, op: 0x19, key: 0, len: 16, encoded_key: 0x................, request_word8: 0x........; outputs unchanged
```

`request_word8` is intentionally not assigned a stronger semantic name. Neither
the value at offset 12 nor the separate result pointer is read; both may hold
uninitialized output storage. Nothing is written to guest memory. `memcpy`
handles unaligned requests. Null, inaccessible, wrong-length or other-operation
requests retain the original message and zero return without inspecting their
buffer. The function remains visibly marked as unsupported.

The range check follows the existing HLE lifetime contract: guest code must keep
its request mapped for the duration of the call. It does not pin mappings or
attempt to handle concurrent unmapping by the caller.

## Checks and next local capture

The focused suite extracts the exact production syscall body and exercises the
real range-validation helper and log formatting with a synthetic guest mapping
table. It does not start the full memory manager or load a game.

Twelve cases passed on Linux x86-64, GCC 13.3: valid input, unaligned input, two
adjacent readable mappings, short request, oversized request, another operation,
null pointer, unmapped pointer, partial mapping, unreadable mapping, mapping gap,
and an invalid result pointer. Each checks unchanged outputs/return and one stub
message. Invalid pointers test that unsupported buffers/result are not accessed.

Both Release and Debug ASan/UBSan with leak checking passed. The existing local
installer's 21 regression tests also passed. The installer now runs the focused
`kernel_regmgr` suite before the full local Docker emulator build. No all-platform
CI or full emulator build was performed here.

```sh
cmake -S tests/kernel_regmgr -B build-regmgr -DCMAKE_BUILD_TYPE=Release
cmake --build build-regmgr
ctest --test-dir build-regmgr --output-on-failure --no-tests=error
```

Next, build this revision locally and use the existing session collector. Once
`CAPTURE_ARMED` appears, launch the ordinary game entry, reach gameplay, and exit
normally. The report is produced after exit. Review its exact binary/revision,
the new encoded keys, and user feedback before choosing any registry behaviour.
If detailed fields are missing, investigate the buffer mapping instead of
loosening validation or inventing values. Existing audio/graphics/input fixes,
saves, and settings are outside this diagnostic's scope.
