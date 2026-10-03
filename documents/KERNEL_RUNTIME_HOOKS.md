# Kernel runtime hooks: audit and supported scope

Audit date: 2026-10-03. Base: `394b2f2987476243de39be9bfc19f5dedb5f95a4`.
Topic branch: `fix/kernel-runtime-hooks`.

## Evidence from the game report

The previously reviewed report `shadps4-graphics-evidence-09y4iovk.tar.gz`
recorded these generic stub calls on revision
`2a6ac3284da58175f386b8dcdda2d1f5ac6d26f0`. That was the subsequently
reverted PR integration build, **not a runtime test of this change**.

| Function | Calls | Result of this audit |
| --- | ---: | --- |
| `sceKernelGetSanitizerMallocReplaceExternal` | 1 | Explicit null pointer response for unavailable guest sanitizer replacement |
| `sceKernelGetSanitizerNewReplaceExternal` | 1 | Explicit null pointer response for unavailable guest sanitizer replacement |
| `_sceKernelSetThreadAtexitCount` | 1 | Deferred; generic stub diagnostic retained |
| `_sceKernelSetThreadAtexitReport` | 1 | Deferred; generic stub diagnostic retained |
| `_sceKernelRtldThreadAtexitIncrement` | 6 | Deferred; generic stub diagnostic retained |

The report ended normally. It does not demonstrate a crash, audio problem or
rendering defect caused by these calls. The user's graphics feedback remains
valid even without a corresponding renderer error in that report.

## Optional sanitizer queries

The existing `sceKernelIsAddressSanitizerEnabled` returns false. The HLE has no
guest sanitizer replacement provider. The two external queries now register as
typed System V functions returning `nullptr`, meaning no replacement is available.
Their NIDs are `py6L8jiVAN8` and `bnZxYgAFeA0`, exported by `libkernel`, version 1.
The real registration macros and host-call wrappers are used.

The public PS4 reference is
[fpPS4 libkernel at 04cefd43](https://github.com/red-prig/fpPS4/blob/04cefd43e6fddd1ab033e7980cd356d14c964905/kernel/ps4_libkernel.pas).
It declares both queries as pointer returns and returns nil. Its comments name
`__asan_malloc_replace_external` and `__asan_new_replace_external` as optional
runtime symbols. This is evidence for the unavailable-provider path, **not** a
complete native PS4 specification or evidence for supporting a loaded guest
sanitizer runtime.

The prior generic stubs also returned zero. Consequently this change preserves
the guest-visible result; it replaces two misleading generic error messages
with explicit optional-feature handling and debug messages. It is not a
confirmed gameplay bug fix and does not enable guest AddressSanitizer. Host
ASan/UBSan compiler options are unrelated to these guest queries.

No replacement tables, guest allocator callbacks, shadow-memory mapping or
non-external sanitizer queries are implemented by this patch.

## Why the thread-exit hooks remain unresolved

The same fpPS4 source associates the count/report setters with module unloading,
but its setters only log and return success. Its
[callback types](https://github.com/red-prig/fpPS4/blob/04cefd43e6fddd1ab033e7980cd356d14c964905/sys/sys_kernel.pas)
describe callbacks taking a module handle. These incomplete implementations do
not establish all PS4 callback and return-value contracts.

Its increment/decrement functions take a **code address**, find the owning
module and hold/release a module reference. The address must not be treated as
a writable integer counter. In this shadPS4 base, modules remain owned by the
linker; there is no implemented `sceKernelStopUnloadModule` path, and TLS update
code explicitly notes that module unloading is unsupported. Adding an unused
counter or saving callbacks without their lifecycle consumers would not
implement that behaviour.

Other checked public projects do not close the gap:

- [GPCS4 libkernel at 88480de3](https://github.com/Inori/GPCS4/blob/88480de36f983c66f7dd2c5951fead1a740d47a5/GPCS4/SceModules/SceLibkernel/sce_libkernel.cpp)
  contains generated/FIXME and dummy implementations.
- [ChonkyStation4 kernel at c101a23e](https://github.com/liuk7071/ChonkyStation4/blob/c101a23ecbc15e56c6e30d5dd24d3ed8880a957a/ChonkyStation4/OS/Libraries/Kernel/Kernel.cpp)
  registers these as stubs.

Before implementing the deferred hooks, establish the PS4 signatures, return
contracts, callback invocation order and module-reference semantics. Then test
registration/replacement of callbacks, thread destructor execution, invalid
code addresses, concurrent threads and unload while destructors remain pending.
Existing `_sceKernelSetThreadDtors` and pthread-exit handling are separate and
unchanged. The deferred hooks' generic stub diagnostics remain visible.

## Validation and limits

The focused test compiles the production sanitizer translation unit. It
captures only the symbol-registration sink and logger, checks both NIDs and
their library/module/version/type, and calls each registered host wrapper
through a System V pointer-returning function pointer. This checks the HLE
registration boundary, not full executable import relocation.

```sh
cmake -S tests/kernel_sanitizer -B build-kernel-sanitizer -DCMAKE_BUILD_TYPE=Release
cmake --build build-kernel-sanitizer
ctest --test-dir build-kernel-sanitizer --output-on-failure --no-tests=error
```

Passed on Linux x86-64 with GCC 13.3.0, C++23, Release. The local Docker installer
runs this check before compiling the emulator on subsequent builds. The main
emulator CMake source list and kernel registration call include the new unit.

No full emulator build, deployment or in-game test was performed for this patch.
There is no new evidence that the remaining thread-exit hooks or the original
graphics glitches are resolved. No audio, graphics, input or system-setup
behaviour is changed.
