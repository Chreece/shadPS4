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
| `_sceKernelSetThreadAtexitCount` | 1 | Stores a module-count callback; warns that unloading remains unsupported |
| `_sceKernelSetThreadAtexitReport` | 1 | Stores a module-report callback; warns that unloading remains unsupported |
| `_sceKernelRtldThreadAtexitIncrement` | 6 | Resolves a module address and acquires a reference in that module's state |

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

## Thread-exit registration and reference tracking

The initial audit deferred these hooks. Further research found an actual PS4
callback consumer in
[Kyty at 4733b7e1](https://github.com/InoriRus/Kyty/blob/4733b7e1c91b10554a52007903d74dc76c39a230/source/emulator/src/Libs/LibKernel.cpp).
Kyty's setters retain callback pointers. Its unload path calls the count callback
with a 32-bit module handle; when the result is positive it optionally calls the
report callback and returns busy. The callback types are an integer-returning
count function and a void report function, both taking that handle.

The fpPS4
[callback types](https://github.com/red-prig/fpPS4/blob/04cefd43e6fddd1ab033e7980cd356d14c964905/sys/sys_kernel.pas)
agree on the module-handle argument and callback results. Its libkernel setters
are incomplete, but its increment/decrement functions acquire/release an actual
module reference. The
[address lookup](https://github.com/red-prig/fpPS4/blob/04cefd43e6fddd1ab033e7980cd356d14c964905/ps4_program.pas)
uses the module's mapped address range. The argument is a **module address**,
not a pointer to a writable integer counter. This also covers addresses in
module data, not just executable instructions.

Implemented scope:

- Both callback setters retain the supplied pointer with synchronized access.
  Registration does not execute either callback. An internal snapshot API makes
  the state available for a future unload consumer without holding the host
  registration lock across guest calls.
- Increment uses the real linker module lookup, returns `-1` for an unknown
  module address, and otherwise increments an atomic counter owned by that
  module and returns zero. The lookup now locks against concurrent module
  insertion and uses overflow-safe address-range comparison.
- The matching decrement export, `8OnWXlgQlvo`, releases one reference from the
  resolved module. Missing modules and unmatched releases leave counters
  unchanged and generate diagnostics. Counters cannot wrap on underflow.
- Guest memory at the supplied address is never dereferenced or changed.

The sources differ on whether the setters/decrement return void or integer
zero. The HLE deliberately retains the previous zero result for these three
entry points; callers expecting void ignore the return register. This is a
compatibility choice, not a claim that the native return signatures have been
fully established. Null/replacement registration is retained literally; this
patch does not invent a native error contract for those cases.

**This is not a module-unloading implementation.** Module objects and mappings
already remain resident for the process lifetime, so the new counters record
outstanding references without changing ownership or unmapping anything. The
count/report callbacks have no live unload consumer yet. Both setters retain
explicit warnings about this limitation. A future unload path must coordinate
module lookup/acquisition, outstanding references, callback invocation, module
stop, TLS and mappings before permitting removal. Storing the callbacks and
tracking references alone do not complete that path.

Other checked public projects were not used as behavioural authority:

- [GPCS4 libkernel at 88480de3](https://github.com/Inori/GPCS4/blob/88480de36f983c66f7dd2c5951fead1a740d47a5/GPCS4/SceModules/SceLibkernel/sce_libkernel.cpp)
  contains generated/FIXME and dummy implementations.
- [ChonkyStation4 kernel at c101a23e](https://github.com/liuk7071/ChonkyStation4/blob/c101a23ecbc15e56c6e30d5dd24d3ed8880a957a/ChonkyStation4/OS/Libraries/Kernel/Kernel.cpp)
  registers these as stubs.

Existing `_sceKernelSetThreadDtors` and pthread-exit handling are separate and
unchanged. These changes do not introduce an alternative destructor runner or
call the count/report callbacks as destructors. Public emulator evidence does
not replace a native-hardware ABI conformance test.

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

The thread-atexit check uses the production entry points and reference-state
implementation with a synthetic module lookup. The real lookup and adapter
translation unit also compiles against the real Module/Linker declarations;
the focused executable does not exercise real ELF loading.

```sh
cmake -S tests/kernel_thread_atexit -B build-kernel-thread -DCMAKE_BUILD_TYPE=Release
cmake --build build-kernel-thread
ctest --test-dir build-kernel-thread --output-on-failure --no-tests=error

cmake -S tests/kernel_thread_atexit -B build-kernel-thread-asan \
  -DCMAKE_BUILD_TYPE=Debug -DKERNEL_THREAD_ATEXIT_SANITIZERS=ON
cmake --build build-kernel-thread-asan
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
  ctest --test-dir build-kernel-thread-asan --output-on-failure --no-tests=error
```

Passed on Linux x86-64 with GCC 13.3.0 and C++23:

- Release production export/reference test: four NIDs, library/module metadata,
  callback argument/result handling, storage and re-entry, independent module
  counts, invalid addresses, unmatched release and read-only guest data.
- Eight concurrent threads: 32,000 acquisitions, 32,000 releases, then another
  32,000 mixed acquire/release pairs; final count zero without underflow warnings.
  Concurrent callback replacement/snapshots also passed.
- Debug ASan/UBSan with leak checking: passed. LeakSanitizer required process
  visibility outside the restricted test sandbox; its initial `/proc` access
  failure was environmental and the rerun completed successfully.
- Negative control: replacing increment with the old zero-return behaviour
  causes the test to fail on missing module-reference state.
- Existing local installer regression suite: 15 tests passed.

The local Docker installer runs both focused kernel suites before compiling the
emulator on subsequent builds. Main CMake and kernel registration include the
new units. The test adapter suppresses the existing signed/unsigned comparison
warning in `Linker::GetModule`; the production header was not changed for it.

No full emulator build, deployment or in-game test was performed for this patch.
Real guest callback registration, address resolution and thread-exit releases
still need a runtime check on this exact revision. There is no new evidence that
the original graphics glitches are resolved. No audio, graphics, input or
system-setup code is changed.
